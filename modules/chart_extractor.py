"""
Chart type extractor.

Re-opens BI source files to extract chart/visual type per sheet or visual.
Merges chart types into an existing inventory JSON to produce an enriched inventory.

Supported sources:
  - Tableau  (.twb / .twbx) — reads <mark class="..."> per worksheet
  - Power BI (all formats)   — reads report_pages already in inventory (no re-parse)
  - Looker   (.dashboard.lkml / .model.lkml) — reads tile type: field

For Denodo and SAP BO there is no native chart concept; heuristics are used for all visuals.
"""

from __future__ import annotations

import copy
import glob
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Any

from .chart_mappings import (
    tableau_mark_to_plotly,
    tableau_mark_to_echarts,
    powerbi_visual_to_plotly,
    powerbi_visual_to_echarts,
    looker_tile_to_plotly,
    looker_tile_to_echarts,
)
from .heuristics import infer_chart_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Virtual structure builder
# ---------------------------------------------------------------------------

def _build_virtual_structure(inventory: dict) -> dict:
    """
    Build synthetic 'worksheets' and 'dashboards' entries from the
    per-field dashboard_name / page_name metadata that semantic-extraction
    stores inside each dimension/fact/metric entry.

    Called when inventory lacks top-level 'worksheets'/'dashboards' keys.
    Modifies inventory in-place and returns it.
    """
    all_fields = (
        inventory.get("dimensions", []) +
        inventory.get("facts", []) +
        inventory.get("metrics", [])
    )

    # Group fields by (dashboard_name, page_name)
    from collections import defaultdict
    page_fields: dict[tuple, list[dict]] = defaultdict(list)
    for f in all_fields:
        dash = (f.get("dashboard_name") or "").strip()
        page = (f.get("page_name") or f.get("source_view") or "").strip()
        if not page:
            page = "Sheet 1"
        page_fields[(dash, page)].append(f)

    # Build worksheets (unique by page)
    ws_map: dict[str, dict] = {}
    for (dash, page), fields in page_fields.items():
        if page not in ws_map:
            by_ds: dict[str, list] = defaultdict(list)
            for f in fields:
                tbl = f.get("table") or f.get("source_view") or "default"
                by_ds[tbl].append(f)
            ws_map[page] = {
                "name": page,
                "fields_by_datasource": dict(by_ds),
            }

    if not inventory.get("worksheets"):
        inventory["worksheets"] = list(ws_map.values())

    # Build dashboards (group pages by dashboard_name)
    if not inventory.get("dashboards"):
        dash_pages: dict[str, list] = defaultdict(list)
        for (dash, page) in page_fields:
            label = dash or "Dashboard"
            if ws_map.get(page) and ws_map[page] not in dash_pages[label]:
                dash_pages[label].append(ws_map[page])

        inventory["dashboards"] = [
            {"name": dash_name, "sheets": sheets}
            for dash_name, sheets in dash_pages.items()
        ]

    return inventory


# ---------------------------------------------------------------------------
# Tableau extraction
# ---------------------------------------------------------------------------

