"""bim_ui.chart_spec — engine-neutral chart description and shared helpers.

Lives apart from the engine modules so `ChartSpec` can be imported without
pulling in Plotly. That matters: Altair is a hard Streamlit dependency and is
therefore always present in Streamlit in Snowflake, whereas Plotly needs an
External Access Integration the customer has to configure. A chart spec that
imported Plotly would drag that requirement into every app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import pandas as pd

from .fmt import NumberFormat

# Headroom multiplier applied above the tallest mark when outside labels are on.
# 1.18 is enough for a one-line label at the default font without wasting height.
_LABEL_HEADROOM = 1.18
_NO_LABEL_HEADROOM = 1.04

Orientation = Literal["v", "h"]

# Chart engines. "altair" is the default because it ships with Streamlit; see
# the module docstring for why that is the deciding factor.
Engine = Literal["altair", "plotly"]


@dataclass
class ChartSpec:
    """Declarative chart description.

    The builders take a spec rather than 15 keyword arguments so a generator can
    emit a spec as data, and so defaults live in one place.
    """

    title: str = ""
    x: str = ""
    y: str = ""
    series: str | None = None          # column to split/color by
    target: str | None = None          # column holding plan/quota for reference
    target_label: str = "Plan"
    emits: str | None = None           # dimension this chart cross-filters on
    key: str | None = None             # stable Streamlit key; required to emit
    y_format: NumberFormat | None = None
    x_format: NumberFormat | None = None
    show_labels: bool = True
    orientation: Orientation = "v"
    height: int = 330
    stacked: bool = False
    reference_lines: list[tuple[float, str]] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    legend: bool = True
    sort_desc: bool = False
    max_categories: int | None = None  # collapse a long tail into "Other"
    color_by_status: bool = False      # color marks by attainment vs target
    empty_message: str = "No data matches the current filters."
    # Above this many categories, outside value labels on VERTICAL bars are
    # suppressed. Panels are typically half-width, so ~8 bands is the point past
    # which a 6-character label like "$806.9" collides with its neighbour. The
    # value is still in the tooltip and the grid, so nothing is lost -- an
    # unreadable smear of overlapping text is strictly worse than no label.
    # Horizontal bars are exempt: their labels stack vertically and cannot collide.
    max_label_categories: int = 8
    # Per-chart engine override. None means "use the app default", which is
    # Altair. Set to "plotly" for a chart that needs a Plotly-only mark type.
    engine: Engine | None = None

    def replace(self, **changes) -> "ChartSpec":
        """Return a copy with fields overridden.

        Builders need variants of a spec (labels off for a line chart, value
        format moved to the x axis for horizontal bars). Doing that through
        `ChartSpec(**{**spec.__dict__, ...})` is easy to get subtly wrong, so it
        lives here once.
        """
        return ChartSpec(**{**self.__dict__, **changes})


_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_FULL = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")


def calendar_order(values: Sequence[Any]) -> list[Any] | None:
    """Return calendar order when `values` look like month names, else None.

    Month names sort alphabetically as strings -- "Apr, Aug, Dec, Feb..." -- so a
    chart built by naive sorting shows a trend that is not the real trend. That
    is worse than no chart, because it reads as information.
    """
    uniq = {str(v).strip() for v in values if v is not None}
    if not uniq:
        return None
    for calendar in (_MONTH_ABBR, _MONTH_FULL):
        lookup = {m.lower(): i for i, m in enumerate(calendar)}
        if all(v.lower() in lookup for v in uniq):
            return sorted(uniq, key=lambda v: lookup[v.lower()])
    return None


def ordered_frame(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Order rows along a category axis without destroying meaningful sequence.

    Plain `df.sort_values(column)` is wrong for a label axis: month names sort
    to "Apr, Aug, Feb, Jan", which turns a cumulative line into a zig-zag and a
    trend into noise. So use calendar order when the values are month names,
    sort when they are genuinely numeric or dates, and otherwise leave the
    caller's row order alone -- a caller that grouped by a sort key has already
    expressed the order it wants.
    """
    if df is None or column not in getattr(df, "columns", []):
        return df

    cal = calendar_order(df[column])
    if cal:
        rank = {v.lower(): i for i, v in enumerate(cal)}
        return df.assign(
            _bim_ord=[rank.get(str(v).strip().lower(), len(rank)) for v in df[column]]
        ).sort_values("_bim_ord").drop(columns="_bim_ord")

    if pd.api.types.is_numeric_dtype(df[column]) or pd.api.types.is_datetime64_any_dtype(df[column]):
        return df.sort_values(column)

    return df


def axis_order(values: Sequence[Any]) -> list[str]:
    """Unique values in first-seen order, for an explicit categorical domain.

    Altair defaults a nominal axis to alphabetical, so the domain has to be
    stated for the drawn order to match the frame's order.
    """
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(str(v), None)
    return list(seen)


def labels_fit(spec: ChartSpec, n_categories: int) -> bool:
    """Whether outside value labels will be legible on a vertical bar chart.

    Only a count test, because the builders do not know the rendered pixel width
    (`width="container"`). The threshold is deliberately conservative: a label
    that collides is worse than one that is absent, since the value is still
    available in the tooltip and the detail grid.
    """
    if not spec.show_labels:
        return False
    if spec.orientation == "h":
        return True
    return n_categories <= spec.max_label_categories


def label_text(values: Sequence[Any], fmt: NumberFormat | None) -> list[str]:
    """Render mark labels through the shared formatter.

    Going through NumberFormat is what keeps a KPI card, an axis tick, a tooltip
    and a grid cell from showing the same number three different ways.
    """
    if fmt is None:
        return [f"{v:,.0f}" if isinstance(v, (int, float)) else str(v) for v in values]
    return [fmt.render(v) for v in values]


def collapse_tail(df: pd.DataFrame, cat: str, val: str,
                  limit: int | None) -> pd.DataFrame:
    """Keep the top categories, sum the rest into 'Other'.

    `limit` is the TOTAL number of bars the reader ends up with, including the
    'Other' bucket -- so max_categories=8 yields 8 bars, not 9. A 40-category bar
    chart is unreadable; Tableau users solve this with a top-N filter, and doing
    it by default keeps generated output legible without manual intervention.

    Returns a frame already aggregated to one row per category, so callers that
    group afterwards are simply a no-op rather than wrong.
    """
    if not limit or limit < 2 or cat not in df.columns or val not in df.columns:
        return df
    if df[cat].nunique() <= limit:
        return df
    ranked = df.groupby(cat, as_index=False)[val].sum().sort_values(val, ascending=False)
    keep = set(ranked[cat].head(limit - 1))  # reserve one slot for "Other"
    out = df.copy()
    out[cat] = out[cat].where(out[cat].isin(keep), "Other")
    return out.groupby(cat, as_index=False, sort=False)[val].sum()


def headroom(value_max: float | None, show_labels: bool) -> float | None:
    """Axis ceiling that leaves room for outside value labels.

    Computed from the data rather than hard-coded, because clipped labels were
    the single most common defect in the first-generation output.
    """
    if value_max is None or value_max <= 0:
        return None
    return value_max * (_LABEL_HEADROOM if show_labels else _NO_LABEL_HEADROOM)
