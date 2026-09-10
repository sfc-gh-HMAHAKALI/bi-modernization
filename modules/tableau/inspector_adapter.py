"""Adapter: Tableau inspector output -> the parse_workbook contract.

The inspector (`inspector.py`) extracts materially more than the legacy
`parser.py`: LOD classification, untranslatable-function detection, table
calculations, per-field formatting and folders, dashboard layout ratios, visual
styles, and field reach. But every downstream consumer in this skill --
`output/inventory.py`, `yaml_generator`, `si_agent`, `chart_extractor`,
`streamlit_generator` -- is written against `parser.parse_workbook`'s shape.

So this adapter deliberately targets *that* shape rather than the final
inventory. `build_unified_inventory` then runs its existing `_from_tableau`
normalization verbatim, which makes contract preservation a property of the
design instead of something re-implemented and hoped for.

The inspector's extra richness is not thrown away: `inspection_payload()`
returns it for attachment to the inventory under a single additive key, so the
generation path can use it without any existing key changing meaning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common.logger import get_logger
from . import inspector

log = get_logger("tableau.inspector_adapter")

# The inspector grades formulas structurally (parsed LODs, table calcs, blocked
# functions); the rest of this skill speaks the legacy classifier's three-value
# vocabulary. Map structure to vocabulary rather than re-deriving from regex,
# since the structural signal is the more reliable of the two.
_COMPLEXITY_FALLBACK = {
    "low": "simple",
    "medium": "needs_translation",
    "high": "needs_translation",
}


def _complexity(calc: dict[str, Any]) -> str:
    """Map an inspector calculation grade onto the legacy complexity vocabulary.

    Blockers mean no SQL equivalent exists, which is exactly what
    `manual_required` is for downstream (it drives the `flagged` list). LODs and
    table calcs are translatable but not mechanically, so they need translation.
    """
    if calc.get("blockers"):
        return "manual_required"
    if (calc.get("lod_kinds")
            or calc.get("table_calc_functions")
            or calc.get("needs_review")
            or calc.get("lod_nested")):
        return "needs_translation"
    return _COMPLEXITY_FALLBACK.get(calc.get("complexity", "low"), "needs_translation")


def _column(field: dict[str, Any]) -> dict[str, Any]:
    """Convert an inspector column or calculated field to a parser-shaped column.

    Key names here are dictated by `_from_tableau`, which reads `caption`,
    `name`, `datatype`, `role`, `formula`, `desc`, and `complexity`.
    """
    formula = field.get("formula", "")
    return {
        "name": field.get("internal_name", ""),
        "caption": field.get("caption") or field.get("internal_name", ""),
        "datatype": field.get("datatype", "string"),
        "role": field.get("role", "dimension"),
        "formula": formula,
        "desc": field.get("description", ""),
        "complexity": _complexity(field) if formula else "simple",
        # Carried through so generation can format and group fields the way the
        # workbook did. _from_tableau ignores unknown keys, so these are free.
        "suggested_column": field.get("suggested_column", ""),
        "default_format": field.get("default_format", ""),
        "semantic_role": field.get("semantic_role", ""),
        "folder": field.get("folder", ""),
        "aliases": field.get("aliases", {}),
        "hidden": field.get("hidden", False),
        "lod_kinds": field.get("lod_kinds", []),
        "table_calc_functions": field.get("table_calc_functions", []),
        "blockers": field.get("blockers", []),
        "needs_review": field.get("needs_review", []),
        "translate_to": field.get("translate_to", ""),
        "action": field.get("action", ""),
    }


def _clean_table_name(raw: str) -> tuple[str, str]:
    """Split a Tableau relation name into (bare table, fully-qualified form).

    The inspector reports relations as the workbook writes them, which can be
    fully qualified: `[DATAMART_BI_PROD].[FINANCE].[CUSTOMER_FILE]`. Downstream,
    `_from_tableau` normalizes with `.strip("[]").replace(".", "_")`, which on
    that input leaves embedded brackets -- `DATAMART_BI_PROD]_[FINANCE]_[...`.
    So strip the brackets per part here and hand back the bare table name, while
    returning the qualified form so the database/schema context is not lost.
    """
    parts = [p.strip("[]").strip() for p in raw.split(".") if p.strip("[]").strip()]
    if not parts:
        return raw.strip("[]"), raw
    return parts[-1], ".".join(parts)


def _tables_and_joins(ds: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Derive tables and joins from an inspector datasource's relations.

    Relation entries repeat per usage, so tables are de-duplicated while
    preserving first-seen order for stable output. (The legacy parser did not
    de-duplicate, so it reported the same placeholder table N times on
    published-datasource workbooks.)
    """
    tables: list[dict] = []
    joins: list[dict] = []
    seen: set[str] = set()

    for rel in ds.get("relations", []):
        if rel.get("type") == "table":
            raw = rel.get("table", "")
            if not raw:
                continue
            bare, qualified = _clean_table_name(raw)
            if bare not in seen:
                seen.add(bare)
                tables.append({"name": bare, "qualified_name": qualified})
        elif rel.get("type") == "join":
            joins.append({
                "left_table": _clean_table_name(rel.get("left_table", ""))[0],
                "right_table": _clean_table_name(rel.get("right_table", ""))[0],
                "type": rel.get("join_type") or rel.get("type", "inner"),
                "clause": rel.get("clause", ""),
            })

    # A custom-SQL datasource has no table relations; name it after the
    # datasource so downstream code still has something to anchor to rather
    # than silently producing a table-less inventory.
    if not tables and ds.get("custom_sql"):
        name = ds.get("caption") or ds.get("name", "CUSTOM_SQL")
        tables.append({"name": name, "qualified_name": name})

    return tables, joins


