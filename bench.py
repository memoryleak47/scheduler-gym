#!/usr/bin/env python3

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCHEDULERS = [
    "detour-lhs-400.rs",
    "backoff.rs",
    "detour-rhs-400.rs",
    "detour1.rs",
    "ctxt-400.rs",
    "size-400.rs",
    "pat-400.rs",
    "detour-classbased-400.rs",
    "detour-rhs-400-classboost.rs",
    "detour-rhs-400-classboost-halfed.rs",
    "detour-min-400.rs",
    "backoff-fresh.rs",
    "backoff-illegal.rs",
    "backoff-legal.rs",
    "detour-classbased-1000.rs",
    "detour-classbased-500.rs",
    "detour-lhs-400-dynamic.rs",
    "detour-lhs-400-prestart.rs",
    "detour-lhs-400-shortcut-simple.rs",
    "detour-lhs-400-shortcut.rs",
    "detour-lhs-400-simple.rs",
    "detour-lhs-400-simple2.rs",
    "detour-lhs-400-simple3.rs",
    "detour-lhs-400-simple4-800.rs",
    "detour-lhs-400-simple4-nostorage.rs",
    "detour-lhs-400-simple4-shortcut.rs",
    "detour-lhs-400-simple4.rs",
    "detour-lhs-400_800-shortcut.rs",
    "detour-rhs-100.rs",
    "detour-rhs-20.rs",
    "detour-rhs-200.rs",
    "detour-rhs-300.rs",
    "detour-rhs-400-boost.rs",
    "detour-rhs-400-boost2.rs",
    "detour-rhs-400-merge.rs",
    "detour-rhs-500.rs",
    "detour-rhs.rs",
    "detour-vanilla.rs",
    "detour1-400.rs",
    "detour1-rhs.rs",
    "size-bounded.rs",
]
CASE_STUDIES = ["caviar", "szalinski", "trig", "integ"]

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"


def bench1(scheduler: str, case: str, tmp: Path):
    out_dir = BENCHDATA / scheduler
    out_dir.mkdir(parents=True, exist_ok=True)
    out_entries = out_dir / f"{case}.entries"
    log = out_dir / f"{case}.log"
    if out_entries.exists():
        return

    print(f"[{case}] scheduler '{scheduler}' started", flush=True)

    # per-case-study files, so case studies can run in parallel
    sched = tmp / f"{case}.scheduler.rs"
    entries = tmp / f"{case}.entries.txt"
    entries.unlink(missing_ok=True)

    sched.write_text(
        (ROOT / "schedulers" / scheduler).read_text()
        + (ROOT / "gym-common.rs").read_text()
    )

    with log.open("w") as f:
        subprocess.run(
            ["bash", "./run.sh", str(sched)],
            cwd=ROOT / "case-studies" / case,
            env={**os.environ, "GYM_ENTRIES": str(entries)},
            stdout=f,
            stderr=subprocess.STDOUT,
            check=False,
        )

    if entries.exists():
        shutil.move(entries, out_entries)
        print(f"[{case}] scheduler '{scheduler}' done", flush=True)
    else:
        print(
            f"[{case}] scheduler '{scheduler}' FAILED (no entries), see {log}",
            flush=True,
        )


def main():
    BENCHDATA.mkdir(exist_ok=True)
    with (
        tempfile.TemporaryDirectory() as tmp,
        ThreadPoolExecutor(len(CASE_STUDIES)) as pool,
    ):

        def run_case_study(case: str):
            # schedulers run sequentially per case study, since each builds in place
            for scheduler in SCHEDULERS:
                bench1(scheduler, case, Path(tmp))

        # list() re-raises worker exceptions
        list(pool.map(run_case_study, CASE_STUDIES))

    print("FINISHED ALL")


if __name__ == "__main__":
    main()
