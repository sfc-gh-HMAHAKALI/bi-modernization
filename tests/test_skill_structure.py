"""Structural gates on the skill documents themselves.

The bundled skill-development guidance sets hard limits, and breaking them
degrades the agent silently rather than loudly: past 500 lines it starts dropping
earlier instructions, and an unreachable reference is one the router can never
lead it to. This suite makes both mechanically checkable, because both were
already broken once -- SKILL.md reached 903 lines and 13 of 15 references were
orphaned.

Run: python3 tests/test_skill_structure.py
"""

from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"  PASS  {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' -> ' + detail) if detail else ''}")


ROUTER = Path("SKILL.md")
SUBS = sorted(Path(p) for p in glob.glob("*/SKILL.md"))
ALL_DOCS = [ROUTER] + SUBS
TEXT = "\n".join(p.read_text() for p in ALL_DOCS)


def nlines(p: Path) -> int:
    return len(p.read_text().split("\n"))


print("=== length limits ===")
# Router < 200: a fat router defeats the point of splitting, since it is loaded
# on every invocation.
check(f"router is under 200 lines ({nlines(ROUTER)})", nlines(ROUTER) < 200)
for p in SUBS:
    check(f"{p} is under 500 lines ({nlines(p)})", nlines(p) < 500)

print("\n=== reachability: every reference is routed to ===")
refs = sorted(os.path.basename(p) for p in glob.glob("references/*.md"))
check(f"references exist ({len(refs)})", bool(refs))
orphans = [r for r in refs if r not in TEXT]
check("no orphaned references", not orphans, str(orphans))

print("\n=== no dangling pointers ===")
cited_refs = set(re.findall(r"`(references/[\w.\-]+\.md)`", TEXT))
cited_subs = set(re.findall(r"`([\w\-]+/SKILL\.md)`", TEXT))
cited_scripts = set(re.findall(r"`?(scripts/[\w.\-]+\.py)`?", TEXT))
missing = sorted(p for p in (cited_refs | cited_subs | cited_scripts)
                 if not Path(p).exists())
check(f"every cited path exists ({len(cited_refs | cited_subs | cited_scripts)} cited)",
      not missing, str(missing))

print("\n=== router routes to every sub-skill ===")
router_text = ROUTER.read_text()
for p in SUBS:
    check(f"router mentions {p}", str(p) in router_text)

print("\n=== every sub-skill has frontmatter and a return path ===")
for p in SUBS:
    t = p.read_text()
    check(f"{p.parent.name}: has frontmatter", t.startswith("---\n"))
    check(f"{p.parent.name}: declares a name", bool(re.search(r"^name:\s*\S", t, re.M)))
    # Termination: a path that trails off leaves the agent with nowhere to go.
    check(f"{p.parent.name}: routes back when done", "router" in t.lower())

print("\n=== documented CLI commands are real ===")
sys.path.insert(0, str(ROOT))
from modules import cli  # noqa: E402

parser = cli._build_parser()
real: set[str] = set()
for action in parser._actions:
    if getattr(action, "dest", "") == "command" and getattr(action, "choices", None):
        real = set(action.choices)

cited_cmds = set(re.findall(r"modules\.cli\s+([a-z][a-z\-]*)", TEXT))
cited_cmds.discard("--help")
check(f"commands cited in docs ({len(cited_cmds)})", bool(cited_cmds))
unreal = sorted(cited_cmds - real)
check("every documented command exists in the CLI", not unreal, str(unreal))

print("\n=== no duplicated authority ===")
# The translation table lives in references/calculation-translation.md. Copying it
# back into a SKILL.md is how the two drift apart.
skill_has_table = sum(1 for p in ALL_DOCS
                      if "{FIXED [Dim] : SUM([M])}" in p.read_text())
check("the LOD translation table is not duplicated into a SKILL.md",
      skill_has_table == 0)
check("the translation reference still carries it",
      "NULLIF" in Path("references/calculation-translation.md").read_text())

print(f"\n{'FAILED: ' + str(fails) + ' check(s)' if fails else 'All skill structure checks passed.'}")
sys.exit(1 if fails else 0)
