"""bim_ui.charts — engine-agnostic chart facade.

Call sites import from here and never learn which engine drew the chart:

    from bim_ui import charts
    fig = charts.bar_vs_target(df, ctx, charts.ChartSpec(x="month", y="actual"))
    charts.render(fig, ctx, spec, store=store)

Altair is the default. The deciding reason is deployment, not aesthetics: Altair
is a hard Streamlit dependency and is therefore already present in Streamlit in
Snowflake, whereas Plotly requires an External Access Integration the customer
has to request and approve. Defaulting to Altair means a migrated dashboard is
not blocked behind an infrastructure ticket. Altair also gives rerun-free
highlight via `alt.condition`, where the Plotly path must rebuild a colour list
and rerun the script.

Plotly is retained, not deprecated. It is still the right engine for chart types
Vega-Lite does not cover well -- choropleth, treemap, sunburst, sankey, gauge --
and for scatter plots above roughly 5,000 marks, where WebGL matters. Those route
to Plotly automatically.

Selecting an engine:

    charts.set_engine("plotly")            # whole app
    spec = ChartSpec(..., engine="plotly") # one chart

Import cost is deferred: asking for Altair never imports Plotly, so an app that
stays on the default carries no Plotly dependency at all.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

import pandas as pd
import streamlit as st

from . import compat, states
from .chart_spec import ChartSpec, Engine, Orientation, collapse_tail, headroom, label_text

log = logging.getLogger("bim_ui.charts")

__all__ = [
    "ChartSpec",
    "Engine",
    "Orientation",
    "bar_vs_target",
    "clickable_hint",
    "cumulative_pace",
    "engine",
    "heatmap",
    "ranked_bar",
    "render",
    "set_engine",
    "share_bar",
    "stacked_composition",
    "trend_line",
    "waterfall",
]

# Chart types Vega-Lite does not cover well, or where Plotly's WebGL path is the
# reason to use it. Builders named here fall back to Plotly even on the default.
_PLOTLY_ONLY: frozenset[str] = frozenset({
    "choropleth", "treemap", "sunburst", "sankey", "gauge", "scatter_gl",
})

_DEFAULT_ENGINE: Engine = "altair"
_ENGINE_KEY = "_bim_chart_engine"


def set_engine(name: Engine) -> None:
    """Set the engine for the rest of the session."""
    if name not in ("altair", "plotly"):
        raise ValueError(f"unknown chart engine: {name!r} (expected 'altair' or 'plotly')")
    try:
        st.session_state[_ENGINE_KEY] = name
    except Exception:
        # No script run context (unit tests, module import); fall back to the
        # process-wide default so the setting is still honoured.
        global _DEFAULT_ENGINE
        _DEFAULT_ENGINE = name


def engine() -> Engine:
    """Resolve the active engine: session override, env override, then default.

    BIM_CHART_ENGINE exists so a deployment can pin the engine without editing
    generated code -- useful when a customer has Plotly approved already and
    wants the WebGL paths everywhere.
    """
    try:
        chosen = st.session_state.get(_ENGINE_KEY)
        if chosen:
            return chosen
    except Exception:
        pass
    env = os.environ.get("BIM_CHART_ENGINE", "").strip().lower()
    if env in ("altair", "plotly"):
        return env
    return _DEFAULT_ENGINE


def _missing_columns(df: Any, spec: ChartSpec, *, extra: Sequence[str] = ()) -> list[str]:
    """Columns the spec references that the frame does not have.

    Specs are machine-generated against a schema that can drift -- a renamed
    measure, a dropped dimension, a typo in a hand-edited page. Letting that
    surface as a KeyError takes down the whole page for one bad panel, so the
    facade checks first and lets the caller fall back to the empty state.
    """
    if df is None or not hasattr(df, "columns"):
        return []
    have = set(df.columns)
    wanted = [c for c in (spec.x, spec.y, spec.series, spec.target, *extra) if c]
    return [c for c in wanted if c not in have]


def _guard(df: Any, spec: ChartSpec, *, builder: str,
           extra: Sequence[str] = ()) -> bool:
    """True when the chart can be built. Logs and refuses when columns are absent."""
    missing = _missing_columns(df, spec, extra=extra)
    if not missing:
        return True
    log.warning("%s: skipped, frame is missing column(s) %s (have: %s)",
                builder, ", ".join(missing),
                ", ".join(map(str, getattr(df, "columns", []))))
    return False


def _impl(spec: ChartSpec | None = None, *, builder: str = ""):
    """Return the engine module for this call, importing it lazily.

    Lazy import is the point: an app on the default never imports Plotly, so it
    never inherits Plotly's External Access Integration requirement.
    """
    want: Engine = getattr(spec, "engine", None) or engine() if spec else engine()
    if builder in _PLOTLY_ONLY:
        want = "plotly"
    if want == "plotly":
        from . import charts_plotly as mod
    else:
        from . import charts_altair as mod
    return mod


# ─────────────────────────────────────────────────────────────
#  Builders — thin dispatchers, identical signatures per engine
# ─────────────────────────────────────────────────────────────

def bar_vs_target(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Actual bars with the target drawn as a reference line, not a rival series."""
    if not _guard(df, spec, builder="bar_vs_target"):
        return None
    return _impl(spec, builder="bar_vs_target").bar_vs_target(df, ctx, spec)


