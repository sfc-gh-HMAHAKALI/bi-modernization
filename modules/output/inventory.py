"""Unified inventory builder that normalizes parsed output from any source.

Takes source-specific parsed data and produces a standardized inventory
structure that the YAML generator consumes.
"""

import json
from typing import Any

from ..common.logger import get_logger
from ..common.errors import ValidationError, fail_step

log = get_logger("inventory")


def build_unified_inventory(
    parsed_data: dict,
    source_type: str,
    snowflake_target: dict | None = None,
) -> dict:
    """Convert source-specific parsed data into a unified inventory.

    Args:
        parsed_data: Output from a source-specific parser (tableau, looker, etc.)
        source_type: One of 'tableau', 'looker', 'powerbi', 'denodo', 'businessobjects'
        snowflake_target: Optional dict with {database, schema} for target mapping.

    Returns:
        Unified inventory dict.
    """
    log.info("Building unified inventory from %s source.", source_type)

    sf_db = (snowflake_target or {}).get("database", "TARGET_DB")
    sf_schema = (snowflake_target or {}).get("schema", "PUBLIC")

    inventory = {
        "source_type": source_type,
        "snowflake_target": {"database": sf_db, "schema": sf_schema},
        "tables": [],
        "relationships": [],
        "dimensions": [],
        "facts": [],
        "metrics": [],
        "filters": [],
        "flagged": [],
        "complexity_summary": {
            "simple": 0,
            "needs_translation": 0,
            "manual_required": 0,
        },
        "errors": parsed_data.get("errors", []),
    }

    try:
        router = {
            "tableau": _from_tableau,
            "looker": _from_looker,
            "powerbi": _from_powerbi,
            "denodo": _from_denodo,
            "businessobjects": _from_businessobjects,
        }
        builder = router.get(source_type)
        if builder is None:
            raise ValidationError(
                f"Unknown source_type: {source_type}",
                context={"source_type": source_type},
            )
        builder(parsed_data, inventory)
    except Exception as e:
        record = fail_step("build_unified_inventory", e, partial_results=inventory)
        inventory["errors"].append(record)

    # Tally complexity
    for item_list in [inventory["dimensions"], inventory["facts"], inventory["metrics"]]:
        for item in item_list:
            c = item.get("complexity", "simple")
            if c in inventory["complexity_summary"]:
                inventory["complexity_summary"][c] += 1

    # Preserve dashboard and worksheet structure from the parser so downstream
    # consumers (enrich-charts, generate-streamlit, generate-react, build-agent)
    # can use the full dashboard/worksheet topology.  The builder functions above
    # only populate dimensions/facts/metrics — they never write these keys.
    inventory["dashboards"] = parsed_data.get("dashboards", [])
    inventory["worksheets"] = parsed_data.get("worksheets", [])

    log.info(
        "Unified inventory built: %d tables, %d dimensions, %d facts, %d metrics, "
        "%d flagged, %d dashboards, %d worksheets, complexity=%s",
        len(inventory["tables"]),
        len(inventory["dimensions"]),
        len(inventory["facts"]),
        len(inventory["metrics"]),
        len(inventory["flagged"]),
        len(inventory["dashboards"]),
        len(inventory["worksheets"]),
        inventory["complexity_summary"],
    )

    return inventory


def merge_inventories(inventories: list[dict]) -> dict:
    """Merge multiple inventories (e.g., from multiple source files) into one.

    Tables are deduped by name. Dimensions/facts/metrics are appended.
    Flagged items and errors are merged.
    """
    log.info("Merging %d inventories.", len(inventories))

    merged = {
        "source_type": "merged",
        "snowflake_target": inventories[0].get("snowflake_target", {}) if inventories else {},
        "tables": [],
        "relationships": [],
        "dimensions": [],
        "facts": [],
        "metrics": [],
        "filters": [],
        "flagged": [],
        "complexity_summary": {"simple": 0, "needs_translation": 0, "manual_required": 0},
        "errors": [],
    }

    seen_tables: set[str] = set()

    for inv in inventories:
        for table in inv.get("tables", []):
            tname = table.get("name", "")
            if tname and tname not in seen_tables:
                merged["tables"].append(table)
                seen_tables.add(tname)

        merged["relationships"].extend(inv.get("relationships", []))
        merged["dimensions"].extend(inv.get("dimensions", []))
        merged["facts"].extend(inv.get("facts", []))
        merged["metrics"].extend(inv.get("metrics", []))
        merged["filters"].extend(inv.get("filters", []))
        merged["flagged"].extend(inv.get("flagged", []))
        merged["errors"].extend(inv.get("errors", []))

        for k in ("simple", "needs_translation", "manual_required"):
            merged["complexity_summary"][k] += inv.get("complexity_summary", {}).get(k, 0)

    log.info(
        "Merged result: %d tables, %d dimensions, %d facts, %d metrics.",
        len(merged["tables"]),
        len(merged["dimensions"]),
        len(merged["facts"]),
        len(merged["metrics"]),
    )

    return merged


