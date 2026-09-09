"""
bi-modernization CLI — entry point for all code-generation commands.

Usage:
    python3 -m modules.cli <command> [options]

Commands:
    enrich-charts       Enrich inventory.json with chart type per sheet/visual
    generate-streamlit  Generate multi-page Streamlit-in-Snowflake app
    generate-react      Generate Next.js + ECharts app for Snowflake App Runtime
    build-agent         Build Cortex Agent spec + deployment SQL (extends si_agent)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".snowflake" / "cortex" / "logs" / "bi-modernization"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "run.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("bi-modernization")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit(result: dict) -> None:
    """Write JSON result to stdout. Errors always include a non-zero exit."""
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") == "error":
        sys.exit(1)


def _auto_output_dir(command: str, suffix: str = "") -> str:
    """Generate a timestamped output directory in ~/Downloads."""
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"bim_{command}{('_' + suffix) if suffix else ''}_{ts}"
    path = Path.home() / "Downloads" / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# ---------------------------------------------------------------------------
# Command: enrich-charts
# ---------------------------------------------------------------------------

def cmd_enrich_charts(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    from .chart_extractor import enrich

    # Expand glob patterns in source_files
    import glob
    source_files: list[str] = []
    for pattern in (args.source_files or []):
        expanded = glob.glob(pattern, recursive=True)
        source_files.extend(expanded if expanded else [pattern])

    output = args.output or args.input.replace(".json", "_enriched.json")

    try:
        result = enrich(
            inventory_path=args.input,
            source_files=source_files,
            output_path=output,
        )
        result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        _emit(result)
    except Exception as exc:
        logger.exception("enrich-charts failed")
        _emit({"status": "error", "command": "enrich-charts", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: generate-streamlit
# ---------------------------------------------------------------------------

def cmd_generate_streamlit(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    from .streamlit_generator import generate

    output_dir = args.output or _auto_output_dir("streamlit")
    dashboards_filter = (
        [d.strip() for d in args.dashboards.split(",")]
        if args.dashboards else None
    )

    try:
        result = generate(
            inventory_path=args.input,
            output_dir=output_dir,
            semantic_view=args.semantic_view,
            embed_agent=args.embed_agent,
            dashboards_filter=dashboards_filter,
        )
        result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

        # Print human-readable summary to stderr
        print(
            f"\nStreamlit app generated: {output_dir}\n"
            f"  Dashboards: {result['dashboard_count']}\n"
            f"  Files: {len(result['generated_files'])}\n"
            f"  Embed agent: {result['embed_agent'] or 'no'}\n",
            file=sys.stderr,
        )
        _emit(result)
    except Exception as exc:
        logger.exception("generate-streamlit failed")
        _emit({"status": "error", "command": "generate-streamlit", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: generate-react
# ---------------------------------------------------------------------------

def cmd_generate_react(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    from .react_generator import generate

    output_dir = args.output or _auto_output_dir("react", args.app_name.lower().replace(" ", "_"))
    dashboards_filter = (
        [d.strip() for d in args.dashboards.split(",")]
        if args.dashboards else None
    )

    try:
        result = generate(
            inventory_path=args.input,
            output_dir=output_dir,
            app_name=args.app_name or "BI Suite",
            semantic_view=args.semantic_view,
            embed_agent=args.embed_agent,
            dashboards_filter=dashboards_filter,
        )
        result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

        print(
            f"\nReact app generated: {output_dir}\n"
            f"  Dashboards: {result['dashboard_count']}\n"
            f"  Files generated: {result['file_count']}\n"
            f"  Embed agent: {result['embed_agent'] or 'no'}\n"
            f"\nNext steps:\n"
            f"  cd {output_dir}\n"
            f"  npm install\n"
            f"  # Then use the snowflake-apps skill to deploy via `snow app deploy`\n",
            file=sys.stderr,
        )
        _emit(result)
    except Exception as exc:
        logger.exception("generate-react failed")
        _emit({"status": "error", "command": "generate-react", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: build-agent
# ---------------------------------------------------------------------------

def cmd_build_agent(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    from .agent_builder import build

    search_services = (
        [s.strip() for s in args.search_services.split(",")]
        if args.search_services else []
    )

    explicit_domains = None
    if args.domains:
        try:
            explicit_domains = json.loads(args.domains)
        except json.JSONDecodeError as exc:
            _emit({"status": "error", "command": "build-agent",
                   "error": f"--domains must be valid JSON: {exc}"})
            return

    output_dir = args.output if not args.assess_only else None
    if not output_dir and not args.assess_only:
        output_dir = _auto_output_dir("agent", args.agent_name.lower())

    try:
        result = build(
            inventory_path=args.input,
            output_dir=output_dir,
            agent_name=args.agent_name or "BI_ANALYTICS_AGENT",
            database=args.database or "TARGET_DB",
            schema=args.schema or "PUBLIC",
            search_services=search_services,
            explicit_domains=explicit_domains,
            assess_only=args.assess_only,
        )
        result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

        # Always print the human-readable preview to stderr
        if result.get("preview"):
            print(result["preview"], file=sys.stderr)

        if result.get("output_files"):
            print(
                f"\nAgent artifacts written to: {output_dir}\n"
                f"  {result['agent_name']}\n"
                f"  Analyst tools: {result.get('analyst_tool_count', 0)}\n"
                f"  Search tools:  {result.get('search_tool_count', 0)}\n"
                f"\nNext step: invoke the agent-studio skill to deploy and test.\n",
                file=sys.stderr,
            )

        # Don't include full spec in stdout when assessing (too verbose)
        if args.assess_only:
            result.pop("spec", None)
            result.pop("deployment_sql", None)

        _emit(result)
    except Exception as exc:
        logger.exception("build-agent failed")
        _emit({"status": "error", "command": "build-agent", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: preview
# ---------------------------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> None:
    """
    Generate a local preview of the Streamlit or React app using synthetic data.
    Launches the app in the background and opens a browser.
    """
    t0 = time.perf_counter()
    from .preview import (
        generate_streamlit_preview,
        generate_react_preview,
        run_streamlit_preview,
        run_react_preview,
    )
    import json as _json

    with open(args.input) as fh:
        inventory = _json.load(fh)

    app_type = args.type or "streamlit"
    app_dir  = args.app_dir

    if not app_dir:
        _emit({"status": "error", "command": "preview",
               "error": "--app-dir is required (path to generated Streamlit or React output directory)"})
        return

    rows = args.rows or 25

    try:
        if app_type == "streamlit":
            prep = generate_streamlit_preview(app_dir, inventory, rows=rows)
            if prep.get("status") == "error":
                _emit({**prep, "command": "preview"})
                return

            preview_home = prep["preview_home"]
            print(
                f"\nStreamlit preview ready.\n"
                f"  Synthetic tables: {prep['mock_table_count']}  "
                f"({prep['mock_rows_per_table']} rows each)\n"
                f"  Preview files:    {len(prep['preview_files'])}\n",
                file=sys.stderr,
            )

            if not args.generate_only:
                print(
                    f"  Launching:  streamlit run {preview_home}\n"
                    f"  Open:       http://localhost:8501\n",
                    file=sys.stderr,
                )
                proc = run_streamlit_preview(preview_home)
                _emit({
                    "status": "ok",
                    "command": "preview",
                    "type": "streamlit",
                    "url": "http://localhost:8501",
                    "pid": proc.pid,
                    "preview_home": preview_home,
                    "elapsed_seconds": round(time.perf_counter() - t0, 2),
                    "note": f"Press Ctrl+C or kill PID {proc.pid} to stop.",
                })
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
            else:
                _emit({**prep, "command": "preview", "type": "streamlit",
                       "elapsed_seconds": round(time.perf_counter() - t0, 2)})

        elif app_type == "react":
            prep = generate_react_preview(app_dir, inventory, rows=rows)
            if prep.get("status") == "error":
                _emit({**prep, "command": "preview"})
                return

            print(
                f"\nReact preview ready.\n"
                f"  Synthetic tables: {prep['mock_table_count']}  "
                f"({prep['mock_rows_per_table']} rows each)\n"
                f"  Mock data:        {prep['mock_data_path']}\n"
                f"  Production SF client backed up to: {prep['snowflake_backup']}\n",
                file=sys.stderr,
            )

            if not args.generate_only:
                print(
                    f"  Running:  npm run dev (in {app_dir})\n"
                    f"  Open:     http://localhost:3000\n",
                    file=sys.stderr,
                )
                proc = run_react_preview(app_dir)
                _emit({
                    "status": "ok",
                    "command": "preview",
                    "type": "react",
                    "url": "http://localhost:3000",
                    "pid": proc.pid,
                    "app_dir": app_dir,
                    "elapsed_seconds": round(time.perf_counter() - t0, 2),
                    "note": (
                        f"Press Ctrl+C or kill PID {proc.pid} to stop. "
                        f"Run `python3 -m modules.cli restore-react {app_dir}` "
                        "to restore the production Snowflake client."
                    ),
                })
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
            else:
                _emit({**prep, "command": "preview", "type": "react",
                       "elapsed_seconds": round(time.perf_counter() - t0, 2)})

        else:
            _emit({"status": "error", "command": "preview",
                   "error": f"Unknown --type: {app_type}. Use 'streamlit' or 'react'."})

    except Exception as exc:
        logger.exception("preview failed")
        _emit({"status": "error", "command": "preview", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: restore-react (undo mock injection after preview)
# ---------------------------------------------------------------------------

def cmd_restore_react(args: argparse.Namespace) -> None:
    from .preview import restore_react_production
    try:
        restore_react_production(args.app_dir)
        _emit({"status": "ok", "command": "restore-react",
               "message": "Production snowflake.ts restored."})
    except Exception as exc:
        _emit({"status": "error", "command": "restore-react", "error": str(exc)})


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m modules.cli",
        description="bi-modernization: BI source → Snowflake AI/BI outputs",
    )
    sub = p.add_subparsers(dest="command")

    # ── enrich-charts ──────────────────────────────────────────────────────
    p_ec = sub.add_parser("enrich-charts",
                           help="Enrich inventory.json with chart type per sheet/visual.")
    p_ec.add_argument("input",         help="Path to inventory.json from semantic-extraction.")
    p_ec.add_argument("-o", "--output", help="Output path for enriched_inventory.json (default: auto).")
    p_ec.add_argument("--source-files", nargs="+", metavar="PATH",
                      help="Source BI files (.twb/.twbx) or LookML dirs for chart type extraction. "
                           "PBI chart types are read from inventory automatically.")

    # ── generate-streamlit ─────────────────────────────────────────────────
    p_sl = sub.add_parser("generate-streamlit",
                           help="Generate multi-page Streamlit-in-Snowflake app.")
    p_sl.add_argument("input",            help="Path to enriched_inventory.json.")
    p_sl.add_argument("-o", "--output",   help="Output directory (default: ~/Downloads/bim_streamlit_<ts>).")
    p_sl.add_argument("--semantic-view",  help="Snowflake Semantic View FQN for query generation.")
    p_sl.add_argument("--embed-agent",    help="Cortex Agent FQN to embed as a chat panel in the app.")
    p_sl.add_argument("--dashboards",     help="Comma-separated list of dashboard names to include (default: all).")

    # ── generate-react ─────────────────────────────────────────────────────
    p_rx = sub.add_parser("generate-react",
                           help="Generate Next.js + ECharts app for Snowflake App Runtime (SPCS).")
    p_rx.add_argument("input",            help="Path to enriched_inventory.json.")
    p_rx.add_argument("-o", "--output",   help="Output directory (default: ~/Downloads/bim_react_<ts>).")
    p_rx.add_argument("--app-name",       default="BI Suite",
                      help="Human-readable app name (default: 'BI Suite').")
    p_rx.add_argument("--semantic-view",  help="Snowflake Semantic View FQN for query generation.")
    p_rx.add_argument("--embed-agent",    help="Cortex Agent FQN to embed as a floating chat panel.")
    p_rx.add_argument("--dashboards",     help="Comma-separated list of dashboard names to include (default: all).")

    # ── build-agent ────────────────────────────────────────────────────────
    p_ag = sub.add_parser("build-agent",
                           help="Build Cortex Agent spec + deployment SQL.")
    p_ag.add_argument("input",            help="Path to enriched_inventory.json.")
    p_ag.add_argument("-o", "--output",   help="Output directory for agent artifacts (default: ~/Downloads/bim_agent_<ts>).")
    p_ag.add_argument("--agent-name",     default="BI_ANALYTICS_AGENT",
                      help="Cortex Agent object name (default: BI_ANALYTICS_AGENT).")
    p_ag.add_argument("--database",       help="Target Snowflake database.")
    p_ag.add_argument("--schema",         default="PUBLIC", help="Target schema (default: PUBLIC).")
    p_ag.add_argument("--search-services",
                      help="Comma-separated Cortex Search service FQNs to attach as tools.")
    p_ag.add_argument("--domains",
                      help='Explicit domain→tables JSON, e.g. \'{"Revenue": ["FACT_SALES", "DIM_CUSTOMER"]}\'')
    p_ag.add_argument("--assess-only", action="store_true",
                      help="Show domain assessment only — do not generate artifacts.")

    # ── preview ────────────────────────────────────────────────────────────
    p_pv = sub.add_parser("preview",
                           help="Launch a local preview of the generated app using synthetic data.")
    p_pv.add_argument("input",           help="Path to enriched_inventory.json.")
    p_pv.add_argument("--app-dir",       required=True,
                      help="Path to a generated Streamlit or React output directory.")
    p_pv.add_argument("--type",          choices=["streamlit", "react"], default="streamlit",
                      help="App type to preview (default: streamlit).")
    p_pv.add_argument("--rows",          type=int, default=25,
                      help="Synthetic rows per table (default: 25).")
    p_pv.add_argument("--generate-only", action="store_true",
                      help="Inject synthetic data but do not launch the server.")

    # ── restore-react ───────────────────────────────────────────────────────
    p_rr = sub.add_parser("restore-react",
                           help="Restore production snowflake.ts after a React preview.")
    p_rr.add_argument("app_dir", help="Path to the React app directory.")

    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "enrich-charts":      cmd_enrich_charts,
        "generate-streamlit": cmd_generate_streamlit,
        "generate-react":     cmd_generate_react,
        "build-agent":        cmd_build_agent,
        "preview":            cmd_preview,
        "restore-react":      cmd_restore_react,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(0)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
