"""Merge de-duplication and evidence preservation.

Appending was the previous behaviour and it does not survive a real portfolio:
24 finance workbooks over the same tables produced 1,489 dimensions where only
349 are distinct. These assert both halves of the fix -- that duplicates collapse,
and that nothing needed downstream is lost when they do.

Run: python3 tests/test_merge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.output.inventory import merge_inventories  # noqa: E402

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


def inv(source: str, *, dims, facts=(), tables=("BOOKINGS",), dashboards=(),
        rels=(), complexity="simple") -> dict:
    return {
        "source_type": "tableau",
        "source_file": source,
        "snowflake_target": {"database": "DB", "schema": "S"},
        "tables": [{"name": t, "physical_table": t} for t in tables],
        "relationships": list(rels),
        "dimensions": [
            {"name": n, "table": "BOOKINGS", "expr": f"BOOKINGS.{n}",
             "complexity": complexity, "dashboard_name": f"Dash {source}",
             "source_file": source}
            for n in dims
        ],
        "facts": [
            {"name": n, "table": "BOOKINGS", "expr": f"SUM({n})",
             "complexity": complexity, "source_file": source}
            for n in facts
        ],
        "metrics": [],
        "filters": [],
        "flagged": [],
        "errors": [],
        "complexity_summary": {"simple": len(dims) + len(facts),
                               "needs_translation": 0, "manual_required": 0},
        "dashboards": [{"name": d, "sheets": []} for d in dashboards],
        "worksheets": [],
    }


A = inv("a.twb", dims=["ACCOUNT_NAME", "CLOSED_MONTH"], facts=["NET_ARR"],
        dashboards=["Bookings"])
B = inv("b.twb", dims=["ACCOUNT_NAME", "TEAM"], facts=["NET_ARR", "QUOTA"],
        dashboards=["Expansion"])
C = inv("c.twb", dims=["ACCOUNT_NAME"], tables=("BOOKINGS", "INVOICING"),
        dashboards=["Invoices"])

print("=== de-duplication ===")
m = merge_inventories([A, B, C])

names = [d["name"] for d in m["dimensions"]]
# 5 input rows (2 + 2 + 1) over 3 distinct names: ACCOUNT_NAME appears in all
# three workbooks, CLOSED_MONTH and TEAM in one each.
check(f"dimensions de-duplicated, got {len(names)} from 5 input rows", len(names) == 3,
      str(names))
check("ACCOUNT_NAME appears exactly once", names.count("ACCOUNT_NAME") == 1)
check(f"facts de-duplicated, got {len(m['facts'])}", len(m["facts"]) == 2)
check(f"tables de-duplicated, got {len(m['tables'])}", len(m["tables"]) == 2)
check("no duplicate (table, name) pairs survive anywhere",
      all(len({(i["table"], i["name"]) for i in m[c]}) == len(m[c])
          for c in ("dimensions", "facts", "metrics")))

print("\n=== provenance survives de-duplication ===")
acct = next(d for d in m["dimensions"] if d["name"] == "ACCOUNT_NAME")
check("every contributing workbook is recorded",
      all(s in acct["source_file"] for s in ("a.twb", "b.twb", "c.twb")),
      acct["source_file"])
check("every contributing dashboard is recorded",
      all(f"Dash {s}" in acct["dashboard_name"] for s in ("a.twb", "b.twb", "c.twb")),
      acct["dashboard_name"])
# A field from only one workbook must not gain phantom provenance.
team = next(d for d in m["dimensions"] if d["name"] == "TEAM")
check("a single-source field keeps only its own provenance",
      "a.twb" not in team["source_file"] and "b.twb" in team["source_file"],
      team["source_file"])

print("\n=== usage evidence is carried, not dropped ===")
# propose-views groups tables by which dashboards use them together, so a merged
# inventory without dashboards cannot be grouped at all.
check(f"dashboards carried, got {len(m['dashboards'])}", len(m["dashboards"]) == 3)
check("each dashboard is attributed to its workbook",
      all(d.get("source_file") for d in m["dashboards"]))
check(f"sources recorded, got {len(m.get('sources', []))}", len(m["sources"]) == 3)
check("worksheets key present", "worksheets" in m)

print("\n=== complexity is recounted, not summed ===")
# Summing per-source counts reports pre-dedup totals and disagrees with the
# inventory it describes.
total_cols = len(m["dimensions"]) + len(m["facts"]) + len(m["metrics"])
summed = sum(m["complexity_summary"].values())
check(f"summary matches actual column count ({summed} vs {total_cols})",
      summed == total_cols)

print("\n=== relationships and filters de-duplicated ===")
rel = {"left_table": "BOOKINGS", "right_table": "INVOICING",
       "condition": "a=b", "join_type": "inner"}
D = inv("d.twb", dims=["X"], rels=[rel])
E = inv("e.twb", dims=["X"], rels=[rel, {**rel, "condition": "c=d"}])
m2 = merge_inventories([D, E])
check(f"identical relationships collapse, got {len(m2['relationships'])}",
      len(m2["relationships"]) == 2, str(m2["relationships"]))

print("\n=== inspection payload merging ===")
F = dict(A)
F["tableau_inspection"] = {
    "translation": [{"datasource": "DS", "field": "ARR", "lod_kinds": ["FIXED"]}],
    "warnings": ["orphan sheets"],
    "visual_styles": {"palette": ["#004952"]},
    "layout": {"Bookings": []},
}
G = dict(B)
G["tableau_inspection"] = {
    "translation": [
        {"datasource": "DS", "field": "ARR", "lod_kinds": ["FIXED"]},   # dupe
        {"datasource": "DS", "field": "QUOTA", "lod_kinds": []},
    ],
    "warnings": ["blending"],
    "visual_styles": {"palette": ["#ff8d6e"]},
}
m3 = merge_inventories([F, G])
insp = m3.get("tableau_inspection", {})
check(f"shared calculations translate once, got {len(insp.get('translation', []))}",
      len(insp.get("translation", [])) == 2,
      str([t["field"] for t in insp.get("translation", [])]))
check("translations attribute their source",
      all(t.get("source_file") for t in insp.get("translation", [])))
check(f"warnings from every source kept, got {len(insp.get('warnings', []))}",
      len(insp.get("warnings", [])) == 2)
check("warnings name their workbook",
      all(":" in w for w in insp.get("warnings", [])), str(insp.get("warnings")))
check("per-workbook styles kept separately (they cannot be merged)",
      len(insp.get("by_source", {})) == 2, str(list(insp.get("by_source", {}))))

print("\n=== degenerate inputs ===")
check("a single inventory merges to itself",
      len(merge_inventories([A])["dimensions"]) == 2)
one = merge_inventories([A])
check("merging one inventory does not lose its dashboards",
      len(one["dashboards"]) == 1)
empty = merge_inventories([])
check("empty input yields an empty but well-formed inventory",
      empty["dimensions"] == [] and "tables" in empty and "dashboards" in empty)
# An inventory with no source_file must not produce a phantom "" source.
H = {k: v for k, v in A.items() if k != "source_file"}
m4 = merge_inventories([H])
check("a source-less inventory does not record an empty source",
      "" not in m4.get("sources", []), str(m4.get("sources")))

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All merge tests passed.'}")
sys.exit(1 if fails else 0)
