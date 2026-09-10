"""Tests for charts, grid, and kpi modules.

Focus: the guarantees that previously failed in the rendered app.
  - computed headroom so outside value labels cannot be clipped
  - no Plotly APIs newer than 5.9
  - no duplicate update_layout kwargs
  - percent columns scaled once, and total rows averaging (not summing) ratios

Run: python3 tests/test_components.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_st  # noqa: E402

ST = mock_st.install()

import pandas as pd  # noqa: E402

from bim_ui import UIContext, fmt  # noqa: E402
# This suite asserts Plotly figure internals (layout.yaxis.range, trace types),
# so it targets that engine directly rather than the facade, whose default is
# Altair. Cross-engine behaviour is covered by test_chart_engines.py.
from bim_ui import charts_plotly as charts  # noqa: E402
from bim_ui import grid, kpi  # noqa: E402
from bim_ui.chart_spec import ChartSpec  # noqa: E402
from bim_ui.grid import GridSpec  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


CTX = UIContext.from_visuals(
    None, brand_colors=["#004952", "#417e86", "#8cb2b6", "#ff8d6e"], demo_mode=True
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]


def bookings() -> pd.DataFrame:
    return pd.DataFrame({
        "month": MONTHS,
        "actual": [120, 180, 150, 240, 210, 300],
        "plan":   [140, 160, 180, 200, 220, 240],
    })


# ── headroom: the top defect class ──

print("\nlabel headroom (top defect class)")

fig = charts.bar_vs_target(
    bookings(), CTX,
    ChartSpec(title="Bookings", x="month", y="actual", target="plan",
              y_format=fmt.CURRENCY_COMPACT),
)
rng = fig.layout.yaxis.range
data_max = 300  # tallest bar
check(rng is not None and rng[1] > data_max,
      f"y-range {rng} extends above the tallest mark ({data_max})")
check(rng[1] >= data_max * 1.15,
      "headroom is at least 15%, enough for an outside label")
check(all(t.cliponaxis is False for t in fig.data if t.type == "bar"),
      "bar traces set cliponaxis=False as a second line of defence")

# Headroom must scale with the data, not be a fixed constant.
big = bookings()
big["actual"] = big["actual"] * 1000
fig_big = charts.bar_vs_target(
    big, CTX, ChartSpec(x="month", y="actual", target="plan", y_format=fmt.CURRENCY_COMPACT)
)
check(fig_big.layout.yaxis.range[1] > 300_000,
      "headroom is computed from the data, not hardcoded")

# The target series must be able to push the ceiling up.
under = pd.DataFrame({"month": ["Jan"], "actual": [50], "plan": [500]})
fig_u = charts.bar_vs_target(under, CTX, ChartSpec(x="month", y="actual", target="plan"))
check(fig_u.layout.yaxis.range[1] >= 500,
      "a target above every actual still fits inside the range")

# Ranked (horizontal) bars put the value on x.
fig_r = charts.ranked_bar(
    pd.DataFrame({"specialty": ["Primary Care", "Urgent Care", "Derm"],
                  "bookings": [700, 420, 180]}),
    CTX, ChartSpec(x="specialty", y="bookings", y_format=fmt.CURRENCY_COMPACT),
)
check(fig_r.layout.xaxis.range[1] > 700, "horizontal bars get headroom on the x axis")
check(fig_r.layout.yaxis.range is None, "category axis is not force-ranged")

fig_s = charts.share_bar(
    pd.DataFrame({"seg": ["SMB", "Mid", "Ent"], "v": [500, 300, 200]}),
    CTX, ChartSpec(x="seg", y="v"),
)
check(fig_s.layout.xaxis.range[1] > 0.5, "share bars get headroom for percent labels")

# ── Plotly version floor ──

print("\nPlotly 5.9 compatibility")


def spec_json(f) -> str:
    return f.to_json()


all_figs = {
    "bar_vs_target": fig,
    "ranked_bar": fig_r,
    "share_bar": fig_s,
    "trend_line": charts.trend_line(
        bookings(), CTX, ChartSpec(x="month", y="actual", target="plan"),
        series_cols=[("actual", "Actual")]),
    "stacked": charts.stacked_composition(
        pd.DataFrame({"m": MONTHS * 2, "v": list(range(12)),
                      "team": ["A"] * 6 + ["B"] * 6}),
        CTX, ChartSpec(x="m", y="v", series="team")),
    "heatmap": charts.heatmap(
        pd.DataFrame({"m": MONTHS * 2, "v": list(range(12)),
                      "prod": ["A"] * 6 + ["B"] * 6}),
        CTX, ChartSpec(x="m", y="v", series="prod")),
    "pace": charts.cumulative_pace(
        pd.DataFrame({"d": list(range(1, 7)) * 2, "cum": list(range(12)),
                      "series": ["Jan"] * 6 + ["Feb"] * 6}),
        CTX, ChartSpec(x="d", y="cum")),
    "waterfall": charts.waterfall(
        pd.DataFrame({"step": ["Open", "Won", "Lost"], "v": [100, 50, -30],
                      "measure": ["absolute", "relative", "relative"]}),
        CTX, ChartSpec(x="step", y="v")),
}

banned = ["cornerradius", "legendgrouptitle", "insidetextorientation"]
for name, f in all_figs.items():
    check(f is not None, f"{name} builds")
    if f is None:
        continue
    js = spec_json(f)
    found = [b for b in banned if b in js]
    check(not found, f"{name} uses no post-5.9 Plotly properties {found or ''}")

# ── empty and degenerate input ──

print("\nempty and degenerate input")
empty_df = pd.DataFrame({"month": [], "actual": [], "plan": []})
check(charts.bar_vs_target(empty_df, CTX, ChartSpec(x="month", y="actual")) is None,
      "empty frame returns None so render() can show an empty state")
check(charts.bar_vs_target(None, CTX, ChartSpec(x="month", y="actual")) is None,
      "None frame returns None")
check(charts.share_bar(pd.DataFrame({"s": ["A"], "v": [0]}), CTX,
                       ChartSpec(x="s", y="v")) is None,
      "a zero total does not divide by zero")
single = charts.bar_vs_target(pd.DataFrame({"month": ["Jan"], "actual": [10], "plan": [12]}),
                              CTX, ChartSpec(x="month", y="actual", target="plan"))
check(single is not None, "single-row frame builds")

# ── long tail collapse ──

print("\nlong tail collapse")
many = pd.DataFrame({"cat": [f"C{i}" for i in range(30)], "v": list(range(30, 0, -1))})
f_many = charts.ranked_bar(many, CTX, ChartSpec(x="cat", y="v", max_categories=8))
ycats = list(f_many.data[0].y)
check(len(ycats) == 8, f"30 categories collapse to 8, got {len(ycats)}")
check("Other" in ycats, "the tail is aggregated into an 'Other' bucket")
check(abs(sum(f_many.data[0].x) - many["v"].sum()) < 1e-6,
      "collapsing preserves the total (no data silently dropped)")

# ── grid: column_config derivation ──

print("\ngrid column_config")
gdf = pd.DataFrame({
    "team": ["BC Sales", "PO Sales"],
    "CLOSED_MONTH": pd.to_datetime(["2026-01-01", "2026-02-01"]),
    "SPLIT_ARR": [1_200_000.0, 980_000.0],
    "attainment": [0.87, 1.04],
    "deals": [47, 38],
    "active": [True, False],
})
cfg = grid.build_column_config(gdf, CTX, GridSpec(progress={"attainment": 1.0}))
check(type(cfg["SPLIT_ARR"]).__name__ == "NumberColumn", "numeric measure -> NumberColumn")
check(type(cfg["attainment"]).__name__ == "ProgressColumn", "attainment -> ProgressColumn")
check(type(cfg["CLOSED_MONTH"]).__name__ == "DatetimeColumn", "date -> DatetimeColumn")
check(type(cfg["active"]).__name__ == "CheckboxColumn", "bool -> CheckboxColumn")
check(type(cfg["team"]).__name__ == "TextColumn", "string -> TextColumn")
check(grid._prettify("CLOSED_MONTH") == "Closed Month",
      "raw DB identifiers are prettified, never surfaced as-is")
check(grid._prettify("SPLIT_ARR") == "Split Arr", "underscores become spaces")

spark_cfg = grid.build_column_config(
    pd.DataFrame({"team": ["A"], "Trend": [[1, 2, 3]]}), CTX,
    GridSpec(sparkline={"Trend": "line"}))
check(type(spark_cfg["Trend"]).__name__ == "LineChartColumn", "sparkline -> LineChartColumn")

# ── grid: percent scaling ──

print("\ngrid percent scaling")
scaled = grid._scale_percent_columns(gdf, CTX)
check(abs(scaled["attainment"].iloc[0] - 87.0) < 1e-6,
      "fractional percent scaled to 87.0 for NumberColumn's %% suffix")
already = pd.DataFrame({"attainment": [87.0, 104.0]})
check(abs(grid._scale_percent_columns(already, CTX)["attainment"].iloc[0] - 87.0) < 1e-6,
      "already-scaled percentages are not scaled twice")
check(abs(gdf["attainment"].iloc[0] - 0.87) < 1e-6,
      "scaling does not mutate the caller's dataframe")

# ── grid: total row ──

print("\ngrid total row")
tot = grid._append_total(gdf, CTX, GridSpec(total_row=True))
last = tot.iloc[-1]
check(last["team"] == "Total", "label lands in the first text column")
check(abs(last["SPLIT_ARR"] - 2_180_000) < 1e-6, "measures are summed")
check(abs(last["attainment"] - 0.955) < 1e-3,
      "percent columns are AVERAGED, not summed (0.87+1.04 would be 191%)")
check(last["deals"] == 85, "integer counts are summed")

# ── grid: pivot + sparkline helpers ──

print("\ngrid helpers")
long = pd.DataFrame({
    "product": ["PM", "PM", "Billing", "Billing"],
    "month": ["Jan", "Feb", "Jan", "Feb"],
    "arr": [10, 20, 5, 8],
})
pv = grid.pivot_for_grid(long, index="product", columns="month", values="arr",
                         column_order=["Jan", "Feb"])
check(list(pv.columns) == ["product", "Jan", "Feb"],
      "pivot keeps the index as a real column so it can be pinned")
check(pv[pv["product"] == "PM"]["Feb"].iloc[0] == 20, "pivot values are correct")
check(grid.pivot_for_grid(pd.DataFrame(), index="a", columns="b", values="c").empty,
      "empty pivot input is safe")

withspark = grid.add_sparkline_column(
    pd.DataFrame({"product": ["PM", "Billing"]}), long,
    key="product", order_by="month", value="arr")
check(withspark["Trend"].iloc[0] == [10.0, 20.0],
      "month-name ordering follows the calendar, not the alphabet "
      "(naive sort puts Feb before Jan and inverts the trend)")

check(grid._calendar_order(["Mar", "Jan", "Feb"]) == ["Jan", "Feb", "Mar"],
      "calendar order detected for month abbreviations")
check(grid._calendar_order(["March", "January"]) == ["January", "March"],
      "calendar order detected for full month names")
check(grid._calendar_order(["Alpha", "Beta"]) is None,
      "non-month values fall through to natural sort")

explicit = grid.add_sparkline_column(
    pd.DataFrame({"product": ["PM"]}),
    pd.DataFrame({"product": ["PM"] * 3, "q": ["Q3", "Q1", "Q2"], "v": [3, 1, 2]}),
    key="product", order_by="q", value="v", order=["Q1", "Q2", "Q3"])
check(explicit["Trend"].iloc[0] == [1.0, 2.0, 3.0],
      "an explicit order sequence is honoured")

# ── grid: excel export ──

print("\ngrid export")
xls = grid._excel_bytes(gdf, "BC Sales — Product ARR/2026")
check(xls is None or xls[:2] == b"PK", "excel bytes are a zip or cleanly unavailable")
check(len(grid._safe_sheet_name("a" * 40)) <= 31, "sheet name truncated to Excel's limit")
check(not set("[]:*?/\\") & set(grid._safe_sheet_name("A[B]:C/D")),
      "illegal sheet-name characters stripped")

# ── kpi ──

print("\nKPI cards")
html = kpi._card_html(
    kpi.KPI("YTD Bookings", 4_200_000, fmt.CURRENCY_COMPACT, target=3_750_000,
            delta_vs=3_750_000, delta_label="vs plan",
            sparkline=[1, 2, 3, 4, 5], show_progress=True), CTX)
check("$4.2M" in html, "value formatted through the registry")
check("▲" in html, "delta direction rendered")
check(CTX.palette["good"] in html, "beating plan colors with the good role")
check("<svg" in html and "polyline" in html, "sparkline SVG inlined")
check("bim-kpi-label" in html, "label uses the wrapping class, not a fixed height")

worse = kpi._card_html(
    kpi.KPI("Churn", 12, fmt.NUMBER, delta_vs=8, higher_is_better=False), CTX)
check(CTX.palette["bad"] in worse,
      "higher_is_better=False turns an increase red (no green arrow on worsening)")

flat = kpi._card_html(kpi.KPI("Flat", 100, fmt.NUMBER, delta_vs=100), CTX)
check("no change" in flat, "zero delta reads as 'no change'")

check(kpi._sparkline_svg([5], "#000") == "", "a single point produces no sparkline")
check(kpi._sparkline_svg([], "#000") == "", "empty series produces no sparkline")
check("<svg" in kpi._sparkline_svg([3, 3, 3], "#000"),
      "a flat series does not divide by zero span")

trip = kpi.attainment_set(actual=4_200_000, target=3_750_000,
                          period_label="YTD", ctx=CTX, trend=[1, 2, 3])
check(len(trip) == 3, "attainment_set yields the standard triplet")
check("Attainment" in trip[2].label, "third card is attainment")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("All component tests passed.")
