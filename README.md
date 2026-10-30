scheduler gym
=============

In this repo I want to collect a couple of benchmarks, and see how different schedulers fare on them.
In particular, I want to benchmark the detour cost idea.

The schedulers directory contains rust files which define `sched_iter` and `sched_init` functions. Those concatted together with `gym-common.rs` define a run function, similar to eggs `Runner::run`.
The case-studies directory contains case studies that we use to measure the quality of schedulers.

## Benchmarking

To run the benchmarks, run `uv run bench.py`. This will write data to `benchdata`.

## Figures

To create the figures, run `uv sync` and open the `figures.ipynb` notebook.
Make sure to pick the right virtual env.