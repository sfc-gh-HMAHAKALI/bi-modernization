"""Multi-source parsing: source resolution, resumability, failure isolation.

These paths are easy to get subtly wrong in ways that stay invisible: a glob that
silently matches nothing, a checkpoint that restarts from scratch, or an
unreadable workbook counted as a success and contributing nothing to the merged
inventory. Each of those is asserted here.

Run: python3 tests/test_portfolio.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.portfolio import parse_portfolio, resolve_sources  # noqa: E402

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


WORK = Path(tempfile.mkdtemp(prefix="bim_portfolio_"))
SRC = WORK / "src"
(SRC / "nested").mkdir(parents=True)

# Minimal but structurally valid Tableau workbooks. Content does not matter to
# resolution; only extension and readability do.
WB = """<?xml version='1.0'?>
<workbook><datasources><datasource name='ds' caption='DS'>
<connection class='snowflake'/>
<column name='[A]' caption='A' datatype='string' role='dimension'/>
</datasource></datasources><worksheets/><dashboards/></workbook>"""

for n in ("a.twb", "b.twb", "c.tds"):
    (SRC / n).write_text(WB)
(SRC / "nested" / "d.twb").write_text(WB)
(SRC / "notes.txt").write_text("not a source")

print("=== source resolution ===")
files, notes = resolve_sources(str(SRC), "tableau")
check(f"directory finds all 4 sources recursively, got {len(files)}", len(files) == 4)
check("non-source files excluded", not any(f.endswith(".txt") for f in files))

files, _ = resolve_sources(str(SRC), "tableau", recurse=False)
check(f"no-recurse stays at top level, got {len(files)}", len(files) == 3)

files, notes = resolve_sources(str(SRC / "*.twb"), "tableau")
check(f"glob matches 2 .twb at top level, got {len(files)}", len(files) == 2)

files, notes = resolve_sources(str(SRC / "**" / "*.twb"), "tableau")
check(f"recursive glob matches 3 .twb, got {len(files)}", len(files) == 3)

files, notes = resolve_sources(str(SRC / "a.twb"), "tableau")
check("a single file resolves to itself", files == [str(SRC / "a.twb")])

# A glob matching nothing must SAY so rather than quietly returning empty.
files, notes = resolve_sources(str(SRC / "*.nope"), "tableau")
check("empty glob reports a note", not files and any("no" in n for n in notes), str(notes))

files, notes = resolve_sources(str(SRC), "tableau", max_files=2)
check(f"max_files caps the run, got {len(files)}", len(files) == 2)
check("hitting the cap is reported, not silent", any("cap" in n for n in notes), str(notes))

try:
    resolve_sources(str(WORK / "does_not_exist"), "tableau")
    check("a missing directory raises", False, "no exception")
except FileNotFoundError:
    check("a missing directory raises", True)

print("\n=== failure isolation ===")


def fake_parse(path: str, source_type: str) -> dict:
    """Succeeds unless the filename says otherwise."""
    if "broken" in path:
        raise ValueError("unreadable")
    return {"datasources": [{"name": "ds", "tables": [{"name": "T"}],
                             "columns": [{"name": "c", "caption": "c",
                                          "role": "dimension"}]}],
            "worksheets": [], "dashboards": [], "parameters": [], "errors": []}


def fake_build(parsed: dict, source_type: str, sf_target) -> dict:
    return {"source_type": source_type, "tables": [{"name": "T"}],
            "dimensions": [{"name": "c", "table": "T"}], "facts": [], "metrics": [],
            "relationships": [], "filters": [], "flagged": [], "errors": [],
            "complexity_summary": {}}


(SRC / "broken.twb").write_text("not xml")
paths = [str(SRC / "a.twb"), str(SRC / "broken.twb"), str(SRC / "b.twb")]
inv_dir = str(WORK / "inv1")
run = parse_portfolio(paths, "tableau", parse_one=fake_parse,
                      build_inventory=fake_build, inventory_dir=inv_dir,
                      state_path=str(WORK / "s1.json"))
check(f"good sources still parsed, got {run['ok_count']}", run["ok_count"] == 2)
check("the bad source is reported failed", run["failed_count"] == 1)
check("failure carries a cause", "unreadable" in run["failed"][0]["error"])
check("one bad source does not stop the rest",
      len(run["inventories"]) == 2, f"{len(run['inventories'])} inventories")
check("each inventory records its source file",
      all(i.get("source_file") for i in run["inventories"]))

print("\n=== resumability ===")
state_path = str(WORK / "s2.json")
inv_dir = str(WORK / "inv2")
paths = [str(SRC / n) for n in ("a.twb", "b.twb", "c.tds")]

run1 = parse_portfolio(paths, "tableau", parse_one=fake_parse,
                       build_inventory=fake_build, inventory_dir=inv_dir,
                       state_path=state_path)
check(f"first run parses all 3, got {run1['ok_count']}", run1["ok_count"] == 3)
check("state file written", Path(state_path).is_file())

state = json.loads(Path(state_path).read_text())
check(f"state records all 3 processed, got {len(state['processed'])}",
      len(state["processed"]) == 3)
check("state clears `current` on clean finish", state["current"] is None)

# Second run must reuse the checkpoint rather than reparse.
calls: list[str] = []


def counting_parse(path: str, source_type: str) -> dict:
    calls.append(path)
    return fake_parse(path, source_type)


run2 = parse_portfolio(paths, "tableau", parse_one=counting_parse,
                       build_inventory=fake_build, inventory_dir=inv_dir,
                       state_path=state_path)
check(f"resume reparses nothing, got {len(calls)} call(s)", not calls)
check("resume still yields every inventory", len(run2["inventories"]) == 3)
check("resumed sources are marked", all(s.get("resumed") for s in run2["sources"]))

# Partial checkpoint: drop one entry and confirm only that one is reparsed.
state = json.loads(Path(state_path).read_text())
dropped = paths[1]
del state["processed"][dropped]
Path(state_path).write_text(json.dumps(state))
calls.clear()
run3 = parse_portfolio(paths, "tableau", parse_one=counting_parse,
                       build_inventory=fake_build, inventory_dir=inv_dir,
                       state_path=state_path)
check(f"only the missing source is reparsed, got {len(calls)}", calls == [dropped],
      str([Path(c).name for c in calls]))
check("all 3 inventories present after partial resume", len(run3["inventories"]) == 3)

# resume=False must ignore a valid checkpoint entirely.
calls.clear()
parse_portfolio(paths, "tableau", parse_one=counting_parse,
                build_inventory=fake_build, inventory_dir=inv_dir,
                state_path=state_path, resume=False)
check(f"resume=False reparses everything, got {len(calls)}", len(calls) == 3)

# A corrupt checkpoint must not block the run.
Path(state_path).write_text("{ this is not json")
calls.clear()
run4 = parse_portfolio(paths, "tableau", parse_one=counting_parse,
                       build_inventory=fake_build, inventory_dir=inv_dir,
                       state_path=state_path)
check("a corrupt checkpoint is ignored, not fatal", run4["ok_count"] == 3)

# Switching source type must not reuse another type's checkpoint.
calls.clear()
run5 = parse_portfolio(paths, "looker", parse_one=counting_parse,
                       build_inventory=fake_build, inventory_dir=inv_dir,
                       state_path=state_path)
check(f"a different source type does not reuse the checkpoint, got {len(calls)}",
      len(calls) == 3)

shutil.rmtree(WORK, ignore_errors=True)

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All portfolio tests passed.'}")
sys.exit(1 if fails else 0)
