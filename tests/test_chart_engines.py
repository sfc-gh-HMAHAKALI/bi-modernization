"""Both chart engines build every chart type from the same spec and frame.

The facade only earns its keep if a call site can switch engines without
changing anything else, so each builder is exercised on Altair and Plotly with
identical inputs, plus the edge cases that broke the first-generation output:
empty frames, a long tail, a zero target, and a missing column.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_st  # noqa: E402

ST = mock_st.install()

import pandas as pd  # noqa: E402

from bim_ui import UIContext, theme  # noqa: E402
from bim_ui import charts as C  # noqa: E402
from bim_ui.chart_spec import ChartSpec  # noqa: E402
from bim_ui.fmt import COUNT, CURRENCY_COMPACT, PERCENT, FormatRegistry  # noqa: E402

TEBRA = ["#004952", "#417e86", "#8cb2b6", "#cbdde0", "#ff8d6e", "#f5a623"]

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


ctx = UIContext(palette=theme.resolve_palette({"colors": TEBRA}),
                formats=FormatRegistry())

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
df = pd.DataFrame({
    "month": MONTHS,
    "actual": [120_000, 145_000, 98_000, 176_000, 154_000, 191_000],
    "plan": [130_000, 130_000, 130_000, 160_000, 160_000, 160_000],
})
cat = pd.DataFrame({
    "specialty": ["Cardiology", "Dermatology", "Pediatrics", "Oncology",
                  "Neurology", "Podiatry", "Urology", "ENT", "Rheumatology"],
    "arr": [90_000, 72_000, 61_000, 55_000, 40_000, 22_000, 18_000, 11_000, 6_000],
})
stack = pd.DataFrame({
    "month": MONTHS * 2,
    "arr": [50, 60, 40, 70, 65, 80, 30, 35, 28, 44, 40, 52],
    "team": ["Cross Sell"] * 6 + ["CSM"] * 6,
})
pace = pd.DataFrame({
    "day": list(range(1, 7)) * 2,
    "cum": [10, 25, 40, 60, 75, 95, 12, 30, 45, 55, 80, 102],
    "series": ["May"] * 6 + ["Jun"] * 6,
})
fall = pd.DataFrame({
    "step": ["Open", "New", "Churn", "Expansion", "Close"],
    "delta": [100_000, 40_000, -25_000, 18_000, 0],
    "measure": ["total", "relative", "relative", "relative", "total"],
})

# (name, callable, frame, spec) — one table drives both engines.
CASES = [
    ("bar_vs_target", lambda m, s: m.bar_vs_target(df, ctx, s),
     ChartSpec(title="Bookings vs Plan", x="month", y="actual", target="plan",
               y_format=CURRENCY_COMPACT, emits="month", key="k_bar")),
    ("trend_line", lambda m, s: m.trend_line(df, ctx, s,
                                             [("actual", "Actual"), ("plan", "Plan")]),
     ChartSpec(title="Trend", x="month", y="actual", y_format=CURRENCY_COMPACT)),
    ("ranked_bar", lambda m, s: m.ranked_bar(cat, ctx, s),
     ChartSpec(title="By specialty", x="specialty", y="arr",
               y_format=CURRENCY_COMPACT, max_categories=6, emits="specialty",
               key="k_rank")),
    ("stacked_composition", lambda m, s: m.stacked_composition(stack, ctx, s),
     ChartSpec(title="Mix", x="month", y="arr", series="team", y_format=COUNT)),
    ("share_bar", lambda m, s: m.share_bar(cat, ctx, s),
     ChartSpec(title="Share", x="specialty", y="arr", max_categories=5,
               emits="specialty", key="k_share")),
    ("cumulative_pace", lambda m, s: m.cumulative_pace(pace, ctx, s),
     ChartSpec(title="Pace", x="day", y="cum", y_format=COUNT)),
    ("heatmap", lambda m, s: m.heatmap(stack, ctx, s),
     ChartSpec(title="Heat", x="month", y="arr", series="team", y_format=COUNT)),
    ("waterfall", lambda m, s: m.waterfall(fall, ctx, s),
     ChartSpec(title="Bridge", x="step", y="delta", y_format=CURRENCY_COMPACT)),
]

for eng in ("altair", "plotly"):
    print(f"\n=== {eng} builds every chart ===")
    mod = (__import__(f"bim_ui.charts_{eng}", fromlist=["x"]))
    for name, build, spec in CASES:
        try:
            fig = build(mod, spec)
            check(f"{name} builds", fig is not None)
        except Exception as e:
            check(f"{name} builds", False, f"{type(e).__name__}: {e}")

print("\n=== facade dispatches to the requested engine ===")
for eng in ("altair", "plotly"):
    C.set_engine(eng)
    check(f"engine() reports {eng}", C.engine() == eng)
    fig = C.bar_vs_target(df, ctx, ChartSpec(x="month", y="actual", target="plan"))
    mod_name = type(fig).__module__ or ""
    expected = "altair" if eng == "altair" else "plotly"
    check(f"{eng}: figure comes from {expected}", expected in mod_name, mod_name)

print("\n=== per-spec override beats the app default ===")
C.set_engine("altair")
fig = C.bar_vs_target(df, ctx, ChartSpec(x="month", y="actual", engine="plotly"))
check("spec engine=plotly overrides altair default", "plotly" in (type(fig).__module__ or ""))
fig = C.bar_vs_target(df, ctx, ChartSpec(x="month", y="actual"))
check("unset spec still uses altair default", "altair" in (type(fig).__module__ or ""))

print("\n=== edge cases on both engines ===")
for eng in ("altair", "plotly"):
    C.set_engine(eng)
    empty = pd.DataFrame({"month": [], "actual": [], "plan": []})
    check(f"{eng}: empty frame returns None (drives empty state)",
          C.bar_vs_target(empty, ctx, ChartSpec(x="month", y="actual")) is None)
    check(f"{eng}: None frame returns None",
          C.bar_vs_target(None, ctx, ChartSpec(x="month", y="actual")) is None)
    # A zero target must not raise: Tableau yields null, Snowflake raises, and a
    # divide here would take the page down.
    zero = df.assign(plan=0)
    try:
        fig = C.bar_vs_target(zero, ctx, ChartSpec(x="month", y="actual", target="plan",
                                                   color_by_status=True))
        check(f"{eng}: zero target does not raise", fig is not None)
    except Exception as e:
        check(f"{eng}: zero target does not raise", False, f"{type(e).__name__}: {e}")
    # Long tail must honour max_categories as a TOTAL bar count.
    try:
        fig = C.ranked_bar(cat, ctx, ChartSpec(x="specialty", y="arr", max_categories=4))
        check(f"{eng}: long tail collapses", fig is not None)
    except Exception as e:
        check(f"{eng}: long tail collapses", False, f"{type(e).__name__}: {e}")
    # A spec naming a column the frame lacks should not explode the page.
    try:
        C.ranked_bar(cat, ctx, ChartSpec(x="nope", y="arr"))
        check(f"{eng}: missing column handled", True)
    except KeyError:
        check(f"{eng}: missing column handled", False, "raised KeyError")
    except Exception:
        check(f"{eng}: missing column handled", True)

print("\n=== max_categories is a total, not a floor ===")
from bim_ui.chart_spec import collapse_tail  # noqa: E402
out = collapse_tail(cat, "specialty", "arr", 4)
check(f"collapse_tail(limit=4) yields 4 rows, got {len(out)}", len(out) == 4)
check("'Other' bucket present", "Other" in set(out["specialty"]))
check("total value preserved by collapse",
      abs(float(out["arr"].sum()) - float(cat["arr"].sum())) < 1e-6)

print("\n=== vega_format agrees with the other surfaces ===")
check("percent -> d3 percent", PERCENT.vega_format().endswith("%"))
check("compact -> SI prefix", "s" in CURRENCY_COMPACT.vega_format())
check("currency prefix carried into format string",
      CURRENCY_COMPACT.vega_format().startswith("$"))

print("\n=== altair selection ingestion ===")
from bim_ui import filters as F  # noqa: E402

store = F.FilterStore(namespace="t")
store.register_emitter("k_bar", "month")
changed = store.ingest_altair({"selection": {"k_bar": {"month": ["Feb", "Mar"]}}},
                              "k_bar", dimension="month", selection="k_bar")
check("named selection applies", changed and store.state.get("month") == ["Feb", "Mar"])
check("repeat payload is ignored (no fight with the user)",
      not store.ingest_altair({"selection": {"k_bar": {"month": ["Feb", "Mar"]}}},
                              "k_bar", dimension="month", selection="k_bar"))
store2 = F.FilterStore(namespace="t2")
store2.register_emitter("k2", "month")
check("sole-field fallback works when name differs",
      store2.ingest_altair({"selection": {"other": {"month": ["Jan"]}}}, "k2",
                           dimension="month"))
check("empty selection clears",
      store.ingest_altair({"selection": {"k_bar": {"month": []}}}, "k_bar",
                          dimension="month", selection="k_bar")
      and not store.state.is_active("month"))
check("None event is safe", not store.ingest_altair(None, "k_bar", dimension="month"))

print("\n=== exclude-self still holds with the altair path ===")
store3 = F.FilterStore(namespace="t3")
store3.register_emitter("k_bar", "month")
store3.set("month", ["Feb"])
own = store3.frame_for(df, "k_bar")
check("emitting chart does not filter itself", len(own) == len(df))
other = store3.frame_for(df, "k_other")
check("a different consumer is filtered", len(other) == 1)

print("\n=== month axis keeps calendar order (browser-found defect) ===")
from bim_ui.chart_spec import axis_order, calendar_order, labels_fit, ordered_frame  # noqa: E402

# Alphabetical sorting of month LABELS turned a cumulative line into a zig-zag.
shuffled = pd.DataFrame({
    "month": ["Mar", "Jan", "Sep", "Feb", "Jun"],
    "cum": [30, 10, 90, 20, 60],
})
ordered = ordered_frame(shuffled, "month")
check(f"months sort by calendar, got {list(ordered['month'])}",
      list(ordered["month"]) == ["Jan", "Feb", "Mar", "Jun", "Sep"])
check("cumulative values follow calendar order (monotonic)",
      list(ordered["cum"]) == sorted(ordered["cum"]))
check("axis_order preserves the frame's order",
      axis_order(ordered["month"]) == ["Jan", "Feb", "Mar", "Jun", "Sep"])
check("full month names also recognised",
      calendar_order(["March", "January"]) == ["January", "March"])
check("non-month categories are left alone (no invented order)",
      calendar_order(["Cardiology", "Oncology"]) is None)
# A non-month label axis must keep the caller's order rather than be re-sorted.
teams = pd.DataFrame({"team": ["Zeta", "Alpha", "Mid"], "v": [3, 1, 2]})
check("caller row order preserved for plain labels",
      list(ordered_frame(teams, "team")["team"]) == ["Zeta", "Alpha", "Mid"])
# Numerics still sort, since a day-of-month axis must ascend.
nums = pd.DataFrame({"day": [3, 1, 2], "v": [3, 1, 2]})
check("numeric axis still sorts ascending",
      list(ordered_frame(nums, "day")["day"]) == [1, 2, 3])

print("\n=== altair honours calendar order in the emitted spec ===")
import json  # noqa: E402
C.set_engine("altair")
fig = C.trend_line(shuffled, ctx, ChartSpec(x="month", y="cum"), [("cum", "Cumulative")])
spec_json = json.loads(fig.to_json())
def find_sort(node):
    if isinstance(node, dict):
        if "encoding" in node and "x" in node["encoding"]:
            s = node["encoding"]["x"].get("sort")
            if isinstance(s, list):
                return s
        for v in node.values():
            r = find_sort(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = find_sort(v)
            if r:
                return r
    return None
domain = find_sort(spec_json)
check(f"x domain is stated explicitly, got {domain}",
      domain == ["Jan", "Feb", "Mar", "Jun", "Sep"])

print("\n=== value labels are suppressed when they would collide ===")
wide = pd.DataFrame({"month": [f"M{i:02d}" for i in range(12)],
                     "actual": list(range(100, 112))})
check("12 vertical bars -> labels off", not labels_fit(ChartSpec(x="month", y="actual"), 12))
check("6 vertical bars -> labels on", labels_fit(ChartSpec(x="month", y="actual"), 6))
check("horizontal bars are exempt regardless of count",
      labels_fit(ChartSpec(x="month", y="actual", orientation="h"), 40))
check("show_labels=False still wins",
      not labels_fit(ChartSpec(x="month", y="actual", show_labels=False), 3))
for eng in ("altair", "plotly"):
    C.set_engine(eng)
    check(f"{eng}: 12-category bar chart still builds", 
          C.bar_vs_target(wide, ctx, ChartSpec(x="month", y="actual")) is not None)

print("\n=== compact tick format is internally consistent ===")
# ".3s" produced "$0.00, $200, $1.00k" on one axis; "~s" trims to "$0, $200, $1k".
check(f"compact uses trimmed SI, got {CURRENCY_COMPACT.vega_format()}",
      "~s" in CURRENCY_COMPACT.vega_format())
check("no fixed significant-digit padding", ".3s" not in CURRENCY_COMPACT.vega_format())

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All chart engine tests passed.'}")
sys.exit(1 if fails else 0)