def _datasources(inv: dict[str, Any]) -> list[dict[str, Any]]:
    """Group the inspector's flat field lists back under their datasources."""
    # Fields carry the datasource *caption*, while datasource entries carry both
    # name and caption, so index by caption with a name fallback.
    by_ds: dict[str, list[dict]] = {}
    for field in list(inv.get("columns", [])) + list(inv.get("calculated_fields", [])):
        by_ds.setdefault(field.get("datasource", ""), []).append(field)

    out: list[dict[str, Any]] = []
    for ds in inv.get("datasources", []):
        caption = ds.get("caption") or ds.get("name", "")
        fields = by_ds.get(caption) or by_ds.get(ds.get("name", "")) or []
        tables, joins = _tables_and_joins(ds)
        connections = ds.get("connections", [])
        out.append({
            # _from_tableau uses this as the table/source label, so prefer the
            # human-readable caption the workbook author chose.
            "name": caption,
            "internal_name": ds.get("name", ""),
            "connection": connections[0] if connections else {},
            "connections": connections,
            "tables": tables,
            "joins": joins,
            "columns": [_column(f) for f in fields],
        })
    return out


def _dashboards(inv: dict[str, Any]) -> list[dict[str, Any]]:
    """Build dashboards in the shape `_from_tableau`'s provenance index expects.

    That index is keyed by (datasource, field) -> [(dashboard, worksheet)], so
    each sheet needs its fields grouped by datasource. The inspector records
    fields per worksheet as internal names, which is the same key space
    `_column` writes to `name`.
    """
    ws_index = {w.get("name", ""): w for w in inv.get("worksheets", [])}

    # internal field name -> owning datasource caption
    owner: dict[str, str] = {}
    for field in list(inv.get("columns", [])) + list(inv.get("calculated_fields", [])):
        owner.setdefault(field.get("internal_name", ""), field.get("datasource", ""))

    out: list[dict[str, Any]] = []
    for dash in inv.get("dashboards", []):
        sheets: list[dict] = []
        for ws_name in _worksheet_names(dash):
            ws = ws_index.get(ws_name)
            if ws is None:
                continue
            by_ds: dict[str, list[str]] = {}
            for field in ws.get("fields_used", []):
                by_ds.setdefault(owner.get(field, ""), []).append(field)
            sheets.append({"name": ws_name, "fields_by_datasource": by_ds})
        out.append({
            "name": dash.get("name", ""),
            "sheets": sheets,
            "size": dash.get("size", {}),
            "layout": dash.get("layout", []),
        })
    return out


def _worksheet_names(dash: dict[str, Any]) -> list[str]:
    """Collect worksheet names referenced by a dashboard's zone tree."""
    found: list[str] = []

    def walk(zones: Any) -> None:
        if isinstance(zones, dict):
            name = zones.get("worksheet") or zones.get("name")
            if zones.get("worksheet") and name not in found:
                found.append(name)
            walk(zones.get("children"))
        elif isinstance(zones, list):
            for z in zones:
                walk(z)

    walk(dash.get("zones", []))
    return found


