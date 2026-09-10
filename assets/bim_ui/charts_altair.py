"""bim_ui.charts_altair — Altair implementations of the chart builders.

Altair is the default engine for one deciding reason: it is a hard Streamlit
dependency, so it is already present in Streamlit in Snowflake. Plotly needs an
External Access Integration the customer has to request and approve, which turns
a dashboard migration into an infrastructure ticket.

It is also better on two things that matter here:

  * Rerun-free highlight. `alt.condition` on a selection restyles marks client
    side, so hovering or clicking to emphasise a category costs nothing. The
    Plotly path has to rebuild a colour list and rerun the script.
  * Label collision. Altair's text marks carry real layout semantics, so value
    labels can be positioned declaratively rather than nudged.

Every builder mirrors the signature in `charts_plotly` and honours the same
`ChartSpec`, so the facade in `charts.py` can dispatch to either engine and a
call site never learns which one it got.

Plotly is still the right answer for choropleth, treemap, sunburst, sankey,
gauge, and scatter above roughly 5,000 marks (WebGL). `charts.py` routes those
automatically.
"""

from __future__ import annotations

from typing import Sequence

import altair as alt
import pandas as pd

from .chart_spec import (
    ChartSpec,
    axis_order,
    collapse_tail,
    headroom,
    label_text,
    labels_fit,
    ordered_frame,
)
from .fmt import NumberFormat
from .theme import (
    ROLE_ACTUAL,
    ROLE_GRID,
    ROLE_TARGET,
    ROLE_TEXT,
    ROLE_TEXT_MUTED,
)

# Altair caps rows at 5,000 by default and raises MaxRowsError beyond it. A
# dashboard aggregate should never approach that; if it does, the chart is the
# wrong tool. Raise the ceiling rather than crash, and let the grid handle detail.
_MAX_ROWS = 100_000


def _axis(fmt: NumberFormat | None, *, title: str | None = None,
          grid: bool = True, ctx=None) -> alt.Axis:
    """Axis honouring the shared number format, so ticks match KPIs and tooltips.

    Altair validates eagerly and rejects None for properties like `format`,
    where Plotly would simply ignore it, so unset properties are omitted rather
    than passed as None.
    """
    p = ctx.palette if ctx else None
    props: dict[str, Any] = {
        "grid": grid,
        "gridColor": p[ROLE_GRID] if p else "#e6e6e6",
        "gridDash": [3, 3],
        "domain": False,
        "tickSize": 0,
        "labelPadding": 6,
        "labelColor": p[ROLE_TEXT_MUTED] if p else "#666",
        "titleColor": p[ROLE_TEXT_MUTED] if p else "#666",
        "labelFontSize": 10,
        "titleFontSize": 11,
    }
    # Vega-Lite treats an explicit null title as "no title"; omitting the key
    # entirely lets it fall back to the field name, which is not what we want on
    # a dashboard where the panel heading already names the measure.
    props["title"] = title
    spec_fmt = _vega_format(fmt)
    if spec_fmt:
        props["format"] = spec_fmt
    return alt.Axis(**props)


def _vega_format(fmt: NumberFormat | None) -> str | None:
    """d3 format string for a NumberFormat, or None when there is nothing to say."""
    if fmt is None or not hasattr(fmt, "vega_format"):
        return None
    try:
        return fmt.vega_format() or None
    except Exception:
        return None


def _base(chart: alt.Chart, ctx, spec: ChartSpec) -> alt.Chart:
    """Apply shared sizing, title and background treatment."""
    p = ctx.palette
    out = chart.properties(height=spec.height, width="container")
    if spec.title:
        out = out.properties(
            title=alt.TitleParams(
                text=spec.title, anchor="start", fontSize=13,
                color=p[ROLE_TEXT], fontWeight=600, offset=8,
            )
        )
    return out.configure_view(strokeWidth=0).configure_axis(labelOverlap=True)


def _tooltip(spec: ChartSpec, *, cat: str, val: str,
             cat_title: str | None = None) -> list[alt.Tooltip]:
    fmt = spec.y_format
    return [
        alt.Tooltip(f"{cat}:N", title=cat_title or cat.replace("_", " ").title()),
        alt.Tooltip(f"{val}:Q", title=val.replace("_", " ").title(),
                    format=fmt.vega_format() if fmt and hasattr(fmt, "vega_format") else ",.0f"),
    ]