def save_inventory(inventory: dict, output_path: str) -> str:
    """Save inventory to JSON file."""
    log.info("Saving inventory to %s", output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, default=str)
    log.info("Inventory saved: %s", output_path)
    return output_path


def load_inventory(input_path: str) -> dict:
    """Load inventory from JSON file."""
    log.info("Loading inventory from %s", input_path)
    with open(input_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)
    log.info(
        "Inventory loaded: %d tables, %d dimensions, %d facts, %d metrics.",
        len(inventory.get("tables", [])),
        len(inventory.get("dimensions", [])),
        len(inventory.get("facts", [])),
        len(inventory.get("metrics", [])),
    )
    return inventory


# ---------------------------------------------------------------------------
# Source-specific normalizers
# ---------------------------------------------------------------------------

def _normalize_item(
    name: str,
    expr: str,
    data_type: str = "VARCHAR",
    table: str = "",
    description: str = "",
    synonyms: list[str] | None = None,
    complexity: str = "simple",
    original: dict | None = None,
    source_view: str = "",
    dashboard_name: str = "",
    page_name: str = "",
    widget_name: str = "",
    source_file: str = "",
) -> dict:
    """Build a normalized dimension/fact/metric dict."""
    return {
        "name": name.upper().replace(" ", "_"),
        "expr": expr,
        "data_type": data_type,
        "table": table,
        "description": description,
        "synonyms": synonyms or [],
        "complexity": complexity,
        "original": original or {},
        "source_view": source_view,
        "dashboard_name": dashboard_name,
        "page_name": page_name,
        "widget_name": widget_name,
        "source_file": source_file,
    }


