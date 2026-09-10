"""
bim_ui.filters — cross-filter store with exclude-self semantics, URL state, drill-down.

This is the module that turns a static report into a BI tool. Three behaviors:

1. Cross-filtering with exclude-self. Clicking a Team bar filters the KPIs, the
   trend, and the grid, but NOT the Team chart itself. Without exclude-self the
   clicked chart collapses to the single selected mark and the user is stranded
   with no visible way back. Tableau calls this "exclude self" on action filters;
   it is not optional polish, it is what makes click-to-filter usable.

2. URL state. Filters round-trip through st.query_params so a filtered view is a
   shareable link. This is the difference between "look at the dashboard and set
   Team to PX" and pasting a URL into Slack.

3. Drill-down. Ordered dimension paths (Team -> Rep, Year -> Quarter -> Month)
   with a breadcrumb, so a chart can go one level deeper in place rather than
   needing a separate dashboard per grain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import pandas as pd
import streamlit as st

from . import compat

# Session/URL key prefix. Namespaced so a filter dimension called "page" cannot
# collide with app-level navigation state.
_SS_KEY = "_bim_filters"
_SS_DRILL = "_bim_drill"
_URL_PREFIX = "f_"
_URL_DRILL_PREFIX = "d_"

# Multi-value URL encoding. A literal separator is used rather than repeated
# query keys because st.query_params flattens repeats inconsistently across
# Streamlit versions. Values containing the separator are JSON-escaped instead.
_SEP = "~"


@dataclass
class Dimension:
    """A filterable dimension.

    `label` is what the user sees in chips and breadcrumbs; `column` is the
    dataframe column. They differ often -- the column may be CLOSED_MONTH while
    the label should read "Closed Month".
    """

    column: str
    label: str | None = None
    drill_to: str | None = None  # next dimension column in a drill path

    @property
    def display(self) -> str:
        return self.label or self.column.replace("_", " ").title()


@dataclass
class FilterState:
    """Active filter selections, dimension column -> selected values."""

    values: dict[str, list[Any]] = field(default_factory=dict)

    def is_active(self, column: str) -> bool:
        return bool(self.values.get(column))

    def get(self, column: str) -> list[Any]:
        return list(self.values.get(column, []))

    def __bool__(self) -> bool:
        return any(bool(v) for v in self.values.values())

    def active_columns(self) -> list[str]:
        return [c for c, v in self.values.items() if v]


class FilterStore:
    """Session-backed cross-filter store.

    Charts register the dimension they emit. When asked for a frame, the store
    applies every active filter EXCEPT the requesting chart's own dimension.

        store = FilterStore(dimensions=[Dimension("team", "Team")])
        store.register_emitter("team_bar", emits="team")

        df_for_chart = store.frame_for(df, consumer="team_bar")  # excludes team
        df_for_kpis  = store.frame_for(df)                       # applies all
    """

    def __init__(
        self,
        dimensions: Sequence[Dimension | str] = (),
        *,
        sync_url: bool = True,
        namespace: str = "",
    ) -> None:
        self.dimensions: dict[str, Dimension] = {}
        for d in dimensions:
            dim = Dimension(d) if isinstance(d, str) else d
            self.dimensions[dim.column] = dim

        self.sync_url = sync_url and compat.supports("query_params")
        # Namespace keeps per-page filter state separate, so navigating from one
        # dashboard to another does not silently carry a Team filter across.
        self.namespace = namespace
        self._emitters: dict[str, str] = {}

        self._ss_key = f"{_SS_KEY}:{namespace}" if namespace else _SS_KEY
        self._ss_drill = f"{_SS_DRILL}:{namespace}" if namespace else _SS_DRILL

        if self._ss_key not in st.session_state:
            # Hydrate from URL on first load so a shared link restores state.
            st.session_state[self._ss_key] = (
                self._read_url() if sync_url else {}
            )
        if self._ss_drill not in st.session_state:
            st.session_state[self._ss_drill] = (
                self._read_url_drill() if sync_url else {}
            )

    # ── dimension registration ──

    def add_dimension(self, dim: Dimension | str) -> "FilterStore":
        d = Dimension(dim) if isinstance(dim, str) else dim
        self.dimensions[d.column] = d
        return self

    def register_emitter(self, consumer_key: str, emits: str) -> "FilterStore":
        """Declare that chart `consumer_key` emits filters on dimension `emits`.

        Registering is what enables exclude-self for that chart.
        """
        if emits not in self.dimensions:
            self.add_dimension(emits)
        self._emitters[consumer_key] = emits
        return self

    def emitted_by(self, consumer_key: str) -> str | None:
        return self._emitters.get(consumer_key)

    # ── state access ──

    @property
    def state(self) -> FilterState:
        return FilterState(dict(st.session_state.get(self._ss_key, {})))

    def _write(self, values: dict[str, list[Any]]) -> None:
        cleaned = {k: list(v) for k, v in values.items() if v}
        st.session_state[self._ss_key] = cleaned
        if self.sync_url:
            self._write_url(cleaned)

    def set(self, column: str, values: Iterable[Any] | None) -> None:
        cur = dict(st.session_state.get(self._ss_key, {}))
        vals = [v for v in (values or []) if v is not None]
        if vals:
            cur[column] = vals
        else:
            cur.pop(column, None)
        self._write(cur)

    def toggle(self, column: str, value: Any) -> None:
        """Add or remove one value. Click a bar to select, click again to clear."""
        cur = self.state.get(column)
        if value in cur:
            cur.remove(value)
        else:
            cur.append(value)
        self.set(column, cur)

    def clear(self, column: str | None = None) -> None:
        if column is None:
            self._write({})
            st.session_state[self._ss_drill] = {}
            if self.sync_url:
                self._clear_url_drill()
        else:
            self.set(column, None)

    # ── filtering ──

    def frame_for(
        self,
        df: pd.DataFrame,
        consumer: str | None = None,
        *,
        exclude: Iterable[str] = (),
    ) -> pd.DataFrame:
        """Apply active filters to df.

        When `consumer` is a registered emitter, its own dimension is skipped so
        the chart keeps showing every category and stays clickable. Pass
        consumer=None (KPIs, grids) to apply every filter.
        """
        if df is None or len(df) == 0:
            return df

        skip = set(exclude)
        if consumer is not None:
            own = self._emitters.get(consumer)
            if own:
                skip.add(own)

        out = df
        for column, values in self.state.values.items():
            if not values or column in skip or column not in out.columns:
                continue
            out = out[out[column].isin(values)]
        return out

    def selection_summary(self, consumer: str | None = None) -> str:
        """One-line description of what is filtered, for chart subtitles."""
        parts = []
        skip = {self._emitters.get(consumer)} if consumer else set()
        for column, values in self.state.values.items():
            if not values or column in skip:
                continue
            dim = self.dimensions.get(column, Dimension(column))
            shown = ", ".join(str(v) for v in values[:2])
            if len(values) > 2:
                shown += f" +{len(values) - 2}"
            parts.append(f"{dim.display}: {shown}")
        return " · ".join(parts)

    # ── Plotly selection ingestion ──

    def ingest_plotly(
        self,
        event: Any,
        consumer_key: str,
        *,
        dimension: str | None = None,
        additive: bool = False,
    ) -> bool:
        """Translate a plotly on_select event into filter state.

        Returns True when state changed, so the caller can compat.rerun().

        Streamlit re-delivers the same selection payload on every rerun while the
        marks stay selected. Writing unconditionally would fight the user: they
        clear a chip, the stale event immediately re-applies it. So the event is
        fingerprinted and only acted on when it differs from last time.
        """
        dim = dimension or self._emitters.get(consumer_key)
        if not dim or event is None:
            return False

        points = []
        try:
            sel = event.get("selection") if isinstance(event, dict) else getattr(event, "selection", None)
            points = (sel or {}).get("points", []) if sel else []
        except (AttributeError, TypeError):
            return False

        # Category charts put the category on x; horizontal bars put it on y.
        picked: list[Any] = []
        for p in points:
            for key in ("x", "y", "label", "customdata"):
                v = p.get(key) if isinstance(p, dict) else None
                if isinstance(v, list):
                    v = v[0] if v else None
                if v is not None and not isinstance(v, (int, float)):
                    picked.append(v)
                    break

        # De-dupe, preserve click order.
        seen: set[Any] = set()
        picked = [p for p in picked if not (p in seen or seen.add(p))]

        fingerprint = f"{consumer_key}:{json.dumps(picked, default=str, sort_keys=True)}"
        fp_key = f"_bim_fp:{consumer_key}"
        if st.session_state.get(fp_key) == fingerprint:
            return False
        st.session_state[fp_key] = fingerprint

        if not picked:
            # An emptied selection means the user deselected in the chart.
            if self.state.is_active(dim):
                self.set(dim, None)
                return True
            return False

        if additive:
            merged = self.state.get(dim)
            for p in picked:
                if p not in merged:
                    merged.append(p)
            picked = merged

        if self.state.get(dim) == picked:
            return False
        self.set(dim, picked)
        return True

    def ingest_altair(
        self,
        event: Any,
        consumer_key: str,
        *,
        dimension: str | None = None,
        selection: str | None = None,
        additive: bool = False,
    ) -> bool:
        """Translate an Altair on_select event into filter state.

        Altair's payload differs from Plotly's in shape: instead of a list of
        points carrying x/y, Streamlit returns
        `{"selection": {<selection name>: {<field>: [values]}}}` -- the selected
        field values directly. That is easier to read correctly, since there is
        no guessing whether the category sat on x or y.

        Fingerprinted for the same reason as ingest_plotly: Streamlit re-delivers
        the same payload on every rerun while the marks stay selected, so writing
        unconditionally would fight a user who just cleared a chip.
        """
        dim = dimension or self._emitters.get(consumer_key)
        if not dim or event is None:
            return False

        try:
            sel = (event.get("selection") if isinstance(event, dict)
                   else getattr(event, "selection", None)) or {}
        except (AttributeError, TypeError):
            return False

        # Prefer the named selection; otherwise take whichever one carries data,
        # so a chart built without an explicit name still cross-filters.
        payload = sel.get(selection or consumer_key)
        if payload is None:
            payload = next((v for v in sel.values() if isinstance(v, dict) and v), {})

        picked: list[Any] = []
        if isinstance(payload, dict):
            # Exact field match first; fall back to the sole field present, which
            # is the common case for a point selection on one dimension.
            values = payload.get(dim)
            if values is None and len(payload) == 1:
                values = next(iter(payload.values()))
            if isinstance(values, (list, tuple)):
                picked = [v for v in values if v is not None]
            elif values is not None:
                picked = [values]
        elif isinstance(payload, (list, tuple)):
            # Some builds deliver a row list rather than a field map.
            for row in payload:
                if isinstance(row, dict) and row.get(dim) is not None:
                    picked.append(row[dim])

        seen: set[Any] = set()
        picked = [p for p in picked if not (p in seen or seen.add(p))]

        fingerprint = f"{consumer_key}:{json.dumps(picked, default=str, sort_keys=True)}"
        fp_key = f"_bim_fp:{consumer_key}"
        if st.session_state.get(fp_key) == fingerprint:
            return False
        st.session_state[fp_key] = fingerprint

        if not picked:
            # An emptied selection means the user deselected in the chart.
            if self.state.is_active(dim):
                self.set(dim, None)
                return True
            return False

        if additive:
            merged = self.state.get(dim)
            for p in picked:
                if p not in merged:
                    merged.append(p)
            picked = merged

        if self.state.get(dim) == picked:
            return False
        self.set(dim, picked)
        return True

    def ingest_dataframe(
        self,
        event: Any,
        df: pd.DataFrame,
        consumer_key: str,
        *,
        dimension: str,
    ) -> bool:
        """Translate st.dataframe row selection into filter state."""
        if event is None or df is None or dimension not in df.columns:
            return False
        try:
            sel = event.get("selection") if isinstance(event, dict) else getattr(event, "selection", None)
            rows = (sel or {}).get("rows", []) if sel else []
        except (AttributeError, TypeError):
            return False

        picked = [df.iloc[r][dimension] for r in rows if 0 <= r < len(df)]
        seen: set[Any] = set()
        picked = [p for p in picked if not (p in seen or seen.add(p))]

        fingerprint = f"{consumer_key}:{json.dumps(picked, default=str, sort_keys=True)}"
        fp_key = f"_bim_fp:{consumer_key}"
        if st.session_state.get(fp_key) == fingerprint:
            return False
        st.session_state[fp_key] = fingerprint

        if not picked:
            if self.state.is_active(dimension):
                self.set(dimension, None)
                return True
            return False
        if self.state.get(dimension) == picked:
            return False
        self.set(dimension, picked)
        return True

    # ── drill-down ──

    def drill_path(self, root: str) -> list[str]:
        """Current drill path for a hierarchy rooted at `root`."""
        return list(st.session_state.get(self._ss_drill, {}).get(root, []))

    def drill_level(self, root: str) -> str:
        """The dimension column currently being displayed for this hierarchy."""
        path = self.drill_path(root)
        if not path:
            return root
        last = path[-1]
        dim = self.dimensions.get(last)
        return dim.drill_to if dim and dim.drill_to else last

    def drill_into(self, root: str, value: Any) -> None:
        """Descend one level, pinning `value` at the current level."""
        level = self.drill_level(root)
        dim = self.dimensions.get(level)
        if not dim or not dim.drill_to:
            return  # already at the deepest level
        drills = dict(st.session_state.get(self._ss_drill, {}))
        path = list(drills.get(root, []))
        path.append(level)
        drills[root] = path
        st.session_state[self._ss_drill] = drills
        self.set(level, [value])
        if self.sync_url:
            self._write_url_drill(drills)

    def drill_up(self, root: str, to_index: int | None = None) -> None:
        """Ascend. `to_index` truncates the path for breadcrumb clicks."""
        drills = dict(st.session_state.get(self._ss_drill, {}))
        path = list(drills.get(root, []))
        if not path:
            return
        cut = len(path) - 1 if to_index is None else max(0, to_index)
        for level in path[cut:]:
            self.set(level, None)
        drills[root] = path[:cut]
        if not drills[root]:
            drills.pop(root, None)
        st.session_state[self._ss_drill] = drills
        if self.sync_url:
            self._write_url_drill(drills)

    # ── rendering ──

    def render_chips(
        self,
        *,
        container=None,
        show_clear_all: bool = True,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Render active filters as removable chips.

        A filter the user cannot see is a filter they will misread a number
        because of. Every active filter appears here with a way to remove it.
        """
        target = container or st
        active = self.state.active_columns()
        drills = st.session_state.get(self._ss_drill, {})
        if not active and not drills:
            return

        cols = target.columns([6, 1])
        with cols[0]:
            chips = []
            for column in active:
                dim = self.dimensions.get(column, Dimension(column))
                vals = self.state.get(column)
                shown = ", ".join(str(v) for v in vals[:3])
                if len(vals) > 3:
                    shown += f" +{len(vals) - 3} more"
                chips.append(
                    f'<span class="bim-chip">'
                    f'<span class="bim-chip-dim">{dim.display}:</span> {shown}</span>'
                )
            st.markdown("".join(chips), unsafe_allow_html=True)

        with cols[1]:
            if show_clear_all and active:
                if st.button("Clear all", use_container_width=True,
                             key=f"bim_clear_all_{self.namespace}"):
                    self.clear()
                    if on_change:
                        on_change()
                    compat.rerun()

        # Per-dimension removal. Buttons rather than HTML links because a chip
        # rendered via st.markdown cannot call back into Python.
        if len(active) > 1:
            rm_cols = target.columns(min(len(active), 6))
            for i, column in enumerate(active):
                dim = self.dimensions.get(column, Dimension(column))
                with rm_cols[i % len(rm_cols)]:
                    if st.button(f"✕ {dim.display}", use_container_width=True,
                                 key=f"bim_rm_{self.namespace}_{column}"):
                        self.set(column, None)
                        if on_change:
                            on_change()
                        compat.rerun()

    def render_breadcrumb(self, root: str, *, container=None) -> None:
        """Drill breadcrumb: Team > BC Sales > Rep."""
        target = container or st
        path = self.drill_path(root)
        if not path:
            return

        root_dim = self.dimensions.get(root, Dimension(root))
        cols = target.columns([1] + [1] * len(path))
        with cols[0]:
            if st.button(f"⌂ {root_dim.display}", key=f"bim_crumb_{self.namespace}_{root}_root"):
                self.drill_up(root, to_index=0)
                compat.rerun()
        for i, level in enumerate(path):
            dim = self.dimensions.get(level, Dimension(level))
            vals = self.state.get(level)
            label = str(vals[0]) if vals else dim.display
            with cols[i + 1]:
                if st.button(f"› {label}", key=f"bim_crumb_{self.namespace}_{root}_{i}"):
                    self.drill_up(root, to_index=i + 1)
                    compat.rerun()

    def render_sidebar_controls(
        self,
        df: pd.DataFrame,
        columns: Sequence[str] | None = None,
        *,
        container=None,
    ) -> None:
        """Multiselect controls for each dimension, bound to the same store.

        Sidebar controls and chart clicks write to one place, so they cannot
        disagree about what is filtered.
        """
        target = container or st.sidebar
        cols = list(columns) if columns else list(self.dimensions)
        for column in cols:
            if column not in df.columns:
                continue
            dim = self.dimensions.get(column, Dimension(column))
            options = sorted(df[column].dropna().unique().tolist(), key=str)
            if not options or len(options) > 200:
                continue
            current = [v for v in self.state.get(column) if v in options]
            picked = target.multiselect(
                dim.display, options, default=current,
                key=f"bim_ms_{self.namespace}_{column}",
            )
            if picked != current:
                self.set(column, picked)
                compat.rerun()

    # ── URL persistence ──

    def _read_url(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        params = compat.get_query_params()
        for key, raw in params.items():
            if not key.startswith(_URL_PREFIX):
                continue
            column = key[len(_URL_PREFIX):]
            if self.namespace:
                if not column.startswith(f"{self.namespace}."):
                    continue
                column = column.split(".", 1)[-1]
            out[column] = _decode_values(raw or "")
        return out

    def _write_url(self, values: dict[str, list[Any]]) -> None:
        prefix = f"{_URL_PREFIX}{self.namespace}." if self.namespace else _URL_PREFIX
        for key in list(compat.get_query_params()):
            if key.startswith(prefix):
                compat.del_query_param(key)
        for column, vals in values.items():
            if vals:
                compat.set_query_param(f"{prefix}{column}", _encode_values(vals))

    def _read_url_drill(self) -> dict[str, list[str]]:
        prefix = (f"{_URL_DRILL_PREFIX}{self.namespace}."
                  if self.namespace else _URL_DRILL_PREFIX)
        out: dict[str, list[str]] = {}
        for key, raw in compat.get_query_params().items():
            if key.startswith(prefix):
                out[key[len(prefix):]] = [str(v) for v in _decode_values(raw or "")]
        return out

    def _write_url_drill(self, drills: dict[str, list[str]]) -> None:
        prefix = (f"{_URL_DRILL_PREFIX}{self.namespace}."
                  if self.namespace else _URL_DRILL_PREFIX)
        for key in list(compat.get_query_params()):
            if key.startswith(prefix):
                compat.del_query_param(key)
        for root, path in drills.items():
            if path:
                compat.set_query_param(f"{prefix}{root}", _encode_values(path))

    def _clear_url_drill(self) -> None:
        self._write_url_drill({})


def _encode_values(values: Iterable[Any]) -> str:
    """Join values for a URL. JSON-escape when a value contains the separator."""
    vals = [str(v) for v in values]
    if any(_SEP in v for v in vals):
        return "json:" + json.dumps(vals)
    return _SEP.join(vals)


def _decode_values(raw: str) -> list[Any]:
    if not raw:
        return []
    if raw.startswith("json:"):
        try:
            parsed = json.loads(raw[5:])
            return list(parsed) if isinstance(parsed, list) else [raw]
        except (ValueError, TypeError):
            return [raw]
    return [v for v in raw.split(_SEP) if v != ""]