def _selection(spec: ChartSpec, field: str):
    """Point selection bound to the emitted dimension, or None when not emitting.

    Named after the chart key so Streamlit's selection payload is unambiguous
    when several charts on a page emit.
    """
    if not (spec.emits and spec.key):
        return None
    return alt.selection_point(
        name=spec.key, fields=[field], toggle=True, empty="all",
        on="click", clear="dblclick",
    )


def _emphasis(sel, ctx, base_color: str):
    """Colour condition that dims unselected marks without a rerun."""
    if sel is None:
        return alt.value(base_color)
    return alt.condition(sel, alt.value(base_color), alt.value(ctx.palette[ROLE_GRID]))


def _ref_lines(ctx, spec: ChartSpec, *, horizontal: bool = True) -> list[alt.Chart]:
    """Static reference lines (goal, breakeven) as layered rules."""
    if not spec.reference_lines:
        return []
    p = ctx.palette
    layers: list[alt.Chart] = []
    for value, label in spec.reference_lines:
        frame = pd.DataFrame({"_ref": [value], "_label": [label]})
        enc = {"y": alt.Y("_ref:Q", title=None)} if horizontal else {"x": alt.X("_ref:Q", title=None)}
        layers.append(
            alt.Chart(frame).mark_rule(
                color=p[ROLE_TARGET], strokeDash=[4, 4], strokeWidth=1.5,
            ).encode(**enc)
        )
        layers.append(
            alt.Chart(frame).mark_text(
                align="right", dy=-6, fontSize=10, color=p[ROLE_TEXT_MUTED],
            ).encode(text="_label:N", **enc)
        )
    return layers


# ─────────────────────────────────────────────────────────────
#  Builders
# ─────────────────────────────────────────────────────────────

