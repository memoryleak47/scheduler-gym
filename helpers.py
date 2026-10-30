import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import matplotlib as mpl
import plotnine as p9
import polars as pl

from gym import Scheduler

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"
FIGURES = ROOT / "figures"

KEY = ("case", "scheduler", "run")
Resource = Literal["time", "size"]
Metric = Literal["cost", "time", "size"]

RESOURCES: dict[Resource, str] = {"time": "time [s]", "size": "e-graph size"}
METRICS: dict[Metric, str] = {"cost": "cost", **RESOURCES}
# the labels as ordered categories, so facets come in this order
RESOURCE = pl.Enum(RESOURCES.values())
METRIC = pl.Enum(METRICS.values())

ENTRY_RE = re.compile(
    r"costs=\[([^\]]*)\], total_size=(\S+), time=(\S+), iteration=(\d+), stop=(.*)$"
)


def setup_style():
    mpl.rcParams["pdf.fonttype"] = 42
    p9.theme_set(
        p9.theme_minimal(base_size=8)
        # + p9.theme(
        #     panel_grid_minor=p9.element_blank(),
        #     strip_text=p9.element_text(size=9, weight="bold", ha="left"),
        #     legend_title=p9.element_blank(),
        #     plot_title=p9.element_text(size=11, weight="bold", ha="left"),
        #     plot_title_position="plot",
        #     dpi=110,
        # )
    )


def save(plot: p9.ggplot, name: str, size: tuple[float, float]) -> p9.ggplot:
    """Write `plot` to figures/`name`.pdf and return it for display."""
    plot += p9.theme(figure_size=size)
    FIGURES.mkdir(exist_ok=True)
    plot.save(FIGURES / f"{name}.pdf", verbose=False)
    return plot


def load(schedulers: Sequence[Scheduler], *, root: Path = BENCHDATA) -> pl.DataFrame:
    """One row per entry of `schedulers`; `run` numbers the runs within a file. Unfinished trailing runs are dropped.
    `scheduler` is ordered as given, which fixes the order and colors of the schedulers in all figures."""
    rows = []
    for path in sorted(root.glob("*/*.entries")):
        case, scheduler = path.stem, path.parent.name.removesuffix(".rs")
        if scheduler not in schedulers:
            continue
        run, pending = 0, []
        for line in path.read_text().splitlines():
            m = ENTRY_RE.search(line)
            assert m
            costs, size, time, iteration, stop = m.groups()
            cost = sum(float(c) for c in costs.split(", "))
            pending.append(
                (case, scheduler, run, int(iteration), cost, float(size), float(time))
            )
            if stop.strip() != "None":
                rows += pending
                run, pending = run + 1, []
    schema = [*KEY, "iteration", "cost", "size", "time"]
    df = pl.DataFrame(rows, schema=schema, orient="row")
    return df.with_columns(pl.col("scheduler").cast(pl.Enum(schedulers)))


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
    """Print the cases where the schedulers have a different number of finished runs."""
    counts = fin.group_by("case", "scheduler").len()
    for (case,), g in counts.group_by("case"):
        if g["len"].n_unique() > 1:
            print(f"{case}: schedulers disagree on number of runs:")
            print(g.sort("len").select("scheduler", "len"))


def _geomean(e: pl.Expr) -> pl.Expr:
    return e.log().mean().exp()


def _titled(d: pl.DataFrame, fin: pl.DataFrame) -> pl.DataFrame:
    """`d` with `title` = case and its number of runs, for facet labels."""
    runs = fin.group_by("case").agg(
        title=pl.format(
            "{} ({} runs)", pl.col("case").first(), pl.col("run").n_unique()
        )
    )
    return d.join(runs, on="case")


def _facets(by: str, n: int, scales: Literal["free", "free_x"]) -> p9.facet_wrap:
    """One column per case (`title`), one row per value of `by`, of which there are `n`."""
    return p9.facet_wrap(
        ["title", by],
        nrow=n,
        dir="v",
        scales=scales,
        # plotnine annotates `labeller` too narrowly, facet accepts any labeller
        labeller=p9.labeller(multi_line=False),  # ty: ignore[invalid-argument-type]
    )


