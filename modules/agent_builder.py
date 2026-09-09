"""
Agent builder — extends semantic-extraction's si_agent.py with:
  1. Cortex Search tool support
  2. Human-readable preview summary for user review before deployment
  3. Domain count / tool budget enforcement (warn if > 10)

Delegates the base agent spec / domain grouping / DDL generation to
the semantic-extraction si-agent CLI (called as a subprocess) then
post-processes the output to add Cortex Search tools and preview text.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

_SEM_EX_DIR = Path.home() / ".snowflake" / "cortex" / "skills" / "semantic-extraction"


def _run_si_agent(inventory_path: str, agent_name: str, database: str, schema: str,
                  output_dir: str, assess_only: bool = False,
                  explicit_domains: dict | None = None) -> dict:
    """
    Call semantic-extraction's si-agent CLI as a subprocess.
    Returns the parsed JSON output.
    """
    if not _SEM_EX_DIR.exists():
        raise RuntimeError(
            f"semantic-extraction skill not found at {_SEM_EX_DIR}. "
            "Please install it before using bi-modernization."
        )

    cmd = [
        sys.executable, "-m", "modules.cli", "si-agent",
        inventory_path,
        "--agent-name", agent_name,
        "--database", database,
        "--schema", schema,
        "--output", output_dir,
    ]
    if assess_only:
        cmd.append("--assess-only")
    if explicit_domains:
        cmd += ["--domains", json.dumps(explicit_domains)]

    result = subprocess.run(
        cmd,
        cwd=str(_SEM_EX_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"si-agent returned non-JSON output.\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )


# ---------------------------------------------------------------------------
# Cortex Search tool helpers
# ---------------------------------------------------------------------------

def _build_search_tool(service_fqn: str, index: int) -> dict:
    """Build a cortex_search tool spec dict for one Cortex Search service."""
    parts = service_fqn.split(".")
    name_hint = parts[-1] if parts else f"SearchService{index}"
    # CamelCase tool name
    tool_name = "Search" + "".join(w.title() for w in name_hint.replace("_", " ").split())
    return {
        "tool_spec": {
            "type": "cortex_search",
            "name": tool_name,
            "description": (
                f"Search unstructured content and metadata using {name_hint}. "
                "Use this tool when the user asks about documentation, report descriptions, "
                "data definitions, or any question that doesn't require an aggregate SQL query."
            ),
        },
        "_service_fqn": service_fqn,  # kept for tool_resources; stripped before final spec
        "_tool_name": tool_name,
    }


def _inject_search_tools(spec: dict, search_services: list[str]) -> dict:
    """Add Cortex Search tools to an existing agent spec dict (in-place)."""
    for i, svc in enumerate(search_services):
        tool = _build_search_tool(svc, i)
        tool_name = tool["_tool_name"]
        svc_fqn = tool["_service_fqn"]

        spec["tools"].append(
            {"tool_spec": tool["tool_spec"]}
        )
        spec.setdefault("tool_resources", {})[tool_name] = {
            "cortex_search_service": svc_fqn
        }
    return spec


def _search_tool_sql(search_services: list[str]) -> list[str]:
    """Return SQL statements to create or verify Cortex Search services."""
    stmts = []
    for svc in search_services:
        parts = svc.split(".")
        if len(parts) == 3:
            stmts.append(
                f"-- VERIFY Cortex Search service exists:\n"
                f"SHOW CORTEX SEARCH SERVICES LIKE '{parts[2]}' IN SCHEMA {parts[0]}.{parts[1]};"
            )
        else:
            stmts.append(f"-- VERIFY: SHOW CORTEX SEARCH SERVICES;  -- check {svc}")
    return stmts


# ---------------------------------------------------------------------------
# Human-readable preview
# ---------------------------------------------------------------------------

def _format_assessment(assessment: dict) -> str:
    """Format the si-agent assessment dict into a human-readable preview."""
    lines = [
        "",
        "Domain Assessment:",
        f"  Total tables:   {assessment.get('total_tables', 0)}",
        f"  Total columns:  {assessment.get('total_columns', 0)}",
        f"  Domains:        {assessment.get('domain_count', 0)}",
        f"  Feasible:       {'Yes' if assessment.get('feasible') else 'No'}",
        "",
        f"  {'Domain':<30} {'Tables':>6} {'Cols':>5} {'Dims':>5} {'Facts':>5} {'Metrics':>7} {'Status'}",
        f"  {'-'*30} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*7} {'-'*10}",
    ]
    for d in assessment.get("domains", []):
        status = "OVER LIMIT" if d.get("exceeds_limit") else "OK"
        lines.append(
            f"  {d['domain'][:30]:<30} {d.get('table_count',0):>6} "
            f"{d.get('columns',0):>5} {d.get('dimensions',0):>5} "
            f"{d.get('facts',0):>5} {d.get('metrics',0):>7} {status}"
        )
    for w in assessment.get("warnings", []):
        lines.append(f"\n  ⚠  {w}")
    lines.append("")
    return "\n".join(lines)


def _format_preview(spec: dict, domains: dict, search_services: list[str]) -> str:
    """
    Format a concise human-readable summary of the proposed agent configuration
    for display to the user before they approve deployment.
    """
    agent_name = spec.get("_agent_fqn", "AGENT")

    analyst_tools = [
        t["tool_spec"] for t in spec.get("tools", [])
        if t.get("tool_spec", {}).get("type") == "cortex_analyst_text_to_sql"
    ]
    search_tools = [
        t["tool_spec"] for t in spec.get("tools", [])
        if t.get("tool_spec", {}).get("type") == "cortex_search"
    ]

    lines = [
        "",
        "┌─────────────────────────────────────────────────────────┐",
        f"  Proposed Cortex Agent: {agent_name}",
        "└─────────────────────────────────────────────────────────┘",
        "",
        f"  Cortex Analyst tools  ({len(analyst_tools)})  — text-to-SQL, one per domain:",
    ]

    resources = spec.get("tool_resources", {})
    for i, t in enumerate(analyst_tools, 1):
        tname = t.get("name", "")
        sv = resources.get(tname, {}).get("semantic_view", "")
        domain = tname.replace("Query", "").replace("Data", "").replace("_", " ")
        lines.append(f"    {i}. {tname:<35} → {sv or domain}")

    if search_tools:
        lines.append("")
        lines.append(f"  Cortex Search tools  ({len(search_tools)})  — semantic search:")
        for i, t in enumerate(search_tools, 1):
            tname = t.get("name", "")
            svc = resources.get(tname, {}).get("cortex_search_service", "")
            lines.append(f"    {i}. {tname:<35} → {svc}")
    else:
        lines.append("")
        lines.append("  Cortex Search tools  (0)  — none attached")
        lines.append("    (add --search-services DB.SCH.SVC to include search)")

    total_tools = len(analyst_tools) + len(search_tools)
    if total_tools > 10:
        lines.append("")
        lines.append(f"  ⚠  {total_tools} tools exceeds the recommended maximum of 10.")
        lines.append("     Consider splitting into multiple agents by domain.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(
    inventory_path: str,
    output_dir: str | None = None,
    agent_name: str = "BI_ANALYTICS_AGENT",
    database: str = "TARGET_DB",
    schema: str = "PUBLIC",
    search_services: list[str] | None = None,
    explicit_domains: dict | None = None,
    assess_only: bool = False,
) -> dict[str, Any]:
    """
    Build Cortex Agent artifacts from an enriched inventory.

    Returns structured result dict with:
        status, preview_text, spec (dict), deployment_sql (list[str]),
        output_files (if output_dir provided), domain_count, tool_count
    """
    import tempfile

    # Use a temp dir for si-agent output if none provided
    _tmp = None
    if output_dir is None and not assess_only:
        _tmp = tempfile.mkdtemp(prefix="bim_agent_")
        output_dir = _tmp

    work_dir = output_dir or tempfile.mkdtemp(prefix="bim_agent_assess_")

    # --- Call semantic-extraction si-agent via subprocess ---
    si_result = _run_si_agent(
        inventory_path=inventory_path,
        agent_name=agent_name,
        database=database,
        schema=schema,
        output_dir=work_dir,
        assess_only=assess_only,
        explicit_domains=explicit_domains,
    )

    if si_result.get("status") == "error":
        return {**si_result, "command": "build-agent"}

    # --- Assessment only path ---
    if assess_only:
        assessment = si_result.get("assessment") or {}
        preview = _format_assessment(assessment)
        return {
            "status": "ok",
            "command": "build-agent",
            "mode": "assess-only",
            "assessment": assessment,
            "preview": preview,
        }

    # --- Read generated spec from file ---
    spec_file = si_result.get("agent_spec")
    sql_file  = si_result.get("deployment_sql")

    if not spec_file or not Path(spec_file).exists():
        return {"status": "error", "command": "build-agent",
                "error": f"si-agent did not produce spec file: {si_result}"}

    with open(spec_file) as fh:
        spec = json.load(fh)

    with open(sql_file) as fh:
        statements = fh.read().splitlines()

    spec["_agent_fqn"] = f"{database}.{schema}.{agent_name}"

    # --- Inject Cortex Search tools ---
    search_svcs = search_services or []
    if search_svcs:
        spec = _inject_search_tools(spec, search_svcs)

    # Append Search service verification SQL
    if search_svcs:
        statements += _search_tool_sql(search_svcs)

    # Write updated spec back (with search tools if any)
    clean_spec = {k: v for k, v in spec.items() if not k.startswith("_")}
    Path(spec_file).write_text(json.dumps(clean_spec, indent=2))
    with open(sql_file, "w") as fh:
        fh.write("\n".join(statements))

    # --- Preview ---
    domains = si_result.get("domains", [])
    preview = _format_preview(spec, {d: [] for d in domains}, search_svcs)
    analyst_tool_count = sum(
        1 for t in clean_spec.get("tools", [])
        if t.get("tool_spec", {}).get("type") == "cortex_analyst_text_to_sql"
    )
    search_tool_count = sum(
        1 for t in clean_spec.get("tools", [])
        if t.get("tool_spec", {}).get("type") == "cortex_search"
    )

    result: dict[str, Any] = {
        "status": "ok",
        "command": "build-agent",
        "agent_name": f"{database}.{schema}.{agent_name}",
        "domain_count": len(domains),
        "domains": domains,
        "analyst_tool_count": analyst_tool_count,
        "search_tool_count": search_tool_count,
        "total_tool_count": analyst_tool_count + search_tool_count,
        "preview": preview,
        "spec": clean_spec,
        "deployment_sql": statements,
        "output_files": [spec_file, sql_file],
    }

    return result