def trend_line(df: pd.DataFrame, ctx, spec: ChartSpec,
               series_cols: Sequence[tuple[str, str]] | None = None):
    """Multi-series line. series_cols is [(column, display_label), ...]."""
    if not _guard(df, spec, builder="trend_line"):
        return None
    return _impl(spec, builder="trend_line").trend_line(df, ctx, spec, series_cols)


def ranked_bar(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Horizontal ranked bars, for long category labels."""
    if not _guard(df, spec, builder="ranked_bar"):
        return None
    return _impl(spec, builder="ranked_bar").ranked_bar(df, ctx, spec)


def stacked_composition(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Stacked bars for part-to-whole over a category or time axis."""
    if not _guard(df, spec, builder="stacked_composition"):
        return None
    return _impl(spec, builder="stacked_composition").stacked_composition(df, ctx, spec)


def share_bar(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Percent-of-total as a ranked bar, chosen over a donut deliberately."""
    if not _guard(df, spec, builder="share_bar"):
        return None
    return _impl(spec, builder="share_bar").share_bar(df, ctx, spec)


def cumulative_pace(df: pd.DataFrame, ctx, spec: ChartSpec, *, series: str = "series"):
    """Cumulative lines per period, for pacing comparisons across months."""
    if not _guard(df, spec, builder="cumulative_pace", extra=(series,)):
        return None
    return _impl(spec, builder="cumulative_pace").cumulative_pace(df, ctx, spec, series=series)


def heatmap(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Matrix heatmap, replacing Tableau Square/Circle marks with colour."""
    if not _guard(df, spec, builder="heatmap"):
        return None
    return _impl(spec, builder="heatmap").heatmap(df, ctx, spec)


def waterfall(df: pd.DataFrame, ctx, spec: ChartSpec):
    """Waterfall for contribution / variance decomposition."""
    if not _guard(df, spec, builder="waterfall"):
        return None
    return _impl(spec, builder="waterfall").waterfall(df, ctx, spec)


# ─────────────────────────────────────────────────────────────
#  Render entry point
# ─────────────────────────────────────────────────────────────

def _is_altair(fig: Any) -> bool:
    """Detect an Altair chart without importing Altair.

    Checking the module name rather than isinstance keeps the Plotly-only path
    from importing Altair and vice versa.
    """
    mod = type(fig).__module__ or ""
    return mod.startswith("altair")


def render(
    fig: Any,
    ctx,
    spec: ChartSpec,
    *,
    store=None,
    container=None,
    on_filter_change=None,
) -> None:
    """Render a chart, wiring cross-filter selection when the spec emits.

    Consolidating render here means every chart in the app gets the same empty
    state, the same selection plumbing, and the same config, regardless of which
    engine drew it.
    """
    if fig is None:
        # Pass the container through rather than using `with target:` -- the st
        # module is not a context manager, so `with st:` raises TypeError. This
        # path fires whenever a filter empties a chart, so it must be safe.
        states.empty(spec.empty_message,
                     height=spec.height,
                     on_clear=(store.clear if store else None),
                     container=container)
        return

    if _is_altair(fig):
        _render_altair(fig, spec, store=store, container=container,
                       on_filter_change=on_filter_change)
    else:
        _render_plotly(fig, spec, store=store, container=container,
                       on_filter_change=on_filter_change)


def _render_plotly(fig, spec: ChartSpec, *, store, container, on_filter_change) -> None:
    config = {
        # Scroll-zoom on a dashboard hijacks page scrolling.
        "scrollZoom": False,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d",
                                   "zoomIn2d", "zoomOut2d"],
        "displayModeBar": False,
        "responsive": True,
    }
    if spec.emits and store is not None and spec.key:
        store.register_emitter(spec.key, spec.emits)
        # compat.plotly_chart drops the selection kwargs on a Streamlit too old
        # to support them; the chart still renders, it just does not cross-filter.
        event = compat.plotly_chart(
            fig, on_select="rerun", use_container_width=True, key=spec.key,
            selection_mode=("points", "box"), config=config,
        )
        if store.ingest_plotly(event, spec.key, dimension=spec.emits):
            if on_filter_change:
                on_filter_change()
            compat.rerun()
    else:
        compat.plotly_chart(fig, use_container_width=True, config=config, key=spec.key)


def _render_altair(fig, spec: ChartSpec, *, store, container, on_filter_change) -> None:
    if spec.emits and store is not None and spec.key:
        store.register_emitter(spec.key, spec.emits)
        event = compat.altair_chart(
            fig, on_select="rerun", use_container_width=True, key=spec.key,
        )
        if store.ingest_altair(event, spec.key, dimension=spec.emits,
                               selection=spec.key):
            if on_filter_change:
                on_filter_change()
            compat.rerun()
    else:
        compat.altair_chart(fig, use_container_width=True, key=spec.key)


def clickable_hint(ctx, *, container=None) -> None:
    """Tell the user charts are clickable. Discoverability is not automatic.

    Silent when the running Streamlit cannot deliver selection events, so the
    app never invites an interaction it cannot honour.
    """
    if not compat.supports("chart_selection"):
        return
    (container or st).caption(
        "Tip: click a bar to filter this dashboard. Click again to clear."
    )