def to_parsed(inv: dict[str, Any]) -> dict[str, Any]:
    """Convert an inspector inventory into the parse_workbook contract."""
    return {
        "datasources": _datasources(inv),
        "worksheets": [
            {
                "name": w.get("name", ""),
                "fields": w.get("fields_used", []),
                "marks": w.get("marks", []),
                "suggested_mark": w.get("suggested_mark", ""),
                "rows_shelf": w.get("rows_shelf", ""),
                "cols_shelf": w.get("cols_shelf", ""),
                "encodings": w.get("encodings", []),
            }
            for w in inv.get("worksheets", [])
        ],
        "dashboards": _dashboards(inv),
        "parameters": inv.get("parameters", []),
        "errors": [],
    }


def inspection_payload(inv: dict[str, Any]) -> dict[str, Any]:
    """Return the inspector detail that has no home in the legacy contract.

    Attached to the inventory under one additive key so no existing key changes
    meaning. This is what the generation path reads for order-of-operations
    filtering, layout ratios, member colours, and translation prescriptions.
    """
    return {
        "source": inv.get("source", ""),
        "source_kind": inv.get("source_kind", ""),
        "visual_styles": inv.get("visual_styles", {}),
        "groups_and_bins": inv.get("groups_and_bins", []),
        "filters": inv.get("filters", []),
        "actions": inv.get("actions", []),
        "field_usage": inv.get("field_usage", {}),
        "layout": {d.get("name", ""): d.get("layout", []) for d in inv.get("dashboards", [])},
        "worksheet_marks": {
            w.get("name", ""): {
                "marks": w.get("marks", []),
                "suggested_mark": w.get("suggested_mark", ""),
                "rows_shelf": w.get("rows_shelf", ""),
                "cols_shelf": w.get("cols_shelf", ""),
            }
            for w in inv.get("worksheets", [])
        },
        "translation": [
            {
                "field": c.get("caption") or c.get("internal_name", ""),
                "datasource": c.get("datasource", ""),
                "formula": c.get("formula", ""),
                "complexity": c.get("complexity", ""),
                "complexity_score": c.get("complexity_score", 0),
                "lod_kinds": c.get("lod_kinds", []),
                "lod_nested": c.get("lod_nested", False),
                "table_calc_functions": c.get("table_calc_functions", []),
                "blockers": c.get("blockers", []),
                "needs_review": c.get("needs_review", []),
                "semi_additive_suspect": c.get("semi_additive_suspect", False),
                "action": c.get("action", ""),
                "translate_to": c.get("translate_to", ""),
            }
            for c in inv.get("calculated_fields", [])
        ],
        "warnings": inv.get("warnings", []),
    }


def parse_workbook(path: str) -> dict[str, Any]:
    """Drop-in replacement for `parser.parse_workbook`, backed by the inspector.

    Returns the same keys the legacy parser returns, plus `inspection`, which
    carries the richer detail. Falls back to the legacy parser if the inspector
    raises on readable content, so a workbook only the old path can parse still
    gets through.

    A missing or unreadable file is *not* fallback-eligible: silently degrading
    there would report a successful parse of a file that was never read.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Tableau source not found: {path}")

    try:
        inv = inspector.build_inventory(src)
    except OSError:
        # File exists but cannot be read (permissions, truncation, bad archive).
        # Nothing to fall back to, so let the caller see it.
        raise
    except Exception as exc:
        log.warning("inspector failed on %s (%s: %s); falling back to legacy parser",
                    path, type(exc).__name__, exc)
        from .parser import parse_workbook as legacy
        parsed = legacy(path)
        parsed["inspection"] = {}
        parsed.setdefault("errors", []).append({
            "step": "inspector",
            "error": f"{type(exc).__name__}: {exc}",
            "recovered": "fell back to legacy parser",
        })
        return parsed

    parsed = to_parsed(inv)
    parsed["inspection"] = inspection_payload(inv)
    log.info("inspector: %d datasources, %d fields, %d worksheets, %d dashboards",
             len(parsed["datasources"]),
             sum(len(d["columns"]) for d in parsed["datasources"]),
             len(parsed["worksheets"]), len(parsed["dashboards"]))
    return parsed
