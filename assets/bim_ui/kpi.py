"""
bim_ui.kpi — KPI cards with inline sparkline and progress-vs-target.

A number alone is not a KPI. "$4.2M" answers nothing; "$4.2M, 12% ahead of plan,
trending up for four months, 87% of the annual target" is a decision. Each card
carries value, delta with semantic direction, an optional sparkline, and an
optional progress bar, all formatted through the shared registry.

Sparklines are hand-built inline SVG rather than a chart library: nine
Plotly figures in a KPI row is a measurable page-weight cost for a 40px graphic,
and inline SVG carries no dependency and cannot fail to load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import streamlit as st

from . import compat

from .fmt import NumberFormat, delta as fmt_delta
from .theme import ROLE_ACTUAL, ROLE_BAD, ROLE_GOOD, ROLE_NEUTRAL, ROLE_TEXT_MUTED


@dataclass
class KPI:
    """One KPI card.

    `higher_is_better=False` inverts delta coloring for metrics where an
    increase is bad (churn, days-to-close, cost). Getting this wrong produces a
    green arrow on a worsening number, which is worse than no color at all.
    """

    label: str
    value: Any
    fmt: NumberFormat | None = None
    target: Any | None = None
    target_label: str = "plan"
    delta_vs: Any | None = None          # baseline for the delta line
    delta_label: str = ""                # e.g. "vs plan", "YoY"
    delta_as_percent: bool = False
    higher_is_better: bool = True
    sparkline: Sequence[float] | None = None
    show_progress: bool = False
    help: str | None = None
    footnote: str = ""


def _sparkline_svg(
    values: Sequence[float],
    color: str,
    *,
    width: int = 132,
    height: int = 26,
    stroke: float = 1.6,
) -> str:
    """Inline SVG sparkline with a subtle fill and an end-point marker."""
    pts = [float(v) for v in values if v is not None]
    if len(pts) < 2:
        return ""

    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    pad = 3.0
    usable_h = height - pad * 2
    step = width / (len(pts) - 1)

    coords = [
        (i * step, pad + usable_h - ((v - lo) / span) * usable_h)
        for i, v in enumerate(pts)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"0,{height} {line} {width},{height}"
    ex, ey = coords[-1]

    return (
        f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" aria-hidden="true" '
        f'style="display:block">'
        f'<polygon points="{area}" fill="{color}" opacity="0.11"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.1" fill="{color}"/>'
        f"</svg>"
    )


def _progress_svg(ratio: float, color: str, track: str, *, height: int = 5) -> str:
    """Thin progress bar. Overflow past 100% is clamped but still colored."""
    pct = max(0.0, min(1.0, ratio))
    return (
        f'<svg width="100%" height="{height}" role="img" aria-hidden="true" '
        f'style="display:block;margin-top:4px">'
        f'<rect x="0" y="0" width="100%" height="{height}" rx="{height/2}" fill="{track}"/>'
        f'<rect x="0" y="0" width="{pct*100:.1f}%" height="{height}" '
        f'rx="{height/2}" fill="{color}"/>'
        f"</svg>"
    )


def _card_html(k: KPI, ctx) -> str:
    p = ctx.palette
    fmt = k.fmt or ctx.formats.get(k.label)
    value_txt = fmt.render(k.value)

    # ── delta ──
    delta_html = ""
    baseline = k.delta_vs if k.delta_vs is not None else k.target
    if baseline is not None:
        txt, direction = fmt_delta(
            k.value, baseline, fmt, as_percent=k.delta_as_percent
        )
        if direction == "flat":
            color = p[ROLE_NEUTRAL]
        else:
            improving = (direction == "up") == k.higher_is_better
            color = p[ROLE_GOOD] if improving else p[ROLE_BAD]
        suffix = f" {k.delta_label}" if k.delta_label else ""
        delta_html = (f'<div class="bim-kpi-delta" style="color:{color}">'
                      f'{txt}{suffix}</div>')
    elif k.footnote:
        delta_html = (f'<div class="bim-kpi-delta" '
                      f'style="color:{p[ROLE_TEXT_MUTED]}">{k.footnote}</div>')

    # ── progress vs target ──
    progress_html = ""
    if k.show_progress and k.target:
        try:
            ratio = float(k.value) / float(k.target)
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = 0.0
        progress_html = _progress_svg(ratio, ctx.status_color(ratio), p["grid"])

    # ── sparkline ──
    spark_html = ""
    if k.sparkline:
        svg = _sparkline_svg(k.sparkline, p[ROLE_ACTUAL])
        if svg:
            spark_html = f'<div class="bim-kpi-spark">{svg}</div>'

    return (
        '<div class="bim-kpi">'
        f'<div class="bim-kpi-label">{k.label}</div>'
        f'<div class="bim-kpi-value">{value_txt}</div>'
        f"{delta_html}{progress_html}{spark_html}"
        "</div>"
    )


def card(k: KPI, ctx, *, container=None) -> None:
    target = container or st
    target.markdown(_card_html(k, ctx), unsafe_allow_html=True)
    if k.help:
        target.caption(k.help)


def row(kpis: Sequence[KPI], ctx, *, per_row: int = 4, container=None) -> None:
    """Render KPIs in rows of `per_row`.

    Four per row is the default for a reason: seven equal columns squeezed KPI
    values until they clipped, then squeezed the labels until they ellipsised.
    Four keeps a currency value and a wrapped two-line label readable even with
    the sidebar expanded on a 900px viewport.
    """
    target = container or st
    if not kpis:
        return
    per_row = max(1, min(per_row, 4))
    for start in range(0, len(kpis), per_row):
        chunk = list(kpis[start:start + per_row])
        cols = compat.columns(per_row, gap="small")
        for col, k in zip(cols, chunk):
            with col:
                st.markdown(_card_html(k, ctx), unsafe_allow_html=True)
        # Pad the final row so cards keep their width instead of stretching.
        for col in cols[len(chunk):]:
            with col:
                st.empty()


# ─────────────────────────────────────────────────────────────
#  Derivation helper
# ─────────────────────────────────────────────────────────────

def attainment_set(
    *,
    actual: float,
    target: float,
    period_label: str,
    ctx,
    fmt: NumberFormat | None = None,
    trend: Sequence[float] | None = None,
    target_label: str = "Plan",
) -> list[KPI]:
    """The standard actual / target / attainment triplet.

    Every team dashboard in the source workbook repeats this pattern, so it is
    built once here rather than at each of thirteen call sites.
    """
    money = fmt or ctx.formats.get("bookings")
    from .fmt import PERCENT

    ratio = (actual / target) if target else 0.0
    return [
        KPI(f"{period_label} Bookings", actual, money, target=target,
            delta_vs=target, delta_label=f"vs {target_label.lower()}",
            sparkline=trend, footnote=period_label),
        KPI(f"{target_label} — {period_label}", target, money,
            footnote=period_label),
        KPI(f"{period_label} Attainment", ratio, PERCENT, target=1.0,
            show_progress=True, footnote=f"vs {target_label.lower()}"),
    ]