def _from_tableau(parsed: dict, inv: dict) -> None:
    """Normalize Tableau parsed data into unified inventory."""

    # Build reverse provenance index: (datasource, field_name) → [(dashboard, worksheet)]
    # from the dashboards data produced by extract_dashboard_field_usage().
    _prov: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for db in parsed.get("dashboards", []):
        db_name = db.get("name", "")
        for sheet in db.get("sheets", []):
            ws_name = sheet.get("name", "")
            for ds_key, fields in sheet.get("fields_by_datasource", {}).items():
                for field in fields:
                    _prov.setdefault((ds_key, field), []).append((db_name, ws_name))

    for ds in parsed.get("datasources", []):
        ds_name = ds.get("name", "UNKNOWN")

        # Tables
        for table in ds.get("tables", []):
            inv["tables"].append({
                "name": table.get("name", "").strip("[]").replace(".", "_").upper(),
                "physical_table": table.get("name", ""),
                "connection": ds.get("connection", {}),
            })

        # Joins
        for join in ds.get("joins", []):
            inv["relationships"].append({
                "left_table": join.get("left_table", "").strip("[]").upper(),
                "right_table": join.get("right_table", "").strip("[]").upper(),
                "join_type": join.get("type", "inner"),
                "condition": join.get("clause", ""),
            })

        # Columns
        for col in ds.get("columns", []):
            role = col.get("role", "dimension")
            complexity = col.get("complexity", "simple")

            # Resolve dashboard/worksheet provenance for this field
            col_name = col.get("name", "")
            usages = _prov.get((ds_name, col_name), [])
            dash_name = ", ".join(sorted({u[0] for u in usages})) if usages else ""
            page_name = ", ".join(sorted({u[1] for u in usages})) if usages else ""

            item = _normalize_item(
                name=col.get("caption", col_name),
                expr=col.get("formula", f"{ds_name}.{col_name}"),
                data_type=_map_tableau_type(col.get("datatype", "string")),
                table=ds_name,
                description=col.get("desc", ""),
                complexity=complexity,
                original=col,
                source_view=ds_name,
                dashboard_name=dash_name,
                page_name=page_name,
            )
            if role == "measure" or col.get("formula", ""):
                if any(agg in (col.get("formula", "") or "").upper()
                       for agg in ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX(")):
                    inv["metrics"].append(item)
                else:
                    inv["facts"].append(item)
            else:
                inv["dimensions"].append(item)

            if complexity == "manual_required":
                inv["flagged"].append({
                    "item": item["name"],
                    "source": ds_name,
                    "reason": "complex_formula",
                    "expression": col.get("formula", ""),
                })


def _from_looker(parsed: dict, inv: dict) -> None:
    """Normalize Looker parsed data into unified inventory."""

    # -- Build view→explore mapping so fields get dashboard/page labels ------
    # Each explore references a base view (from_view) and joined views.
    # We map view_name → list of (project_dir, explore_name) tuples.
    view_to_explores: dict[str, list[tuple[str, str]]] = {}
    for explore in parsed.get("explores", []):
        if explore.get("hidden"):
            continue
        exp_name = explore.get("name", "")
        # Derive project name from _source_file (e.g. "looker-hr-mart/models/hr.model.lkml")
        src_file = explore.get("_source_file", "")
        project = src_file.split("/")[0] if "/" in src_file else src_file
        # Clean up common prefixes for readability
        if project.startswith("looker_") or project.startswith("looker-"):
            project = project[7:]

        # Base view
        base_view = explore.get("from_view", explore.get("from", exp_name))
        view_to_explores.setdefault(base_view, []).append((project, exp_name))
        # Joined views
        for join in explore.get("joins", []):
            join_view = join.get("from", join.get("name", ""))
            if join_view:
                view_to_explores.setdefault(join_view, []).append((project, exp_name))

    for view in parsed.get("views", []):
        view_name = view.get("name", "UNKNOWN")
        sql_table = (view.get("sql_table_name") or view_name).strip("`").upper()

        inv["tables"].append({
            "name": sql_table,
            "physical_table": view.get("sql_table_name", ""),
            "view_name": view_name,
        })

        # Resolve dashboard/page labels from explore mapping
        explore_refs = view_to_explores.get(view_name, [])
        if explore_refs:
            # dashboard_name = unique project names
            projects = sorted({p for p, _ in explore_refs})
            dash_name = ", ".join(projects)
            # page_name = unique explore names
            exp_names = sorted({e for _, e in explore_refs})
            page_name = ", ".join(exp_names)
        else:
            # View not referenced by any explore — use source_file path
            src = view.get("_source_file", "")
            project = src.split("/")[0] if "/" in src else ""
            if project.startswith("looker_") or project.startswith("looker-"):
                project = project[7:]
            dash_name = project
            page_name = ""

        for dim in view.get("dimensions", []):
            if dim.get("hidden"):
                continue
            item = _normalize_item(
                name=dim.get("name", ""),
                expr=(dim.get("sql") or "").replace("${TABLE}", sql_table),
                data_type=_map_looker_type(dim.get("type", "string")),
                table=sql_table,
                description=dim.get("description", ""),
                synonyms=[dim["label"]] if dim.get("label") else [],
                complexity=dim.get("complexity", "simple"),
                original=dim,
                source_view=view_name,
                dashboard_name=dash_name,
                page_name=page_name,
            )
            if dim.get("primary_key"):
                item["primary_key"] = True
            inv["dimensions"].append(item)

        for measure in view.get("measures", []):
            if measure.get("hidden"):
                continue
            inv["metrics"].append(_normalize_item(
                name=measure.get("name", ""),
                expr=(measure.get("sql") or "").replace("${TABLE}", sql_table),
                data_type="NUMBER",
                table=sql_table,
                description=measure.get("description", ""),
                synonyms=[measure["label"]] if measure.get("label") else [],
                complexity=measure.get("complexity", "simple"),
                original=measure,
                source_view=view_name,
                dashboard_name=dash_name,
                page_name=page_name,
            ))

    # Joins from explores
    for explore in parsed.get("explores", []):
        for join in explore.get("joins", []):
            inv["relationships"].append({
                "left_table": explore.get("from", explore.get("name", "")).upper(),
                "right_table": join.get("name", "").upper(),
                "join_type": join.get("type", "left_outer"),
                "relationship_type": join.get("relationship", "many_to_one"),
                "condition": join.get("sql_on", ""),
            })


def _from_powerbi(parsed: dict, inv: dict) -> None:
    """Normalize Power BI parsed data into unified inventory."""

    # Build reverse provenance index: queryRef → [(page_display_name, visual_type)]
    # from the report_pages data produced by extract_report_pages().
    # queryRefs look like "Table.Column" or "Table.Measure".
    _prov: dict[str, list[tuple[str, str]]] = {}
    for page in parsed.get("report_pages", []):
        page_display = page.get("display_name", page.get("name", ""))
        for visual in page.get("visuals", []):
            vtype = visual.get("type", "")
            for qref in visual.get("fields", []):
                _prov.setdefault(qref, []).append((page_display, vtype))

    def _resolve_pbi_prov(table_name: str, col_name: str) -> tuple[str, str, str]:
        """Look up page/visual provenance for a Power BI field."""
        # Try exact queryRef match: "TableName.ColumnName"
        qref = f"{table_name}.{col_name}"
        usages = _prov.get(qref, [])
        if not usages:
            # Try with original (non-uppercased) table name
            qref_orig = f"{table_name}.{col_name}"
            usages = _prov.get(qref_orig, [])
        if usages:
            pages = ", ".join(sorted({u[0] for u in usages}))
            widgets = ", ".join(sorted({u[1] for u in usages if u[1] and u[1] != "unknown"}))
            return ("", pages, widgets)
        return ("", "", "")

    for table in parsed.get("tables", []):
        tname = table.get("name", "UNKNOWN").upper()
        tname_orig = table.get("name", "UNKNOWN")
        inv["tables"].append({
            "name": tname,
            "physical_table": table.get("name", ""),
            "source_query": table.get("source_query"),
        })

        for col in table.get("columns", []):
            col_name = col.get("name", "")
            _, pg, wg = _resolve_pbi_prov(tname_orig, col_name)
            item = _normalize_item(
                name=col_name,
                expr=f"{tname}.{col.get('source_column', col_name)}",
                data_type=_map_pbi_type(col.get("data_type", "string")),
                table=tname,
                complexity=col.get("complexity", "simple"),
                original=col,
                source_view=table.get("name", ""),
                page_name=pg,
                widget_name=wg,
            )
            if col.get("summarize_by", "none") != "none":
                inv["facts"].append(item)
            else:
                inv["dimensions"].append(item)

    for measure in parsed.get("measures", []):
        m_table = measure.get("table", "")
        m_name = measure.get("name", "")
        _, pg, wg = _resolve_pbi_prov(m_table, m_name)
        item = _normalize_item(
            name=m_name,
            expr=measure.get("expression", ""),
            data_type="NUMBER",
            table=m_table,
            description=measure.get("description", ""),
            complexity=measure.get("complexity", "simple"),
            original=measure,
            source_view=m_table,
            page_name=pg,
            widget_name=wg,
        )
        # Propagate DAX enrichment detail if present
        dax_detail = measure.get("dax_detail")
        if dax_detail:
            item["dax_patterns"] = ", ".join(dax_detail.get("matched_patterns", []))
            item["dax_categories"] = ", ".join(dax_detail.get("categories", []))
            item["dax_description"] = dax_detail.get("description", "")
            item["dax_recommendation"] = dax_detail.get("recommendation", "")
            item["dax_effort"] = dax_detail.get("effort", "")
        inv["metrics"].append(item)

    for rel in parsed.get("relationships", []):
        inv["relationships"].append({
            "left_table": rel.get("from_table", "").upper(),
            "left_column": rel.get("from_column", ""),
            "right_table": rel.get("to_table", "").upper(),
            "right_column": rel.get("to_column", ""),
            "relationship_type": rel.get("cardinality", "many_to_one"),
            "is_active": rel.get("is_active", True),
        })

    for flagged in parsed.get("flagged", []):
        inv["flagged"].append(flagged)


def _from_denodo(parsed: dict, inv: dict) -> None:
    """Normalize Denodo parsed data into unified inventory."""
    for view in parsed.get("views", []):
        vname = view.get("name", "UNKNOWN").upper()
        inv["tables"].append({
            "name": vname,
            "physical_table": view.get("name", ""),
            "view_type": view.get("type", "DERIVED"),
        })

        for col in view.get("select_columns", []):
            col_name = col.get("alias", col.get("expression", ""))
            classification = col.get("classification", "dimension")
            item = _normalize_item(
                name=col_name,
                expr=col.get("expression", f"{vname}.{col_name}"),
                table=vname,
                complexity=col.get("complexity", "simple"),
                original=col,
                source_view=view.get("name", ""),
            )
            if classification == "metric":
                inv["facts"].append(item)
            else:
                inv["dimensions"].append(item)

        for join in view.get("joins", []):
            inv["relationships"].append({
                "left_table": join.get("left", "").upper(),
                "right_table": join.get("right", "").upper(),
                "join_type": join.get("type", "inner"),
                "condition": join.get("condition", ""),
            })

        for agg in view.get("aggregations", []):
            inv["metrics"].append(_normalize_item(
                name=agg.get("alias", f"{agg.get('function', '')}_{agg.get('column', '')}"),
                expr=f"{agg.get('function', 'SUM')}({vname}.{agg.get('column', '')})",
                data_type="NUMBER",
                table=vname,
                original=agg,
                source_view=view.get("name", ""),
            ))


def _from_businessobjects(parsed: dict, inv: dict) -> None:
    """Normalize Business Objects parsed data into unified inventory."""
    # Physical tables
    for table in parsed.get("tables", []):
        inv["tables"].append({
            "name": table.get("name", "").upper(),
            "physical_table": table.get("name", ""),
            "schema": table.get("schema", ""),
        })

    # Joins
    for join in parsed.get("joins", []):
        inv["relationships"].append({
            "left_table": join.get("leftTable", "").upper(),
            "right_table": join.get("rightTable", "").upper(),
            "join_type": join.get("type", "inner"),
            "condition": join.get("expression", ""),
            "cardinality": join.get("cardinality", ""),
        })

    # Business objects
    for obj in parsed.get("objects", []):
        qual = obj.get("qualification", "dimension").lower()
        complexity = obj.get("complexity", "simple")
        item = _normalize_item(
            name=obj.get("name", ""),
            expr=obj.get("resolved_sql", obj.get("select_expression", "")),
            data_type=_map_bo_type(obj.get("data_type", "string")),
            table=obj.get("table", ""),
            description=obj.get("description", ""),
            complexity=complexity,
            original=obj,
            source_view=obj.get("class", obj.get("table", "")),
        )

        if qual == "measure":
            inv["metrics"].append(item)
        elif qual == "detail":
            inv["dimensions"].append(item)
        elif qual == "filter":
            inv["filters"].append(item)
        else:
            inv["dimensions"].append(item)

        if complexity == "manual_required":
            inv["flagged"].append({
                "item": item["name"],
                "reason": obj.get("flag_reason", "complex_expression"),
                "expression": obj.get("select_expression", ""),
            })


# ---------------------------------------------------------------------------
# Type mappers
# ---------------------------------------------------------------------------

def _map_tableau_type(dt: str) -> str:
    return {"string": "VARCHAR", "integer": "NUMBER", "real": "NUMBER",
            "boolean": "BOOLEAN", "date": "DATE", "datetime": "TIMESTAMP"}.get(dt, "VARCHAR")

def _map_looker_type(dt: str) -> str:
    return {"string": "VARCHAR", "number": "NUMBER", "yesno": "BOOLEAN",
            "date": "DATE", "time": "TIMESTAMP", "date_time": "TIMESTAMP",
            "zipcode": "VARCHAR"}.get(dt, "VARCHAR")

def _map_pbi_type(dt: str) -> str:
    return {"string": "VARCHAR", "int64": "NUMBER", "double": "NUMBER",
            "decimal": "NUMBER", "boolean": "BOOLEAN", "dateTime": "TIMESTAMP",
            "binary": "VARCHAR"}.get(dt, "VARCHAR")

def _map_bo_type(dt: str) -> str:
    return {"Character": "VARCHAR", "Numeric": "NUMBER", "Date": "DATE",
            "DateTime": "TIMESTAMP", "Long text": "VARCHAR"}.get(dt, "VARCHAR")
