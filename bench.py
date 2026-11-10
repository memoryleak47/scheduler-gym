#!/usr/bin/env python3

import argparse
import asyncio
import csv
import os
import re
import shutil
import tempfile
import time
from collections import deque
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"
# per-worker copies of the case studies, kept across runs for incremental builds
WORKDIR = ROOT / ".bench-work"
SYS_CPU = Path("/sys/devices/system/cpu")
# written by `benchmode.sh on`
BENCHMODE_CPUS = Path("/run/benchmode/bench-cpus")
# every scheduler in schedulers/, without the .rs suffix
SCHEDULERS = tuple(sorted(p.stem for p in (ROOT / "schedulers").glob("*.rs")))
# slowest first, so the long jobs don't end up as the tail of the run
CASE_STUDIES = ("szalinski", "integ", "trig", "lean-egg", "caviar")
# CASE_STUDIES = ("lean-egg", "szalinski", "integ", "trig")


def physical_cpus() -> list[int]:
    """The allowed CPUs, one per physical core, grouped by shared L3 cache."""
    groups: dict[str, list[int]] = {}
    seen_cores = set()
    for cpu in sorted(os.sched_getaffinity(0)):
        d = SYS_CPU / f"cpu{cpu}"
        core = (d / "topology/core_cpus_list").read_text().strip()
        if core in seen_cores:
            continue  # SMT sibling
        seen_cores.add(core)
        l3 = (d / "cache/index3/shared_cpu_list").read_text().strip()
        groups.setdefault(l3, []).append(cpu)
    return [cpu for group in groups.values() for cpu in group]


class CopyPool:
    """Hands out private copies of a case study, so several builds can run in place at once.

    Copies are created lazily (at most one per concurrently running job) and synced with
    the original once per run. Build outputs (`target/`) are not synced, every copy keeps
    its own, so later builds in it stay incremental.
    """

    def __init__(self, case: str):
        self.case = case
        self.free: list[Path] = []
        self.count = 0

    async def acquire(self, cpu: int) -> Path:
        if self.free:
            return self.free.pop()
        # the case name has to stay the last path component, some run.sh pick
        # what to run based on `$(pwd)`
        copy = WORKDIR / str(self.count) / self.case
        self.count += 1
        copy.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "taskset", "-c", str(cpu),
            "rsync", "-a", "--delete", "--exclude=target/",
            f"{ROOT / 'case-studies' / self.case}/", f"{copy}/",
        )  # fmt: skip
        if await proc.wait() != 0:
            raise RuntimeError(f"syncing {copy} failed")
        return copy

    def release(self, copy: Path):
        self.free.append(copy)


def find_tctl() -> Path | None:
    for hwmon in Path("/sys/class/hwmon").iterdir():
        if (hwmon / "name").read_text().strip() == "k10temp":
            return hwmon / "temp1_input"
    return None


def parse_args() -> tuple[Path, dict[str, str], list[str]]:
    """The output folder for the given limit, the env vars that pass it on to
    gym-common.rs, and the schedulers to run."""
    parser = argparse.ArgumentParser(
        description="Run every scheduler on every case study."
    )
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument(
        "--time-limit",
        type=float,
        metavar="SECONDS",
        help="stop each run after SECONDS, with no node limit",
    )
    limit.add_argument(
        "--node-limit",
        type=int,
        metavar="NODES",
        help="stop each run at an e-graph size of NODES, with no time limit",
    )
    limit.add_argument(
        "--native-limit",
        action="store_true",
        help="keep each case study's own node and time limits",
    )
    parser.add_argument(
        "--schedulers",
        metavar="PREFIXES",
        help="comma-separated prefixes, only run the schedulers named <prefix>-<k>"
        " or exactly <prefix>, e.g. backoff,random,class-cost",
    )
    args = parser.parse_args()
    schedulers = list(SCHEDULERS)
    if args.schedulers is not None:
        prefixes = [p for p in args.schedulers.split(",") if p]
        if not prefixes:
            parser.error("--schedulers needs at least one prefix")
        pattern = re.compile(rf"(?:{'|'.join(map(re.escape, prefixes))})(?:-\d+)?")
        schedulers = [s for s in SCHEDULERS if pattern.fullmatch(s)]
        if unmatched := [
            p
            for p in prefixes
            if not any(re.fullmatch(rf"{re.escape(p)}(?:-\d+)?", s) for s in schedulers)
        ]:
            parser.error(f"no scheduler matches {', '.join(unmatched)}")
    if args.native_limit:
        # empty, not absent, so values inherited from the shell don't leak in;
        # gym-common.rs treats empty as unset
        return (
            BENCHDATA / "native",
            {"GYM_TIME_LIMIT": "", "GYM_NODE_LIMIT": ""},
            schedulers,
        )
    if args.time_limit is not None:
        if args.time_limit <= 0:
            parser.error("--time-limit must be positive")
        return (
            BENCHDATA / f"time-{args.time_limit:g}",
            {"GYM_TIME_LIMIT": str(args.time_limit)},
            schedulers,
        )
    if args.node_limit <= 0:
        parser.error("--node-limit must be positive")
    return (
        BENCHDATA / f"nodes-{args.node_limit}",
        {"GYM_NODE_LIMIT": str(args.node_limit)},
        schedulers,
    )