def bar_vs_target(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Actual bars with the target as a reference step line, not a rival series.

    Tableau draws plan as a reference line or Gantt marker. Drawing it as a
    second bar doubles the mark count and turns "am I above plan" into a
    comparison of two heights instead of a glance.
    """
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = ordered_frame(df, spec.x)
    # Altair defaults a nominal axis to alphabetical, so state the domain
    # explicitly or the drawn order will not match the frame's order.
    _order = axis_order(d[spec.x]) if spec.x in d.columns else None

    sel = _selection(spec, spec.x)
    vmax = float(pd.to_numeric(d[spec.y], errors="coerce").max() or 0)
    if spec.target and spec.target in d.columns:
        vmax = max(vmax, float(pd.to_numeric(d[spec.target], errors="coerce").max() or 0))

    color: alt.Color | alt.value
    if spec.color_by_status and spec.target and spec.target in d.columns:
        ratio = pd.to_numeric(d[spec.y], errors="coerce") / pd.to_numeric(
            d[spec.target], errors="coerce").replace(0, pd.NA)
        d = d.assign(_status=[ctx.status_color(r if pd.notna(r) else 0) for r in ratio])
        color = alt.Color("_status:N", scale=None, legend=None)
    elif spec.color_by_status:
        # No target column: y is already a ratio (an attainment or share), so it
        # IS the status value. Without this, a normalized measure could only be
        # status-coloured by reaching into engine internals from the call site.
        ratio = pd.to_numeric(d[spec.y], errors="coerce").fillna(0)
        d = d.assign(_status=[ctx.status_color(r) for r in ratio])
        color = alt.Color("_status:N", scale=None, legend=None)
    else:
        color = _emphasis(sel, ctx, p[ROLE_ACTUAL])

    scale = alt.Scale(domainMax=headroom(vmax, labels_fit(spec, len(d)))) if vmax else alt.Scale()

    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=2).encode(
        x=alt.X(f"{spec.x}:N", axis=_axis(None, grid=False, ctx=ctx), sort=_order),
        y=alt.Y(f"{spec.y}:Q", axis=_axis(spec.y_format, ctx=ctx), scale=scale),
        color=color,
        tooltip=_tooltip(spec, cat=spec.x, val=spec.y),
    )
    if sel is not None:
        bars = bars.add_params(sel)

    layers: list[alt.Chart] = [bars]

    if labels_fit(spec, len(d)):
        layers.append(
            alt.Chart(d.assign(_lab=label_text(d[spec.y], spec.y_format)))
            .mark_text(dy=-7, fontSize=10, color=ctx.palette[ROLE_TEXT_MUTED])
            .encode(
                x=alt.X(f"{spec.x}:N", sort=_order),
                y=alt.Y(f"{spec.y}:Q", scale=scale),
                text="_lab:N",
            )
        )

    if spec.target and spec.target in d.columns:
        # interpolate="step-after" reads as a per-period benchmark rather than a
        # trend line through the targets.
        layers.append(
            alt.Chart(d).mark_line(
                interpolate="step-after", strokeDash=[6, 4], strokeWidth=2.5,
                color=p[ROLE_TARGET],
            ).encode(
                x=alt.X(f"{spec.x}:N", sort=_order),
                y=alt.Y(f"{spec.target}:Q", scale=scale),
                tooltip=[alt.Tooltip(f"{spec.target}:Q", title=spec.target_label,
                                     format=",.0f")],
            )
        )

    layers += _ref_lines(ctx, spec)
    return _base(alt.layer(*layers).resolve_scale(y="shared"), ctx, spec)


def trend_line(df: pd.DataFrame, ctx, spec: ChartSpec,
               series_cols: Sequence[tuple[str, str]] | None = None) -> alt.LayerChart | None:
    """Multi-series line. series_cols is [(column, display_label), ...]."""
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = ordered_frame(df, spec.x)
    # Altair defaults a nominal axis to alphabetical, so state the domain
    # explicitly or the drawn order will not match the frame's order.
    _order = axis_order(d[spec.x]) if spec.x in d.columns else None

    cols = [c for c in (series_cols or [(spec.y, spec.y)]) if c[0] in d.columns]
    if not cols:
        return None

    # Reshape to long form: Altair encodes series as data, not as N traces.
    frames = [
        d[[spec.x, col]].rename(columns={col: "_value"}).assign(_series=lbl)
        for col, lbl in cols
    ]
    long = pd.concat(frames, ignore_index=True)
    vmax = float(pd.to_numeric(long["_value"], errors="coerce").max() or 0)

    order = [lbl for _, lbl in cols]
    line = alt.Chart(long).mark_line(point=alt.OverlayMarkDef(size=28), strokeWidth=2.4).encode(
        x=alt.X(f"{spec.x}:N", axis=_axis(None, grid=False, ctx=ctx), sort=_order),
        # No interpolation across gaps: a genuine hole in the data must read as a
        # hole, not as a trend. Altair breaks the line on null by default.
        y=alt.Y("_value:Q", axis=_axis(spec.y_format, ctx=ctx),
                scale=alt.Scale(domainMax=headroom(vmax, False)) if vmax else alt.Scale()),
        color=alt.Color("_series:N", title=None,
                        scale=alt.Scale(domain=order, range=p.series(len(order))),
                        legend=alt.Legend(orient="bottom", direction="horizontal",
                                          labelFontSize=11, symbolType="stroke")
                        if spec.legend and len(order) > 1 else None),
        tooltip=[alt.Tooltip(f"{spec.x}:N"), alt.Tooltip("_series:N", title="Series"),
                 alt.Tooltip("_value:Q", title="Value",
                             format=spec.y_format.vega_format()
                             if spec.y_format and hasattr(spec.y_format, "vega_format") else ",.0f")],
    )

    layers: list[alt.Chart] = [line]
    if spec.target and spec.target in d.columns:
        layers.append(
            alt.Chart(d).mark_line(strokeDash=[6, 4], strokeWidth=2,
                                   color=p[ROLE_TARGET]).encode(
                x=alt.X(f"{spec.x}:N", sort=_order),
                y=alt.Y(f"{spec.target}:Q"),
            )
        )
    layers += _ref_lines(ctx, spec)
    return _base(alt.layer(*layers), ctx, spec)


def ranked_bar(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Horizontal ranked bars. The right choice for long category labels.

    Replaces the treemap the first generation used: treemaps make small values
    unreadable and force a dark background.
    """
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = collapse_tail(df, spec.x, spec.y, spec.max_categories)
    d = d.groupby(spec.x, as_index=False)[spec.y].sum().sort_values(spec.y, ascending=False)

    sel = _selection(spec, spec.x)
    vmax = float(pd.to_numeric(d[spec.y], errors="coerce").max() or 0)
    # Bars here are horizontal whatever the spec says, and horizontal value
    # labels stack vertically rather than colliding, so exempt them from the
    # density rule that applies to vertical bars.
    _hspec = spec.replace(orientation="h")
    scale = alt.Scale(domainMax=headroom(vmax, labels_fit(_hspec, len(d)))) if vmax else alt.Scale()

    if spec.series and spec.series in df.columns:
        cmap = p.for_category(sorted(d[spec.x].astype(str)))
        d = d.assign(_c=[cmap.get(str(v), p[ROLE_ACTUAL]) for v in d[spec.x]])
        color = alt.Color("_c:N", scale=None, legend=None)
    else:
        color = _emphasis(sel, ctx, p[ROLE_ACTUAL])

    order = d[spec.x].astype(str).tolist()
    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y(f"{spec.x}:N", sort=order, axis=_axis(None, grid=False, ctx=ctx)),
        x=alt.X(f"{spec.y}:Q", axis=_axis(spec.y_format, ctx=ctx), scale=scale),
        color=color,
        tooltip=_tooltip(spec, cat=spec.x, val=spec.y),
    )
    if sel is not None:
        bars = bars.add_params(sel)

    layers: list[alt.Chart] = [bars]
    if labels_fit(_hspec, len(d)):
        layers.append(
            alt.Chart(d.assign(_lab=label_text(d[spec.y], spec.y_format)))
            .mark_text(align="left", dx=4, fontSize=10, color=p[ROLE_TEXT_MUTED])
            .encode(y=alt.Y(f"{spec.x}:N", sort=order),
                    x=alt.X(f"{spec.y}:Q", scale=scale), text="_lab:N")
        )
    layers += _ref_lines(ctx, spec, horizontal=False)
    return _base(alt.layer(*layers), ctx, spec)


