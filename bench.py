#!/usr/bin/env python3

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gym import CASE_STUDIES, SCHEDULERS

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"


def bench1(scheduler: str, case: str, tmp: Path):
    out_dir = BENCHDATA / f"{scheduler}.rs"
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
        (ROOT / "schedulers" / f"{scheduler}.rs").read_text()
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
