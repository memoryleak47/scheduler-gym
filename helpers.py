import re
from collections.abc import Sequence
from itertools import chain, repeat
from pathlib import Path
from typing import Literal, get_args

import matplotlib as mpl
import numpy as np
import polars as pl
from matplotlib.colors import LinearSegmentedColormap

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"
FIGURES = ROOT / "figures"

KEY = ("case", "scheduler", "run")
Resource = Literal["time", "size"]
Metric = Literal["cost", "time", "size"]
Scheduler = Literal[
    "backoff",
    "detour-lhs-400",
    "detour-rhs-400",
    "detour-min-400",
    "detour1",
    "ctxt-400",
    "size-400",
    "pat-400",
    "detour-classbased-400",
    "detour-rhs-400-classboost",
    "detour-rhs-400-classboost-halfed",
    "backoff-fresh",
    "backoff-illegal",
    "backoff-legal",
    "detour-classbased-1000",
    "detour-classbased-500",
    "detour-lhs-400-dynamic",
    "detour-lhs-400-prestart",
    "detour-lhs-400-shortcut-simple",
    "detour-lhs-400-shortcut",
    "detour-lhs-400-simple",
    "detour-lhs-400-simple2",
    "detour-lhs-400-simple3",
    "detour-lhs-400-simple4-800",
    "detour-lhs-400-simple4-nostorage",
    "detour-lhs-400-simple4-shortcut",
    "detour-lhs-400-simple4",
    "detour-lhs-400_800-shortcut",
    "detour-rhs-100",
    "detour-rhs-20",
    "detour-rhs-200",
    "detour-rhs-300",
    "detour-rhs-400-boost",
    "detour-rhs-400-boost2",
    "detour-rhs-400-merge",
    "detour-rhs-500",
    "detour-rhs",
    "detour-vanilla",
    "detour1-400",
    "detour1-rhs",
    "size-bounded",
]

RESOURCES: dict[Resource, str] = {"time": "time [s]", "size": "e-graph size"}

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
NEUTRAL = "#f0efec"
PALETTE = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)

# the shown schedulers and their colors, fixed so a scheduler looks the same in every figure
SCHEDULERS: dict[Scheduler, str] = {}


def select(schedulers: Sequence[Scheduler]):
    """Show only `schedulers`, colored in this order (the palette has 8 colors, the rest are gray)."""
    global SCHEDULERS
    SCHEDULERS = dict(zip(schedulers, chain(PALETTE, repeat(MUTED))))


select(get_args(Scheduler))
DIVERGING = LinearSegmentedColormap.from_list("wins", [PALETTE[7], NEUTRAL, PALETTE[0]])

ENTRY_RE = re.compile(
    r"costs=\[([^\]]*)\], total_size=(\S+), time=(\S+), iteration=(\d+), stop=(.*)$"
)


def setup_style():
    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "lines.linewidth": 2,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.dpi": 110,
            "pdf.fonttype": 42,
        }
    )


def save(fig, name: str):
    FIGURES.mkdir(exist_ok=True)
    fig.savefig(FIGURES / f"{name}.pdf", bbox_inches="tight")


def legend(fig):
    """One legend for all axes of `fig`, outside on the right."""
    handles = {}
    for ax in fig.axes:
        for h, label in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(label, h)
    labels = [s for s in SCHEDULERS if s in handles]
    fig.legend([handles[s] for s in labels], labels, loc="outside right upper")


def load(*, root: Path = BENCHDATA) -> pl.DataFrame:
    """One row per entry; `run` numbers the runs within a file. Unfinished trailing runs are dropped."""
    rows = []
    for path in sorted(root.glob("*/*.entries")):
        run, file_rows = 0, []
        for line in path.read_text().splitlines():
            m = ENTRY_RE.search(line)
            if m is None:
                raise ValueError(f"{path}: malformed entry {line!r}")
            costs, size, time, iteration, stop = m.groups()
            stop = stop.strip()
            file_rows.append(
                (
                    path.stem,
                    path.parent.name.removesuffix(".rs"),
                    run,
                    int(iteration),
                    sum(float(c) for c in costs.split(", ")),
                    float(size),
                    float(time),
                    None if stop == "None" else stop,
                )
            )
            if stop != "None":
                run += 1
                rows += file_rows
                file_rows = []

    schema = ("case", "scheduler", "run", "iteration", "cost", "size", "time", "stop")
    return pl.DataFrame(rows, schema=schema, orient="row")


