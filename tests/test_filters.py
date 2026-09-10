"""Unit tests for bim_ui.filters — the cross-filter contract.

Run: python3 tests/test_filters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_st  # noqa: E402

ST = mock_st.install()

import pandas as pd  # noqa: E402

from bim_ui.filters import Dimension, FilterStore, _decode_values, _encode_values  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def frame() -> pd.DataFrame:
    return pd.DataFrame({
        "team": ["BC Sales", "BC Sales", "PO Sales", "PO Sales", "PX Sales", "PX Sales"],
        "product": ["PM", "Billing", "PM", "AINA", "Billing", "AINA"],
        "actual": [100, 50, 80, 40, 60, 30],
    })


def new_store(**kw) -> FilterStore:
    mock_st.reset(ST)
    return FilterStore(
        dimensions=[Dimension("team", "Team"), Dimension("product", "Product")],
        **kw,
    )


# ── exclude-self: the core contract ──

print("\nexclude-self")
s = new_store()
s.register_emitter("team_bar", emits="team")
s.register_emitter("product_bar", emits="product")
df = frame()

s.set("team", ["BC Sales"])

own = s.frame_for(df, consumer="team_bar")
check(sorted(own["team"].unique()) == ["BC Sales", "PO Sales", "PX Sales"],
      "chart that emits Team still sees all teams (stays clickable)")

other = s.frame_for(df, consumer="product_bar")
check(other["team"].unique().tolist() == ["BC Sales"],
      "chart that emits Product IS filtered by Team")

kpis = s.frame_for(df, consumer=None)
check(len(kpis) == 2 and kpis["team"].unique().tolist() == ["BC Sales"],
      "KPIs with no consumer apply every filter")

# Two filters at once: each chart excludes only its own dimension.
s.set("product", ["PM"])
own2 = s.frame_for(df, consumer="team_bar")
check(own2["product"].unique().tolist() == ["PM"] and len(own2) == 2,
      "Team chart is filtered by Product but not by Team")
check(len(s.frame_for(df, consumer=None)) == 1,
      "grid with both filters narrows to one row")

# ── toggle ──

print("\ntoggle")
s = new_store()
s.toggle("team", "BC Sales")
check(s.state.get("team") == ["BC Sales"], "toggle selects")
s.toggle("team", "PO Sales")
check(s.state.get("team") == ["BC Sales", "PO Sales"], "toggle accumulates")
s.toggle("team", "BC Sales")
check(s.state.get("team") == ["PO Sales"], "toggle removes on repeat click")
s.toggle("team", "PO Sales")
check(not s.state.is_active("team"), "emptying a dimension deactivates it")

# ── clear ──

print("\nclear")
s = new_store()
s.set("team", ["BC Sales"])
s.set("product", ["PM"])
s.clear("team")
check(not s.state.is_active("team") and s.state.is_active("product"),
      "clear(column) is scoped to one dimension")
s.clear()
check(not s.state, "clear() removes everything")

# ── URL round-trip ──

print("\nURL state")
s = new_store()
s.set("team", ["BC Sales", "PO Sales"])
check(ST.query_params.get("f_team") == "BC Sales~PO Sales", "filters written to URL")

# Simulate a fresh page load with that URL present.
saved = dict(ST.query_params)
mock_st.reset(ST)
ST.query_params.update(saved)
s2 = FilterStore(dimensions=[Dimension("team", "Team")])
check(s2.state.get("team") == ["BC Sales", "PO Sales"],
      "a shared link restores filter state on load")

s2.clear("team")
check("f_team" not in ST.query_params, "clearing a filter removes it from the URL")

# Values containing the separator must survive.
s3 = new_store()
s3.set("product", ["A~B", "C"])
restored = _decode_values(ST.query_params.get("f_product"))
check(restored == ["A~B", "C"], "values containing the separator round-trip via JSON")
check(_encode_values(["x", "y"]) == "x~y", "plain values use the compact encoding")

# ── namespacing ──

print("\nnamespacing")
mock_st.reset(ST)
a = FilterStore([Dimension("team")], namespace="new_bookings")
b = FilterStore([Dimension("team")], namespace="expansion")
a.set("team", ["BC Sales"])
check(not b.state.is_active("team"),
      "per-page namespaces do not leak filters across dashboards")
check("f_new_bookings.team" in ST.query_params, "namespaced URL key")

# ── plotly ingestion ──

print("\nplotly selection ingestion")
s = new_store()
s.register_emitter("team_bar", emits="team")

changed = s.ingest_plotly({"selection": {"points": [{"x": "BC Sales", "y": 100}]}}, "team_bar")
check(changed and s.state.get("team") == ["BC Sales"], "click writes a filter")

changed = s.ingest_plotly({"selection": {"points": [{"x": "BC Sales", "y": 100}]}}, "team_bar")
check(not changed,
      "an identical replayed event is ignored (does not fight the user)")

changed = s.ingest_plotly({"selection": {"points": []}}, "team_bar")
check(changed and not s.state.is_active("team"), "deselecting in-chart clears the filter")

s2 = new_store()
s2.register_emitter("tb", emits="team")
changed = s2.ingest_plotly(
    {"selection": {"points": [{"x": "BC Sales"}, {"x": "PO Sales"}, {"x": "BC Sales"}]}}, "tb")
check(s2.state.get("team") == ["BC Sales", "PO Sales"],
      "multi-select dedupes while preserving click order")

s3 = new_store()
s3.register_emitter("hb", emits="team")
s3.ingest_plotly({"selection": {"points": [{"x": 42, "y": "PX Sales"}]}}, "hb")
check(s3.state.get("team") == ["PX Sales"],
      "horizontal bars read the category from y, not the numeric x")

check(not new_store().ingest_plotly({"selection": {"points": [{"x": "A"}]}}, "unregistered"),
      "an unregistered chart cannot write filters")
check(not new_store().ingest_plotly(None, "team_bar"), "None event is safe")
check(not new_store().ingest_plotly({}, "team_bar"), "malformed event is safe")

# ── dataframe ingestion ──

print("\ndataframe selection ingestion")
s = new_store()
changed = s.ingest_dataframe({"selection": {"rows": [0, 2]}}, frame(), "grid", dimension="team")
check(changed and s.state.get("team") == ["BC Sales", "PO Sales"],
      "row selection maps rows to dimension values")

# Out-of-range indices must not crash and must not invent a selection. Use a
# fresh store so a prior test's state cannot mask the result: on a dirty store
# an empty selection correctly CLEARS, which is a different code path.
s = new_store()
check(not s.ingest_dataframe({"selection": {"rows": [99]}}, frame(), "g2", dimension="team")
      and not s.state.is_active("team"),
      "out-of-range row indices produce no selection")

# On a store that already has state, an empty selection means deselect.
s = new_store()
s.set("team", ["BC Sales"])
check(s.ingest_dataframe({"selection": {"rows": []}}, frame(), "g3", dimension="team")
      and not s.state.is_active("team"),
      "clearing the row selection clears the filter")

check(not s.ingest_dataframe({"selection": {"rows": [0]}}, frame(), "g4", dimension="missing_col"),
      "selection on a dimension the frame lacks is safe")

# ── drill-down ──

print("\ndrill-down")
mock_st.reset(ST)
s = FilterStore(dimensions=[
    Dimension("team", "Team", drill_to="rep"),
    Dimension("rep", "Rep"),
])
check(s.drill_level("team") == "team", "starts at the root level")
s.drill_into("team", "BC Sales")
check(s.drill_level("team") == "rep", "descends to the child dimension")
check(s.state.get("team") == ["BC Sales"], "drilling pins the parent value")
check(s.drill_path("team") == ["team"], "path records the level descended from")

s.drill_into("team", "Alice")
check(s.drill_level("team") == "rep", "cannot descend past the deepest level")

s.drill_up("team")
check(s.drill_level("team") == "team" and not s.state.is_active("team"),
      "drilling up releases the pinned filter")

# ── selection summary ──

print("\nselection summary")
s = new_store()
s.register_emitter("team_bar", emits="team")
s.set("team", ["BC Sales"])
s.set("product", ["PM", "Billing", "AINA", "Telehealth"])
check("Team: BC Sales" in s.selection_summary(), "summary names the dimension")
check("+2" in s.selection_summary(), "summary truncates long value lists")
check("Team" not in s.selection_summary(consumer="team_bar"),
      "summary honours exclude-self for the emitting chart")

# ── robustness ──

print("\nrobustness")
s = new_store()
s.set("nonexistent_column", ["x"])
check(len(s.frame_for(frame())) == 6,
      "a filter on a column the frame lacks is skipped, not fatal")
check(s.frame_for(pd.DataFrame()).empty, "empty frame in, empty frame out")
check(s.frame_for(None) is None, "None frame is passed through")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("All filter tests passed.")