def stacked_composition(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Stacked bars for part-to-whole over a category or time axis."""
    if df is None or len(df) == 0 or not spec.series:
        return None
    p = ctx.palette
    cats = sorted(df[spec.series].dropna().astype(str).unique().tolist())
    cmap = p.for_category(cats)

    d = df.copy()
    d[spec.series] = d[spec.series].astype(str)
    d = d.groupby([spec.x, spec.series], as_index=False)[spec.y].sum()
    d = ordered_frame(d, spec.x)
    _order = axis_order(d[spec.x])

    bars = alt.Chart(d).mark_bar().encode(
        x=alt.X(f"{spec.x}:N", axis=_axis(None, grid=False, ctx=ctx), sort=_order),
        y=alt.Y(f"{spec.y}:Q", axis=_axis(spec.y_format, ctx=ctx), stack="zero"),
        color=alt.Color(f"{spec.series}:N", title=None,
                        scale=alt.Scale(domain=cats, range=[cmap[c] for c in cats]),
                        legend=alt.Legend(orient="bottom", direction="horizontal",
                                          labelFontSize=11) if spec.legend else None),
        tooltip=[alt.Tooltip(f"{spec.x}:N"), alt.Tooltip(f"{spec.series}:N"),
                 alt.Tooltip(f"{spec.y}:Q", format=",.0f")],
    )
    return _base(alt.layer(bars, *_ref_lines(ctx, spec)), ctx, spec)


def share_bar(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Percent-of-total as a ranked bar.

    Chosen over a donut deliberately: people compare bar lengths far more
    accurately than pie angles, and category labels never clip the way donut
    outside-labels do.
    """
    if df is None or len(df) == 0:
        return None
    from .fmt import PERCENT

    p = ctx.palette
    d = collapse_tail(df, spec.x, spec.y, spec.max_categories)
    d = d.groupby(spec.x, as_index=False)[spec.y].sum()
    total = float(pd.to_numeric(d[spec.y], errors="coerce").sum())
    if total <= 0:
        return None
    d["_share"] = pd.to_numeric(d[spec.y], errors="coerce") / total
    d = d.sort_values("_share", ascending=False)
    d["_lab"] = [PERCENT.render(v) for v in d["_share"]]

    sel = _selection(spec, spec.x)
    cmap = p.for_category(d[spec.x].astype(str).tolist())
    d["_c"] = [cmap[str(v)] for v in d[spec.x]]
    order = d[spec.x].astype(str).tolist()
    scale = alt.Scale(domainMax=float(d["_share"].max()) * 1.18)

    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y(f"{spec.x}:N", sort=order, axis=_axis(None, grid=False, ctx=ctx)),
        x=alt.X("_share:Q", axis=alt.Axis(format="%", title=None, grid=True,
                                          gridDash=[3, 3], domain=False, tickSize=0,
                                          labelFontSize=10), scale=scale),
        color=alt.Color("_c:N", scale=None, legend=None),
        tooltip=[alt.Tooltip(f"{spec.x}:N"), alt.Tooltip("_share:Q", title="Share", format=".1%"),
                 alt.Tooltip(f"{spec.y}:Q", title="Value", format=",.0f")],
    )
    if sel is not None:
        bars = bars.add_params(sel)

    labels = alt.Chart(d).mark_text(
        align="left", dx=4, fontSize=10.5, color=p[ROLE_TEXT_MUTED],
    ).encode(y=alt.Y(f"{spec.x}:N", sort=order),
             x=alt.X("_share:Q", scale=scale), text="_lab:N")

    return _base(alt.layer(bars, labels), ctx, spec)