def finals(df: pl.DataFrame) -> pl.DataFrame:
    """One row per run: final cost, best final cost over all schedulers, and resources needed to reach it."""
    last = df.group_by(KEY, maintain_order=True).last()
    last = last.with_columns(best=pl.col("cost").min().over("case", "run"))

    reached = (
        df.join(last.select(*KEY, "best"), on=KEY)
        .filter(pl.col("cost") <= pl.col("best"))
        .group_by(KEY, maintain_order=True)
        .first()
        .select(*KEY, *(pl.col(r).alias(f"{r}_to_best") for r in RESOURCES))
    )
    return last.join(reached, on=KEY, how="left")


def check_runs(fin: pl.DataFrame):
    counts = fin.group_by("case", "scheduler").len()
    for (case,), g in counts.group_by("case"):
        if g["len"].n_unique() > 1:
            print(f"{case}: schedulers disagree on number of runs:")
            print(g.sort("len").select("scheduler", "len"))


def plot_ranking(fin: pl.DataFrame, case: str, ax):
    """Geometric mean of final cost relative to the best scheduler per run (1 = always best)."""
    d = (
        fin.filter(pl.col("case") == case)
        .group_by("scheduler")
        .agg(ratio=(pl.col("cost") / pl.col("best")).log().mean().exp(), runs=pl.len())
        .sort("ratio", descending=True)
    )
    y = np.arange(len(d))
    ax.barh(y, d["ratio"] - 1, left=1, height=0.6, color=PALETTE[0])
    ax.set_yticks(y, d["scheduler"])
    for yi, r in zip(y, d["ratio"]):
        ax.annotate(
            f"{r:.3f}",
            (r, yi),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK_2,
        )
    ax.grid(axis="y", visible=False)
    ax.set_xlim(left=1)
    ax.set_xlabel("final cost / best final cost (geomean)")
    ax.set_title(f"{case} ({d['runs'][0]} runs)")


def plot_sum_ranking(fin: pl.DataFrame, case: str, ax, *, metric: Metric = "cost"):
    """Sum of the final `metric` (cost, time or size) over all runs, like dump.py's dumpall."""
    d = (
        fin.filter(pl.col("case") == case)
        .group_by("scheduler")
        .agg(total=pl.col(metric).sum(), runs=pl.len())
        .sort("total", descending=True)
    )
    y = np.arange(len(d))
    ax.barh(y, d["total"], height=0.6, color=PALETTE[0])
    ax.set_yticks(y, d["scheduler"])
    for yi, t in zip(y, d["total"]):
        ax.annotate(
            f"{t:.4g}",
            (t, yi),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK_2,
        )
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"total final {RESOURCES.get(metric, metric)}")
    ax.set_title(f"{case} ({d['runs'][0]} runs)")


def win_matrix(fin: pl.DataFrame, case: str) -> tuple[list[Scheduler], np.ndarray]:
    """M[i, j] = (wins - losses) / runs of scheduler i against j."""
    wide = (
        fin.filter(pl.col("case") == case)
        .pivot("scheduler", index="run", values="cost")
        .drop("run")
    )
    names = [s for s in SCHEDULERS if s in wide.columns]
    c = wide.select(names).to_numpy()
    n = len(names)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            ok = ~np.isnan(c[:, i]) & ~np.isnan(c[:, j])
            m[i, j] = (np.sign(c[ok, j] - c[ok, i])).mean() if ok.any() else np.nan
    return names, m


def plot_wins(fin: pl.DataFrame, case: str, ax):
    names, m = win_matrix(fin, case)
    im = ax.imshow(m, cmap=DIVERGING, vmin=-1, vmax=1)
    ax.set_xticks(range(len(names)), names, rotation=45, ha="right")
    ax.set_yticks(range(len(names)), names)
    ax.grid(False)
    ax.spines[:].set_visible(False)
    ax.set_xticks(np.arange(len(names) + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(names) + 1) - 0.5, minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="both", length=0)
    for i in range(len(names)):
        for j in range(len(names)):
            if i != j:
                ax.text(
                    j,
                    i,
                    f"{m[i, j]:+.2f}",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if abs(m[i, j]) > 0.6 else INK,
                )
    ax.set_title(f"{case}: row vs column\n(wins − losses) / runs")
    return im