def _extract_tableau_mark_types(path: str) -> dict[str, str]:
    """
    Return {worksheet_name: mark_class} for every worksheet in a .twb or .twbx file.
    mark_class is the raw string from the XML (e.g. "Bar", "Line", "Automatic").
    """
    try:
        import defusedxml.ElementTree as ET
    except ImportError:
        import xml.etree.ElementTree as ET  # type: ignore[no-redef]

    result: dict[str, str] = {}

    def _parse_xml(xml_content: bytes) -> None:
        root = ET.fromstring(xml_content)
        for ws in root.findall(".//worksheet"):
            name = ws.get("name", "")
            if not name:
                continue
            # <mark class="Bar"/> lives at: worksheet/table/view/panes/pane/mark
            mark = ws.find(".//panes//mark")
            if mark is None:
                mark = ws.find(".//mark")  # fallback: any mark under worksheet
            result[name] = mark.get("class", "Automatic") if mark is not None else "Automatic"

    p = Path(path)
    if p.suffix.lower() in (".twbx", ".tdsx"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                for member in zf.namelist():
                    if member.endswith(".twb") or member.endswith(".tds"):
                        _parse_xml(zf.read(member))
        except zipfile.BadZipFile:
            logger.warning("Could not open %s as zip — trying as plain XML", path)
            _parse_xml(p.read_bytes())
    else:
        _parse_xml(p.read_bytes())

    logger.info("Tableau: extracted mark types for %d worksheets from %s", len(result), path)
    return result


def _enrich_tableau(inventory: dict, source_files: list[str]) -> tuple[dict, dict]:
    """
    Merge mark types into inventory["worksheets"] and any worksheets referenced
    by inventory["dashboards"].  Returns (enriched_inventory, coverage_stats).
    """
    mark_map: dict[str, str] = {}
    for path in source_files:
        mark_map.update(_extract_tableau_mark_types(path))

    total, explicit, heuristic = 0, 0, 0

    for ws in inventory.get("worksheets", []):
        name = ws.get("name", "")
        total += 1
        raw = mark_map.get(name, "Automatic")
        plotly_type = tableau_mark_to_plotly(raw)
        echarts_type = tableau_mark_to_echarts(raw)

        if plotly_type is None:
            # "Automatic" or unknown — fall back to heuristics
            all_fields = []
            for fields in ws.get("fields_by_datasource", {}).values():
                if fields and isinstance(fields[0], dict):
                    all_fields.extend(fields)
                elif fields:
                    all_fields.extend({"name": f, "role": "dimension"} for f in fields)
            plotly_type = infer_chart_type(all_fields)
            echarts_type = plotly_type  # heuristics return Plotly keys, same string is fine
            heuristic += 1
        else:
            explicit += 1

        ws["chart_type_raw"] = raw
        ws["chart_type_plotly"] = plotly_type
        ws["chart_type_echarts"] = echarts_type

    # Post-pass: if EVERY worksheet was "Automatic" and heuristics assigned "bar"
    # to most of them, this is likely a tabular/crosstab workbook. Override to "table".
    if total > 0 and heuristic == total:
        bar_count = sum(1 for ws in inventory.get("worksheets", [])
                        if ws.get("chart_type_plotly") == "bar")
        if bar_count > total * 0.5:
            for ws in inventory.get("worksheets", []):
                if ws.get("chart_type_plotly") == "bar":
                    ws["chart_type_plotly"] = "table"
                    ws["chart_type_echarts"] = "table"

    # Also propagate into dashboard sheet references.
    # Dashboard sheets may be zone references (bracket notation) rather than
    # worksheet names.  Try to match them back to actual worksheets.
    ws_lookup = {ws.get("name"): ws for ws in inventory.get("worksheets", [])}
    for dash in inventory.get("dashboards", []):
        resolved_sheets = []
        for sheet in dash.get("sheets", []):
            sname = sheet.get("name") if isinstance(sheet, dict) else sheet
            src = ws_lookup.get(sname)

            # If direct match failed, try matching zone references to worksheets
            if src is None and sname:
                # Zone format: [datasource].[qualifier:field:type] — try to find
                # a worksheet whose fields_by_datasource keys overlap
                for ws_name, ws in ws_lookup.items():
                    if ws_name in sname or sname in ws_name:
                        src = ws
                        break

            if src:
                if isinstance(sheet, dict):
                    sheet["chart_type_plotly"] = src.get("chart_type_plotly")
                    sheet["chart_type_echarts"] = src.get("chart_type_echarts")
                    # Also copy fields_by_datasource if the zone had none
                    if not sheet.get("fields_by_datasource"):
                        sheet["fields_by_datasource"] = src.get("fields_by_datasource", {})
                resolved_sheets.append(sheet)
            else:
                # Skip zones that are clearly not worksheets (images, filter refs)
                if sname and not any(sname.lower().endswith(ext)
                                     for ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg')):
                    resolved_sheets.append(sheet)
        dash["sheets"] = resolved_sheets

    stats = {
        "total": total,
        "explicit": explicit,
        "heuristic": heuristic,
        "explicit_pct": round(explicit / total * 100, 1) if total else 0,
        "heuristic_pct": round(heuristic / total * 100, 1) if total else 0,
    }
    return inventory, stats


# ---------------------------------------------------------------------------
# Power BI extraction (no re-parsing needed)
# ---------------------------------------------------------------------------

def _enrich_powerbi(inventory: dict) -> tuple[dict, dict]:
    """
    Power BI report_pages already contain visual types from the existing parser.
    Propagate them into a normalised structure for downstream generators.
    """
    total, explicit, heuristic = 0, 0, 0

    for page in inventory.get("report_pages", []):
        for visual in page.get("visuals", []):
            total += 1
            raw_type = visual.get("type", "unknown")
            plotly_type = powerbi_visual_to_plotly(raw_type)
            echarts_type = powerbi_visual_to_echarts(raw_type)

            if plotly_type is None:
                # Unknown PBI type — use heuristics on field list
                fields = [{"name": f, "role": "dimension"} for f in visual.get("fields", [])]
                plotly_type = infer_chart_type(fields)
                echarts_type = plotly_type
                heuristic += 1
            else:
                explicit += 1

            visual["chart_type_plotly"] = plotly_type
            visual["chart_type_echarts"] = echarts_type

    # Build a worksheet-style list from report pages for generator compatibility
    worksheets = []
    for page in inventory.get("report_pages", []):
        for visual in page.get("visuals", []):
            worksheets.append({
                "name": f"{page.get('display_name', page.get('name', 'Page'))} — {visual.get('type', 'Visual')}",
                "page": page.get("display_name") or page.get("name"),
                "fields_by_datasource": {"default": visual.get("fields", [])},
                "chart_type_raw": visual.get("type", "unknown"),
                "chart_type_plotly": visual.get("chart_type_plotly"),
                "chart_type_echarts": visual.get("chart_type_echarts"),
            })

    # Only add if there are no existing worksheets (avoid duplicating Tableau data)
    if not inventory.get("worksheets") and worksheets:
        inventory["worksheets"] = worksheets

    # Build dashboards from pages if none exist
    if not inventory.get("dashboards"):
        inventory["dashboards"] = [
            {
                "name": page.get("display_name") or page.get("name", "Dashboard"),
                "sheets": [
                    {
                        "name": f"{page.get('display_name', 'Page')} — {v.get('type', 'Visual')}",
                        "chart_type_plotly": v.get("chart_type_plotly"),
                        "chart_type_echarts": v.get("chart_type_echarts"),
                    }
                    for v in page.get("visuals", [])
                    if v.get("chart_type_plotly") != "filter"  # skip slicers
                ],
            }
            for page in inventory.get("report_pages", [])
        ]

    stats = {
        "total": total,
        "explicit": explicit,
        "heuristic": heuristic,
        "explicit_pct": round(explicit / total * 100, 1) if total else 0,
        "heuristic_pct": round(heuristic / total * 100, 1) if total else 0,
    }
    return inventory, stats


# ---------------------------------------------------------------------------
# Looker extraction
# ---------------------------------------------------------------------------

def _extract_looker_tile_types(project_dir: str) -> dict[str, str]:
    """
    Parse .dashboard.lkml files in project_dir and return
    {dashboard_name:tile_title: tile_type} mapping.
    """
    try:
        import lkml
    except ImportError:
        logger.warning("lkml not installed — skipping Looker chart type extraction")
        return {}

    result: dict[str, str] = {}
    pattern = os.path.join(project_dir, "**", "*.dashboard.lkml")

    for filepath in glob.glob(pattern, recursive=True):
        try:
            with open(filepath) as fh:
                parsed = lkml.load(fh)
        except Exception as exc:
            logger.warning("Could not parse %s: %s", filepath, exc)
            continue

        for dash in parsed.get("dashboards", []):
            dash_name = dash.get("label") or dash.get("dashboard", "")
            for element in dash.get("elements", []):
                title = element.get("title") or element.get("name") or ""
                tile_type = element.get("type", "looker_column")
                key = f"{dash_name}::{title}"
                result[key] = tile_type

    return result


def _enrich_looker(inventory: dict, source_dirs: list[str]) -> tuple[dict, dict]:
    tile_map: dict[str, str] = {}
    for d in source_dirs:
        if os.path.isdir(d):
            tile_map.update(_extract_looker_tile_types(d))

    total, explicit, heuristic = 0, 0, 0

    for ws in inventory.get("worksheets", []):
        name = ws.get("name", "")
        total += 1
        raw = tile_map.get(name)

        if raw:
            plotly_type = looker_tile_to_plotly(raw) or infer_chart_type([])
            echarts_type = looker_tile_to_echarts(raw) or plotly_type
            explicit += 1
        else:
            all_fields: list[dict] = []
            for fields in ws.get("fields_by_datasource", {}).values():
                all_fields.extend(
                    fields if fields and isinstance(fields[0], dict)
                    else [{"name": f, "role": "dimension"} for f in fields]
                )
            plotly_type = infer_chart_type(all_fields)
            echarts_type = plotly_type
            raw = "unknown"
            heuristic += 1

        ws["chart_type_raw"] = raw
        ws["chart_type_plotly"] = plotly_type
        ws["chart_type_echarts"] = echarts_type

    stats = {
        "total": total,
        "explicit": explicit,
        "heuristic": heuristic,
        "explicit_pct": round(explicit / total * 100, 1) if total else 0,
        "heuristic_pct": round(heuristic / total * 100, 1) if total else 0,
    }
    return inventory, stats


# ---------------------------------------------------------------------------
# Generic heuristic fallback (Denodo / SAP BO / already-enriched)
# ---------------------------------------------------------------------------

def _enrich_heuristic(inventory: dict) -> tuple[dict, dict]:
    """Apply heuristics to any worksheets lacking chart_type_plotly."""
    total, enriched = 0, 0
    for ws in inventory.get("worksheets", []):
        total += 1
        if ws.get("chart_type_plotly"):
            continue
        all_fields: list[dict] = []
        for fields in ws.get("fields_by_datasource", {}).values():
            all_fields.extend(
                fields if fields and isinstance(fields[0], dict)
                else [{"name": f, "role": "dimension"} for f in fields]
            )
        chart_type = infer_chart_type(all_fields)
        ws["chart_type_plotly"] = chart_type
        ws["chart_type_echarts"] = chart_type
        ws["chart_type_raw"] = "heuristic"
        enriched += 1

    stats = {
        "total": total,
        "heuristic": enriched,
        "explicit": total - enriched,
        "explicit_pct": round((total - enriched) / total * 100, 1) if total else 0,
        "heuristic_pct": round(enriched / total * 100, 1) if total else 0,
    }
    return inventory, stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich(
    inventory_path: str,
    source_files: list[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    Load inventory_path, enrich with chart types, write to output_path.

    source_files: list of .twb / .twbx paths (Tableau) or LookML project dirs (Looker).
                  Power BI chart types come from inventory["report_pages"] automatically.

    Returns the result dict suitable for CLI JSON output.
    """
    with open(inventory_path) as fh:
        inventory = json.load(fh)

    enriched = copy.deepcopy(inventory)
    source_type = (enriched.get("source_type") or "").lower()
    source_files = source_files or []

    # Build virtual worksheets/dashboards from per-field metadata if absent
    _build_virtual_structure(enriched)

    coverage: dict[str, Any] = {}

    if source_type == "tableau":
        twb_files = [f for f in source_files if Path(f).suffix.lower() in (".twb", ".twbx", ".tds", ".tdsx")]
        if not twb_files:
            # Try to find .twb files near the inventory
            twb_files = source_files  # pass through; extractor will skip non-XML
        enriched, coverage = _enrich_tableau(enriched, twb_files)

    elif source_type == "powerbi":
        enriched, coverage = _enrich_powerbi(enriched)

    elif source_type == "looker":
        looker_dirs = [f for f in source_files if os.path.isdir(f)]
        enriched, coverage = _enrich_looker(enriched, looker_dirs)

    else:
        # Denodo / SAP BO / unknown — pure heuristics
        enriched, coverage = _enrich_heuristic(enriched)

    # Final pass: fill any remaining gaps with heuristics
    enriched, _ = _enrich_heuristic(enriched)

    enriched["chart_type_coverage"] = coverage

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as fh:
            json.dump(enriched, fh, indent=2)
        logger.info("Enriched inventory written to %s", output_path)

    dashboard_count = len(enriched.get("dashboards", []))
    worksheet_count = len(enriched.get("worksheets", []))

    return {
        "status": "ok",
        "command": "enrich-charts",
        "source_type": source_type or "unknown",
        "dashboard_count": dashboard_count,
        "worksheet_count": worksheet_count,
        "chart_type_coverage": coverage,
        "output_path": output_path,
    }