def _steps(d: pl.DataFrame, y: str) -> p9.ggplot:
    """One step line per scheduler of `y` over `x` (log scale)."""
    return (
        p9.ggplot(d, p9.aes("x", y, color="scheduler"))
        + p9.geom_step(size=0.8)
        + p9.scale_x_log10()
        + p9.scale_color_hue()
    )


def _bars(d: pl.DataFrame, value: str, fmt: str, *, base: float = 0) -> p9.ggplot:
    """Horizontal bars per scheduler from `base` to `value`, each labelled with its value."""
    dtype = d["scheduler"].dtype
    assert isinstance(dtype, pl.Enum)
    top_down = dtype.categories.reverse().to_list()
    d = d.with_columns(base=pl.lit(base))
    return (
        p9.ggplot(d, p9.aes(y="scheduler", x="base", xend=value, color="scheduler"))
        + p9.geom_segment(p9.aes(yend="scheduler"), size=4)
        + p9.geom_text(
            p9.aes(x=value, label=value),
            format_string=f" {{:{fmt}}}",
            ha="left",
            size=7,
        )
        + p9.scale_x_continuous(
            labels=lambda xs: [f"{x:.3g}" for x in xs], expand=(0, 0, 0.25, 0)
        )
        + p9.scale_y_discrete(limits=top_down)
        + p9.scale_color_hue()
        + p9.guides(color="none")
        + p9.labs(y="")
        + p9.theme(panel_grid_major_y=p9.element_blank())
    )


def ranking(fin: pl.DataFrame) -> p9.ggplot:
    """Geometric mean of final cost relative to the best scheduler per run (1 = always best)."""
    d = fin.group_by("case", "scheduler").agg(
        ratio=_geomean(pl.col("cost") / pl.col("best"))
    )
    return (
        _bars(_titled(d, fin), "ratio", ".3f", base=1)
        + p9.facet_wrap("title", nrow=1, scales="free")
        + p9.labs(
            x="final cost / best final cost (geomean)",
            title="Final cost relative to the best scheduler",
            subtitle="geometric mean over runs, 1 = always the best; lower is better",
        )
    )


def sums(fin: pl.DataFrame) -> p9.ggplot:
    """Sum of the final cost, time and size over all runs, like dump.py's dumpall."""
    d = pl.concat(
        fin.group_by("case", "scheduler").agg(
            metric=pl.lit(label, METRIC), total=pl.col(m).sum()
        )
        for m, label in METRICS.items()
    )
    return (
        _bars(_titled(d, fin), "total", ".4g")
        + _facets("metric", len(METRICS), "free")
        + p9.labs(
            x="total final value",
            title="Totals over all runs",
            subtitle="sum of the final cost, time and e-graph size; lower is better",
        )
    )


def solved(fin: pl.DataFrame, case: str = "caviar", *, solved_cost: float = 1):
    """Number of runs per scheduler that ended at `solved_cost`, i.e. proved the goal."""
    d = (
        fin.filter(pl.col("case") == case)
        .group_by("case", "scheduler")
        .agg(solved=(pl.col("cost") == solved_cost).sum(), runs=pl.len())
    )
    runs = d["runs"].max()
    return (
        _bars(d, "solved", "d")
        + p9.expand_limits(x=runs)
        + p9.labs(
            x=f"solved runs (of {runs})",
            title=f"{case}: proved goals",
            subtitle="runs that ended at the goal; higher is better",
        )
    )


def profile(fin: pl.DataFrame) -> p9.ggplot:
    """Fraction of runs where a scheduler reached the best known cost within the given resource.
    Each curve starts at 0 at the smallest resource any scheduler needed and is extended to
    the largest resource any scheduler used."""
    group = ["case", "resource", "scheduler"]
    d = pl.concat(
        fin.select(
            "case",
            "scheduler",
            resource=pl.lit(label, RESOURCE),
            x=pl.col(f"{res}_to_best"),
            hi=pl.col(res).max().over("case"),
        )
        for res, label in RESOURCES.items()
    ).with_columns(
        lo=pl.col("x").min().over("case", "resource"),
        # runs that never reached the best have a null `x`, and so a null rank
        y=(pl.col("x").rank("ordinal") / pl.len()).over(group),
    )
    ends = d.group_by(group).agg(
        pl.col("lo", "hi").first(), pl.col("y").max().fill_null(0)
    )
    curves = pl.concat(
        [
            ends.select(*group, x="lo", y=pl.lit(0.0)),
            d.drop_nulls("x").select(*group, "x", "y"),
            ends.select(*group, x="hi", y="y"),
        ]
    ).sort("x", maintain_order=True)
    return (
        _steps(_titled(curves, fin), "y")
        + _facets("resource", len(RESOURCES), "free_x")
        + p9.coord_cartesian(ylim=(0, 1))
        + p9.labs(
            x="",
            y="runs at best known cost",
            title="Reaching the best known cost",
            subtitle="fraction of runs that reached the best final cost of any scheduler"
            " within a time / e-graph size budget; higher is better",
        )
    )


