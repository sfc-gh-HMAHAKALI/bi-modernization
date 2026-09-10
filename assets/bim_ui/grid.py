"""
bim_ui.grid — native st.dataframe wrapper with derived column_config and export.

Why native rather than AgGrid: on the SiS container runtime AgGrid is legal, but
the features that justify it (row grouping, pivot, Excel export, master/detail)
are AgGrid Enterprise and require a paid licence. Native st.dataframe is built on
Glide Data Grid and already provides column pinning, sorting, resizing, hiding,
reordering, search, row/column selection, and lazy loading for large frames.

This module also retires the hand-rolled HTML tables the first generation used.
Those cost three rounds of fixes for sticky columns and an unreachable Total
column, and still could not sort or search. `use_aggrid=True` remains available
for the genuine pivot cases, behind an explicit licence acknowledgement.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import pandas as pd
import streamlit as st

from . import compat, states
from .fmt import NumberFormat


@dataclass
class GridSpec:
    """Declarative grid description."""

    title: str = ""
    caption: str = ""
    pin: Sequence[str] = ()           # columns frozen at the left
    hide: Sequence[str] = ()
    order: Sequence[str] | None = None
    # Columns to render as an in-cell progress bar, mapped to their max value.
    progress: dict[str, float] = field(default_factory=dict)
    # Columns holding a list per row, rendered as an inline sparkline.
    sparkline: dict[str, str] = field(default_factory=dict)  # col -> "line"|"bar"|"area"
    labels: dict[str, str] = field(default_factory=dict)     # col -> display label
    help: dict[str, str] = field(default_factory=dict)
    widths: dict[str, str] = field(default_factory=dict)     # col -> small|medium|large
    total_row: bool = False
    total_label: str = "Total"
    height: int | None = None
    selectable: bool = False
    emits: str | None = None          # dimension for row-click cross-filtering
    key: str | None = None
    export: bool = True
    export_name: str = "data"
    use_aggrid: bool = False
    aggrid_licence_ack: bool = False
    empty_message: str = "No rows match the current filters."


# ─────────────────────────────────────────────────────────────
#  column_config derivation
# ─────────────────────────────────────────────────────────────

def build_column_config(
    df: pd.DataFrame,
    ctx,
    spec: GridSpec,
) -> dict[str, Any]:
    """Derive st.column_config from dtypes plus the format registry.

    This is where formatting stops being hand-rolled: the same FormatRegistry
    that drives KPI cards and axis ticks drives grid cells, so the three cannot
    disagree about how a field looks.

    Returns {} when the running Streamlit has no column_config, letting the grid
    render unformatted rather than raising.
    """
    if not compat.column_config_available():
        return {}

    cfg: dict[str, Any] = {}

    for col in df.columns:
        label = spec.labels.get(col) or _prettify(col)
        help_text = spec.help.get(col)
        width = spec.widths.get(col)
        common = {"label": label, "help": help_text}
        if width:
            common["width"] = width

        # Sparkline columns hold a sequence per row. These column types are newer
        # than the base column_config API, so fall back to hiding rather than
        # rendering a raw Python list in a cell.
        if col in spec.sparkline:
            kind = spec.sparkline[col]
            maker = {
                "line": getattr(st.column_config, "LineChartColumn", None),
                "bar": getattr(st.column_config, "BarChartColumn", None),
                "area": getattr(st.column_config, "AreaChartColumn", None),
            }.get(kind) or getattr(st.column_config, "LineChartColumn", None)
            if maker is None:
                cfg[col] = st.column_config.TextColumn(**common)
            else:
                cfg[col] = maker(**common)
            continue

        # Progress columns: attainment reads far faster as a bar than a number.
        if col in spec.progress:
            fmt = ctx.formats.get(col, df[col].dtype)
            progress_cls = getattr(st.column_config, "ProgressColumn", None)
            if progress_cls is None:
                cfg[col] = st.column_config.NumberColumn(
                    **common, format=fmt.st_number_format())
            else:
                cfg[col] = progress_cls(
                    **common,
                    format=fmt.st_number_format(),
                    min_value=0,
                    max_value=spec.progress[col],
                )
            continue

        dtype = df[col].dtype
        if pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype):
            fmt = ctx.formats.get(col, dtype)
            if fmt.kind == "text":
                cfg[col] = st.column_config.TextColumn(**common)
            else:
                cfg[col] = st.column_config.NumberColumn(
                    **common, format=fmt.st_number_format()
                )
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            cfg[col] = st.column_config.DatetimeColumn(**common, format="MMM D, YYYY")
        elif pd.api.types.is_bool_dtype(dtype):
            cfg[col] = st.column_config.CheckboxColumn(**common)
        else:
            cfg[col] = st.column_config.TextColumn(**common)

    return cfg


def _prettify(col: str) -> str:
    """CLOSED_MONTH -> Closed Month. Never surface a raw DB identifier."""
    s = str(col).replace("_", " ").strip()
    if s.isupper() or s.islower():
        s = s.title()
    return s


# ─────────────────────────────────────────────────────────────
#  Percent scaling
# ─────────────────────────────────────────────────────────────

def _scale_percent_columns(df: pd.DataFrame, ctx) -> pd.DataFrame:
    """Multiply fractional percent columns by 100 for display.

    Streamlit's NumberColumn renders a literal value with a %% suffix; it does
    not scale. A ratio of 0.87 would show as "0.87%". Scaling here keeps the
    registry as the single source of truth for decimals and suffix.
    """
    out = df
    copied = False
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col].dtype):
            continue
        if ctx.formats.get(col, df[col].dtype).kind != "percent":
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        # Only scale when values look like fractions rather than already-scaled
        # percentages, so a column holding 87.0 is not turned into 8700.
        finite = series.dropna()
        if len(finite) and finite.abs().max() <= 1.5:
            if not copied:
                out = df.copy()
                copied = True
            out[col] = series * 100
    return out


# ─────────────────────────────────────────────────────────────
#  Total row
# ─────────────────────────────────────────────────────────────

def _append_total(df: pd.DataFrame, ctx, spec: GridSpec) -> pd.DataFrame:
    """Append a totals row, summing measures and averaging ratios.

    Summing a percentage column produces nonsense (four teams at 90% is not
    360%), so percent and ratio columns are averaged instead.
    """
    if df.empty:
        return df
    total: dict[str, Any] = {}
    first_text = None
    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype):
            kind = ctx.formats.get(col, dtype).kind
            series = pd.to_numeric(df[col], errors="coerce")
            total[col] = series.mean() if kind == "percent" else series.sum()
        else:
            # List-valued columns (sparklines) must stay list in the total row,
            # or PyArrow raises "cannot mix list and non-list". An empty list is
            # the correct total: there is no single trend for the aggregate.
            sample = df[col].iloc[0] if len(df) else None
            if isinstance(sample, list):
                total[col] = []
            elif first_text is None:
                first_text = col
                total[col] = spec.total_label
            else:
                total[col] = ""
    if first_text is None and len(df.columns):
        total[df.columns[0]] = spec.total_label
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


# ─────────────────────────────────────────────────────────────
#  Export
# ─────────────────────────────────────────────────────────────

def _excel_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes | None:
    """Excel export. Returns None when no writer engine is installed."""
    buf = io.BytesIO()
    safe = _safe_sheet_name(sheet_name)
    for engine in ("xlsxwriter", "openpyxl"):
        try:
            with pd.ExcelWriter(buf, engine=engine) as xl:
                df.to_excel(xl, index=False, sheet_name=safe)
            return buf.getvalue()
        except (ImportError, ModuleNotFoundError, ValueError):
            buf = io.BytesIO()
            continue
    return None


def _safe_sheet_name(name: str) -> str:
    """Excel sheet names: <=31 chars, no []:*?/\\ """
    bad = set('[]:*?/\\')
    cleaned = "".join(c for c in str(name) if c not in bad).strip() or "Data"
    return cleaned[:31]


def export_buttons(
    df: pd.DataFrame,
    *,
    name: str = "data",
    container=None,
    key_suffix: str = "",
) -> None:
    """CSV and Excel download buttons.

    Every BI tool has export and the first generation had none. Users will take
    the number into a deck regardless; the only question is whether they retype
    it by hand.
    """
    target = container or st
    cols = target.columns([1, 1, 4])
    csv = df.to_csv(index=False).encode("utf-8")
    with cols[0]:
        st.download_button(
            "CSV", csv, file_name=f"{name}.csv", mime="text/csv",
            use_container_width=True, key=f"bim_csv_{name}{key_suffix}",
        )
    xls = _excel_bytes(df, name)
    with cols[1]:
        if xls is not None:
            st.download_button(
                "Excel", xls, file_name=f"{name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key=f"bim_xls_{name}{key_suffix}",
            )
        else:
            st.button("Excel", disabled=True, use_container_width=True,
                      help="Install xlsxwriter or openpyxl to enable Excel export.",
                      key=f"bim_xls_off_{name}{key_suffix}")


# ─────────────────────────────────────────────────────────────
#  Render
# ─────────────────────────────────────────────────────────────

def data_grid(
    df: pd.DataFrame,
    ctx,
    spec: GridSpec,
    *,
    store=None,
    container=None,
    on_filter_change=None,
) -> Any:
    """Render a grid. Returns the selection event when selectable."""
    target = container or st

    if spec.title:
        target.markdown(f'<div class="bim-section">{spec.title}</div>',
                        unsafe_allow_html=True)

    if df is None or len(df) == 0:
        # container passed through, not `with target:` -- st is not a context
        # manager and this path fires on any filter that empties the grid.
        states.empty(spec.empty_message, height=200,
                     on_clear=(store.clear if store else None),
                     container=container)
        return None

    view = df.drop(columns=[c for c in spec.hide if c in df.columns], errors="ignore")
    if spec.order:
        ordered = [c for c in spec.order if c in view.columns]
        ordered += [c for c in view.columns if c not in ordered]
        view = view[ordered]

    # Export the analytical frame, before display-only transforms.
    export_frame = view.copy()

    if spec.total_row:
        view = _append_total(view, ctx, spec)
    view = _scale_percent_columns(view, ctx)

    if spec.use_aggrid:
        result = _render_aggrid(view, ctx, spec, target)
        if result is not None:
            if spec.export:
                export_buttons(export_frame, name=spec.export_name,
                               container=target, key_suffix=spec.key or "")
            return result
        # Fall through to native on any AgGrid failure.

    cfg = build_column_config(view, ctx, spec)

    kwargs: dict[str, Any] = {
        "use_container_width": True,
        "hide_index": True,
        "column_config": cfg,
    }
    if spec.height:
        kwargs["height"] = spec.height
    if spec.pin:
        # Pinning keeps row labels visible while scrolling to far-right columns:
        # the exact problem the hand-rolled HTML table could not solve cleanly.
        kwargs["column_order"] = (
            list(spec.pin) + [c for c in view.columns if c not in spec.pin]
        )
        pinned = [c for c in spec.pin if c in cfg]
        for col in pinned:
            try:
                cfg[col].pinned = True
            except (AttributeError, TypeError):
                pass

    event = None
    if spec.selectable or spec.emits:
        kwargs["selection_mode"] = "multi-row"
        kwargs["key"] = spec.key or f"bim_grid_{spec.export_name}"
        event = compat.dataframe(view, on_select="rerun", **kwargs)
        if spec.emits and store is not None:
            if store.ingest_dataframe(event, view, kwargs["key"], dimension=spec.emits):
                if on_filter_change:
                    on_filter_change()
                compat.rerun()
    else:
        if spec.key:
            kwargs["key"] = spec.key
        compat.dataframe(view, **kwargs)

    if spec.caption:
        target.caption(spec.caption)

    if spec.export:
        export_buttons(export_frame, name=spec.export_name, container=target,
                       key_suffix=spec.key or "")

    return event


def _render_aggrid(df: pd.DataFrame, ctx, spec: GridSpec, target) -> Any:
    """Optional AgGrid path for genuine pivot/row-grouping needs.

    Requires SiS container runtime (Components v2). Returns None on any failure
    so the caller falls back to the native grid rather than showing a broken
    component.
    """
    if not spec.aggrid_licence_ack:
        compat.warning(
            "AgGrid was requested but `aggrid_licence_ack` is False. "
            "AgGrid Enterprise features (row grouping, pivot, Excel export) "
            "require a commercial licence from AG Grid Ltd. Falling back to the "
            "native grid.",
            icon=":material/gavel:", parent=target,
        )
        return None
    try:
        from st_aggrid import AgGrid, GridOptionsBuilder  # type: ignore
    except ImportError:
        compat.info(
            "streamlit-aggrid is not installed; using the native grid. "
            "Add it to requirements.txt and deploy on a container runtime "
            "(warehouse runtime blocks custom components via CSP).",
            icon=":material/info:", parent=target,
        )
        return None
    try:
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(resizable=True, sortable=True, filterable=True)
        for col in spec.pin:
            if col in df.columns:
                gb.configure_column(col, pinned="left")
        return AgGrid(df, gridOptions=gb.build(), height=spec.height or 400,
                      allow_unsafe_jscode=False)
    except Exception as exc:  # noqa: BLE001 - never let the grid kill the page
        compat.warning(f"AgGrid failed to render ({exc}); using the native grid.",
                       icon=":material/warning:", parent=target)
        return None


# ─────────────────────────────────────────────────────────────
#  Pivot helper
# ─────────────────────────────────────────────────────────────

def pivot_for_grid(
    df: pd.DataFrame,
    *,
    index: str,
    columns: str,
    values: str,
    column_order: Sequence[Any] | None = None,
    aggfunc: str = "sum",
) -> pd.DataFrame:
    """Crosstab shaped for data_grid, with the index kept as a real column.

    Tableau "Text" mark worksheets are crosstabs; this is the shape they need.
    Keeping the index as a column (rather than a DataFrame index) means it can
    be pinned and hide_index can stay on.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    pv = df.pivot_table(index=index, columns=columns, values=values,
                        aggfunc=aggfunc, fill_value=0)
    if column_order:
        cols = [c for c in column_order if c in pv.columns]
        cols += [c for c in pv.columns if c not in cols]
        pv = pv[cols]
    pv = pv.reset_index()
    pv.columns = [str(c) for c in pv.columns]
    return pv


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _calendar_order(values: Sequence[Any]) -> list[Any] | None:
    """Return calendar order when `values` look like month names, else None.

    Month names sort alphabetically as strings -- "Apr, Aug, Dec, Feb..." -- so a
    sparkline built by naive sorting shows a trend that is not the real trend.
    That is worse than no sparkline, because it reads as information.
    """
    uniq = {str(v).strip() for v in values if v is not None}
    if not uniq:
        return None
    for calendar in (_MONTH_ABBR, _MONTH_FULL):
        lookup = {m.lower(): i for i, m in enumerate(calendar)}
        if all(v.lower() in lookup for v in uniq):
            return sorted(uniq, key=lambda v: lookup[v.lower()])
    return None


