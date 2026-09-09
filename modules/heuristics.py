"""
Field-composition heuristics for inferring chart type when the source
BI tool does not provide an explicit visual/mark type.

Used when Tableau mark class is "Automatic" or unknown, or for sources
like Denodo/SAP BO that have no dashboard concept at all.
"""

from __future__ import annotations

# Field role constants (as they appear in the unified inventory)
_DIMENSION_ROLES = {"dimension", "dim"}
_MEASURE_ROLES   = {"measure", "fact", "metric"}
_DATE_TYPES      = {"date", "datetime", "timestamp", "timestamp_ntz", "timestamp_ltz",
                    "timestamp_tz", "time"}
_GEO_KEYWORDS    = ("lat", "lon", "latitude", "longitude", "geo", "geom",
                    "country", "state", "region", "city", "zip", "postal", "province")
_ID_SUFFIXES     = ("_id", "_key", "_sk", "_nk", "_pk", "id", "key")


def _is_likely_id(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(sfx) for sfx in _ID_SUFFIXES)


def _is_date(field: dict) -> bool:
    dt = (field.get("data_type") or field.get("type") or
          field.get("original", {}).get("datatype") or "").lower()
    return dt in _DATE_TYPES or "date" in dt or "time" in dt


def _is_geo(field: dict) -> bool:
    n = (field.get("name") or field.get("column") or "").lower()
    return any(kw in n for kw in _GEO_KEYWORDS)


def _is_dimension(field: dict) -> bool:
    role = (field.get("role") or field.get("original", {}).get("role") or "").lower()
    return role in _DIMENSION_ROLES


def _is_measure(field: dict) -> bool:
    role = (field.get("role") or field.get("original", {}).get("role") or "").lower()
    return role in _MEASURE_ROLES


def infer_chart_type(fields: list[dict]) -> str:
    """
    Infer a Plotly render type string from a list of field dicts.

    Each field dict is expected to have at minimum:
        name      (str)
        role      ("dimension" | "measure" | "fact" | "metric")
        data_type (str, optional)

    Returns one of the Plotly render type strings defined in chart_mappings.py.
    Falls back to "bar" when nothing better can be determined.
    """
    if not fields:
        return "metric"

    dims     = [f for f in fields if _is_dimension(f)]
    measures = [f for f in fields if _is_measure(f)]
    dates    = [f for f in fields if _is_date(f)]
    geos     = [f for f in fields if _is_geo(f)]

    # Single measure, no grouping → KPI card
    if len(measures) == 1 and not dims:
        return "metric"

    # No measures at all → show as table
    if not measures:
        return "table"

    # Geographic fields → map
    if geos:
        return "map_scatter"

    # Date dimension + measure(s) → line chart (trend)
    if dates and measures:
        return "line"

    # Two measures, no categorical dim → scatter
    if len(measures) >= 2 and not dims:
        return "scatter"

    # Single categorical dimension (low cardinality hint) with 1 measure → bar
    if len(dims) == 1 and len(measures) == 1:
        dim_name = (dims[0].get("name") or "").lower()
        # If the sole dim looks like an ID, prefer table
        if _is_likely_id(dim_name):
            return "table"
        return "bar"

    # Many dimensions, few measures → horizontal bar (fits long labels)
    if len(dims) >= 3 and len(measures) <= 2:
        return "bar_h"

    # Multiple measures, one dimension → grouped bar
    if len(dims) == 1 and len(measures) >= 2:
        return "bar"

    # Default: vertical bar chart
    return "bar"


def infer_filter_widgets(filter_fields: list[dict]) -> list[dict]:
    """
    Suggest sidebar filter widget types from extracted filter field dicts.

    Returns a list of widget spec dicts with keys:
        field_name, widget_type, label
    """
    widgets = []
    for f in filter_fields:
        name = f.get("name") or f.get("column") or "Unknown"
        label = name.replace("_", " ").title()

        if _is_date(f):
            widget_type = "date_range"
        elif (f.get("data_type") or "").lower() in ("boolean", "bool"):
            widget_type = "checkbox"
        elif f.get("values"):  # explicit value list from BI tool
            values = f["values"]
            widget_type = "selectbox" if len(values) <= 5 else "multiselect"
        else:
            widget_type = "multiselect"  # safe default for categoricals

        widgets.append({"field_name": name, "label": label, "widget_type": widget_type})
    return widgets
