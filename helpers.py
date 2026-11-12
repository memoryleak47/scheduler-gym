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

KEY = ("case", "scheduler", "benchmark")
Resource = Literal["time", "size", "rewrites"]
Side = Literal["backoff", "lhs", "rhs", "random", "other"]
COLORS: dict[Side, str] = {
    "backoff": "black",
    "lhs": "#E69F00",  # orange
    "rhs": "#0072B2",  # blue
    "random": "#CC79A7",  # reddish purple
    "other": "#009E73",  # bluish green
}  # Okabe-Ito colorblind-safe palette
SIDE = pl.Enum(COLORS.keys())
_NAME = pl.col("scheduler").cast(pl.String)
SIDE_OF = (
    pl.when(_NAME == "backoff")
    .then(pl.lit("backoff"))
    .when(_NAME.str.contains(r"^random-\d+$"))
    .then(pl.lit("random"))
    .otherwise(_NAME.str.extract(r"^(lhs|rhs)-cost-\d+$"))
    .cast(SIDE)
)
K_OF = _NAME.str.extract(r"-(\d+)$").cast(pl.Int64)
COST_SIDE = pl.col("side").is_in(["lhs", "rhs", "random"])
# whether the goal is proved, or the cost reached 1
PROVED = (pl.col("cost") <= 1) | pl.col("proved")
# the scheduler the bar charts are relative to
BASELINE = "backoff"
# alpha of the curves and bars that are not the best of their side
REST_ALPHA = 0.4
# marker for the break in cut bars: two slanted parallel strokes, like //
BREAK = MplPath(
    [(-0.6, -1), (-0.1, 1), (0.1, -1), (0.6, 1)],
    [MplPath.MOVETO, MplPath.LINETO, MplPath.MOVETO, MplPath.LINETO],
)
# bars are cut off at ±ZOOM percent with a // break, their labels still give the value
ZOOM = 100
# space between the modes in the bar charts, in bar rows
MODE_GAP = 0.5
# how the baseline is called in the plot texts
BASELINE_NAME = "Backoff"
# how the sides are called in the plot texts
SHOWN = {"lhs": "LHS", "rhs": "RHS", "random": "Random"}
MODES = "for each mode (LHS/RHS/Random)"


def _bar_note(best: str) -> str:
    """Last lines of a bar chart's subtitle, the opaque bars being all k with `best`."""
    return (
        f"all k with {best} per case study {MODES}\n"
        f"marked opaque and in bold; // marks bar cut off;"
        f" the line at 0 is {BASELINE_NAME}"
    )


RESOURCES: dict[Resource, str] = {
    "time": "Time [s]",
    "size": "E-graph size",
    "rewrites": "Rewrites applied",
}
# rewrites applied in an iteration: the k-schedulers apply at most k of the matches,
# backoff (no k) applies all it returns
APPLIED = pl.min_horizontal(K_OF, pl.col("matches"))
# the labels as ordered categories, so facets come in this order
RESOURCE = pl.Enum(RESOURCES.values())
# one row per resource, to cross join with
RESOURCE_ROWS = pl.DataFrame({"resource": RESOURCES.values()}, {"resource": RESOURCE})

ENTRY_PATTERN = (
    r"costs=\[(?P<costs>[^\]]*)\], total_size=(?P<size>\S+), time=(?P<time>\S+), "
    # matches is missing in entries written before it was added
    r"iteration=(?P<iteration>\d+), (?:matches=(?P<matches>\d+), )?stop=(?P<stop>.*)$"
)


def setup_style(dpi: int = 100):
    mpl.rcParams["pdf.fonttype"] = 42
    p9.theme_set(
        p9.theme_minimal()
        + p9.theme(
            # panel_grid_minor=p9.element_line(color="#EBEBEB", size=0.5),
            #     panel_grid_minor=p9.element_blank(),
            #     strip_text=p9.element_text(size=9, weight="bold", ha="left"),
            #     legend_title=p9.element_blank(),
            #     plot_title=p9.element_text(size=11, weight="bold", ha="left"),
            #     plot_title_position="plot",
            dpi=dpi,
        )
    )


def size(width: float, height: float) -> p9.theme:
    """Figure size in inches, to add to a plot: `plot + size(12, 4)`."""
    return p9.theme(figure_size=(width, height))


