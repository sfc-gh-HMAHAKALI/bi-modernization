"""
bi-modernization CLI — entry point for all code-generation commands.

Usage:
    python3 -m modules.cli <command> [options]

Extraction commands (source BI -> inventory / semantic YAML):
    crawl                   Discover source files under a directory
    parse                   Parse source files into inventory.json
    classify                Classify inventory complexity
    generate-yaml           Generate Semantic View YAML
    report                  Generate HTML report + Excel workbook
    compare                 Compare two inventories
    generate-from-workbook  One-shot workbook -> semantic YAML
    seed-data               Generate seed / sample data
    si-agent                Generate Snowflake Intelligence agent artifacts
    test-connection         Test live source connectivity

Generation commands (inventory -> apps / agents):
    enrich-charts       Enrich inventory.json with chart type per sheet/visual
    extract-visuals     Extract colors, layout and formats from BI source files
    generate-streamlit  Generate multi-page Streamlit-in-Snowflake app
    generate-react      Generate Next.js + ECharts app for Snowflake App Runtime
    build-agent         Build Cortex Agent spec + deployment SQL (extends si_agent)
    preview             Launch a local preview using synthetic data
    restore-react       Restore production snowflake.ts after a React preview
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import cli_semex

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
            visuals_path=getattr(args, 'visuals', None),
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
# Command: extract-visuals
# ---------------------------------------------------------------------------

def cmd_extract_visuals(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    from .visual_extractor import extract

    output = args.output or args.input.rsplit(".", 1)[0] + "_visuals.json"

    try:
        result = extract(
            source_path=args.input,
            output_path=output,
        )
        result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

        # Summary to stderr
        color_count = sum(len(v) for v in result.get("color_mappings", {}).values())
        alias_count = len(result.get("column_aliases", {}))
        layout_count = len(result.get("dashboard_layouts", {}))
        filter_count = sum(len(v) for v in result.get("worksheet_filters", {}).values())
        param_count = len(result.get("parameters", []))
        print(
            f"\nVisual metadata extracted: {output}\n"
            f"  Color mappings:    {color_count} value→color pairs\n"
            f"  Column aliases:    {alias_count}\n"
            f"  Dashboard layouts: {layout_count}\n"
            f"  Worksheet filters: {filter_count}\n"
            f"  Parameters:        {param_count}\n",
            file=sys.stderr,
        )
        _emit(result)
    except Exception as exc:
        logger.exception("extract-visuals failed")
        _emit({"status": "error", "command": "extract-visuals", "error": str(exc)})


# ---------------------------------------------------------------------------
# Command: restore-react (undo mock injection after preview)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Command: migrate — the whole deterministic chain
# ---------------------------------------------------------------------------

def cmd_migrate(args: argparse.Namespace) -> None:
    """Run crawl -> parse -> merge -> enrich -> visuals -> propose -> yaml.

    Stops before app generation on purpose. Everything up to here is mechanical
    and safe to run unattended; deciding which dashboards to rebuild, and
    reconciling the numbers, is not. Individual commands remain available for
    anyone who wants to drive the steps by hand.

    Resumable: a step whose artifact already exists is skipped unless --force,
    so re-running after a failure continues rather than starting over.
    """
    import json as _json
    import time as _time
    from pathlib import Path as _Path

    t0 = time.perf_counter()

    out = _Path(args.output or _auto_output_dir("migrate"))
    out.mkdir(parents=True, exist_ok=True)

    inv_path = out / "inventory.json"
    enr_path = out / "enriched.json"
    vis_dir = out / "visuals"
    views_path = out / "views.json"
    yaml_dir = out / "semantic_views"
    state_path = out / "migrate_state.json"

    state: dict = {}
    if state_path.is_file() and not args.force:
        try:
            state = _json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
    steps: dict = state.get("steps", {})

    def record(name: str, **info) -> None:
        steps[name] = {"at": _time.strftime("%Y-%m-%dT%H:%M:%S"), **info}
        state.update({"output_dir": str(out), "source": args.input,
                      "source_type": args.type, "steps": steps})
        try:
            state_path.write_text(_json.dumps(state, indent=2, default=str))
        except OSError:
            pass

    def done(name: str, artifact: _Path) -> bool:
        return (not args.force) and name in steps and artifact.exists()

    total = 6
    step_log: list[str] = []

    def say(n: int, msg: str) -> None:
        line = f"[{n}/{total}] {msg}"
        step_log.append(line)
        print(line, file=sys.stderr)

    try:
        # ── 1-3. resolve + parse (+ merge, for a portfolio) ────────────────
        if done("parse", inv_path):
            inventory = _json.loads(inv_path.read_text())
            say(1, f"parse       skipped, {inv_path.name} exists")
            say(2, "merge       skipped")
            say(3, "resolve     skipped")
        else:
            from .portfolio import parse_portfolio, resolve_sources
            from .output.inventory import (build_unified_inventory,
                                           merge_inventories, save_inventory)
            from .cli_semex import _run_parser

            paths, notes = resolve_sources(args.input, args.type,
                                           recurse=not args.no_recurse,
                                           max_files=args.max_files)
            if not paths:
                _emit({"status": "error", "command": "migrate",
                       "error": f"no {args.type} sources found at {args.input}",
                       "notes": notes})
                return
            say(1, f"crawl       {len(paths)} source(s)")
            for n in notes:
                print(f"        note: {n}", file=sys.stderr)

            sf_target = {}
            if args.database:
                sf_target["database"] = args.database
            if args.schema:
                sf_target["schema"] = args.schema

            run = parse_portfolio(
                paths, args.type,
                parse_one=_run_parser,
                build_inventory=build_unified_inventory,
                sf_target=sf_target or None,
                inventory_dir=str(out / "sources"),
                state_path=str(out / "parse_state.json"),
                resume=not args.force,
            )
            if not run["inventories"]:
                _emit({"status": "error", "command": "migrate",
                       "error": f"all {len(paths)} source(s) failed to parse",
                       "sources": run["sources"]})
                return
            say(2, f"parse       {run['ok_count']} ok, {run['failed_count']} failed")
            for f in run["failed"]:
                print(f"        failed: {_Path(f['path']).name}: {f['error'][:90]}",
                      file=sys.stderr)

            inventory = merge_inventories(run["inventories"])
            if sf_target:
                inventory["snowflake_target"] = {
                    **inventory.get("snowflake_target", {}), **sf_target}
            save_inventory(inventory, str(inv_path))
            say(3, f"merge       {len(inventory.get('tables', []))} tables, "
                   f"{len(inventory.get('dimensions', []))} dims, "
                   f"{len(inventory.get('facts', []))} facts, "
                   f"{len(inventory.get('metrics', []))} metrics")
            record("parse", sources=len(paths), ok=run["ok_count"],
                   failed=run["failed_count"], artifact=str(inv_path))
            record("merge", artifact=str(inv_path))

        # ── 4. enrich with chart types ─────────────────────────────────────
        if done("enrich", enr_path):
            say(4, f"enrich      skipped, {enr_path.name} exists")
        else:
            from .chart_extractor import enrich
            src_files = state.get("source_paths") or []
            if not src_files:
                from .portfolio import resolve_sources
                src_files, _ = resolve_sources(args.input, args.type,
                                               recurse=not args.no_recurse,
                                               max_files=args.max_files)
                state["source_paths"] = src_files
            res = enrich(inventory_path=str(inv_path), source_files=src_files,
                         output_path=str(enr_path))
            cov = res.get("chart_type_coverage", {}) or {}
            # Report the explicit/heuristic split, not just a count. 100%
            # heuristic means every chart type was inferred rather than read from
            # the workbook, which is exactly the sort of thing a reviewer needs to
            # know before trusting the generated app's chart choices.
            say(4, f"enrich      {cov.get('total', res.get('worksheet_count', 0))} "
                   f"worksheet(s): {cov.get('explicit', 0)} explicit mark(s), "
                   f"{cov.get('heuristic', 0)} inferred")
            if cov.get("total") and not cov.get("explicit"):
                print("        note: no explicit mark types found; every chart "
                      "type is a heuristic guess. Verify chart choices.",
                      file=sys.stderr)
            record("enrich", artifact=str(enr_path), coverage=cov)

        # ── 5. visual metadata ─────────────────────────────────────────────
        if done("visuals", vis_dir):
            say(5, "visuals     skipped")
        else:
            from .visual_extractor import extract
            vis_dir.mkdir(parents=True, exist_ok=True)
            src_files = state.get("source_paths") or []
            if not src_files:
                from .portfolio import resolve_sources
                src_files, _ = resolve_sources(args.input, args.type,
                                               recurse=not args.no_recurse,
                                               max_files=args.max_files)
            written, failed_vis = [], 0
            for src in src_files:
                stem = "".join(c if (c.isalnum() or c in "-_") else "_"
                               for c in _Path(src).stem)
                try:
                    extract(source_path=src, output_path=str(vis_dir / f"{stem}.json"))
                    written.append(stem)
                except Exception as exc:
                    # Visual metadata is a nice-to-have; losing it for one
                    # workbook must not end the migration.
                    failed_vis += 1
                    logger.warning("visual extraction failed for %s: %s", src, exc)
            # Deliberately NOT merged into one file: palettes and background
            # colours are per-workbook, and averaging two themes produces a
            # theme neither workbook had.
            say(5, f"visuals     {len(written)} extracted"
                   + (f", {failed_vis} failed" if failed_vis else ""))
            record("visuals", extracted=len(written), failed=failed_vis,
                   artifact=str(vis_dir))

        # ── 6. propose views + generate YAML ───────────────────────────────
        if done("views", yaml_dir):
            say(6, "views       skipped")
            proposal = _json.loads(views_path.read_text()) if views_path.is_file() else {}
        else:
            from .views import format_proposal, propose
            from .output.yaml_generator import generate_all_yamls

            proposal = propose(inventory, strategy=args.strategy)
            views_path.write_text(_json.dumps(proposal, indent=2, default=str))
            paths_written = generate_all_yamls(
                inventory, str(yaml_dir), groups=proposal["views"])
            say(6, f"views       {proposal['view_count']} proposed, "
                   f"{len(paths_written)} YAML written")
            print("\n" + format_proposal(proposal), file=sys.stderr)
            record("views", count=proposal["view_count"],
                   yaml=len(paths_written), artifact=str(yaml_dir))
    except Exception as exc:
        logger.exception("migrate failed")
        _emit({"status": "error", "command": "migrate", "error": str(exc),
               "output_dir": str(out), "completed_steps": sorted(steps),
               "resume_hint": "re-run the same command; completed steps are skipped"})
        return

    _emit({
        "status": "ok",
        "command": "migrate",
        "output_dir": str(out),
        "inventory": str(inv_path),
        "enriched": str(enr_path),
        "visuals_dir": str(vis_dir),
        "views": str(views_path),
        "semantic_views_dir": str(yaml_dir),
        "state": str(state_path),
        "steps": step_log,
        "view_count": (proposal or {}).get("view_count", 0),
        "notes": (proposal or {}).get("notes", []),
        "next": "Review the proposal, then choose what to build "
                "(semantic views, agent, Streamlit app, React app).",
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
    })


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
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (used by the extraction commands).",
    )
    sub = p.add_subparsers(dest="command")

    # Extraction commands, defined in cli_semex so the argparse definitions are
    # not duplicated between the two entry points.
    cli_semex.register_subparsers(sub)

    # ── migrate ────────────────────────────────────────────────────────────
    p_mg = sub.add_parser("migrate",
                          help="Run the whole chain: crawl, parse, merge, enrich, "
                               "visuals, propose-views, generate-yaml.")
    p_mg.add_argument("input", help="BI source file, directory, or glob.")
    p_mg.add_argument("--type", required=True,
                      choices=["tableau", "powerbi", "looker", "denodo", "businessobjects"])
    p_mg.add_argument("-o", "--output", help="Output directory (default: ~/Downloads/bim_migrate_<ts>).")
    p_mg.add_argument("--database", help="Target Snowflake database.")
    p_mg.add_argument("--schema", help="Target Snowflake schema.")
    p_mg.add_argument("--strategy", default="auto",
                      choices=["auto", "co-usage", "one-per-table"],
                      help="Semantic view grouping strategy (default: auto).")
    p_mg.add_argument("--max-files", type=int, default=500,
                      help="Cap on sources parsed (default: 500).")
    p_mg.add_argument("--no-recurse", action="store_true",
                      help="Do not descend into subdirectories.")
    p_mg.add_argument("--force", action="store_true",
                      help="Re-run every step, ignoring completed artifacts.")

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
    p_sl.add_argument("--visuals",        help="Path to visuals.json from extract-visuals (colors, layout, formats).")
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

    # ── extract-visuals ──────────────────────────────────────────────────────
    p_ev = sub.add_parser("extract-visuals",
                           help="Extract visual metadata (colors, layout, formats) from BI source files.")
    p_ev.add_argument("input",         help="Path to BI source file (.twb, .twbx, .pbix, .pbit).")
    p_ev.add_argument("-o", "--output", help="Output path for visuals.json (default: auto).")

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
        "migrate":            cmd_migrate,
        "enrich-charts":      cmd_enrich_charts,
        "extract-visuals":    cmd_extract_visuals,
        "generate-streamlit": cmd_generate_streamlit,
        "generate-react":     cmd_generate_react,
        "build-agent":        cmd_build_agent,
        "preview":            cmd_preview,
        "restore-react":      cmd_restore_react,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Extraction commands follow a different contract: they return a result dict
    # which the caller prints as JSON and turns into an exit code, whereas the
    # generation commands above print through _emit and return None.
    if args.command in cli_semex.COMMANDS:
        cli_semex.setup_logging(level=getattr(args, "log_level", "INFO"))
        try:
            result = cli_semex.COMMANDS[args.command](args)
        except Exception as e:
            result = {
                "status": "error",
                "command": args.command,
                "failure": cli_semex.fail_step(args.command, e),
            }
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("status") == "ok" else 1)

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(0)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