def plot_profile(
    fin: pl.DataFrame,
    case: str,
    res: Resource,
    ax,
    *,
    schedulers: Sequence[Scheduler] | None = None,
):
    """Fraction of runs where a scheduler reached the best known cost within the given resource."""
    if schedulers is None:
        schedulers = tuple(SCHEDULERS)
    d = fin.filter(pl.col("case") == case)
    for s in schedulers:
        runs = d.filter(pl.col("scheduler") == s)
        if runs.is_empty():
            continue
        xs = np.sort(runs[f"{res}_to_best"].drop_nulls().to_numpy())
        ys = np.arange(1, len(xs) + 1) / len(runs)
        x = np.r_[d[f"{res}_to_best"].min(), xs, d[res].max()]
        y = np.r_[0, ys, ys[-1] if len(ys) else 0]
        ax.step(x, y, where="post", color=SCHEDULERS[s], label=s)
    ax.set_xscale("log")
    ax.set_ylim(0, 1.02)
    ax.set_xlabel(RESOURCES[res])
    ax.set_ylabel("runs at best known cost")
    ax.set_title(case)


def plot_quality(
    df: pl.DataFrame,
    case: str,
    run: int,
    res: Resource,
    ax,
    *,
    schedulers: Sequence[Scheduler] | None = None,
):
    """Cost over resource for one run, cut off once the best cost among `schedulers` is reached (marked with ★)."""
    if schedulers is None:
        schedulers = tuple(SCHEDULERS)
    d = df.filter(
        (pl.col("case") == case)
        & (pl.col("run") == run)
        & pl.col("scheduler").is_in(schedulers)
    )
    best = d.group_by("scheduler").agg(pl.col("cost").last())["cost"].min()
    for s in schedulers:
        e = d.filter(pl.col("scheduler") == s)
        if e.is_empty():
            continue
        hit = e["cost"] <= best
        if hit.any():
            e = e.head(hit.arg_true()[0] + 1)
        ax.step(e[res], e["cost"], where="post", color=SCHEDULERS[s], label=s)
        if hit.any():
            ax.plot(
                e[res][-1],
                e["cost"][-1],
                marker="*",
                markersize=12,
                color=SCHEDULERS[s],
                markeredgecolor=SURFACE,
            )
    ax.set_xlabel(RESOURCES[res])
    ax.set_ylabel("extraction cost")
    ax.set_title(f"{case} / run {run}")


def plot_dev(
    df: pl.DataFrame,
    case: str,
    run: int,
    ax,
    *,
    metric: Metric = "cost",
    schedulers: Sequence[Scheduler] | None = None,
):
    """`metric` per iteration for one run, like dump.py's dev_plot."""
    if schedulers is None:
        schedulers = tuple(SCHEDULERS)
    d = df.filter((pl.col("case") == case) & (pl.col("run") == run))
    for s in schedulers:
        e = d.filter(pl.col("scheduler") == s)
        if not e.is_empty():
            ax.step(
                e["iteration"], e[metric], where="post", color=SCHEDULERS[s], label=s
            )
    ax.set_xlabel("iteration")
    ax.set_ylabel(RESOURCES.get(metric, "extraction cost"))
    ax.set_title(f"{case} / run {run}: {metric} per iteration")


def plot_solved(fin: pl.DataFrame, ax, case: str, *, solved_cost: float = 1):
    d = (
        fin.filter(pl.col("case") == case)
        .group_by("scheduler")
        .agg(solved=(pl.col("cost") == solved_cost).sum(), runs=pl.len())
        .sort("solved", "scheduler", descending=[False, True])
    )
    y = np.arange(len(d))
    ax.barh(y, d["solved"], height=0.6, color=PALETTE[0])
    ax.set_yticks(y, d["scheduler"])
    for yi, n in zip(y, d["solved"]):
        ax.annotate(
            str(n),
            (n, yi),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK_2,
        )
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, d["runs"].max())
    ax.set_xlabel(f"solved runs (of {d['runs'].max()})")
    ax.set_title(f"{case}: proved goals")