def save(plot: p9.ggplot, name: str) -> None:
    """Write `plot` to figures/`name`.pdf.

    Also writes figures/`name`-no-title.pdf without title and subtitle, for use
    in a tex file, and prints them so they can go into the caption.
    """
    FIGURES.mkdir(exist_ok=True)
    plot.save(FIGURES / f"{name}.pdf", verbose=False)
    bare = plot + p9.theme(
        plot_title=p9.element_blank(), plot_subtitle=p9.element_blank()
    )
    bare.save(FIGURES / f"{name}-no-title.pdf", verbose=False)
    print(f"{name}\n{plot.labels.get('title', '')}\n{plot.labels.get('subtitle', '')}")


def _parse(path: Path) -> pl.DataFrame:
    """Entries of one file; `benchmark` numbers its benchmarks, unfinished trailing ones dropped."""
    lines = pl.read_csv(
        path,
        has_header=False,
        separator="\x1f",
        quote_char=None,
        schema={"line": pl.String},
    )
    df = lines.select(pl.col("line").str.extract_groups(ENTRY_PATTERN)).unnest("line")
    if bad := df.drop("matches").null_count().sum_horizontal().item():
        raise ValueError(f"{path}: {bad} fields of malformed entry lines")
    finished = (pl.col("stop").str.strip_chars() != "None").cast(pl.Int64)
    return df.filter(finished.cum_sum(reverse=True) > 0).select(
        pl.lit(path.stem).alias("case"),
        pl.lit(path.parent.name.removesuffix(".rs")).alias("scheduler"),
        benchmark=finished.cum_sum().shift(1, fill_value=0),
        iteration=pl.col("iteration").cast(pl.Int64),
        cost=pl.col("costs").str.split(", ").cast(pl.List(pl.Float64)).list.sum(),
        size=pl.col("size").cast(pl.Float64),
        time=pl.col("time").cast(pl.Float64),
        # substitutions returned by BackoffScheduler::search_rewrite in this iteration,
        # 0 for schedulers that don't use it, null for old entries
        matches=pl.col("matches").cast(pl.Int64),
        # lean-egg's goal hook stops the benchmark on a proof, its cost stays above 1
        proved=pl.col("stop").str.contains("Proved goal", literal=True),
        # trig's unsound rules can merge different constants, its benchmark then ends with
        # this stop and a copy of the last entry before the panic
        panicked=pl.col("stop").str.contains('Other("Panicked")', literal=True),
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


def load(*, root: Path = BENCHDATA) -> pl.DataFrame:
    """One row per entry of the benchmarks bench.py wrote to `root`; `benchmark` numbers
    the benchmarks in a file, unfinished ones dropped. Benchmarks that panicked are left out, and listed."""
    cache = root / ".cache"
    paths = sorted(root.glob("*/*.entries"))
    # an enum, not a categorical, so cases sort (and facet) alphabetically as strings
    cases = pl.Enum(sorted({p.stem for p in paths}))
    # diagonal: frames cached before `matches` was added lack that column
    df = pl.concat(
        [_cached(p, cache, cases) for p in paths], how="diagonal", rechunk=True
    )
    return _drop_panicked(df)


def _drop_panicked(df: pl.DataFrame) -> pl.DataFrame:
    """`df` without the benchmarks that panicked, printing how many per case and scheduler:
    their e-graph merged different constants, so their costs can't be trusted."""
    benchmarks = df.group_by(KEY).agg(pl.col("panicked").any())
    counts = (
        benchmarks.group_by("case", "scheduler")
        .agg(pl.col("panicked").sum(), benchmarks=pl.len())
        .filter(pl.col("panicked") > 0)
        .sort("case", "scheduler")
    )
    for case, scheduler, panicked, total in counts.iter_rows():
        print(
            f"{case}: leaving out {panicked}/{total} benchmarks of {scheduler} that panicked"
        )
    return df.join(benchmarks.filter("panicked").select(KEY), on=KEY, how="anti").drop(
        "panicked"
    )


def select(
    df: pl.DataFrame, *, schedulers: Sequence[str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rows of `schedulers` in that order, and finals: one row per benchmark with the final
    cost, the benchmark's best final cost, resources to reach it, `side` and `top`."""
    sides = pl.DataFrame({"scheduler": schedulers}).select("scheduler", side=SIDE_OF)
    if unknown := sides.filter(pl.col("side").is_null())["scheduler"].to_list():
        raise ValueError(f"schedulers without a side: {unknown}")
    df = df.filter(pl.col("scheduler").is_in(schedulers)).with_columns(
        pl.col("scheduler").cast(pl.String).cast(pl.Enum(schedulers)),
        # rewrites applied so far in the benchmark
        rewrites=APPLIED.cum_sum().over(KEY).cast(pl.Float64),
    )
    fin = df.group_by(KEY, maintain_order=True).last()
    fin = fin.with_columns(best=pl.col("cost").min().over("case", "benchmark"))
    reached = (
        df.join(fin.select(*KEY, "best"), on=KEY)
        .filter(pl.col("cost") <= pl.col("best"))
        .group_by(KEY, maintain_order=True)
        .first()
        .select(*KEY, *(pl.col(r).alias(f"{r}_to_best") for r in RESOURCES))
    )
    fin = fin.join(reached, on=KEY, how="left")

    # of each mode the one with the lowest geomean of final cost relative to the best
    # final cost of each benchmark
    ratios = fin.group_by("case", "scheduler").agg(
        ratio=_geomean(pl.col("cost") / pl.col("best"))
    )
    top = _tops(ratios, ["case"], "ratio")
    return df, fin.join(top, on=["case", "scheduler"], how="left")


def check_benchmarks(df: pl.DataFrame, *, schedulers: Sequence[str]):
    """Print cases where `schedulers` have different numbers of finished benchmarks,
    benchmarks that panicked counting as finished."""
    _, fin = select(df, schedulers=schedulers)
    # `benchmark` is the goal's index in every scheduler's file, so benchmarks left out in
    # the middle (see `_drop_panicked`) don't count, only missing ones at the end
    counts = fin.group_by("case", "scheduler").agg(len=pl.col("benchmark").max() + 1)
    for (case,), g in counts.group_by("case"):
        if g["len"].n_unique() > 1:
            print(f"{case}: schedulers disagree on number of benchmarks:")
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


def _tops(
    d: pl.DataFrame, by: Sequence[str], *keys: str | pl.Expr, ties: bool = False
) -> pl.DataFrame:
    """`side` and `top` per `by` and scheduler: backoff is always top, and of each mode
    the first by `keys` (nulls last), ties going to the smallest k, or with `ties` all
    that tie with the first."""
    d = d.with_columns(side=SIDE_OF).sort(*keys, K_OF, nulls_last=True)
    if not ties:
        return d.select(*by, "scheduler", "side", top=_top(*by))
    exprs = [pl.col(k) if isinstance(k, str) else k for k in keys]
    first = pl.all_horizontal(e == e.first().over(*by, "side") for e in exprs)
    return d.select(
        *by,
        "scheduler",
        "side",
        top=pl.when(COST_SIDE)
        .then(first.fill_null(False))
        .otherwise(pl.col("side") == "backoff"),
    )


def _titled(
    d: pl.DataFrame,
    fin: pl.DataFrame,
    tops: pl.DataFrame | None = None,
    *,
    picks: bool = True,
) -> pl.DataFrame:
    """`d` with `side`, `top` and a facet `title`: case, benchmarks, top k of each mode.
    `tops`: `side` and `top` per scheduler and further keys, instead of `fin`'s;
    `picks`: whether the title lists the top k, bar charts show them anyway."""
    if tops is None:
        tops = fin.select("case", "scheduler", "side", "top").unique()
    by = [c for c in tops.columns if c not in ("scheduler", "side", "top")]
    top = pl.col("top") & COST_SIDE
    pick = pl.format("{} {}", pl.col("side").cast(pl.String).replace(SHOWN), K_OF)
    benchmarks = fin.group_by("case").agg(benchmarks=pl.col("benchmark").n_unique())
    titles = (
        tops.join(benchmarks, on="case")
        .group_by(by)
        .agg(
            # the top picks on a line of their own, they don't fit next to the case
            title=pl.format(
                "{} ({} benchmarks)\n{}",
                pl.col("case").first(),
                pl.col("benchmarks").first(),
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


class _FreeGrid(p9.facet_grid):
    """facet_grid with its own x and y scale and axes on every panel, unlike
    facet_grid's free scales, which only free them per column / row."""

    def compute_layout(self, data):
        layout = super().compute_layout(data)
        panel = layout["PANEL"].astype(int)
        layout["SCALE_X"] = panel if self.free["x"] else 1
        layout["SCALE_Y"] = panel if self.free["y"] else 1
        layout["AXIS_X"] = layout["AXIS_Y"] = True
        return layout


def _case_grid(scales: Literal["free", "free_x"]) -> p9.facet_grid:
    """One column per case, titled with case and benchmarks; one row per resource,
    labeled on the right; each panel with its own `scales`."""
    return _FreeGrid("resource", "title", scales=scales)


def _steps(d: pl.DataFrame, y: str, *, log: bool) -> p9.ggplot:
    """Step lines per scheduler, top ones opaque; `log`: log x axis."""
    top = d.filter(pl.col("top"))
    # legend with only the sides present
    breaks = [side for side in COLORS if side in d["side"]]
    return (
        p9.ggplot(d, p9.aes("x", y, color="side", group="scheduler"))
        + p9.geom_step(data=d.filter(~pl.col("top")), size=0.5, alpha=REST_ALPHA)
        # a white halo, so the top curves stand out where they cross the rest
        + p9.geom_step(data=top, size=2.4, color="white")
        + p9.geom_step(data=top, size=1.4)
        + (p9.scale_x_log10() if log else p9.scale_x_continuous())
        + p9.scale_color_manual(
            values=COLORS,
            breaks=breaks,
            labels=[
                BASELINE_NAME if s == BASELINE else SHOWN.get(s, s) for s in breaks
            ],
            name="Schedulers",
        )
    )


class _BarPlot(p9.ggplot):
    """A ggplot whose y tick labels are bold where a layer whose geom has the param
    `bold_ticks` has a row in that panel, so each facet bolds its own top schedulers."""

    def draw(self, *, show: bool = False):
        figure = super().draw(show=show)
        for layer in self.layers:
            if not layer.geom.params.get("bold_ticks"):
                continue
            data = layer.data
            for pidx, ax in enumerate(self.axs):
                panel = pidx + 1
                bold = set(data.loc[data["PANEL"] == panel, "y"])
                for loc, tick in zip(
                    ax.yaxis.get_majorticklocs(), ax.yaxis.get_major_ticks()
                ):
                    if loc in bold:
                        tick.label1.set_fontweight("bold")
        return figure


def _positions(d: pl.DataFrame) -> tuple[pl.DataFrame, list[float], list[str]]:
    """`d` with the y position `pos` of its scheduler, and the breaks and labels:
    the first category on top, `MODE_GAP` between the modes. Backoff is left out."""
    dtype = d["scheduler"].dtype
    assert isinstance(dtype, pl.Enum)
    names = [s for s in dtype.categories.reverse() if s != BASELINE]
    sides = pl.DataFrame({"scheduler": names}).select(side=SIDE_OF)["side"]
    pos, breaks = 0.0, []
    for i, side in enumerate(sides):
        if i and side != sides[i - 1]:
            pos += MODE_GAP
        breaks.append(pos)
        pos += 1
    where = pl.DataFrame(
        {"scheduler": names, "pos": breaks}, {"scheduler": dtype, "pos": pl.Float64}
    )
    # lhs-cost-400 -> LHS 400, random-400 -> Random 400
    labels = [re.sub(r"(-cost)?-(\d+)$", r" \2", s) for s in names]
    labels = [re.sub(r"^\w+", lambda m: SHOWN.get(m[0], m[0]), s) for s in labels]
    return d.join(where, on="scheduler"), breaks, labels


def _bars(d: pl.DataFrame, value: str, *, over: Sequence[str] = ("case",)) -> p9.ggplot:
    """Bars of `value` per scheduler, cut at ±`ZOOM` with a // break.
    Backoff is left out, it is the line at 0. `over`: the columns of a facet."""
    d, breaks, labels = _positions(d)
    gain = pl.col(value) >= 0
    # room for the labels past the bars, about a third of the case's range of bars
    span = pl.max_horizontal(pl.col("end").max(), 0) - pl.min_horizontal(
        pl.col("end").min(), 0
    )
    d = (
        d.filter(pl.col("scheduler") != BASELINE)
        .with_columns(end=pl.col(value).clip(-ZOOM, ZOOM))
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
    bar = p9.aes(yend="pos")
    label = p9.aes(x="end", label="text")
    top = p9.geom_segment(bar, data=d.filter(pl.col("top")), size=4)
    top.params["bold_ticks"] = True
    return (
        _BarPlot(d, p9.aes(y="pos", x=0, xend="end", color="side"))
        + p9.geom_vline(xintercept=0, color=COLORS["backoff"], size=0.5)
        + p9.geom_blank(p9.aes(x="reach"))
        + p9.geom_segment(bar, data=d.filter(~pl.col("top")), size=4, alpha=REST_ALPHA)
        + top
        + p9.geom_point(
            p9.aes(x="gap"),
            data=d.filter(pl.col(value).abs() > ZOOM),
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
        + p9.scale_y_continuous(
            breaks=breaks,
            labels=labels,
            limits=(breaks[0], breaks[-1]),
            # as a discrete scale would, half a row past the outer bars
            expand=(0, 0.6),
        )
        + p9.scale_color_manual(values=COLORS)
        + p9.guides(color="none")
        + p9.labs(y="")
        + p9.theme(
            panel_grid_major_y=p9.element_blank(),
            panel_grid_minor_y=p9.element_blank(),
            # left aligned, so the modes line up and the k are easier to compare
            axis_text_y=p9.element_text(ha="left"),
        )
    )


def _bars_noted(
    d: pl.DataFrame, value: str, note: pl.Expr, *, over: Sequence[str]
) -> p9.ggplot:
    """`_bars` of the rows with a `value`; the others get `note` in gray at 0,
    on the side of 0 with more room."""
    gain = pl.col(value).clip(-ZOOM, ZOOM)
    left = (-gain.min().over(over) > gain.max().over(over)).fill_null(False)
    short = (
        _positions(d)[0]
        .with_columns(left=left)
        .filter(pl.col(value).is_null())
        .with_columns(
            text=pl.when("left")
            .then(pl.format("{} ", note))
            .otherwise(pl.format(" {}", note))
        )
    )
    aes = p9.aes(x=0, y="pos", label="text")
    return (
        _bars(d.filter(pl.col(value).is_not_null()), value, over=over)
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


def ranking(df: pl.DataFrame, schedulers: Sequence[str]) -> p9.ggplot:
    """Final cost reduction over backoff in percent, geomean over benchmarks."""
    _, fin = select(df, schedulers=schedulers)
    base = fin.filter(pl.col("scheduler") == BASELINE).select(
        "case", "benchmark", base=pl.col("cost")
    )
    d = (
        fin.join(base, on=["case", "benchmark"])
        .group_by("case", "scheduler")
        .agg(gain=(1 - _geomean(pl.col("cost") / pl.col("base"))) * 100)
    )
    return (
        # of each mode all with the largest reduction, the same order as `select`'s
        # ratio to the best final cost
        _bars(
            _titled(
                d, fin, _tops(d, ["case"], -pl.col("gain"), ties=True), picks=False
            ),
            "gain",
        )
        + p9.facet_wrap("title", nrow=1, scales="free")
        + p9.labs(
            x=f"Cost reduction vs. {BASELINE_NAME}",
            title=f"Final cost relative to {BASELINE_NAME}",
            subtitle=f"1 − geomean over benchmarks of final cost / {BASELINE_NAME}'s final"
            f" cost; higher is better;\n{_bar_note('the lowest final cost')}",
        )
    )


def solved(df: pl.DataFrame, schedulers: Sequence[str]) -> p9.ggplot:
    """Change in benchmarks that proved the goal, relative to backoff in percent."""
    _, fin = select(df, schedulers=schedulers)
    d = fin.group_by("case", "scheduler").agg(solved=PROVED.sum(), benchmarks=pl.len())
    base = d.filter(pl.col("scheduler") == BASELINE).select(
        "case", base="solved", base_benchmarks="benchmarks"
    )
    for case, b, _ in base.iter_rows():
        assert b > 0, f"{BASELINE} solved no benchmarks of {case}"
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
        _bars(_titled(d, fin, tops, picks=False), "gain")
        + p9.facet_wrap("title", nrow=1, scales="free")
        + p9.labs(
            x=f"Extra solved vs. {BASELINE_NAME}",
            title="Proved goals in the proof case studies",
            subtitle=f"benchmarks that proved the goal, relative to {BASELINE_NAME}'s;\n"
            f"{BASELINE_NAME} solved {bases}; higher is better;\n"
            + _bar_note("the most proofs"),
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
    """Per benchmark and resource, `x` at the first proof; benchmarks without one are left out."""
    return (
        d.filter(proved)
        .group_by(*KEY, "resource", maintain_order=True)
        .agg(pl.col("x").first())
    )


def solved_profile(
    df: pl.DataFrame,
    schedulers: Sequence[str],
    *,
    y_min: float = 0,
    y_max: float = 1,
    log: bool = False,
) -> p9.ggplot:
    """Fraction of benchmarks that proved the goal within a resource budget.
    `y_min`, `y_max`: ends of the y axis, to zoom in on a range; `log`: log x axis."""
    df, fin = select(df, schedulers=schedulers)
    group = ["case", "resource", "scheduler"]
    d = _per_resource(df, "cost", "proved")
    benchmarks = fin.group_by("case", "scheduler").agg(benchmarks=pl.len())
    proofs = (
        _first_proofs(d, PROVED)
        .join(benchmarks, on=["case", "scheduler"])
        .with_columns(y=pl.col("x").rank("ordinal").over(group) / pl.col("benchmarks"))
    )
    # without any proof, the curves start at the smallest resource used at all
    lo = d.group_by("case", "resource").agg(
        lo=pl.col("x").filter(PROVED).min().fill_null(pl.col("x").min())
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
        .with_columns(pl.col("y", "n").fill_null(0))
    )
    # step curves from 0 at `lo` through the proofs to `hi`
    curves = pl.concat(
        [
            ends.select(*group, x="lo", y=pl.lit(0.0)),
            proofs.select(*group, "x", "y"),
            ends.select(*group, x="hi", y="y"),
        ]
    ).sort("x", maintain_order=True)
    # per resource, of each mode the one with the most proofs, ties going to the
    # smallest budget of the last proof
    most = -pl.col("n").cast(pl.Int64)
    curves = _titled(curves, fin, _tops(ends, ["case", "resource"], most, "last"))
    # the top k are picked per resource, but the column title can only list them if
    # they agree across a case's resources
    differ = (
        curves.group_by("case")
        .agg(pl.col("title").unique())
        .filter(pl.col("title").list.len() > 1)
    )
    assert differ.is_empty(), f"top k differ between resources:\n{differ}"
    return (
        _steps(curves, "y", log=log)
        + _case_grid("free_x")
        + p9.coord_cartesian(ylim=(y_min, y_max))
        + p9.labs(
            x="",
            y="Benchmarks with a proof",
            title="Fraction of problems proven within a time / e-graph size / rewrites budget"
            " in the proof case studies",
            subtitle=f"{BASELINE_NAME} and the k with the most proofs for each mode"
            " (LHS/RHS/Random,\nties go to the smaller budget at the last proof)"
            " marked opaque;\nhigher is better",
        )
    )


def solved_budget(df: pl.DataFrame, schedulers: Sequence[str]) -> p9.ggplot:
    """Resource budget a scheduler needs to prove as many benchmarks as backoff does in
    total, as savings over backoff's budget in percent; schedulers that prove fewer
    get no bar."""
    df, fin = select(df, schedulers=schedulers)
    facet = ["case", "resource"]
    proofs = _first_proofs(_per_resource(df, "cost", "proved"), PROVED)
    need = (
        proofs.filter(pl.col("scheduler") == BASELINE)
        .group_by(facet)
        .agg(need=pl.len())
    )
    for case in fin["case"].unique():
        assert case in need["case"], f"{BASELINE} solved no benchmarks of {case}"
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
        .join(RESOURCE_ROWS, how="cross")
        .join(need, on=facet)
    )
    d = (
        every.join(budgets, on=[*facet, "scheduler"], how="left")
        .join(base, on=facet)
        .with_columns(gain=(1 - pl.col("budget") / pl.col("base")) * 100)
    )
    # per resource, of each mode all with the smallest budget
    d = _titled(d, fin, _tops(d, facet, "budget", ties=True), picks=False)
    note = pl.format("fewer than {} proofs", pl.col("need"))
    return (
        _bars_noted(d, "gain", note, over=facet)
        + _case_grid("free")
        + p9.labs(
            x=f"Budget saved vs. {BASELINE_NAME}",
            title=f"Budget to prove as many benchmarks as {BASELINE_NAME}"
            " in the proof case studies",
            subtitle="time / e-graph size / rewrites until as many benchmarks are proved as"
            f" {BASELINE_NAME} proves in total\n({bases}), relative to"
            f" {BASELINE_NAME}'s; higher is better;\n"
            + _bar_note("the smallest budget"),
        )
    )


def cost_budget(df: pl.DataFrame, schedulers: Sequence[str]) -> p9.ggplot:
    """Resource budget until a benchmark first reaches backoff's final cost of that
    benchmark, as savings over backoff's budget in percent, 1 − geomean over
    benchmarks; schedulers that miss it in some benchmark get no bar."""
    df, fin = select(df, schedulers=schedulers)
    facet = ["case", "resource"]
    goal = fin.filter(pl.col("scheduler") == BASELINE).select(
        "case", "benchmark", goal="cost"
    )
    reached = (
        _per_resource(df, "cost")
        .join(goal, on=["case", "benchmark"])
        .filter(pl.col("cost") <= pl.col("goal"))
        .group_by(*KEY, "resource", maintain_order=True)
        .agg(budget=pl.col("x").first())
    )
    base = reached.filter(pl.col("scheduler") == BASELINE).select(
        "case", "benchmark", "resource", base="budget"
    )
    budgets = (
        reached.join(base, on=["case", "benchmark", "resource"])
        .group_by(*facet, "scheduler")
        .agg(ratio=_geomean(pl.col("budget") / pl.col("base")), reached=pl.len())
    )
    every = (
        fin.group_by("case", "scheduler")
        .agg(benchmarks=pl.len())
        .join(RESOURCE_ROWS, how="cross")
    )
    d = (
        every.join(budgets, on=[*facet, "scheduler"], how="left")
        .with_columns(pl.col("reached").fill_null(0))
        .with_columns(
            gain=pl.when(pl.col("reached") == pl.col("benchmarks")).then(
                (1 - pl.col("ratio")) * 100
            )
        )
    )
    # per resource, of each mode all with the largest saving
    d = _titled(d, fin, _tops(d, facet, -pl.col("gain"), ties=True), picks=False)
    note = pl.format(
        "reached in {} of {} benchmarks", pl.col("reached"), pl.col("benchmarks")
    )
    return (
        _bars_noted(d, "gain", note, over=facet)
        + _case_grid("free")
        + p9.labs(
            x=f"Budget saved vs. {BASELINE_NAME}",
            title=f"Budget to reach {BASELINE_NAME}'s final cost",
            subtitle="time / e-graph size / rewrites until a benchmark first reaches"
            f" {BASELINE_NAME}'s final cost,\nrelative to {BASELINE_NAME}'s;"
            " 1 − geomean over benchmarks; higher is better;\n"
            + _bar_note("the largest saving"),
        )
    )


def apply_rate(
    df: pl.DataFrame,
    schedulers: Sequence[str],
    *,
    ks: Sequence[int] | None = None,
    log: bool = False,
) -> p9.ggplot:
    """Fraction of the available matches applied per iteration, geomean over the
    benchmarks still running; one row per mode, one column per case, one curve per k.
    Backoff applies all it gets, so it is left out, and so are iterations without matches.
    `ks`: the k to show, all of `schedulers`' by default; `log`: log x axis."""
    if ks is not None:
        named = pl.DataFrame({"scheduler": schedulers}).with_columns(k=K_OF)
        if missing := set(ks) - set(named["k"]):
            raise ValueError(f"ks not in schedulers: {sorted(missing)}")
        schedulers = named.filter(pl.col("k").is_in(ks))["scheduler"].to_list()
    df, fin = select(df, schedulers=schedulers)
    benchmarks = fin.group_by("case").agg(n=pl.col("benchmark").n_unique())
    d = (
        df.filter(pl.col("matches") > 0)
        .with_columns(side=SIDE_OF, k=K_OF)
        .filter(COST_SIDE)
        .group_by("case", "side", "scheduler", "k", "iteration")
        .agg(rate=_geomean(APPLIED / pl.col("matches")))
        .join(benchmarks, on="case")
        .with_columns(
            title=pl.format("{} ({} benchmarks)", pl.col("case"), pl.col("n"))
        )
        .sort("iteration")
    )
    ks = sorted(d["k"].unique())
    d = d.with_columns(
        # ordered, so the legend and the colors follow k
        k=pl.col("k").cast(pl.String).cast(pl.Enum([str(k) for k in ks])),
        side=pl.col("side")
        .cast(pl.String)
        .replace(SHOWN)
        .cast(pl.Enum([*SHOWN.values()])),
    )
    return (
        p9.ggplot(d, p9.aes("iteration", "rate", color="k", group="scheduler"))
        + p9.geom_step(size=0.8)
        + p9.facet_grid("side", "title", scales="free_x")
        + (p9.scale_x_log10() if log else p9.scale_x_continuous())
        + p9.scale_y_log10(labels=lambda ys: [f"{y * 100:g}%" for y in ys])
        # sequential, as k is a magnitude; without viridis' yellow end, too light on white
        + p9.scale_color_manual(
            values=[
                mpl.colors.to_hex(
                    mpl.colormaps["viridis"](i / max(len(ks) - 1, 1) * 0.85)
                )
                for i in range(len(ks))
            ],
            name="k",
        )
        + p9.labs(
            x="Iteration",
            y="Applied / available matches (geomean)",
            title="Fraction of the available matches applied per iteration",
            subtitle="min(k, matches) / matches, geometric mean over the benchmarks still"
            " running at that iteration;\niterations without matches left out",
        )
    )


def _x_samples(d: pl.DataFrame, by: list[str], log: bool, n: int = 300) -> pl.DataFrame:
    """`n` evenly (or log-) spaced `x` per group of `by`."""
    lo, hi = pl.col("x").min(), pl.col("x").max()
    if log:
        lo, hi = lo.log(), hi.log()
    grid = d.group_by(by).agg(x=pl.linear_spaces(lo, hi, n)).explode("x")
    return grid.with_columns(pl.col("x").exp()) if log else grid


def _geomean_curves(d: pl.DataFrame, by: list[str], grid: pl.DataFrame) -> pl.DataFrame:
    """Geomean of `rel` at each `x` of `grid`, benchmarks held at their ends."""
    bench = [*by, "scheduler", "benchmark"]
    d = d.select(*bench, "x", "rel").sort("x", maintain_order=True)
    starts = d.group_by(bench).agg(start=pl.col("rel").first())
    return (
        grid.join(starts, on=by)
        .sort("x")
        .join_asof(d, on="x", by=bench, check_sortedness=False)
        .group_by(*by, "scheduler", "x")
        .agg(rel=_geomean(pl.col("rel").fill_null(pl.col("start"))))
    )


def quality(
    df: pl.DataFrame, schedulers: Sequence[str], *, log: bool = False
) -> p9.ggplot:
    """Cost relative to the benchmark's best final cost over resource, geomean over benchmarks.
    `log`: log x and y axes."""
    df, fin = select(df, schedulers=schedulers)
    df = df.join(fin.select(*KEY, "best"), on=KEY)
    d = _per_resource(df.with_columns(rel=pl.col("cost") / pl.col("best")), "rel")
    by = ["case", "resource"]
    # the top k are per case, so the column title can list them
    curves = _titled(_geomean_curves(d, by, _x_samples(d, by, log)), fin)
    return (
        _steps(curves, "rel", log=log)
        + _case_grid("free")
        + (p9.scale_y_log10() if log else p9.scale_y_continuous())
        + p9.labs(
            x="",
            y="Cost / best final cost (geomean)",
            title="Cost over time, e-graph size and rewrites applied",
            subtitle="cost relative to the best final cost of each benchmark,"
            " geometric mean over benchmarks;\n"
            f"{BASELINE_NAME} and the k with the lowest final cost per case study"
            f" {MODES} marked opaque;\nlower is better",
        )
    )
