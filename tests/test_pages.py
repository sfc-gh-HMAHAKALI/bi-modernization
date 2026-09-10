"""Headless render of every page in every parameter mode.

Executes the real page functions against a mocked Streamlit. Catches import
errors, KeyErrors, aggregation bugs, and NaN KPIs without needing a browser --
which is what makes this usable as a generator-time gate rather than a manual
review step.

Run: python3 tests/test_pages.py
"""

from __future__ import annotations

import os
import math
import sys
import traceback
from pathlib import Path

# This suite exercises a *generated* app, which is not part of the skill. Point
# it at one with BIM_APP_DIR, or drop a generated app at ./generated_app.
# Without one there is nothing to test, so skip rather than fail — the skill's
# own component tests (test_filters, test_components) cover bim_ui directly.
_SKILL = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get("BIM_APP_DIR") or _SKILL / "generated_app")

if not (APP / "pages_impl.py").exists():
    print(f"SKIP  test_pages: no generated app at {APP}")
    print("      set BIM_APP_DIR=/path/to/generated/app to run this suite")
    raise SystemExit(0)

sys.path.insert(0, str(APP))
sys.path.insert(0, str(_SKILL / "assets"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_st  # noqa: E402

ST = mock_st.install()

import pandas as pd  # noqa: E402

import data as D  # noqa: E402
import pages_impl as P  # noqa: E402
from bim_ui import UIContext, filters as F  # noqa: E402
from bim_ui.fmt import COUNT, CURRENCY_COMPACT, PERCENT, NumberFormat  # noqa: E402

TEBRA = ["#004952", "#417e86", "#8cb2b6", "#cbdde0", "#ff8d6e",
         "#ffaf95", "#ffcfbf", "#ffe9e3", "#f6f3eb", "#8ca7a2"]

PAGES = [
    "New Bookings Dashboard", "New Bookings Summary",
    "BC Sales Bookings", "PO Sales Bookings", "PS Sales Bookings",
    "PX Sales Bookings", "Tebra Bookings",
    "Expansion Bookings Dashboard", "Expansion Bookings Summary",
    "BC Expansion Bookings", "CSM Expansion Bookings", "XS Expansion Bookings",
    "BoD Charts",
]

DIMS = [
    F.Dimension("team", "Team", drill_to="rep"),
    F.Dimension("rep", "Rep"),
    F.Dimension("product", "Product"),
    F.Dimension("specialty", "Specialty"),
    F.Dimension("provider_size", "Provider Size"),
    F.Dimension("deal_size", "Deal Size"),
    F.Dimension("month_label", "Month"),
]


def build_ctx() -> UIContext:
    ctx = UIContext.from_visuals(None, brand_colors=TEBRA, background="#FFFFFF",
                                demo_mode=True)
    ctx.formats.register_many({
        "bookings": CURRENCY_COMPACT, "actual": CURRENCY_COMPACT,
        "plan": CURRENCY_COMPACT, "forecast_39": CURRENCY_COMPACT,
        "target": CURRENCY_COMPACT, "amount": CURRENCY_COMPACT,
        "acv": NumberFormat("currency", decimals=0, prefix="$"),
        "attainment": PERCENT, "pg_attach": PERCENT, "aina_attach": PERCENT,
        "cumulative": CURRENCY_COMPACT, "customers": COUNT,
        "deals": COUNT, "wins": COUNT, "ads": NumberFormat("currency", 0, prefix="$"),
        "avg_deal": NumberFormat("currency", 0, prefix="$"),
    })
    return ctx


CTX = build_ctx()
DATA = D.load_all()
FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def render(page: str, target_col: str, target_label: str, store=None):
    if store is None:
        mock_st.reset(ST)
        store = F.FilterStore(dimensions=DIMS,
                              namespace=page.lower().replace(" ", "_"))
    pc = P.PageContext(ctx=CTX, store=store, data=DATA,
                       target_col=target_col, target_label=target_label, page=page)
    if page == "New Bookings Dashboard":
        P.aggregate_dashboard(pc, page, D.TEAMS_NEW)
    elif page == "New Bookings Summary":
        P.new_bookings_summary(pc)
    elif page == "Tebra Bookings":
        P.aggregate_dashboard(pc, page, [D.TEAM_TEBRA])
    elif page == "Expansion Bookings Dashboard":
        P.aggregate_dashboard(pc, page, D.TEAMS_EXP)
    elif page == "Expansion Bookings Summary":
        P.expansion_summary(pc)
    elif page == "BoD Charts":
        P.bod_charts(pc)
    else:
        P.team_dashboard(pc, page.replace(" Bookings", ""))
    return pc


# ── every page, both parameter modes ──

print("\nall pages render, both parameter modes")
errors: list[tuple[str, str, str]] = []
for target_col, tlabel in (("plan", "Plan"), ("forecast_39", "3+9 Forecast")):
    for page in PAGES:
        try:
            render(page, target_col, tlabel)
        except Exception:
            errors.append((tlabel, page, traceback.format_exc()))

check(not errors, f"{len(PAGES) * 2} page renders, {len(errors)} raised")
for tlabel, page, tb in errors[:5]:
    print(f"\n--- {tlabel} / {page} ---\n{tb[-1500:]}")

# ── KPI integrity ──

print("\nKPI integrity")
bad: list[str] = []
for target_col in ("plan", "forecast_39"):
    for team in D.TEAMS_NEW + D.TEAMS_EXP + [D.TEAM_TEBRA]:
        scope = DATA["bookings"][DATA["bookings"]["team"] == team]
        f = D.kpi_figures(scope, target_col)
        for k, v in f.items():
            if k == "trend":
                if any(math.isnan(x) or math.isinf(x) for x in v):
                    bad.append(f"{team}.{target_col}.trend")
                continue
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                bad.append(f"{team}.{target_col}.{k}")
check(not bad, f"no NaN/Inf in any KPI figure {bad[:4]}")

nb = D.kpi_figures(DATA["bookings"][DATA["bookings"]["team"].isin(D.TEAMS_NEW)])
check(nb["ytd"] > 0 and nb["ytd_target"] > 0, "aggregate KPIs are non-zero")
check(len(nb["trend"]) == len(D.MONTHS), "sparkline covers every YTD month")

# Plan must not be multiplied by the product fan-out.
bc = DATA["bookings"][DATA["bookings"]["team"] == "BC Sales"]
plan_rows = bc[bc["plan"] > 0].groupby("month_label").size().unique().tolist()
check(plan_rows == [1],
      f"plan carried on exactly one row per month (fan-out safe), got {plan_rows}")

# ── empty-filter safety: the classic crash path ──

print("\nempty result safety")
empty_errors = []
for page in PAGES:
    mock_st.reset(ST)
    store = F.FilterStore(dimensions=DIMS, namespace=page.lower().replace(" ", "_"))
    # A filter that matches nothing must render an empty state, not raise.
    store.set("product", ["__does_not_exist__"])
    try:
        render(page, "plan", "Plan", store=store)
    except Exception:
        empty_errors.append((page, traceback.format_exc()))
check(not empty_errors,
      f"every page survives a filter matching zero rows, {len(empty_errors)} raised")
for page, tb in empty_errors[:3]:
    print(f"\n--- {page} (empty filter) ---\n{tb[-1200:]}")

# ── cross-filtered render ──

print("\ncross-filtered render")
cf_errors = []
for page in PAGES:
    mock_st.reset(ST)
    store = F.FilterStore(dimensions=DIMS, namespace=page.lower().replace(" ", "_"))
    store.set("product", ["Billing"])
    store.set("month_label", [D.MONTH_LABELS[0]])
    try:
        render(page, "plan", "Plan", store=store)
    except Exception:
        cf_errors.append((page, traceback.format_exc()))
check(not cf_errors, f"every page renders with filters applied, {len(cf_errors)} raised")
for page, tb in cf_errors[:3]:
    print(f"\n--- {page} (filtered) ---\n{tb[-1200:]}")

# ── drilled render ──

print("\ndrilled render")
drill_errors = []
for page in ("New Bookings Dashboard", "Expansion Bookings Dashboard"):
    mock_st.reset(ST)
    store = F.FilterStore(dimensions=DIMS, namespace=page.lower().replace(" ", "_"))
    store.drill_into("team", "BC Sales" if "New" in page else "BC Expansion")
    try:
        pc = render(page, "plan", "Plan", store=store)
        check(store.drill_level("team") == "rep", f"{page} is at rep grain after drilling")
    except Exception:
        drill_errors.append((page, traceback.format_exc()))
check(not drill_errors, f"drilled pages render, {len(drill_errors)} raised")
for page, tb in drill_errors[:2]:
    print(f"\n--- {page} (drilled) ---\n{tb[-1200:]}")

# ── what the kit actually asked Streamlit to render ──

print("\nrendered surface assertions")
mock_st.reset(ST)
store = F.FilterStore(dimensions=DIMS, namespace="new_bookings_dashboard")
render("New Bookings Dashboard", "plan", "Plan", store=store)

# Charts route through whichever engine is active, so count both surfaces rather
# than assuming Plotly.
charts_rendered = ST.calls.get("plotly_chart", []) + ST.calls.get("altair_chart", [])
check(len(charts_rendered) >= 4, f"dashboard renders multiple charts ({len(charts_rendered)})")
check(any(c.get("on_select") == "rerun" for c in charts_rendered),
      "at least one chart is wired for cross-filter selection")
# scrollZoom is a Plotly config concept; Altair does not zoom on scroll by default.
check(all(c.get("config", {}).get("scrollZoom") is False
          for c in ST.calls.get("plotly_chart", []) if "config" in c),
      "scroll zoom disabled so charts do not hijack page scrolling")

grids = ST.calls.get("dataframe", [])
check(grids, "dashboard renders a grid")
check(all(g.get("hide_index") for g in grids), "grids hide the pandas index")
check(any("column_config" in g for g in grids), "grids pass a derived column_config")
check(any(g.get("column_order") for g in grids), "grid pins its label column")

dl = ST.calls.get("download_button", [])
check(any(d["label"] == "CSV" and d["size"] > 0 for d in dl),
      "CSV export is offered with non-empty content")

# ── export content correctness ──

print("\nexport content")
mock_st.reset(ST)
store = F.FilterStore(dimensions=DIMS, namespace="bod_charts")
store.set("product", ["Billing"])
render("BoD Charts", "plan", "Plan", store=store)
csvs = [d for d in ST.calls.get("download_button", []) if d["label"] == "CSV"]
check(csvs, "filtered page still offers export")
check(all(d["size"] > 20 for d in csvs), "exported CSV is not an empty shell")

print()
if FAILS or errors or empty_errors or cf_errors:
    print(f"{len(FAILS)} FAILURE(S)")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("All page tests passed.")