def add_sparkline_column(
    df: pd.DataFrame,
    source: pd.DataFrame,
    *,
    key: str,
    order_by: str,
    value: str,
    name: str = "Trend",
    order: Sequence[Any] | None = None,
) -> pd.DataFrame:
    """Attach a per-row list column for LineChartColumn rendering.

    An inline sparkline per row turns a table of totals into something you can
    read a trajectory off, without adding a chart.

    Ordering is the sharp edge here. `order_by` values are sequenced by, in
    priority: an explicit `order`, detected calendar order for month names, then
    natural sort. Pass `order` whenever the sequence is not self-evident -- a
    sparkline in the wrong order silently reports the wrong trend direction.
    """
    if df is None or len(df) == 0 or source is None or len(source) == 0:
        return df
    if not {key, order_by, value}.issubset(source.columns):
        return df

    src = source
    seq = list(order) if order else _calendar_order(source[order_by].tolist())
    if seq:
        rank = {v: i for i, v in enumerate(seq)}
        src = source.copy()
        src["_bim_rank"] = src[order_by].map(lambda v: rank.get(str(v).strip(),
                                                               rank.get(v, len(rank))))
        sort_col = "_bim_rank"
    else:
        sort_col = order_by

    series = (src.sort_values(sort_col)
                 .groupby(key)[value]
                 .apply(lambda s: [float(x) for x in s.tolist()])
                 .to_dict())
    out = df.copy()
    out[name] = out[key].map(series)
    # Rows with no matching series get NaN, which makes the column mixed-type
    # (list + NaN) and causes Arrow serialization failure in st.dataframe.
    # Replace NaN with an empty list so the dtype stays homogeneous.
    out[name] = out[name].apply(lambda v: v if isinstance(v, list) else [])
    return out
