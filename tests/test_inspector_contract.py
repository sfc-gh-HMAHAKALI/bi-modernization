"""Contract check: inspector-backed parse vs legacy parser.

The adapter is only safe if `build_unified_inventory` produces the same *shape*
from both paths. This compares key sets and types rather than values, since the
whole point of the inspector is that it finds more.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.output.inventory import build_unified_inventory  # noqa: E402
from modules.tableau import inspector_adapter  # noqa: E402
from modules.tableau import parser as legacy  # noqa: E402

WB = sys.argv[1] if len(sys.argv) > 1 else "/Users/hmahakali/Downloads/09. Finance/FBR (New).twb"

old = build_unified_inventory(legacy.parse_workbook(WB), "tableau")
new = build_unified_inventory(inspector_adapter.parse_workbook(WB), "tableau")

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


print("=== inventory contract ===")
missing = set(old) - set(new)
check("no top-level key lost", not missing, f"missing {sorted(missing)}")

for key in sorted(set(old) & set(new)):
    check(f"{key}: type preserved ({type(old[key]).__name__})",
          type(old[key]) is type(new[key]),
          f"{type(old[key]).__name__} -> {type(new[key]).__name__}")

print("\n=== item schemas ===")
for coll in ("dimensions", "facts", "metrics", "tables", "relationships", "flagged"):
    o, n = old.get(coll) or [], new.get(coll) or []
    if not o or not n:
        print(f"  SKIP  {coll} (old={len(o)} new={len(n)})")
        continue
    lost = set(o[0]) - set(n[0])
    check(f"{coll}[0] keeps every key", not lost, f"lost {sorted(lost)}")

print("\n=== coverage (inspector should find at least as much) ===")
# Tables are compared as *distinct* names: the legacy parser did not de-duplicate
# relations, so on published-datasource (sqlproxy) workbooks it reported the same
# placeholder table N times. A lower raw count from the adapter is de-duplication,
# not lost information.
for coll in ("dimensions", "facts", "metrics"):
    o, n = len(old.get(coll) or []), len(new.get(coll) or [])
    check(f"{coll}: {o} -> {n}", n >= o, f"regressed by {o - n}")

o_t = {t["name"] for t in old.get("tables") or []}
n_t = {t["name"] for t in new.get("tables") or []}
check(f"tables: {len(o_t)} -> {len(n_t)} distinct", len(n_t) >= len(o_t),
      f"lost {sorted(o_t - n_t)}")

print("\n=== table identifiers stay clean ===")
# _from_tableau normalizes with .strip("[]").replace(".", "_"), which mangles a
# fully-qualified [DB].[SCHEMA].[TABLE] into DB]_[SCHEMA]_[TABLE. The adapter must
# hand it a bare table name.
bad = [t["name"] for t in new.get("tables") or [] if "[" in t["name"] or "]" in t["name"]]
check("no brackets survive into table names", not bad, f"{bad[:4]}")

print("\n=== additive payload ===")
insp = inspector_adapter.parse_workbook(WB).get("inspection", {})
check("inspection payload present", bool(insp))
for k in ("translation", "visual_styles", "layout", "filters", "worksheet_marks"):
    check(f"inspection carries {k}", k in insp)

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All contract checks passed.'}")
sys.exit(1 if fails else 0)