async def monitor(out: Path, cpus: list[int], running: dict[int, str]):
    """Logs CPU temperature and per-core job and frequency, to check for throttling."""
    tctl = find_tctl()
    path = out / f"thermals-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    # min_mhz only gets entries for cores that actually ran something
    max_temp, min_mhz = float("-inf"), {}
    with path.open("w", newline="") as f:
        rows = csv.writer(f)
        rows.writerow(
            ["time", "tctl"] + [f"cpu{c}_{x}" for c in cpus for x in ("job", "mhz")]
        )
        try:
            while True:
                temp = int(tctl.read_text()) / 1000 if tctl else float("nan")
                max_temp = max(max_temp, temp)
                row = [f"{time.time():.1f}", f"{temp:.1f}"]
                for cpu in cpus:
                    freq = SYS_CPU / f"cpu{cpu}/cpufreq/cpuinfo_avg_freq"
                    mhz = int(freq.read_text()) / 1000
                    if running[cpu]:
                        min_mhz[cpu] = min(min_mhz.get(cpu, mhz), mhz)
                    row += [running[cpu], f"{mhz:.0f}"]
                rows.writerow(row)
                f.flush()
                await asyncio.sleep(1)
        finally:
            print(f"thermal log: {path}")
            temp = f"{max_temp:.1f} °C" if tctl else "n/a"
            print(
                f"max Tctl {temp}, min MHz while running { {c: round(m) for c, m in min_mhz.items()} }"
            )


async def run_job(
    case: str,
    scheduler: str,
    cwd: Path,
    cpu: int,
    tmp: Path,
    out: Path,
    limit_env: dict[str, str],
) -> bool:
    out_dir = out / f"{scheduler}.rs"
    out_dir.mkdir(parents=True, exist_ok=True)
    sched = tmp / f"{case}.{scheduler}.rs"
    entries = tmp / f"{case}.{scheduler}.entries.txt"
    sched.write_text(
        (ROOT / "schedulers" / f"{scheduler}.rs").read_text()
        + (ROOT / "gym-common.rs").read_text()
    )

    with (out_dir / f"{case}.log").open("w") as f:
        # pinned to its own core, so jobs can't disturb each other's caches
        proc = await asyncio.create_subprocess_exec(
            "taskset", "-c", str(cpu), "bash", "./run.sh", str(sched),
            cwd=cwd,
            env={**os.environ, **limit_env, "GYM_ENTRIES": str(entries)},
            stdout=f,
            stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        await proc.wait()

    sched.unlink()
    if not entries.exists():
        return False
    shutil.move(entries, out_dir / f"{case}.entries")
    if ends_mid_run(out_dir / f"{case}.entries"):
        # the exit code can't tell, some run.sh exit non-zero on success
        tqdm.write(
            f"[{case}] scheduler '{scheduler}' stopped mid-run (crashed or killed?), "
            f"its last run will be dropped, see {out_dir / f'{case}.log'}"
        )
    return True


def ends_mid_run(entries: Path) -> bool:
    """Whether the last entry of `entries` has no stop reason, so the process ended
    during a run; reads only the end, the files can be large."""
    with entries.open("rb") as f:
        f.seek(max(0, f.seek(0, os.SEEK_END) - 4096))
        lines = f.read().decode(errors="replace").splitlines()
    return bool(lines) and lines[-1].rstrip().endswith("stop=None")


async def worker(
    cpu: int,
    jobs: deque[tuple[str, str]],
    pools: dict[str, CopyPool],
    bars: dict[str, tqdm],
    running: dict[int, str],
    tmp: Path,
    out: Path,
    limit_env: dict[str, str],
):
    while jobs:
        case, scheduler = jobs.popleft()
        running[cpu] = f"{case}/{scheduler}"
        copy = await pools[case].acquire(cpu)
        try:
            ok = await run_job(case, scheduler, copy, cpu, tmp, out, limit_env)
        finally:
            pools[case].release(copy)
            running[cpu] = ""
        if not ok:
            log = out / f"{scheduler}.rs" / f"{case}.log"
            tqdm.write(
                f"[{case}] scheduler '{scheduler}' FAILED (no entries), see {log}"
            )
        bars[case].update()


async def main(out: Path, limit_env: dict[str, str], schedulers: list[str]):
    out.mkdir(parents=True, exist_ok=True)
    print(f"results in {out}")
    print(f"{len(schedulers)} schedulers: {', '.join(schedulers)}")
    if not BENCHMODE_CPUS.exists():
        print("WARNING: bench mode is off, results may suffer from boost/throttling")
    cpus = physical_cpus()
    print(f"{len(cpus)} workers on cpus {cpus}")

    jobs = deque(
        (case, scheduler)
        for case in CASE_STUDIES
        for scheduler in schedulers
        if not (out / f"{scheduler}.rs" / f"{case}.entries").exists()
    )
    width = max(map(len, CASE_STUDIES))
    bars = {
        case: tqdm(
            desc=case.ljust(width),
            position=i,
            total=sum(c == case for c, _ in jobs),
        )
        for i, case in enumerate(CASE_STUDIES)
    }
    pools = {case: CopyPool(case) for case in CASE_STUDIES}

    running = dict.fromkeys(cpus, "")
    mon = asyncio.create_task(monitor(out, cpus, running))
    with tempfile.TemporaryDirectory() as tmp:
        async with asyncio.TaskGroup() as tg:
            for cpu in cpus:
                tg.create_task(
                    worker(cpu, jobs, pools, bars, running, Path(tmp), out, limit_env)
                )
    for bar in bars.values():
        bar.close()
    mon.cancel()
    # only swallow the cancellation, so a crashed monitor doesn't go unnoticed
    try:
        await mon
    except asyncio.CancelledError:
        pass

    print("FINISHED ALL")


if __name__ == "__main__":
    asyncio.run(main(*parse_args()))
