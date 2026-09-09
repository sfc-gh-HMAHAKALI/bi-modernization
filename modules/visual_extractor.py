"""
Visual metadata extractor for BI source files.

Extracts colors, layout coordinates, column aliases, number formats,
parameters, and filter configurations from Tableau (.twb/.twbx),
Power BI (.pbix/.pbit), and Looker (LookML) files.

Produces a visuals.json sidecar that generators use for pixel-accurate
reproduction of the original dashboard styling.

CLI: python3 -m modules.cli extract-visuals /path/to/file.twb -o visuals.json
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("bi-modernization.visual_extractor")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(source_path: str, output_path: str | None = None) -> dict[str, Any]:
    """Extract visual metadata from a BI source file.

    Returns a structured dict and optionally writes it to *output_path*.
    """
    p = Path(source_path)
    suffix = p.suffix.lower()

    if suffix in (".twb", ".twbx", ".tds", ".tdsx"):
        result = _extract_tableau(source_path)
    elif suffix in (".pbix", ".pbit"):
        result = _extract_powerbi(source_path)
    else:
        result = _empty_visuals("unknown")
        result["warnings"] = [f"Unsupported file type: {suffix}"]

    result["source_file"] = source_path

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info("Visuals written to %s", output_path)

    return result


def _empty_visuals(source_type: str) -> dict:
    return {
        "status": "ok",
        "source_type": source_type,
        "global_styles": {},
        "color_mappings": {},
        "column_aliases": {},
        "number_formats": {},
        "dashboard_layouts": {},
        "worksheet_filters": {},
        "parameters": [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Tableau extraction
# ---------------------------------------------------------------------------

def _extract_tableau(path: str) -> dict:
    try:
        import defusedxml.ElementTree as ET
    except ImportError:
        import xml.etree.ElementTree as ET  # type: ignore[no-redef]

    result = _empty_visuals("tableau")
    xml_bytes = _read_tableau_xml(path)
    if xml_bytes is None:
        result["status"] = "error"
        result["warnings"].append("Could not read TWB XML")
        return result

    root = ET.fromstring(xml_bytes)

    # 1. Color mappings from annotations
    result["color_mappings"] = _extract_color_mappings(root)

    # 2. Column aliases (Calculation_ID → display name)
    result["column_aliases"] = _extract_column_aliases(root)

    # 3. Global styles (background, number formats)
    styles = _extract_global_styles(root)
    result["global_styles"] = styles["global"]
    result["number_formats"] = styles["number_formats"]

    # 4. Dashboard layouts (zone coordinates)
    result["dashboard_layouts"] = _extract_dashboard_layouts(root)

    # 5. Worksheet filter configurations
    result["worksheet_filters"] = _extract_worksheet_filters(root)

    # 6. Parameters (from Parameters datasource)
    result["parameters"] = _extract_parameters(root)

    # 7. All unique non-default hex colors (for reference)
    all_colors = _extract_all_hex_colors(xml_bytes.decode("utf-8", errors="replace"))
    result["all_hex_colors"] = sorted(all_colors)

    return result


def _read_tableau_xml(path: str) -> bytes | None:
    p = Path(path)
    if p.suffix.lower() in (".twbx", ".tdsx"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                for member in zf.namelist():
                    if member.endswith(".twb") or member.endswith(".tds"):
                        return zf.read(member)
        except zipfile.BadZipFile:
            logger.warning("Could not open %s as zip — trying as plain XML", path)
    return p.read_bytes()


def _extract_color_mappings(root) -> dict[str, dict[str, str]]:
    """Extract value→color mappings from annotations and color-one-way-maps.

    Tableau encodes categorical color assignments in two places:
    1. Dashboard annotation text: ``<run fontcolor="#59a14f">Low (L) = Green</run>``
    2. Worksheet color-one-way-map: ``<entry key="L" value="#59a14f"/>``
    """
    mappings: dict[str, dict[str, str]] = {}

    # Strategy 1: Annotation text patterns
    # Looks for patterns like "Low (L) = Green" with fontcolor attribute
    annotation_pairs: list[tuple[str, str]] = []  # (text, hex_color)
    for run in root.iter("run"):
        fontcolor = run.get("fontcolor", "")
        text = (run.text or "").strip()
        if fontcolor and text and fontcolor.startswith("#"):
            annotation_pairs.append((text, fontcolor))

    # Try to group annotation color mappings by detecting category patterns
    # Pattern: "CategoryName (CODE)" or "CODE = description"
    category_colors: dict[str, str] = {}
    for text, color in annotation_pairs:
        # Pattern: "Low (L)" or "Low (L) = Green"
        m = re.match(r".*?\(([A-Z0-9])\)", text)
        if m:
            code = m.group(1)
            category_colors[code] = color
            continue
        # Pattern: "L = Green" or "L: Green"
        m = re.match(r"^([A-Z0-9])\s*[=:]\s*", text)
        if m:
            code = m.group(1)
            category_colors[code] = color

    if category_colors:
        # Try to infer the field name from the annotation context
        # Default to a generic name; the LLM can refine
        field_name = _infer_color_field_name(root, category_colors)
        mappings[field_name] = category_colors

    # Strategy 2: color-one-way-map entries (explicit Tableau encoding)
    for ws in root.findall(".//worksheet"):
        ws_name = ws.get("name", "")
        for entry in ws.findall(".//color-one-way-map/entry"):
            key = entry.get("key", "")
            value = entry.get("value", "")
            if key and value and value.startswith("#"):
                if ws_name not in mappings:
                    mappings[ws_name] = {}
                mappings[ws_name][key] = value

    # Strategy 3: encoding type="color" with a field reference
    for ws in root.findall(".//worksheet"):
        for enc in ws.findall(".//encoding"):
            if enc.get("type") == "color":
                field = enc.get("field", "")
                palette = enc.get("palette", "")
                if field and palette:
                    logger.info("Color encoding: ws=%s field=%s palette=%s",
                                ws.get("name"), field, palette)

    return mappings


def _infer_color_field_name(root, category_colors: dict[str, str]) -> str:
    """Try to determine which field the color categories belong to.

    Looks at calculated fields in datasources to find one whose formula
    produces the same set of category codes.
    """
    codes = set(category_colors.keys())

    for ds in root.findall(".//datasource"):
        for col in ds.findall(".//column"):
            calc = col.find("calculation")
            if calc is None:
                continue
            formula = calc.get("formula", "")
            # Check if the formula produces the same codes as string literals
            formula_codes = set(re.findall(r'"([A-Z0-9])"', formula))
            if codes and codes.issubset(formula_codes):
                caption = col.get("caption", "")
                name = col.get("name", "").strip("[]")
                return caption or name

    return "category_color"


def _extract_column_aliases(root) -> dict[str, str]:
    """Extract Calculation_ID → display name aliases from all datasources."""
    aliases: dict[str, str] = {}

    for ds in root.findall(".//datasource"):
        # Method 1: <aliases><alias key="calc_id" value="Display Name">
        for alias in ds.findall(".//aliases/alias"):
            key = alias.get("key", "")
            value = alias.get("value", "")
            if key and value:
                # Strip datasource prefix if present
                # Format: "[ds_name].[usr:Calculation_123:qk]" → "Calculation_123"
                calc_match = re.search(r"Calculation_\d+", key)
                if calc_match:
                    aliases[calc_match.group(0)] = value
                else:
                    # Store the raw key→value too
                    clean_key = key.strip('"').strip("[]")
                    if clean_key != value:
                        aliases[clean_key] = value

        # Method 2: <column caption="Display Name" name="[Calculation_123]">
        for col in ds.findall(".//column"):
            name = col.get("name", "").strip("[]")
            caption = col.get("caption", "")
            if caption and name and name != caption:
                aliases[name] = caption

    return aliases


def _extract_global_styles(root) -> dict:
    """Extract background colors, number formats, and other global style rules."""
    global_styles: dict[str, Any] = {}
    number_formats: dict[str, str] = {}

    # Background colors from worksheet style rules
    bg_colors: set[str] = set()
    for ws in root.findall(".//worksheet"):
        for fmt in ws.findall(".//style-rule[@element='table']/format"):
            if fmt.get("attr") == "background-color":
                bg_colors.add(fmt.get("value", ""))
        # Number formats
        for fmt in ws.findall(".//style-rule/format"):
            if fmt.get("attr") == "text-format":
                value = fmt.get("value", "")
                if value:
                    # Try to associate with a field name from the parent context
                    parent = fmt.getparent() if hasattr(fmt, "getparent") else None
                    number_formats[f"format_{len(number_formats)}"] = value

    # Dashboard background colors
    for dash in root.findall(".//dashboard"):
        for fmt in dash.findall(".//format"):
            if fmt.get("attr") == "background-color":
                val = fmt.get("value", "")
                if val:
                    bg_colors.add(val)

    if bg_colors:
        # Use the most common non-white background
        bg_colors.discard("#ffffff")
        bg_colors.discard("#FFFFFF")
        if bg_colors:
            global_styles["background_color"] = sorted(bg_colors)[0]

    # Look for accent color (used in bullet points / annotations)
    accent_colors: dict[str, int] = {}
    for run in root.iter("run"):
        fc = run.get("fontcolor", "")
        text = (run.text or "").strip()
        if fc and fc.startswith("#") and text in ("•", "·", "●"):
            accent_colors[fc] = accent_colors.get(fc, 0) + 1
    if accent_colors:
        global_styles["accent_color"] = max(accent_colors, key=accent_colors.get)

    return {"global": global_styles, "number_formats": number_formats}


def _extract_dashboard_layouts(root) -> dict[str, dict]:
    """Extract zone positions from each dashboard."""
    layouts: dict[str, dict] = {}

    for dash in root.findall(".//dashboard"):
        dname = dash.get("name", "")
        size = dash.find("size")
        dash_info: dict[str, Any] = {}
        if size is not None:
            dash_info["width"] = int(size.get("maxwidth", size.get("width", "0")) or "0")
            dash_info["height"] = int(size.get("maxheight", size.get("height", "0")) or "0")

        zones: list[dict] = []
        for zone in dash.iter("zone"):
            name = zone.get("name", "")
            x = zone.get("x", "")
            y = zone.get("y", "")
            w = zone.get("w", "")
            h = zone.get("h", "")
            ztype = zone.get("type-v2", zone.get("type", ""))

            # Only include zones that have positional data and a name
            if name and (x or y or w or h):
                zones.append({
                    "name": name,
                    "type": ztype,
                    "x": int(x) if x else 0,
                    "y": int(y) if y else 0,
                    "w": int(w) if w else 0,
                    "h": int(h) if h else 0,
                })

        if zones:
            dash_info["zones"] = zones
        if dash_info:
            layouts[dname] = dash_info

    return layouts


def _extract_worksheet_filters(root) -> dict[str, list[dict]]:
    """Extract filter configurations per worksheet."""
    filters: dict[str, list[dict]] = {}

    for ws in root.findall(".//worksheet"):
        ws_name = ws.get("name", "")
        ws_filters: list[dict] = []

        for f in ws.findall(".//filter"):
            col = f.get("column", "")
            cls = f.get("class", "")
            if not col:
                continue

            # Parse column reference: [datasource].[field]
            ds_name = ""
            field_name = col
            if "].[" in col:
                parts = col.split("].[", 1)
                ds_name = parts[0].lstrip("[")
                field_name = parts[1].rstrip("]")

            # Strip Tableau qualifiers: none:FIELD:nk → FIELD
            field_clean = field_name
            for prefix in ("none:", "usr:", "sum:", "avg:", "cnt:"):
                if field_clean.lower().startswith(prefix):
                    field_clean = field_clean[len(prefix):]
            if ":" in field_clean:
                field_clean = field_clean.split(":")[0]

            # Extract allowed values from groupfilter members
            values: list[str] = []
            for member in f.findall(".//groupfilter[@function='member']"):
                val = member.get("member", "")
                if val:
                    values.append(val)

            ws_filters.append({
                "column": field_clean,
                "datasource": ds_name,
                "class": cls,
                "values": values if values else None,
            })

        if ws_filters:
            filters[ws_name] = ws_filters

    return filters


def _extract_parameters(root) -> list[dict]:
    """Extract parameters from the Parameters datasource.

    In Tableau, parameters are stored as <column> elements within the
    datasource named "Parameters", not in a <parameters> top-level element.
    """
    params: list[dict] = []

    for ds in root.findall(".//datasource"):
        ds_name = ds.get("name", "")
        if ds_name.lower() != "parameters":
            continue

        for col in ds.findall("column"):
            name = col.get("name", "").strip("[]")
            caption = col.get("caption", "")
            datatype = col.get("datatype", "")
            default_value = col.get("value", "")
            domain_type = col.get("param-domain-type", "")

            # Extract allowed values
            values: list[dict] = []
            for member in col.findall(".//member"):
                val = member.get("value", "")
                alias = member.get("alias", "")
                values.append({"value": val, "alias": alias or val})

            # Extract aliases
            aliases: dict[str, str] = {}
            for alias_el in col.findall(".//aliases/alias"):
                key = alias_el.get("key", "")
                val = alias_el.get("value", "")
                if key and val:
                    aliases[key] = val

            params.append({
                "name": caption or name,
                "internal_name": name,
                "datatype": datatype,
                "default": default_value,
                "domain_type": domain_type,
                "values": values,
                "aliases": aliases,
            })

    return params


def _extract_all_hex_colors(content: str) -> set[str]:
    """Find all unique hex colors in the file, excluding common defaults."""
    defaults = {"#000000", "#ffffff", "#FFFFFF", "#f5f5f5", "#F5F5F5",
                "#333333", "#666666", "#999999", "#cccccc", "#eeeeee"}
    all_colors = set(re.findall(r"#[0-9a-fA-F]{6}", content))
    return all_colors - defaults


# ---------------------------------------------------------------------------
# Power BI extraction (stub — expand as needed)
# ---------------------------------------------------------------------------

def _extract_powerbi(path: str) -> dict:
    """Extract visual metadata from Power BI files.

    Power BI theme colors and visual overrides are in the report layout JSON.
    """
    result = _empty_visuals("powerbi")
    result["warnings"].append("Power BI visual extraction is a stub — colors will use defaults")

    try:
        from pbixray import PBIXRay
        model = PBIXRay(path)
        # Theme colors are in the report layout
        if hasattr(model, "theme") and model.theme:
            theme = model.theme
            if isinstance(theme, dict):
                data_colors = theme.get("dataColors", [])
                if data_colors:
                    result["global_styles"]["data_colors"] = data_colors
    except Exception as e:
        result["warnings"].append(f"Could not parse PBI theme: {e}")

    return result