def _relative(df: pl.DataFrame, fin: pl.DataFrame, metric: Metric) -> pl.DataFrame:
    """`df` with `rel` = `metric` / the smallest final `metric` of any scheduler on that run."""
    best = fin.group_by("case", "run").agg(best=pl.col(metric).min())
    return df.join(best, on=["case", "run"]).with_columns(
        rel=pl.col(metric) / pl.col("best")
    )


def _log_grid(d: pl.DataFrame, by: list[str], n: int = 300) -> pl.DataFrame:
    """`n` log-spaced `x` per group of `by`, spanning that group's `x`."""
    return (
        d.group_by(by)
        .agg(x=pl.linear_spaces(pl.col("x").min().log(), pl.col("x").max().log(), n))
        .explode("x")
        .with_columns(pl.col("x").exp())
    )


def _geomean_curves(d: pl.DataFrame, by: list[str], grid: pl.DataFrame) -> pl.DataFrame:
    """Per group of `by` and scheduler, the geometric mean over all runs of `rel` as a
    step function of `x`, sampled at `grid`. Before a run starts it is at its first
    `rel`, after it ends at its last."""
    run = [*by, "scheduler", "run"]
    d = d.select(*run, "x", "rel").sort("x", maintain_order=True)
    starts = d.group_by(run).agg(start=pl.col("rel").first())
    return (
        grid.join(starts, on=by)
        .sort("x")
        .join_asof(d, on="x", by=run, check_sortedness=False)
        .group_by(*by, "scheduler", "x")
        .agg(rel=_geomean(pl.col("rel").fill_null(pl.col("start"))))
    )


def quality(df: pl.DataFrame, fin: pl.DataFrame) -> p9.ggplot:
    """Cost relative to the best final cost of each run over resource, geometric mean over all runs.
    The resource is the running maximum, since the e-graph size can shrink."""
    rel = _relative(df, fin, "cost")
    d = pl.concat(
        rel.select(
            *KEY,
            "rel",
            resource=pl.lit(label, RESOURCE),
            x=pl.col(r).cum_max().over(KEY),
        )
        for r, label in RESOURCES.items()
    )
    by = ["case", "resource"]
    curves = _titled(_geomean_curves(d, by, _log_grid(d, by)), fin)
    return (
        _steps(curves, "rel")
        + _facets("resource", len(RESOURCES), "free")
        + p9.scale_y_log10()
        + p9.labs(
            x="",
            y="cost / best final cost (geomean)",
            title="Cost over time and e-graph size",
            subtitle="cost relative to the best final cost of each run,"
            " geometric mean over runs; lower is better",
        )
    )


def dev(df: pl.DataFrame, fin: pl.DataFrame) -> p9.ggplot:
    """Cost, time and size per iteration relative to the smallest final value of each run,
    geometric mean over all runs."""
    d = pl.concat(
        _relative(df, fin, m).select(
            *KEY,
            "rel",
            metric=pl.lit(label, METRIC),
            x=pl.col("iteration").cast(pl.Float64),
        )
        for m, label in METRICS.items()
    )
    by = ["case", "metric"]
    grid = _log_grid(d, by).with_columns(pl.col("x").round()).unique()
    curves = _titled(_geomean_curves(d, by, grid), fin)
    return (
        _steps(curves, "rel")
        + _facets("metric", len(METRICS), "free")
        + p9.scale_y_log10()
        + p9.labs(
            x="iteration",
            y="value / smallest final value (geomean)",
            title="Development per iteration",
            subtitle="cost, time and e-graph size relative to the smallest final value"
            " of each run, geometric mean over runs; lower is better",
        )
    )
