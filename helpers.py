import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import matplotlib as mpl
import plotnine as p9
import polars as pl
from matplotlib.path import Path as MplPath

from bench import SCHEDULERS

ROOT = Path(__file__).resolve().parent
BENCHDATA = ROOT / "benchdata"
FIGURES = ROOT / "figures"

KEY = ("case", "scheduler", "run")
Resource = Literal["time", "size"]
Metric = Literal["cost", "time", "size"]
Side = Literal["backoff", "source", "target", "class", "other"]
COLORS: dict[Side, str] = {
    "backoff": "black",
    "source": "#E69F00",  # orange
    "target": "#0072B2",  # blue
    "class": "#009E73",  # bluish green
    "other": "#CC79A7",  # reddish purple
}  # Okabe-Ito colorblind-safe palette
SIDE = pl.Enum(COLORS.keys())
_NAME = pl.col("scheduler").cast(pl.String)
SIDE_OF = (
    pl.when(_NAME == "backoff")
    .then(pl.lit("backoff"))
    .otherwise(
        _NAME.str.extract(r"^(source|target|class)-cost-\d+$").fill_null("other")
    )
    .cast(SIDE)
)
K_OF = _NAME.str.extract(r"-(\d+)$").cast(pl.Int64)
COST_SIDE = pl.col("side").is_in(["source", "target", "class"])
# the scheduler the bar charts are relative to
BASELINE = "backoff"
# alpha of the curves and bars that are not the best of their side
REST_ALPHA = 0.4
# marker for the break in cut bars: two slanted parallel strokes, like //
BREAK = MplPath(
    [(-0.6, -1), (-0.1, 1), (0.1, -1), (0.6, 1)],
    [MplPath.MOVETO, MplPath.LINETO, MplPath.MOVETO, MplPath.LINETO],
)
# how the baseline is called in the plot texts
BASELINE_NAME = "BackoffScheduler"
MODES = "for each mode (source/target/class)"
TOP_NOTE = (
    f"{BASELINE_NAME} and the k with the lowest final cost per benchmark {MODES}"
    " marked opaque"
)
BAR_NOTE = (
    f"k with the lowest final cost per benchmark {MODES} marked opaque;\n"
    f"// marks bar cut off; the line at 0 is {BASELINE_NAME}"
)

RESOURCES: dict[Resource, str] = {"time": "time [s]", "size": "e-graph size"}
METRICS: dict[Metric, str] = {"cost": "cost", **RESOURCES}
# the labels as ordered categories, so facets come in this order
RESOURCE = pl.Enum(RESOURCES.values())
METRIC = pl.Enum(METRICS.values())

ENTRY_PATTERN = (
    r"costs=\[(?P<costs>[^\]]*)\], total_size=(?P<size>\S+), time=(?P<time>\S+), "
    r"iteration=(?P<iteration>\d+), stop=(?P<stop>.*)$"
)
CACHE = BENCHDATA / ".cache"


def setup_style():
    mpl.rcParams["pdf.fonttype"] = 42
    p9.theme_set(
        p9.theme_minimal()
        + p9.theme(
            panel_grid_minor=p9.element_line(color="#EBEBEB", size=0.5),
            #     panel_grid_minor=p9.element_blank(),
            #     strip_text=p9.element_text(size=9, weight="bold", ha="left"),
            #     legend_title=p9.element_blank(),
            #     plot_title=p9.element_text(size=11, weight="bold", ha="left"),
            #     plot_title_position="plot",
            dpi=300,
        )
    )


def save(plot: p9.ggplot, name: str, size: tuple[float, float]) -> p9.ggplot:
    """Write `plot` to figures/`name`.pdf and return it for display.

    Also writes figures/`name`-no-title.pdf without title and subtitle, for use
    in a tex file, and prints them so they can go into the caption.
    """
    plot += p9.theme(figure_size=size)
    FIGURES.mkdir(exist_ok=True)
    plot.save(FIGURES / f"{name}.pdf", verbose=False)
    bare = plot + p9.theme(
        plot_title=p9.element_blank(), plot_subtitle=p9.element_blank()
    )
    bare.save(FIGURES / f"{name}-no-title.pdf", verbose=False)
    print(f"{name}\n{plot.labels.get('title', '')}\n{plot.labels.get('subtitle', '')}")
    return plot


