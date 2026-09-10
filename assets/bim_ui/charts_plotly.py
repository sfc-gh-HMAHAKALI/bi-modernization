"""
bim_ui.charts_plotly — Plotly implementations of the chart builders.

Not the default engine. Plotly needs an External Access Integration the customer
has to request and approve, whereas Altair ships with Streamlit; see
`charts_altair` for that argument. This engine remains the right answer for
choropleth, treemap, sunburst, sankey, gauge, and scatter above roughly 5,000
marks (WebGL), and as an escape hatch when an Altair chart does not land.

Every builder here bakes in the things that were hand-fixed three times over in
the first-generation app:

  - label headroom is COMPUTED from the data, so outside value labels cannot be
    clipped by the plot edge. This was the single most common defect.
  - reference lines for plan/target/quota, which Tableau treats as table stakes
    and the previous generator emitted as "a second bar series".
  - semantic colors from the palette, never literal hex, so actual/target and
    good/bad mean one thing across the whole app.
  - on_select wiring when a chart declares an emitted dimension.
  - an empty-state fallback, so a filter that excludes everything renders a
    message rather than an empty axis box.

Plotly version floor: 5.9. Deliberately avoids cornerradius (5.19+),
legendgrouptitle, and minor axis properties, all of which break on the older
Plotly builds that ship in some Snowflake environments.
"""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import compat, states
from .chart_spec import (
    _LABEL_HEADROOM,
    _NO_LABEL_HEADROOM,
    ChartSpec,
    Orientation,
)
from .chart_spec import collapse_tail as _collapse_tail
from .chart_spec import calendar_order as _calendar_order
from .chart_spec import ordered_frame as _ordered_frame
from .chart_spec import label_text as _label_text
from .chart_spec import labels_fit as _labels_fit
from .fmt import NumberFormat
from .theme import ROLE_ACTUAL, ROLE_BORDER, ROLE_FORECAST, ROLE_GRID, ROLE_TARGET, ROLE_TEXT, ROLE_TEXT_MUTED

# ─────────────────────────────────────────────────────────────
#  Layout primitives
# ─────────────────────────────────────────────────────────────

def _base_layout(ctx, spec: ChartSpec, *, bottom: int = 52, **overrides) -> dict:
    """Shared layout, with overrides merged rather than passed alongside.

    Callers MUST pass extras through this function (`_base_layout(ctx, spec,
    showlegend=False)`) instead of `update_layout(**_base_layout(...),
    showlegend=False)`. The latter raises "got multiple values for keyword
    argument" whenever the key already exists in the base dict -- a real bug
    that shipped in the first-generation app and that the component tests catch.
    Merging here means the collision cannot be expressed.
    """
    p = ctx.palette
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p[ROLE_TEXT], family="Inter, -apple-system, sans-serif", size=12),
        height=spec.height,
        margin=dict(t=46 if spec.title else 20, b=bottom, l=62, r=24),
        title=(dict(text=spec.title, font=dict(size=14), x=0, xanchor="left")
               if spec.title else None),
        showlegend=spec.legend,
        legend=dict(orientation="h", y=-0.20, x=0, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)", title=None),
        hoverlabel=dict(font_size=12, bgcolor=p["surface"],
                        bordercolor=p[ROLE_BORDER], font_color=p[ROLE_TEXT]),
        dragmode=False,  # a dashboard chart should not pan on drag
        uniformtext=dict(mode="hide", minsize=8),
    )
    base.update(overrides)
    return base


def _style_axes(fig: go.Figure, ctx, spec: ChartSpec, *, value_max: float | None = None,
                cat_axis: str = "x") -> None:
    """Apply axis styling and, crucially, computed headroom on the value axis."""
    p = ctx.palette
    cat_kw = dict(showgrid=False, tickfont=dict(size=10), title=None,
                  automargin=True, showline=True, linecolor=p[ROLE_GRID], linewidth=1)
    val_kw = dict(showgrid=True, gridcolor=p[ROLE_GRID], tickfont=dict(size=10),
                  title=None, automargin=True, zeroline=False)

    vfmt = spec.y_format if cat_axis == "x" else spec.x_format
    if vfmt is not None:
        val_kw.update(vfmt.plotly_axis())

    if value_max is not None and value_max > 0:
        pad = _LABEL_HEADROOM if spec.show_labels else _NO_LABEL_HEADROOM
        val_kw["range"] = [0, value_max * pad]

    if cat_axis == "x":
        fig.update_xaxes(**cat_kw)
        fig.update_yaxes(**val_kw)
    else:
        fig.update_yaxes(**cat_kw)
        fig.update_xaxes(**val_kw)

    # cliponaxis=False lets outside labels draw past the plot area as a second
    # line of defence alongside the computed range.
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))


