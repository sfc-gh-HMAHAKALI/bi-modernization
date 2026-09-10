"""
bim_ui.compat — Streamlit version tolerance.

Generated apps target the Streamlit-in-Snowflake container runtime (1.50+), but
they get previewed locally in whatever interpreter the developer has, which is
frequently much older. A hard AttributeError at import time is the worst outcome:
the app shows a red card and nothing can be evaluated.

This module makes the difference explicit and graceful:

  - features that CAN be shimmed are shimmed (fragment, container border/height,
    query_params)
  - features that genuinely cannot be emulated (chart selection events, which
    require frontend support) degrade to a non-interactive equivalent, and
    `missing_features()` reports what was lost so the app can say so plainly
    rather than appearing broken

Nothing here changes behaviour on a modern Streamlit: every shim is a passthrough
once the real API exists.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Iterable

import streamlit as st

# ── version detection ──

def _parse_version(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(raw).split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


VERSION: tuple[int, ...] = _parse_version(getattr(st, "__version__", "0.0.0"))
VERSION_STR: str = getattr(st, "__version__", "unknown")


def at_least(major: int, minor: int, patch: int = 0) -> bool:
    return VERSION >= (major, minor, patch)


# Minimum versions for the APIs the kit relies on.
REQUIREMENTS: dict[str, tuple[tuple[int, int, int], str]] = {
    "fragment":          ((1, 37, 0), "panel-scoped reruns (falls back to full reruns)"),
    "chart_selection":   ((1, 35, 0), "click-a-chart cross-filtering"),
    "dataframe_selection": ((1, 35, 0), "click-a-row cross-filtering"),
    "query_params":      ((1, 30, 0), "shareable filtered URLs"),
    "container_border":  ((1, 29, 0), "bordered empty-state panels"),
    "container_height":  ((1, 31, 0), "fixed-height empty-state panels"),
    "column_config":     ((1, 26, 0), "formatted grid columns"),
    "toggle":            ((1, 26, 0), "the parameter toggle"),
}


def supports(feature: str) -> bool:
    """True when the running Streamlit is new enough for `feature`."""
    req = REQUIREMENTS.get(feature)
    if req is None:
        return True
    (maj, mnr, pat), _ = req
    # Trust an explicit attribute check over the version number where possible:
    # some builds backport, and SiS ships its own patched versions.
    probe = {
        "fragment": lambda: hasattr(st, "fragment") or hasattr(st, "experimental_fragment"),
        "query_params": lambda: hasattr(st, "query_params"),
        "column_config": lambda: hasattr(st, "column_config"),
        "toggle": lambda: hasattr(st, "toggle"),
    }.get(feature)
    if probe is not None and probe():
        return True
    return at_least(maj, mnr, pat)


def missing_features() -> list[tuple[str, str]]:
    """[(feature, why it matters), ...] for everything unavailable here."""
    return [(name, desc) for name, (_, desc) in REQUIREMENTS.items()
            if not supports(name)]


def compatibility_notice() -> str | None:
    """One-line summary of degraded functionality, or None when fully supported."""
    missing = missing_features()
    if not missing:
        return None
    lost = ", ".join(desc for _, desc in missing)
    return (
        f"Running Streamlit {VERSION_STR}. This app targets 1.50+ "
        f"(Snowflake container runtime). Unavailable here: {lost}."
    )


# ── alerts ──
# st.info/warning/error accept `icon=`, but Material shortcodes (":material/x:")
# were only added in newer builds; older ones require a literal emoji and raise
# StreamlitAPIException on a shortcode. Since every alert in the kit is on an
# error or degradation path, an alert that raises turns a soft failure into a
# hard one -- so all alerts route through here and drop the icon when in doubt.

_MATERIAL_ICONS = (1, 40, 0)


def _alert(fn_name: str, message: str, *, icon: str | None = None,
           parent=None) -> None:
    host = parent if parent is not None else st
    fn = getattr(host, fn_name, None) or getattr(st, fn_name, None)
    if fn is None:
        # Last resort: never let a status message be the thing that breaks.
        try:
            st.write(message)
        except Exception:
            pass
        return

    use_icon = icon
    if icon and icon.startswith(":") and not at_least(*_MATERIAL_ICONS):
        use_icon = None
    try:
        if use_icon:
            fn(message, icon=use_icon)
        else:
            fn(message)
    except Exception:
        # Covers unsupported icon values, unsupported kwargs, and anything else.
        try:
            fn(message)
        except Exception:
            try:
                st.write(message)
            except Exception:
                pass


def info(message: str, *, icon: str | None = None, parent=None) -> None:
    _alert("info", message, icon=icon, parent=parent)


def warning(message: str, *, icon: str | None = None, parent=None) -> None:
    _alert("warning", message, icon=icon, parent=parent)


def error(message: str, *, icon: str | None = None, parent=None) -> None:
    _alert("error", message, icon=icon, parent=parent)


def render_compatibility_notice(*, container=None) -> None:
    """Show the notice so degraded behaviour does not read as a bug.

    Wrapped defensively: the notice explaining that features are missing must
    never itself be the reason the app fails to render.
    """
    try:
        msg = compatibility_notice()
        if msg:
            info(msg, icon=":material/info:", parent=container)
    except Exception:
        pass


# ── fragment ──

def fragment(func: Callable | None = None, **kwargs) -> Any:
    """st.fragment with graceful degradation.

    1.37+  -> st.fragment
    1.33+  -> st.experimental_fragment
    older  -> passthrough; the panel still renders, reruns are just full-script

    Losing fragments costs performance, never correctness, so a passthrough is a
    safe fallback rather than a silent behaviour change.
    """
    real = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)

    def decorate(fn: Callable) -> Callable:
        if real is None:
            return fn
        try:
            return real(**kwargs)(fn) if kwargs else real(fn)
        except TypeError:
            # An older signature rejected our kwargs (e.g. parallel=). Retry bare.
            try:
                return real(fn)
            except Exception:
                return fn

    if func is not None and callable(func):
        return decorate(func)
    return decorate


# ── containers ──

def container(*, border: bool | None = None, height: int | None = None,
              parent=None, **kwargs):
    """st.container that drops unsupported kwargs instead of raising.

    `parent` renders into an explicit column/container. When omitted, the ambient
    Streamlit context applies -- which is correct inside a `with col:` block.
    """
    host = parent if parent is not None else st
    passed: dict[str, Any] = dict(kwargs)
    if border is not None and supports("container_border"):
        passed["border"] = border
    if height is not None and supports("container_height"):
        passed["height"] = height
    try:
        return host.container(**passed)
    except TypeError:
        try:
            return host.container()
        except TypeError:
            return st.container()


# ── query params ──

def get_query_params() -> dict[str, str]:
    """Read query params across the modern and legacy APIs."""
    if hasattr(st, "query_params"):
        try:
            return {k: st.query_params.get(k) for k in st.query_params.keys()}
        except Exception:
            return {}
    getter = getattr(st, "experimental_get_query_params", None)
    if getter is None:
        return {}
    try:
        raw = getter() or {}
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in raw.items()}
    except Exception:
        return {}


def set_query_param(key: str, value: str) -> None:
    if hasattr(st, "query_params"):
        try:
            st.query_params[key] = value
        except Exception:
            pass
        return
    setter = getattr(st, "experimental_set_query_params", None)
    if setter is None:
        return
    try:
        current = get_query_params()
        current[key] = value
        setter(**current)
    except Exception:
        pass


def del_query_param(key: str) -> None:
    if hasattr(st, "query_params"):
        try:
            if key in st.query_params:
                del st.query_params[key]
        except Exception:
            pass
        return
    setter = getattr(st, "experimental_set_query_params", None)
    if setter is None:
        return
    try:
        current = get_query_params()
        current.pop(key, None)
        setter(**current)
    except Exception:
        pass


# ── selection-capable renderers ──

def plotly_chart(fig, *, on_select: str | None = None, **kwargs):
    """st.plotly_chart, dropping selection kwargs when unsupported.

    Selection needs frontend support and cannot be emulated, so on an older
    Streamlit the chart renders normally and simply does not cross-filter.
    Returns None in that case, which FilterStore.ingest_plotly handles safely.
    """
    if on_select is not None and supports("chart_selection"):
        try:
            return st.plotly_chart(fig, on_select=on_select, **kwargs)
        except TypeError:
            pass
    safe = {k: v for k, v in kwargs.items()
            if k not in ("selection_mode", "on_select")}
    # `key` on plotly_chart is also newer than some builds accept.
    try:
        return st.plotly_chart(fig, **safe)
    except TypeError:
        safe.pop("key", None)
        safe.pop("config", None)
        return st.plotly_chart(fig, **safe)


def altair_chart(chart, *, on_select: str | None = None, **kwargs):
    """st.altair_chart, dropping selection kwargs when unsupported.

    Altair selection support landed alongside Plotly's (1.35), so the same gate
    applies: on an older Streamlit the chart renders and simply does not
    cross-filter. Returns None then, which FilterStore.ingest_altair handles.
    """
    if on_select is not None and supports("chart_selection"):
        try:
            return st.altair_chart(chart, on_select=on_select, **kwargs)
        except TypeError:
            pass
    safe = {k: v for k, v in kwargs.items()
            if k not in ("selection_mode", "on_select")}
    try:
        return st.altair_chart(chart, **safe)
    except TypeError:
        # `key` and `theme` are newer than some builds accept; shed them rather
        # than failing to draw the chart at all.
        safe.pop("key", None)
        safe.pop("theme", None)
        return st.altair_chart(chart, **safe)


def dataframe(df, *, on_select: str | None = None, **kwargs):
    """st.dataframe, dropping selection and newer display kwargs when unsupported."""
    if on_select is not None and supports("dataframe_selection"):
        try:
            return st.dataframe(df, on_select=on_select, **kwargs)
        except TypeError:
            pass
    safe = {k: v for k, v in kwargs.items()
            if k not in ("selection_mode", "on_select")}
    try:
        return st.dataframe(df, **safe)
    except TypeError:
        # Shed the newest display options one tier at a time rather than
        # failing outright, so an old build still shows the data.
        for drop in ("column_order", "column_config", "key", "hide_index"):
            safe.pop(drop, None)
            try:
                return st.dataframe(df, **safe)
            except TypeError:
                continue
        return st.dataframe(df)


def column_config_available() -> bool:
    return hasattr(st, "column_config")


def rerun() -> None:
    """st.rerun, falling back to st.experimental_rerun on pre-1.27 builds."""
    fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if fn is not None:
        fn()


def columns(spec, **kwargs):
    """st.columns that drops `gap`/`vertical_alignment` on builds without them."""
    try:
        return st.columns(spec, **kwargs)
    except TypeError:
        for drop in ("vertical_alignment", "border", "gap"):
            kwargs.pop(drop, None)
            try:
                return st.columns(spec, **kwargs)
            except TypeError:
                continue
        return st.columns(spec)
