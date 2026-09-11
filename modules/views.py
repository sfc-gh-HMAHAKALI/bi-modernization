"""Propose semantic-view groupings from observed BI usage.

The question "how many semantic views, and which tables in each" has no answer
from the data model alone. One-per-domain sounds right on paper but is often
wrong in practice: it can be one view per domain, or one view spanning several,
and the deciding evidence is how the BI layer actually queries the data. The
dashboards ARE that evidence -- tables that appear together on a dashboard get
joined together in real queries, so they belong in one view.

So nothing here invents a rule. It reads which tables co-occur on a dashboard,
groups those, and reports the evidence behind each group so the proposal can be
argued with. When the evidence is thin it falls back to one view per table, which
is the simplest correct answer rather than a guess dressed up as a
recommendation.

Grouping reuses `output.si_agent`'s union-find rather than reimplementing it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common.logger import get_logger

log = get_logger("views")

_COLLECTIONS = ("dimensions", "facts", "metrics")


def _table_of(item: dict) -> str:
    return str(item.get("table", "")).upper()


def _split(value: Any) -> list[str]:
    """Split a comma-joined provenance field into parts."""
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def build_evidence(inventory: dict) -> dict[str, Any]:
    """Summarise what the BI layer tells us about how tables are used together."""
    tables = [str(t.get("name", "")).upper() for t in inventory.get("tables", []) if t.get("name")]

    cols_per_table: dict[str, dict[str, int]] = defaultdict(
        lambda: {"dimensions": 0, "facts": 0, "metrics": 0})
    dash_tables: dict[str, set[str]] = defaultdict(set)
    table_dashboards: dict[str, set[str]] = defaultdict(set)
    page_tables: dict[str, set[str]] = defaultdict(set)
    table_sources: dict[str, set[str]] = defaultdict(set)

    for coll in _COLLECTIONS:
        for item in inventory.get(coll, []):
            tbl = _table_of(item)
            if not tbl:
                continue
            cols_per_table[tbl][coll] += 1
            for src in _split(item.get("source_file")):
                table_sources[tbl].add(src)
            for dash in _split(item.get("dashboard_name")):
                dash_tables[dash].add(tbl)
                table_dashboards[tbl].add(dash)
            for page in _split(item.get("page_name")):
                page_tables[page].add(tbl)

    reached = {t for t, d in table_dashboards.items() if d}
    return {
        "tables": tables,
        "columns_per_table": {k: dict(v) for k, v in cols_per_table.items()},
        "dashboard_tables": {k: sorted(v) for k, v in dash_tables.items()},
        "table_dashboards": {k: sorted(v) for k, v in table_dashboards.items()},
        "page_tables": {k: sorted(v) for k, v in page_tables.items()},
        "table_sources": {k: sorted(v) for k, v in table_sources.items()},
        # Tables no dashboard touches. Translating them creates parity
        # obligations for things nobody looks at, so they are called out rather
        # than quietly folded into a view.
        "unreached_tables": sorted(set(tables) - reached),
        "has_usage_evidence": bool(dash_tables or page_tables),
    }


def _name_group(group: list[str], evidence: dict) -> str:
    """Name a view after its largest table, which is predictable and stable.

    Naming after the busiest dashboard was tried and produced names like "Other"
    and "Raw Data" -- accurate to the workbook, useless as a Snowflake object name.
    """
    per = evidence["columns_per_table"]

    def weight(t: str) -> int:
        c = per.get(t, {})
        return c.get("dimensions", 0) + c.get("facts", 0) + c.get("metrics", 0)

    return max(sorted(group), key=weight) if group else "SEMANTIC_VIEW"


def propose(inventory: dict, *, strategy: str = "auto") -> dict[str, Any]:
    """Propose view groupings.

    strategy:
      auto        co-usage grouping when the dashboards support it, else 1:1
      co-usage    force co-usage grouping
      one-per-table  force 1:1 replication
    """
    evidence = build_evidence(inventory)
    all_tables = set(evidence["tables"])

    if strategy == "one-per-table" or (strategy == "auto" and not evidence["has_usage_evidence"]):
        used = "one-per-table"
        groups = [[t] for t in sorted(all_tables)]
        if strategy == "auto":
            log.info("no dashboard usage evidence; falling back to one view per table")
    else:
        used = "co-usage"
        from .output.si_agent import _merge_overlapping_clusters

        page_tables = {k: set(v) for k, v in evidence["page_tables"].items()}
        if not page_tables:
            page_tables = {k: set(v) for k, v in evidence["dashboard_tables"].items()}

        # Only cluster tables that actually have usage evidence. Passing the full
        # table set makes si_agent sweep the leftovers into a single "Other"
        # domain, which asserts a relationship between tables that nothing
        # supports -- the opposite of grouping from evidence. Unplaced tables are
        # handled below, each on its own.
        evidenced = {t for tbls in page_tables.values() for t in tbls} & all_tables
        domains = _merge_overlapping_clusters(page_tables, evidenced)
        groups = [sorted({t.upper() for t in tbls}) for tbls in domains.values() if tbls]

        # Anything union-find did not place still needs a home, or it silently
        # disappears from every generated view. One view each: with no evidence
        # that they are queried together, that is the only defensible default.
        placed = {t for g in groups for t in g}
        for orphan in sorted(all_tables - placed):
            groups.append([orphan])

    views = []
    empty: list[str] = []
    for group in sorted(groups, key=lambda g: (-len(g), g)):
        per = evidence["columns_per_table"]
        counts = {"dimensions": 0, "facts": 0, "metrics": 0}
        for t in group:
            for k in counts:
                counts[k] += per.get(t, {}).get(k, 0)

        # A view with no columns is not a view. These are usually join-only or
        # placeholder tables; proposing one would just fail at generation time.
        if sum(counts.values()) == 0:
            empty.extend(group)
            continue

        dashboards = sorted({d for t in group for d in evidence["table_dashboards"].get(t, [])})
        sources = sorted({s for t in group for s in evidence["table_sources"].get(t, [])})

        views.append({
            "name": f"{_name_group(group, evidence)}_SEMANTIC_VIEW",
            "tables": group,
            "evidence": {
                # Why these tables are together, in the terms the decision was made in.
                # Counts are by *column contribution*: a workbook or dashboard that
                # references a table without using any of its columns adds nothing
                # to the view, so counting it would overstate the evidence.
                "dashboards": dashboards,
                "dashboard_count": len(dashboards),
                "workbooks": sources,
                "workbook_count": len(sources),
                "columns": counts,
                "column_total": sum(counts.values()),
                "tables_with_no_dashboard": [
                    t for t in group if not evidence["table_dashboards"].get(t)
                ],
            },
        })

    notes: list[str] = []
    if empty:
        notes.append(
            f"{len(empty)} table(s) carry no columns and were left out: "
            f"{', '.join(sorted(empty)[:6])}"
            f"{' ...' if len(empty) > 6 else ''}. Usually join-only or placeholder tables."
        )
    if used == "one-per-table":
        notes.append(
            "No dashboard-to-table usage evidence was available, so this is 1:1 "
            "replication -- one view per table. That is the simplest correct "
            "answer, not a recommendation; consolidate by hand if you know the model."
        )
    if evidence["unreached_tables"]:
        notes.append(
            f"{len(evidence['unreached_tables'])} table(s) are not used by any "
            f"dashboard: {', '.join(evidence['unreached_tables'][:6])}"
            f"{' ...' if len(evidence['unreached_tables']) > 6 else ''}. "
            "Confirm before including them."
        )
    # A view spanning many workbooks is the consolidation win worth naming.
    spanning = [v for v in views if v["evidence"]["workbook_count"] > 1]
    if spanning:
        notes.append(
            f"{len(spanning)} proposed view(s) span more than one workbook, which "
            "is where consolidating beats migrating workbook-by-workbook."
        )

    return {
        "strategy": used,
        "view_count": len(views),
        "views": views,
        "notes": notes,
        "unreached_tables": evidence["unreached_tables"],
    }


def format_proposal(proposal: dict) -> str:
    """Human-readable proposal, for review before anything is generated."""
    lines = [
        "Proposed semantic views",
        "",
        f"  Strategy: {proposal['strategy']}"
        + ("  (grouped by tables used together on a dashboard)"
           if proposal["strategy"] == "co-usage" else "  (1:1 replication)"),
        f"  Views:    {proposal['view_count']}",
        "",
    ]
    for v in proposal["views"]:
        e = v["evidence"]
        lines.append(f"  {v['name']}")
        lines.append(f"    tables    {', '.join(v['tables'])}")
        lines.append(f"    columns   {e['column_total']} "
                     f"({e['columns']['dimensions']}d / {e['columns']['facts']}f / "
                     f"{e['columns']['metrics']}m)")
        if e["dashboards"]:
            shown = ", ".join(e["dashboards"][:4])
            more = f" (+{len(e['dashboards']) - 4} more)" if len(e["dashboards"]) > 4 else ""
            lines.append(f"    used by   {e['dashboard_count']} dashboard(s): {shown}{more}")
        else:
            lines.append("    used by   no dashboard")
        if e["workbook_count"] > 1:
            # Say what is being counted. A workbook can reference a table as a
            # relation while contributing no columns to it, so "spans N
            # workbooks" alone invites a reviewer to count differently and
            # conclude the number is wrong.
            lines.append(f"    spans     {e['workbook_count']} workbooks "
                         f"(contributing columns)")
        if e["tables_with_no_dashboard"]:
            lines.append(f"    unused    {', '.join(e['tables_with_no_dashboard'])}")
        lines.append("")
    for n in proposal["notes"]:
        lines.append(f"  ! {n}")
    if proposal["notes"]:
        lines.append("")
    return "\n".join(lines)