def _add_reference_lines(fig: go.Figure, ctx, spec: ChartSpec, *, horizontal: bool = True) -> None:
    """Draw explicit reference lines (target, quota, pace)."""
    p = ctx.palette
    for value, label in spec.reference_lines:
        kw = dict(line=dict(color=p[ROLE_TARGET], width=2, dash="dash"),
                  annotation_text=label,
                  annotation_position="top left",
                  annotation_font=dict(size=10, color=p[ROLE_TEXT_MUTED]))
        if horizontal:
            fig.add_hline(y=value, **kw)
        else:
            fig.add_vline(x=value, **kw)


# ─────────────────────────────────────────────────────────────
#  Builders
# ─────────────────────────────────────────────────────────────

def bar_vs_target(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Actual bars with the target as a reference LINE, not a competing series.

    Tableau renders plan as a reference line or Gantt marker. Drawing it as a
    second bar (what the first-generation app did) doubles the mark count and
    makes "am I above plan" a comparison of two heights instead of a glance.
    """
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = _ordered_frame(df, spec.x)

    fig = go.Figure()
    _show = _labels_fit(spec, len(d))
    labels = _label_text(d[spec.y], spec.y_format) if _show else None

    colors = p[ROLE_ACTUAL]
    if spec.color_by_status and spec.target and spec.target in d.columns:
        # Divide through numpy rather than replace(0, pd.NA).fillna(0): that
        # route produces an object-dtype column and emits a pandas
        # FutureWarning about silent downcasting.
        num = pd.to_numeric(d[spec.y], errors="coerce")
        den = pd.to_numeric(d[spec.target], errors="coerce")
        ratios = (num / den.where(den != 0)).replace([float("inf"), float("-inf")],
                                                     float("nan")).fillna(0.0).astype("float64")
        colors = [ctx.status_color(r) for r in ratios]
    elif spec.color_by_status:
        # No target column: y is already a ratio (an attainment or share), so it
        # IS the status value. Without this, a normalized measure could only be
        # status-coloured by reaching into Plotly internals from the call site.
        ratios = pd.to_numeric(d[spec.y], errors="coerce").fillna(0.0).astype("float64")
        colors = [ctx.status_color(r) for r in ratios]

    fig.add_trace(go.Bar(
        x=d[spec.x], y=d[spec.y], name="Actual",
        marker_color=colors,
        text=labels, textposition="outside", textfont=dict(size=10),
        hovertemplate=f"<b>%{{x}}</b><br>Actual: {(spec.y_format.plotly_hover() if spec.y_format else '%{y:,.0f}')}<extra></extra>",
    ))

    value_max = float(d[spec.y].max())

    if spec.target and spec.target in d.columns:
        tgt = d[spec.target]
        value_max = max(value_max, float(tgt.max()))
        # Step line: reads as a benchmark per period rather than a trend.
        fig.add_trace(go.Scatter(
            x=d[spec.x], y=tgt, name=spec.target_label,
            mode="lines", line=dict(color=p[ROLE_TARGET], width=2.5, dash="dash",
                                    shape="hv"),
            hovertemplate=f"{spec.target_label}: {(spec.y_format.plotly_hover() if spec.y_format else '%{y:,.0f}')}<extra></extra>",
        ))

    fig.update_layout(**_base_layout(ctx, spec), barmode="group", bargap=0.28)
    _style_axes(fig, ctx, spec, value_max=value_max)
    _add_reference_lines(fig, ctx, spec)
    return fig


def trend_line(df: pd.DataFrame, ctx, spec: ChartSpec,
               series_cols: Sequence[tuple[str, str]] | None = None) -> go.Figure | None:
    """Multi-series line. series_cols is [(column, display_label), ...]."""
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = _ordered_frame(df, spec.x)

    cols = list(series_cols) if series_cols else [(spec.y, spec.y)]
    palette = p.series(len(cols))

    fig = go.Figure()
    vmax = 0.0
    for (col, label), color in zip(cols, palette):
        if col not in d.columns:
            continue
        vmax = max(vmax, float(pd.to_numeric(d[col], errors="coerce").max() or 0))
        fig.add_trace(go.Scatter(
            x=d[spec.x], y=d[col], name=label, mode="lines+markers",
            line=dict(color=color, width=2.4), marker=dict(size=5),
            # connectgaps=False so a genuine gap in the data shows as a gap
            # rather than being silently interpolated into a trend.
            connectgaps=False,
            hovertemplate=f"<b>%{{x}}</b><br>{label}: "
                          f"{(spec.y_format.plotly_hover() if spec.y_format else '%{y:,.0f}')}"
                          "<extra></extra>",
        ))

    if spec.target and spec.target in d.columns:
        vmax = max(vmax, float(pd.to_numeric(d[spec.target], errors="coerce").max() or 0))
        fig.add_trace(go.Scatter(
            x=d[spec.x], y=d[spec.target], name=spec.target_label, mode="lines",
            line=dict(color=p[ROLE_TARGET], width=2, dash="dash"),
        ))

    spec_no_labels = spec.replace(show_labels=False)
    fig.update_layout(**_base_layout(ctx, spec))
    _style_axes(fig, ctx, spec_no_labels, value_max=vmax if vmax > 0 else None)
    _add_reference_lines(fig, ctx, spec)
    return fig


def ranked_bar(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Horizontal ranked bars. Correct choice for long category labels.

    Replaces the treemap the first generation used for specialty breakdown:
    treemaps make small values unreadable and forced a dark background.
    """
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    d = _collapse_tail(df, spec.x, spec.y, spec.max_categories)
    d = d.groupby(spec.x, as_index=False)[spec.y].sum()
    d = d.sort_values(spec.y, ascending=True)  # ascending: largest at top in h-bars

    colors = p[ROLE_ACTUAL]
    if spec.series and spec.series in df.columns:
        colors = [p.for_category(sorted(d[spec.x])).get(v, p[ROLE_ACTUAL]) for v in d[spec.x]]

    fig = go.Figure(go.Bar(
        x=d[spec.y], y=d[spec.x], orientation="h",
        marker_color=colors,
        text=_label_text(d[spec.y], spec.y_format) if spec.show_labels else None,
        textposition="outside", textfont=dict(size=10),
        hovertemplate=f"<b>%{{y}}</b><br>{(spec.y_format.plotly_hover().replace('%{y', '%{x') if spec.y_format else '%{x:,.0f}')}<extra></extra>",
    ))

    layout = _base_layout(
        ctx, spec, bottom=36, showlegend=False,
        margin=dict(t=46 if spec.title else 20, b=36, l=8, r=72),
    )
    fig.update_layout(**layout)
    # Value axis is x for horizontal bars.
    hspec = spec.replace(x_format=spec.y_format)
    _style_axes(fig, ctx, hspec, value_max=float(d[spec.y].max()), cat_axis="y")
    _add_reference_lines(fig, ctx, spec, horizontal=False)
    return fig


def stacked_composition(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Stacked bars for part-to-whole over a category or time axis."""
    if df is None or len(df) == 0 or not spec.series:
        return None
    p = ctx.palette
    cats = sorted(df[spec.series].dropna().unique().tolist(), key=str)
    cmap = p.for_category(cats)

    fig = go.Figure()
    for cat in cats:
        sub = df[df[spec.series] == cat]
        grouped = _ordered_frame(sub.groupby(spec.x, as_index=False)[spec.y].sum(), spec.x)
        fig.add_trace(go.Bar(
            x=grouped[spec.x], y=grouped[spec.y], name=str(cat),
            marker_color=cmap[cat],
            hovertemplate=f"<b>%{{x}}</b><br>{cat}: "
                          f"{(spec.y_format.plotly_hover() if spec.y_format else '%{y:,.0f}')}"
                          "<extra></extra>",
        ))

    totals = df.groupby(spec.x)[spec.y].sum()
    fig.update_layout(**_base_layout(ctx, spec, bottom=64), barmode="stack", bargap=0.28)
    # Stacked totals define the ceiling, not any single segment.
    nolab = spec.replace(show_labels=False)
    _style_axes(fig, ctx, nolab, value_max=float(totals.max()) if len(totals) else None)
    _add_reference_lines(fig, ctx, spec)
    return fig


def share_bar(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Percent-of-total as a ranked bar.

    Chosen over a donut on purpose: humans compare bar lengths far more
    accurately than pie angles, and category labels never get clipped at the
    chart edge the way donut outside-labels do.
    """
    if df is None or len(df) == 0:
        return None
    from .fmt import PERCENT

    d = _collapse_tail(df, spec.x, spec.y, spec.max_categories)
    d = d.groupby(spec.x, as_index=False)[spec.y].sum()
    total = float(d[spec.y].sum())
    if total <= 0:
        return None
    d["_share"] = d[spec.y] / total
    d = d.sort_values("_share", ascending=True)

    p = ctx.palette
    cmap = p.for_category(list(d[spec.x]))
    fig = go.Figure(go.Bar(
        x=d["_share"], y=d[spec.x], orientation="h",
        marker_color=[cmap[v] for v in d[spec.x]],
        text=[PERCENT.render(v) for v in d["_share"]],
        textposition="outside", textfont=dict(size=10.5),
        customdata=d[spec.y],
        hovertemplate="<b>%{y}</b><br>Share: %{x:.1%}<br>"
                      f"Value: {(spec.y_format.plotly_hover().replace('%{y','%{customdata') if spec.y_format else '%{customdata:,.0f}')}"
                      "<extra></extra>",
    ))
    layout = _base_layout(
        ctx, spec, bottom=36, showlegend=False,
        margin=dict(t=46 if spec.title else 20, b=36, l=8, r=64),
    )
    fig.update_layout(**layout)
    hspec = spec.replace(x_format=PERCENT)
    _style_axes(fig, ctx, hspec, value_max=float(d["_share"].max()), cat_axis="y")
    return fig


def cumulative_pace(df: pd.DataFrame, ctx, spec: ChartSpec, *,
                    series: str = "series") -> go.Figure | None:
    """Cumulative lines per period, for pacing comparisons across months."""
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    cats = list(dict.fromkeys(df[series].tolist())) if series in df.columns else []
    colors = p.series(max(len(cats), 1))

    fig = go.Figure()
    vmax = 0.0
    for cat, color in zip(cats, colors):
        sub = _ordered_frame(df[df[series] == cat], spec.x)
        vmax = max(vmax, float(pd.to_numeric(sub[spec.y], errors="coerce").max() or 0))
        # The current, in-progress period is emphasised; priors are context.
        is_current = cat == cats[-1] if cats else False
        fig.add_trace(go.Scatter(
            x=sub[spec.x], y=sub[spec.y], name=str(cat), mode="lines",
            line=dict(color=color, width=2.8 if is_current else 1.6,
                      dash="solid" if is_current else "dot"),
            hovertemplate=f"<b>{cat}</b><br>Day %{{x}}: "
                          f"{(spec.y_format.plotly_hover() if spec.y_format else '%{y:,.0f}')}"
                          "<extra></extra>",
        ))

    layout = _base_layout(
        ctx, spec, bottom=76,
        legend=dict(orientation="h", y=-0.26, x=0, font=dict(size=11), title=None),
    )
    fig.update_layout(**layout)
    nolab = spec.replace(show_labels=False)
    _style_axes(fig, ctx, nolab, value_max=vmax if vmax else None)
    # Axis titles are warranted here: "Business Day" is not inferable from ticks.
    fig.update_xaxes(title=dict(text="Business day of month", font=dict(size=11)))
    _add_reference_lines(fig, ctx, spec)
    return fig


def heatmap(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Matrix heatmap. Replaces Tableau Square/Circle marks with color encoding."""
    if df is None or len(df) == 0 or not spec.series:
        return None
    pivot = df.pivot_table(index=spec.series, columns=spec.x, values=spec.y,
                           aggfunc="sum").fillna(0)
    # Reindex the column axis so a month matrix does not read Apr, Aug, Feb, Jan.
    _cal = _calendar_order(list(pivot.columns))
    if _cal:
        pivot = pivot.reindex(columns=[c for c in _cal if c in pivot.columns])
    if pivot.empty:
        return None
    p = ctx.palette
    fmt = spec.y_format
    text = [[fmt.render(v) if fmt else f"{v:,.0f}" for v in row] for row in pivot.values]

    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[str(c) for c in pivot.columns], y=[str(i) for i in pivot.index],
        colorscale=[[0, p.sequential[0]], [1, p.sequential[1]]],
        text=text, texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="<b>%{y}</b> · %{x}<br>%{text}<extra></extra>",
        showscale=False,
    ))
    layout = _base_layout(
        ctx, spec, bottom=44, showlegend=False,
        margin=dict(t=46 if spec.title else 20, b=44, l=132, r=20),
    )
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=False, tickfont=dict(size=10), title=None, automargin=True)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=10), title=None, automargin=True)
    return fig


def waterfall(df: pd.DataFrame, ctx, spec: ChartSpec) -> go.Figure | None:
    """Waterfall for contribution / variance decomposition."""
    if df is None or len(df) == 0:
        return None
    p = ctx.palette
    fig = go.Figure(go.Waterfall(
        x=df[spec.x], y=df[spec.y],
        measure=df["measure"] if "measure" in df.columns else None,
        text=_label_text(df[spec.y], spec.y_format) if spec.show_labels else None,
        textposition="outside", textfont=dict(size=10),
        increasing=dict(marker=dict(color=p["good"])),
        decreasing=dict(marker=dict(color=p["bad"])),
        totals=dict(marker=dict(color=p[ROLE_ACTUAL])),
        connector=dict(line=dict(color=p[ROLE_GRID], width=1)),
    ))
    fig.update_layout(**_base_layout(ctx, spec, bottom=56, showlegend=False))
    _style_axes(fig, ctx, spec)
    return fig
