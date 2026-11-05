#!/usr/bin/env python3

import asyncio
import csv
import os
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
# CASE_STUDIES = ("caviar", "lean-egg", "szalinski", "integ", "trig")
CASE_STUDIES = ("lean-egg", "szalinski", "integ", "trig")


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


async def monitor(cpus: list[int], running: dict[int, str]):
    """Logs CPU temperature and per-core job and frequency, to check for throttling."""
    tctl = find_tctl()
    path = BENCHDATA / f"thermals-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    # min_mhz only gets entries for cores that actually ran something
    max_temp, min_mhz = float("-inf"), {}
    with path.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(
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
                out.writerow(row)
                f.flush()
                await asyncio.sleep(1)
        finally:
            print(f"thermal log: {path}")
            temp = f"{max_temp:.1f} °C" if tctl else "n/a"
            print(
                f"max Tctl {temp}, min MHz while running { {c: round(m) for c, m in min_mhz.items()} }"
            )


async def run_job(case: str, scheduler: str, cwd: Path, cpu: int, tmp: Path) -> bool:
    out_dir = BENCHDATA / f"{scheduler}.rs"
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
            env={**os.environ, "GYM_ENTRIES": str(entries)},
            stdout=f,
            stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        await proc.wait()

    sched.unlink()
    if not entries.exists():
        return False
    shutil.move(entries, out_dir / f"{case}.entries")
    return True


async def worker(
    cpu: int,
    jobs: deque[tuple[str, str]],
    pools: dict[str, CopyPool],
    bars: dict[str, tqdm],
    running: dict[int, str],
    tmp: Path,
):
    while jobs:
        case, scheduler = jobs.popleft()
        running[cpu] = f"{case}/{scheduler}"
        copy = await pools[case].acquire(cpu)
        try:
            ok = await run_job(case, scheduler, copy, cpu, tmp)
        finally:
            pools[case].release(copy)
            running[cpu] = ""
        if not ok:
            log = BENCHDATA / f"{scheduler}.rs" / f"{case}.log"
            tqdm.write(
                f"[{case}] scheduler '{scheduler}' FAILED (no entries), see {log}"
            )
        bars[case].update()


async def main():
    BENCHDATA.mkdir(exist_ok=True)
    if not BENCHMODE_CPUS.exists():
        print("WARNING: bench mode is off, results may suffer from boost/throttling")
    cpus = physical_cpus()
    print(f"{len(cpus)} workers on cpus {cpus}")

    jobs = deque(
        (case, scheduler)
        for case in CASE_STUDIES
        for scheduler in SCHEDULERS
        if not (BENCHDATA / f"{scheduler}.rs" / f"{case}.entries").exists()
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
    mon = asyncio.create_task(monitor(cpus, running))
    with tempfile.TemporaryDirectory() as tmp:
        async with asyncio.TaskGroup() as tg:
            for cpu in cpus:
                tg.create_task(worker(cpu, jobs, pools, bars, running, Path(tmp)))
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
    asyncio.run(main())