def cumulative_pace(df: pd.DataFrame, ctx, spec: ChartSpec, *,
                    series: str = "series") -> alt.LayerChart | None:
    """Cumulative lines per period, for pacing comparisons across months."""
    if df is None or len(df) == 0 or series not in df.columns:
        return None
    p = ctx.palette
    cats = list(dict.fromkeys(df[series].astype(str).tolist()))
    if not cats:
        return None
    current = cats[-1]

    d = df.copy()
    d[series] = d[series].astype(str)
    d = ordered_frame(d, spec.x)
    # Altair defaults a nominal axis to alphabetical, so state the domain
    # explicitly or the drawn order will not match the frame's order.
    _order = axis_order(d[spec.x]) if spec.x in d.columns else None
    # The in-progress period is emphasised; priors are context.
    d["_current"] = d[series] == current

    vmax = float(pd.to_numeric(d[spec.y], errors="coerce").max() or 0)
    line = alt.Chart(d).mark_line().encode(
        x=alt.X(f"{spec.x}:Q", axis=_axis(None, grid=False, ctx=ctx,
                                          title="Business day of month")),
        y=alt.Y(f"{spec.y}:Q", axis=_axis(spec.y_format, ctx=ctx),
                scale=alt.Scale(domainMax=headroom(vmax, False)) if vmax else alt.Scale()),
        color=alt.Color(f"{series}:N", title=None,
                        scale=alt.Scale(domain=cats, range=p.series(len(cats))),
                        legend=alt.Legend(orient="bottom", direction="horizontal",
                                          labelFontSize=11, symbolType="stroke")
                        if spec.legend else None),
        strokeWidth=alt.condition("datum._current", alt.value(2.8), alt.value(1.6)),
        strokeDash=alt.condition("datum._current", alt.value([1, 0]), alt.value([2, 3])),
        tooltip=[alt.Tooltip(f"{series}:N", title="Period"),
                 alt.Tooltip(f"{spec.x}:Q", title="Day"),
                 alt.Tooltip(f"{spec.y}:Q", format=",.0f")],
    )
    return _base(alt.layer(line, *_ref_lines(ctx, spec)), ctx, spec)


