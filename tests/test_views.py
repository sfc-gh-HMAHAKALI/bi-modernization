"""Semantic-view grouping from observed usage.

The whole point of propose-views is that it does not invent a rule -- it reads
which tables appear together on a dashboard. So these tests are mostly about what
it must NOT do: group tables with no evidence connecting them, drop tables it
cannot place, or propose a view with no columns.

Run: python3 tests/test_views.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.views import build_evidence, format_proposal, propose  # noqa: E402

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


def col(name, table, *, dash="", page="", src="wb.twb"):
    return {"name": name, "table": table, "expr": f"{table}.{name}",
            "complexity": "simple", "dashboard_name": dash,
            "page_name": page, "source_file": src}


def inv(tables, dims=(), facts=(), metrics=()):
    return {
        "source_type": "tableau",
        "tables": [{"name": t} for t in tables],
        "dimensions": list(dims), "facts": list(facts), "metrics": list(metrics),
        "relationships": [], "filters": [], "flagged": [], "errors": [],
        "complexity_summary": {}, "dashboards": [], "worksheets": [],
    }


print("=== co-usage groups tables seen together ===")
# BOOKINGS and QUOTA share a page, so they are queried together. INVOICING is
# only ever on its own page, so it must stay separate.
i = inv(
    ["BOOKINGS", "QUOTA", "INVOICING"],
    dims=[col("ACCOUNT", "BOOKINGS", dash="Sales", page="Sales Detail"),
          col("TEAM", "QUOTA", dash="Sales", page="Sales Detail"),
          col("INV_NO", "INVOICING", dash="Billing", page="Billing Detail")],
    facts=[col("ARR", "BOOKINGS", dash="Sales", page="Sales Detail"),
           col("TARGET", "QUOTA", dash="Sales", page="Sales Detail"),
           col("AMOUNT", "INVOICING", dash="Billing", page="Billing Detail")],
)
p = propose(i)
check(f"strategy is co-usage, got {p['strategy']}", p["strategy"] == "co-usage")
check(f"two views proposed, got {p['view_count']}", p["view_count"] == 2)

groups = {frozenset(v["tables"]) for v in p["views"]}
check("BOOKINGS and QUOTA grouped (same page)",
      frozenset({"BOOKINGS", "QUOTA"}) in groups, str(groups))
check("INVOICING kept separate (never co-occurs)",
      frozenset({"INVOICING"}) in groups, str(groups))

print("\n=== evidence is reported, not just the answer ===")
sales = next(v for v in p["views"] if set(v["tables"]) == {"BOOKINGS", "QUOTA"})
e = sales["evidence"]
check("the driving dashboard is named", e["dashboards"] == ["Sales"], str(e["dashboards"]))
check(f"columns counted, got {e['column_total']}", e["column_total"] == 4)
check("column breakdown by kind", e["columns"]["dimensions"] == 2 and e["columns"]["facts"] == 2)
check("contributing workbooks recorded", e["workbooks"] == ["wb.twb"])

print("\n=== tables with no evidence are NOT invented into a group ===")
# Two unrelated unused tables must become two views, not one. Lumping them
# asserts a relationship nothing supports.
i2 = inv(
    ["USED", "ORPHAN_A", "ORPHAN_B"],
    dims=[col("D", "USED", dash="Dash", page="Page"),
          col("X", "ORPHAN_A"), col("Y", "ORPHAN_B")],
)
p2 = propose(i2)
tabs = [set(v["tables"]) for v in p2["views"]]
check(f"three separate views, got {len(tabs)}", len(tabs) == 3, str(tabs))
check("orphans are not merged together",
      {"ORPHAN_A"} in tabs and {"ORPHAN_B"} in tabs, str(tabs))
check("unreached tables are reported",
      set(p2["unreached_tables"]) == {"ORPHAN_A", "ORPHAN_B"},
      str(p2["unreached_tables"]))
check("a note warns about unused tables",
      any("not used by any dashboard" in n for n in p2["notes"]))

print("\n=== no table is silently dropped ===")
placed = {t for v in p2["views"] for t in v["tables"]}
check("every table with columns lands in some view",
      placed == {"USED", "ORPHAN_A", "ORPHAN_B"}, str(placed))

print("\n=== column-less tables are excluded, with a note ===")
i3 = inv(["REAL", "JOIN_ONLY"],
         dims=[col("D", "REAL", dash="Dash", page="Page")])
p3 = propose(i3)
check(f"only the table with columns is proposed, got {p3['view_count']}",
      p3["view_count"] == 1)
check("the column-less table is not proposed",
      all("JOIN_ONLY" not in v["tables"] for v in p3["views"]))
check("a note explains the omission",
      any("no columns" in n for n in p3["notes"]), str(p3["notes"]))

print("\n=== fallback when there is no usage evidence at all ===")
i4 = inv(["A", "B"], dims=[col("x", "A"), col("y", "B")])
p4 = propose(i4)
check(f"falls back to one-per-table, got {p4['strategy']}",
      p4["strategy"] == "one-per-table")
check(f"one view per table, got {p4['view_count']}", p4["view_count"] == 2)
check("the fallback is stated as a fallback, not a recommendation",
      any("simplest correct answer" in n for n in p4["notes"]), str(p4["notes"]))

print("\n=== strategy overrides ===")
p5 = propose(i, strategy="one-per-table")
check(f"one-per-table forced, got {p5['view_count']} views", p5["view_count"] == 3)
check("forcing 1:1 splits the co-usage group",
      all(len(v["tables"]) == 1 for v in p5["views"]))
p6 = propose(i4, strategy="co-usage")
check("co-usage forced on evidence-free input still returns every table",
      {t for v in p6["views"] for t in v["tables"]} == {"A", "B"})

print("\n=== multi-workbook consolidation is surfaced ===")
i7 = inv(["SHARED"],
         dims=[col("D", "SHARED", dash="D1", page="P1", src="a.twb"),
               col("E", "SHARED", dash="D2", page="P2", src="b.twb")])
p7 = propose(i7)
v = p7["views"][0]
check(f"workbook count aggregated, got {v['evidence']['workbook_count']}",
      v["evidence"]["workbook_count"] == 2)
check("a note flags the consolidation opportunity",
      any("span more than one workbook" in n for n in p7["notes"]), str(p7["notes"]))

print("\n=== naming ===")
check("view names end in _SEMANTIC_VIEW",
      all(v["name"].endswith("_SEMANTIC_VIEW") for v in p["views"]))
# Named after the largest table, so the name is predictable rather than taken
# from whichever dashboard happened to be busiest.
i8 = inv(["SMALL", "BIG"],
         dims=[col("a", "BIG", dash="D", page="P"), col("b", "BIG", dash="D", page="P"),
               col("c", "SMALL", dash="D", page="P")])
p8 = propose(i8)
check("group named after its largest table",
      p8["views"][0]["name"] == "BIG_SEMANTIC_VIEW", p8["views"][0]["name"])

print("\n=== degenerate input ===")
p9 = propose(inv([]))
check("empty inventory proposes nothing and does not raise", p9["view_count"] == 0)
check("format_proposal renders an empty proposal", isinstance(format_proposal(p9), str))
check("format_proposal renders a real proposal", "SQLPROXY" not in format_proposal(p)
      and "BOOKINGS" in format_proposal(p))

print("\n=== evidence builder ===")
ev = build_evidence(i)
check("dashboard-to-table map built", ev["dashboard_tables"]["Sales"] == ["BOOKINGS", "QUOTA"])
check("table-to-dashboard map built", ev["table_dashboards"]["INVOICING"] == ["Billing"])
check("usage evidence detected", ev["has_usage_evidence"])
check("no usage evidence detected on a bare inventory",
      not build_evidence(i4)["has_usage_evidence"])

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All view-proposal tests passed.'}")
sys.exit(1 if fails else 0)