def _parse(path: Path) -> pl.DataFrame:
    """Entries of one file; `run` numbers its runs, unfinished trailing ones dropped."""
    lines = pl.read_csv(
        path,
        has_header=False,
        separator="\x1f",
        quote_char=None,
        schema={"line": pl.String},
    )
    df = lines.select(pl.col("line").str.extract_groups(ENTRY_PATTERN)).unnest("line")
    if bad := df.null_count().sum_horizontal().item():
        raise ValueError(f"{path}: {bad} fields of malformed entry lines")
    finished = (pl.col("stop").str.strip_chars() != "None").cast(pl.Int64)
    return df.filter(finished.cum_sum(reverse=True) > 0).select(
        pl.lit(path.stem).alias("case"),
        pl.lit(path.parent.name.removesuffix(".rs")).alias("scheduler"),
        run=finished.cum_sum().shift(1, fill_value=0),
        iteration=pl.col("iteration").cast(pl.Int64),
        cost=pl.col("costs").str.split(", ").cast(pl.List(pl.Float64)).list.sum(),
        size=pl.col("size").cast(pl.Float64),
        time=pl.col("time").cast(pl.Float64),
        # lean-egg's goal hook stops the run on a proof, its cost stays above 1
        proved=pl.col("stop").str.contains("Proved goal", literal=True),
    )


def _cached(path: Path, cache: Path, cases: pl.Enum) -> pl.DataFrame:
    """`_parse(path)`, cached as parquet keyed on the file's mtime and size."""
    st = path.stat()
    folder = cache / path.parent.name
    file = folder / f"{path.stem}.{st.st_mtime_ns}-{st.st_size}.parquet"
    if file.exists():
        df = pl.read_parquet(file)
    else:
        df = _parse(path)
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob(f"{path.stem}.*.parquet"):
            if re.fullmatch(rf"{re.escape(path.stem)}\.\d+-\d+\.parquet", stale.name):
                stale.unlink()
        tmp = file.with_suffix(".tmp")
        df.write_parquet(tmp)
        tmp.rename(file)
    # per file, so the full-size string columns never exist
    return df.with_columns(
        pl.col("case").cast(cases), pl.col("scheduler").cast(pl.Enum(SCHEDULERS))
    )


def load(*, root: Path = BENCHDATA, cache: Path = CACHE) -> pl.DataFrame:
    """One row per entry; `run` numbers the runs in a file, unfinished ones dropped."""
    paths = sorted(root.glob("*/*.entries"))
    # an enum, not a categorical, so cases sort (and facet) alphabetically as strings
    cases = pl.Enum(sorted({p.stem for p in paths}))
    return pl.concat([_cached(p, cache, cases) for p in paths], rechunk=True)


