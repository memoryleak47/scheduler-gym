# scheduler gym

In this repo I want to collect a couple of benchmarks, and see how different schedulers fare on them.
In particular, I want to benchmark the lhs/rhs/class cost idea.

The schedulers directory contains rust files which define `sched_iter` and `sched_init` functions. Those concatted together with `gym-common.rs` define a run function, similar to eggs `Runner::run`.
The case-studies directory contains case studies that we use to measure the quality of schedulers.


## Benchmarking

To run the benchmarks, run `uv run bench.py`.
Every run uses the case study's own node and time limits.
See `uv run bench.py --help` for `--schedulers`.

For reproducible timings, switch to bench mode first (disables boost and SMT, locks the CPU frequency, and confines system and user services to CPU 0):

```sh
sudo ./benchmode.sh on
./benchmode.sh run uv run bench.py --schedulers backoff,random,lhs-cost,rhs-cost
sudo ./benchmode.sh off
```

`benchmode.sh run` needs a tty, so start long runs inside tmux (append `2>&1 | tee bench.log` to keep the output).
Switch bench mode off afterwards even if the run failed, it stays on until you do.

Every (case study, scheduler) pair is a job.
One worker per physical core pulls jobs from a shared queue and runs them pinned to its core, in a private copy of the case study under `.bench-work/` (synced with rsync at the start of each run).
Results go to `benchdata/<scheduler>.rs/<case>.entries`, jobs whose results already exist are skipped.
Temperature and per-core frequency are logged to `benchdata/thermals-*.csv`.

## Figures

To create the figures, run `uv sync` and open the `figures.ipynb` notebook.
Make sure to pick the right virtual env.