def heatmap(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Matrix heatmap. Replaces Tableau Square/Circle marks with colour encoding."""
    if df is None or len(df) == 0 or not spec.series:
        return None
    p = ctx.palette
    d = df.groupby([spec.series, spec.x], as_index=False)[spec.y].sum()
    if d.empty:
        return None
    # Calendar-order the column axis too, so a month matrix does not read
    # Apr, Aug, Feb, Jan.
    d = ordered_frame(d, spec.x)
    _order = axis_order(d[spec.x])
    fmt = spec.y_format
    d["_lab"] = [fmt.render(v) if fmt else f"{v:,.0f}" for v in d[spec.y]]

    rects = alt.Chart(d).mark_rect().encode(
        x=alt.X(f"{spec.x}:N", axis=_axis(None, grid=False, ctx=ctx), sort=_order),
        y=alt.Y(f"{spec.series}:N", axis=_axis(None, grid=False, ctx=ctx)),
        color=alt.Color(f"{spec.y}:Q", legend=None,
                        scale=alt.Scale(range=list(p.sequential))),
        tooltip=[alt.Tooltip(f"{spec.series}:N"), alt.Tooltip(f"{spec.x}:N"),
                 alt.Tooltip(f"{spec.y}:Q", format=",.0f")],
    )
    # Label colour flips on the darker half of the ramp so text stays legible.
    mid = float(pd.to_numeric(d[spec.y], errors="coerce").max() or 0) / 2
    text = alt.Chart(d).mark_text(fontSize=10).encode(
        x=alt.X(f"{spec.x}:N", sort=_order),
        y=alt.Y(f"{spec.series}:N"),
        text="_lab:N",
        color=alt.condition(f"datum['{spec.y}'] > {mid}",
                            alt.value("white"), alt.value(p[ROLE_TEXT])),
    )
    return _base(alt.layer(rects, text), ctx, spec)


def waterfall(df: pd.DataFrame, ctx, spec: ChartSpec) -> alt.LayerChart | None:
    """Waterfall for contribution / variance decomposition.

    Altair has no waterfall mark, so running totals are computed here and drawn
    as floating bars. A `measure` column of relative/total (Plotly's vocabulary)
    is honoured so both engines take the same frame.
    """
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = df.copy().reset_index(drop=True)
    measure = (d["measure"] if "measure" in d.columns
               else pd.Series(["relative"] * len(d)))

    lo, hi, colors = [], [], []
    running = 0.0
    for value, kind in zip(pd.to_numeric(d[spec.y], errors="coerce").fillna(0), measure):
        if kind == "total":
            lo.append(0.0)
            hi.append(running)
            colors.append(p[ROLE_ACTUAL])
        else:
            start = running
            running += float(value)
            lo.append(min(start, running))
            hi.append(max(start, running))
            colors.append(p["good"] if value >= 0 else p["bad"])

    d["_lo"], d["_hi"], d["_c"] = lo, hi, colors
    d["_lab"] = label_text(d[spec.y], spec.y_format)
    order = d[spec.x].astype(str).tolist()

    bars = alt.Chart(d).mark_bar(size=28).encode(
        x=alt.X(f"{spec.x}:N", sort=order, axis=_axis(None, grid=False, ctx=ctx)),
        y=alt.Y("_lo:Q", axis=_axis(spec.y_format, ctx=ctx), title=None),
        y2="_hi:Q",
        color=alt.Color("_c:N", scale=None, legend=None),
        tooltip=[alt.Tooltip(f"{spec.x}:N"), alt.Tooltip(f"{spec.y}:Q", format=",.0f")],
    )
    layers: list[alt.Chart] = [bars]
    if spec.show_labels:
        layers.append(
            alt.Chart(d).mark_text(dy=-7, fontSize=10, color=p[ROLE_TEXT_MUTED])
            .encode(x=alt.X(f"{spec.x}:N", sort=order), y=alt.Y("_hi:Q"), text="_lab:N")
        )
    return _base(alt.layer(*layers), ctx, spec)


def enable_max_rows() -> None:
    """Lift Altair's 5,000-row guard.

    A dashboard aggregate should never approach it; raising the ceiling beats
    crashing a page on a wide-but-legitimate frame. Detail belongs in the grid.
    """
    try:
        alt.data_transformers.disable_max_rows()
    except Exception:
        # Older Altair exposes this differently; a failure here is not fatal.
        pass