def select(
    df: pl.DataFrame, *, schedulers: Sequence[str] = SCHEDULERS
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rows of `schedulers` in that order, and finals: one row per run with the final
    cost, the run's best final cost, resources to reach it, `side` and `top`."""
    df = df.filter(pl.col("scheduler").is_in(schedulers)).with_columns(
        pl.col("scheduler").cast(pl.String).cast(pl.Enum(schedulers))
    )
    fin = df.group_by(KEY, maintain_order=True).last()
    fin = fin.with_columns(best=pl.col("cost").min().over("case", "run"))
    reached = (
        df.join(fin.select(*KEY, "best"), on=KEY)
        .filter(pl.col("cost") <= pl.col("best"))
        .group_by(KEY, maintain_order=True)
        .first()
        .select(*KEY, *(pl.col(r).alias(f"{r}_to_best") for r in RESOURCES))
    )
    fin = fin.join(reached, on=KEY, how="left")

    # backoff is always top; of each mode the one with the lowest geomean of final cost
    # relative to the best final cost of each run, ties going to the smallest k
    top = (
        fin.group_by("case", "scheduler")
        .agg(ratio=_geomean(pl.col("cost") / pl.col("best")))
        .with_columns(side=SIDE_OF)
        .sort("ratio", K_OF)
        .select("case", "scheduler", "side", top=_top("case"))
    )
    return df, fin.join(top, on=["case", "scheduler"], how="left")


def check_runs(df: pl.DataFrame, *, schedulers: Sequence[str] = SCHEDULERS):
    """Print cases where `schedulers` have different numbers of finished runs."""
    _, fin = select(df, schedulers=schedulers)
    counts = fin.group_by("case", "scheduler").len()
    for (case,), g in counts.group_by("case"):
        if g["len"].n_unique() > 1:
            print(f"{case}: schedulers disagree on number of runs:")
            print(g.sort("len").select("scheduler", "len"))


def _geomean(e: pl.Expr) -> pl.Expr:
    return e.log().mean().exp()


def _top(*over: str) -> pl.Expr:
    """Backoff, and of each mode the first row per `over` and side."""
    return (
        pl.when(COST_SIDE)
        .then(pl.int_range(pl.len()).over(*over, "side") == 0)
        .otherwise(pl.col("side") == "backoff")
    )


def _titled(
    d: pl.DataFrame,
    fin: pl.DataFrame,
    tops: pl.DataFrame | None = None,
    *,
    picks: bool = True,
) -> pl.DataFrame:
    """`d` with `side`, `top` and a facet `title`: case, runs, top k of each mode.
    `tops`: `side` and `top` per scheduler and further keys, instead of `fin`'s;
    `picks`: whether the title lists the top k, bar charts show them anyway."""
    if tops is None:
        tops = fin.select("case", "scheduler", "side", "top").unique()
    by = [c for c in tops.columns if c not in ("scheduler", "side", "top")]
    top = pl.col("top") & COST_SIDE
    pick = pl.format("{} {}", pl.col("side").cast(pl.String), K_OF)
    runs = fin.group_by("case").agg(runs=pl.col("run").n_unique())
    titles = (
        tops.join(runs, on="case")
        .group_by(by)
        .agg(
            # the top picks on a line of their own, they don't fit next to the case
            title=pl.format(
                "{} ({} runs)\n{}",
                pl.col("case").first(),
                pl.col("runs").first(),
                pick.filter(top)
                .sort_by(pl.col("side").filter(top), K_OF.filter(top))
                .unique(maintain_order=True)
                .str.join(", ")
                if picks
                else pl.lit(""),
            ).str.strip_suffix("\n")
        )
    )
    return d.join(tops, on=[*by, "scheduler"]).join(titles, on=by)


class _Strip(p9.labeller):
    """Facet label: the title's first line · the other facet values, then the rest of
    the title, so the top picks keep their own line."""

    def __call__(self, label_info):
        title, *rest = label_info.variables.values()
        head, nl, picks = title.partition("\n")
        label_info.variables = {"title": " · ".join([head, *rest]) + nl + picks}
        return label_info


def _facets(by: str, n: int, scales: Literal["free", "free_x"]) -> p9.facet_wrap:
    """One column per case, `n` rows, one per value of `by`."""
    return p9.facet_wrap(
        ["title", by],
        nrow=n,
        dir="v",
        scales=scales,
        # plotnine annotates `labeller` too narrowly, facet accepts any labeller
        labeller=_Strip(),  # ty: ignore[invalid-argument-type]
    )


def _steps(
    d: pl.DataFrame,
    y: str,
    by: list[str],
    log_x: bool,
    *,
    star_if: pl.Expr | bool = True,
) -> p9.ggplot:
    """Step lines per scheduler, top ones opaque, starred where final if `star_if`."""
    top = d.filter(pl.col("top"))
    # legend with only the sides present
    breaks = [side for side in COLORS if side in d["side"]]
    # the first point per group and scheduler where `y` reaches its final value
    group = [*by, "scheduler"]
    last = pl.col(y).last().over(group)
    stars = (
        d.sort("x", maintain_order=True)
        .filter((pl.col(y) - last).abs() <= 1e-9 * last.abs())
        .group_by(group)
        .first()
        .filter(star_if & pl.col("top"))
    )
    return (
        p9.ggplot(d, p9.aes("x", y, color="side", group="scheduler"))
        + p9.geom_step(data=d.filter(~pl.col("top")), size=0.5, alpha=REST_ALPHA)
        # a white halo, so the top curves stand out where they cross the rest
        + p9.geom_step(data=top, size=2.4, color="white")
        + p9.geom_step(data=top, size=1.4)
        # filled in the curve's color, with a black edge to stand out from the curve
        + p9.geom_point(
            p9.aes(fill="side"),
            data=stars,
            shape="*",
            size=6,
            color="black",
            stroke=0.3,
            show_legend=False,
        )
        + (p9.scale_x_log10() if log_x else p9.scale_x_continuous())
        + p9.scale_color_manual(
            values=COLORS,
            breaks=breaks,
            labels=[BASELINE_NAME if s == BASELINE else s for s in breaks],
            name="scheduler",
        )
        + p9.scale_fill_manual(values=COLORS, breaks=breaks)
    )


def _bars(
    d: pl.DataFrame, value: str, *, zoom: float = 100, over: Sequence[str] = ("case",)
) -> p9.ggplot:
    """Bars of `value` per scheduler, cut at ±`zoom` with a // break.
    Backoff is left out, it is the line at 0. `over`: the columns of a facet."""
    dtype = d["scheduler"].dtype
    assert isinstance(dtype, pl.Enum)
    top_down = [s for s in dtype.categories.reverse() if s != BASELINE]
    gain = pl.col(value) >= 0
    # room for the labels past the bars, about a third of the case's range of bars
    span = pl.max_horizontal(pl.col("end").max(), 0) - pl.min_horizontal(
        pl.col("end").min(), 0
    )
    d = (
        d.filter(pl.col("scheduler") != BASELINE)
        .with_columns(end=pl.col(value).clip(-zoom, zoom))
        .with_columns(
            reach=pl.col("end")
            + pl.when(gain).then(0.3).otherwise(-0.3) * span.over(over),
            # where a cut bar is broken, a bit before its end
            gap=pl.col("end")
            - pl.when(gain).then(0.08).otherwise(-0.08) * span.over(over),
        )
    )
    d = d.with_columns(
        text=pl.Series(f" {v:+.1f}%" if v >= 0 else f"{v:+.1f}% " for v in d[value])
    )
    bar = p9.aes(yend="scheduler")
    label = p9.aes(x="end", label="text")
    return (
        p9.ggplot(d, p9.aes(y="scheduler", x=0, xend="end", color="side"))
        + p9.geom_vline(xintercept=0, color=COLORS["backoff"], size=0.5)
        + p9.geom_blank(p9.aes(x="reach"))
        + p9.geom_segment(bar, data=d.filter(~pl.col("top")), size=4, alpha=REST_ALPHA)
        + p9.geom_segment(bar, data=d.filter(pl.col("top")), size=4)
        + p9.geom_point(
            p9.aes(x="gap"),
            data=d.filter(pl.col(value).abs() > zoom),
            shape=BREAK,
            size=5,
            stroke=1.2,
            color="white",
        )
        + p9.geom_text(label, data=d.filter(gain), ha="left", size=7, color="black")
        + p9.geom_text(label, data=d.filter(~gain), ha="right", size=7, color="black")
        + p9.scale_x_continuous(
            labels=lambda xs: [f"{x:+.3g}%" if x else "0%" for x in xs],
            expand=(0.02, 0),
        )
        + p9.scale_y_discrete(
            limits=top_down,
            # class-cost-400 -> class cost 400
            labels=[s.replace("-", " ") for s in top_down],
        )
        + p9.scale_color_manual(values=COLORS)
        + p9.guides(color="none")
        + p9.labs(y="")
        + p9.theme(
            panel_grid_major_y=p9.element_blank(),
            # left aligned, so the modes line up and the k are easier to compare
            axis_text_y=p9.element_text(ha="left"),
        )
    )


def _bars_noted(
    d: pl.DataFrame,
    value: str,
    note: pl.Expr,
    *,
    zoom: float = 100,
    over: Sequence[str] = ("case",),
) -> p9.ggplot:
    """`_bars` of the rows with a `value`; the others get `note` in gray at 0,
    on the side of 0 with more room."""
    gain = pl.col(value).clip(-zoom, zoom)
    left = (-gain.min().over(over) > gain.max().over(over)).fill_null(False)
    short = (
        d.with_columns(left=left)
        .filter(pl.col(value).is_null() & (pl.col("scheduler") != BASELINE))
        .with_columns(
            text=pl.when("left")
            .then(pl.format("{} ", note))
            .otherwise(pl.format(" {}", note))
        )
    )
    aes = p9.aes(x=0, y="scheduler", label="text")
    return (
        _bars(d.filter(pl.col(value).is_not_null()), value, zoom=zoom, over=over)
        + p9.geom_text(
            aes,
            data=short.filter(~pl.col("left")),
            inherit_aes=False,
            ha="left",
            size=7,
            color="gray",
        )
        + p9.geom_text(
            aes,
            data=short.filter(pl.col("left")),
            inherit_aes=False,
            ha="right",
            size=7,
            color="gray",
        )
    )


def ranking(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    zoom: float = 100,
) -> p9.ggplot:
    """Final cost reduction over backoff in percent, geomean over runs.
    `zoom`: bars are cut off at ±`zoom` percent, their labels still give the value."""
    _, fin = select(df, schedulers=schedulers)
    base = fin.filter(pl.col("scheduler") == BASELINE).select(
        "case", "run", base=pl.col("cost")
    )
    d = (
        fin.join(base, on=["case", "run"])
        .group_by("case", "scheduler")
        .agg(gain=(1 - _geomean(pl.col("cost") / pl.col("base"))) * 100)
    )
    return (
        _bars(_titled(d, fin, picks=False), "gain", zoom=zoom)
        + p9.facet_wrap("title", nrow=1, scales="free")
        + p9.labs(
            x=f"final cost reduction over {BASELINE_NAME} [%]",
            title=f"Final cost relative to {BASELINE_NAME}",
            subtitle=f"1 − geomean over runs of final cost / {BASELINE_NAME}'s final"
            f" cost;\n{BAR_NOTE}; higher is better",
        )
    )


def _proved(solved_cost: float) -> pl.Expr:
    """Whether the goal is proved, or the cost reached `solved_cost`."""
    return (pl.col("cost") <= solved_cost) | pl.col("proved")


def solved(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    solved_cost: float = 1,
    zoom: float = 100,
) -> p9.ggplot:
    """Change in runs that reached `solved_cost`, relative to backoff in percent.
    `zoom`: bars are cut off at ±`zoom` percent, their labels still give the value."""
    _, fin = select(df, schedulers=schedulers)
    d = fin.group_by("case", "scheduler").agg(
        solved=_proved(solved_cost).sum(),
        runs=pl.len(),
    )
    base = d.filter(pl.col("scheduler") == BASELINE).select(
        "case", base="solved", base_runs="runs"
    )
    for case, b, _ in base.iter_rows():
        assert b > 0, f"{BASELINE} solved no runs of {case}"
    bases = ", ".join(f"{c} {b} of {r}" for c, b, r in base.sort("case").iter_rows())
    d = d.join(base, on="case").with_columns(
        gain=(pl.col("solved") / pl.col("base") - 1) * 100
    )
    # of each mode, all with the most proofs are top
    most = pl.col("solved").filter(COST_SIDE).max().over("case", "side")
    tops = d.with_columns(side=SIDE_OF).select(
        "case",
        "scheduler",
        "side",
        top=pl.when(COST_SIDE)
        .then(pl.col("solved") == most)
        .otherwise(pl.col("side") == "backoff"),
    )
    return (
        _bars(_titled(d, fin, tops, picks=False), "gain", zoom=zoom)
        + p9.facet_wrap("title", nrow=1, scales="free")
        + p9.labs(
            x=f"more solved runs than {BASELINE} [%]",
            title="Proved goals in the proof case studies",
            subtitle=f"runs that proved the goal, relative to {BASELINE}'s;\n"
            f"{BASELINE} solved {bases}; higher is better;\n"
            f"the k with the most proofs per benchmark {MODES} marked opaque;\n"
            f"// = bar cut off; the line at 0 is {BASELINE}",
        )
    )


def _per_resource(df: pl.DataFrame, *cols: str) -> pl.DataFrame:
    """`df` once per resource, `x` its running max."""
    return pl.concat(
        df.select(
            *KEY,
            *cols,
            resource=pl.lit(label, RESOURCE),
            x=pl.col(r).cum_max().over(KEY),
        )
        for r, label in RESOURCES.items()
    )


def _first_proofs(d: pl.DataFrame, proved: pl.Expr) -> pl.DataFrame:
    """Per run and resource, `x` at the first proof; runs without one are left out."""
    return (
        d.filter(proved)
        .group_by(*KEY, "resource", maintain_order=True)
        .agg(pl.col("x").first())
    )


def solved_profile(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    solved_cost: float = 1,
    log_x: bool = False,
    y_min: float = 0,
    y_max: float = 1,
) -> p9.ggplot:
    """Fraction of runs that reached `solved_cost` within a resource budget.
    `log_x`: log x axis; `y_min`, `y_max`: ends of the y axis, to zoom in on a range."""
    df, fin = select(df, schedulers=schedulers)
    group = ["case", "resource", "scheduler"]
    d = _per_resource(df, "cost", "proved")
    runs = fin.group_by("case", "scheduler").agg(runs=pl.len())
    proved = _proved(solved_cost)
    proofs = (
        _first_proofs(d, proved)
        .join(runs, on=["case", "scheduler"])
        .with_columns(y=pl.col("x").rank("ordinal").over(group) / pl.col("runs"))
    )
    # without any proof, the curves start at the smallest resource used at all
    lo = d.group_by("case", "resource").agg(
        lo=pl.col("x").filter(proved).min().fill_null(pl.col("x").min())
    )
    ends = (
        d.group_by(group)
        .agg(hi=pl.col("x").max())
        .join(lo, on=["case", "resource"])
        .join(
            proofs.group_by(group).agg(
                pl.col("y").max(), n=pl.len(), last=pl.col("x").max()
            ),
            on=group,
            how="left",
        )
        .with_columns(pl.col("y", "n").fill_null(0), side=SIDE_OF)
    )
    # step curves from 0 at `lo` through the proofs to `hi`
    curves = pl.concat(
        [
            ends.select(*group, x="lo", y=pl.lit(0.0)),
            proofs.select(*group, "x", "y"),
            ends.select(*group, x="hi", y="y"),
        ]
    ).sort("x", maintain_order=True)
    # backoff is always top; per resource, of each mode the one with the most proofs,
    # ties going to the smallest budget of the last proof, then to the smallest k
    tops = ends.sort(
        "n", "last", K_OF, descending=[True, False, False], nulls_last=True
    ).select(*group, "side", top=_top("case", "resource"))
    curves = _titled(curves, fin, tops)
    return (
        # curves that stay at 0 get no star
        _steps(curves, "y", ["case", "resource"], log_x, star_if=pl.col("y") > 0)
        + _facets("resource", len(RESOURCES), "free_x")
        + p9.coord_cartesian(ylim=(y_min, y_max))
        + p9.labs(
            x="",
            y="runs with a proof",
            title="Fraction of problems proven within a time / e-graph size budget"
            " in the proof case studies",
            subtitle=f"{BASELINE_NAME} and the k with the most proofs for each mode"
            " (source/target/class,\nties go to the smaller budget at the last proof)"
            " marked opaque;\n"
            "★ marks the last proof found; higher is better",
        )
    )


def solved_budget(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    solved_cost: float = 1,
    zoom: float = 100,
) -> p9.ggplot:
    """Resource budget a scheduler needs to prove as many runs as backoff does in
    total, as savings over backoff's budget in percent; schedulers that prove fewer
    get no bar. `zoom`: bars are cut off at ±`zoom` percent, their labels still give
    the value."""
    df, fin = select(df, schedulers=schedulers)
    facet = ["case", "resource"]
    proofs = _first_proofs(_per_resource(df, "cost", "proved"), _proved(solved_cost))
    need = (
        proofs.filter(pl.col("scheduler") == BASELINE)
        .group_by(facet)
        .agg(need=pl.len())
    )
    for case in fin["case"].unique():
        assert case in need["case"], f"{BASELINE} solved no runs of {case}"
    bases = ", ".join(
        f"{c} {n}"
        for c, n in need.group_by("case")
        .agg(pl.col("need").first())
        .sort("case")
        .iter_rows()
    )
    # the budget is where the scheduler's `need`th proof is found
    budgets = (
        proofs.join(need, on=facet)
        .filter(pl.col("x").rank("ordinal").over(*facet, "scheduler") == pl.col("need"))
        .select(*facet, "scheduler", budget="x")
    )
    base = budgets.filter(pl.col("scheduler") == BASELINE).select(*facet, base="budget")
    every = (
        fin.select("case", "scheduler")
        .unique()
        .join(
            pl.DataFrame({"resource": RESOURCES.values()}, {"resource": RESOURCE}),
            how="cross",
        )
        .join(need, on=facet)
    )
    d = (
        every.join(budgets, on=[*facet, "scheduler"], how="left")
        .join(base, on=facet)
        .with_columns(gain=(1 - pl.col("budget") / pl.col("base")) * 100, side=SIDE_OF)
    )
    # backoff is always top; per resource, of each mode the one with the smallest
    # budget, ties going to the smallest k
    tops = d.sort("budget", K_OF, nulls_last=True).select(
        *facet, "scheduler", "side", top=_top(*facet)
    )
    d = _titled(d.drop("side"), fin, tops, picks=False)
    note = pl.format("fewer than {} proofs", pl.col("need"))
    return (
        _bars_noted(d, "gain", note, zoom=zoom, over=facet)
        + _facets("resource", len(RESOURCES), "free")
        + p9.labs(
            x=f"less budget than {BASELINE_NAME} [%]",
            title=f"Budget to prove as many runs as {BASELINE_NAME}"
            " in the proof case studies",
            subtitle="time / e-graph size until as many runs are proved as"
            f" {BASELINE_NAME} proves in total\n({bases}), relative to"
            f" {BASELINE_NAME}'s; higher is better;\nthe k with the smallest budget"
            f" per benchmark {MODES} marked opaque;\n"
            f"// marks bar cut off; the line at 0 is {BASELINE_NAME}",
        )
    )


def cost_budget(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    zoom: float = 100,
) -> p9.ggplot:
    """Resource budget until a run first reaches backoff's final cost of that run, as
    savings over backoff's budget in percent, 1 − geomean over runs; schedulers that
    miss it in some run get no bar.
    `zoom`: bars are cut off at ±`zoom` percent, their labels still give the value."""
    df, fin = select(df, schedulers=schedulers)
    facet = ["case", "resource"]
    goal = fin.filter(pl.col("scheduler") == BASELINE).select(
        "case", "run", goal="cost"
    )
    reached = (
        _per_resource(df, "cost")
        .join(goal, on=["case", "run"])
        .filter(pl.col("cost") <= pl.col("goal"))
        .group_by(*KEY, "resource", maintain_order=True)
        .agg(budget=pl.col("x").first())
    )
    base = reached.filter(pl.col("scheduler") == BASELINE).select(
        "case", "run", "resource", base="budget"
    )
    budgets = (
        reached.join(base, on=["case", "run", "resource"])
        .group_by(*facet, "scheduler")
        .agg(
            ratio=_geomean(pl.col("budget") / pl.col("base")),
            reached=pl.len(),
        )
    )
    every = (
        fin.group_by("case", "scheduler")
        .agg(runs=pl.len())
        .join(
            pl.DataFrame({"resource": RESOURCES.values()}, {"resource": RESOURCE}),
            how="cross",
        )
    )
    d = (
        every.join(budgets, on=[*facet, "scheduler"], how="left")
        .with_columns(pl.col("reached").fill_null(0))
        .with_columns(
            gain=pl.when(pl.col("reached") == pl.col("runs")).then(
                (1 - pl.col("ratio")) * 100
            ),
            side=SIDE_OF,
        )
    )
    # backoff is always top; per resource, of each mode the one with the largest
    # saving, ties going to the smallest k
    tops = d.sort(-pl.col("gain"), K_OF, nulls_last=True).select(
        *facet, "scheduler", "side", top=_top(*facet)
    )
    d = _titled(d.drop("side"), fin, tops, picks=False)
    note = pl.format("reached in {} of {} runs", pl.col("reached"), pl.col("runs"))
    return (
        _bars_noted(d, "gain", note, zoom=zoom, over=facet)
        + _facets("resource", len(RESOURCES), "free")
        + p9.labs(
            x=f"less budget than {BASELINE_NAME} [%]",
            title=f"Budget to reach {BASELINE_NAME}'s final cost",
            subtitle="time / e-graph size until a run first reaches"
            f" {BASELINE_NAME}'s final cost,\nrelative to {BASELINE_NAME}'s;"
            " 1 − geomean over runs; higher is better;\nthe k with the largest"
            f" saving per benchmark {MODES} marked opaque;\n"
            f"// marks bar cut off; the line at 0 is {BASELINE_NAME}",
        )
    )


def _relative(
    df: pl.DataFrame, fin: pl.DataFrame, metric: Metric, absolute: bool = False
) -> pl.DataFrame:
    """`df` with `rel` = `metric` / the run's smallest final one (or `metric`)."""
    if absolute:
        return df.with_columns(rel=pl.col(metric))
    best = fin.group_by("case", "run").agg(best=pl.col(metric).min())
    return df.join(best, on=["case", "run"]).with_columns(
        rel=pl.col(metric) / pl.col("best")
    )


def _grid(d: pl.DataFrame, by: list[str], log: bool, n: int = 300) -> pl.DataFrame:
    """`n` evenly (or log-) spaced `x` per group of `by`."""
    lo, hi = pl.col("x").min(), pl.col("x").max()
    if log:
        lo, hi = lo.log(), hi.log()
    grid = d.group_by(by).agg(x=pl.linear_spaces(lo, hi, n)).explode("x")
    return grid.with_columns(pl.col("x").exp()) if log else grid


def _geomean_curves(d: pl.DataFrame, by: list[str], grid: pl.DataFrame) -> pl.DataFrame:
    """Geomean of `rel` at each `x` of `grid`, runs held at their ends."""
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


def quality(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    log_x: bool = False,
    log_y: bool = False,
    absolute: bool = False,
) -> p9.ggplot:
    """Cost relative to the run's best final cost over resource, geomean over runs.
    `log_x`/`log_y`: log axes; `absolute`: plain cost (only the y scale changes)."""
    df, fin = select(df, schedulers=schedulers)
    d = _per_resource(_relative(df, fin, "cost", absolute), "rel")
    by = ["case", "resource"]
    curves = _titled(_geomean_curves(d, by, _grid(d, by, log_x)), fin)
    return (
        _steps(curves, "rel", by, log_x)
        + _facets("resource", len(RESOURCES), "free")
        + (p9.scale_y_log10() if log_y else p9.scale_y_continuous())
        + p9.labs(
            x="",
            y="cost (geomean)" if absolute else "cost / best final cost (geomean)",
            title="Cost over time and e-graph size",
            subtitle=(
                "cost"
                if absolute
                else "cost relative to the best final cost of each run"
            )
            + f", geometric mean over runs;\n{TOP_NOTE};"
            "\n★ marks where a curve stops improving; lower is better",
        )
    )


def dev(
    df: pl.DataFrame,
    schedulers: Sequence[str] = SCHEDULERS,
    *,
    log_x: bool = False,
    log_y: bool = False,
    absolute: bool = False,
) -> p9.ggplot:
    """Cost, time and size relative to the run's smallest final one, per iteration.
    `log_x`/`log_y`: log axes; `absolute`: plain values (only the y scale changes)."""
    df, fin = select(df, schedulers=schedulers)
    d = pl.concat(
        _relative(df, fin, m, absolute).select(
            *KEY,
            "rel",
            metric=pl.lit(label, METRIC),
            x=pl.col("iteration").cast(pl.Float64),
        )
        for m, label in METRICS.items()
    )
    by = ["case", "metric"]
    grid = _grid(d, by, log_x).with_columns(pl.col("x").round()).unique()
    curves = _titled(_geomean_curves(d, by, grid), fin)
    return (
        # time and size only grow, so only the cost gets stars
        _steps(curves, "rel", by, log_x, star_if=pl.col("metric") == METRICS["cost"])
        + _facets("metric", len(METRICS), "free")
        + (p9.scale_y_log10() if log_y else p9.scale_y_continuous())
        + p9.labs(
            x="iteration",
            y="value (geomean)"
            if absolute
            else "value / smallest final value (geomean)",
            title="Development per iteration",
            subtitle="cost, time and e-graph size"
            + ("" if absolute else " relative to the smallest final value of each run")
            + f", geometric mean over runs;\n{TOP_NOTE};"
            "\n★ marks where the cost stops improving; lower is better",
        )
    )
