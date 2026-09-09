"""
Streamlit-in-Snowflake app generator.

Takes an enriched inventory JSON and generates a deployable multi-page
Streamlit app with enterprise-quality Plotly Express visualizations.

Outputs:
  home.py                        — st.navigation() entry point
  dashboard_{slug}.py            — one file per dashboard
  requirements.txt               — plotly, pandas (streamlit already present in SiS)
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any

from .chart_mappings import plotly_render_spec
from .heuristics import infer_filter_widgets

# Snowflake brand palette for Plotly charts
_SNOWFLAKE_COLORS = [
    "#29B5E8",  # Snowflake blue
    "#11567F",  # dark blue
    "#00A4EF",  # sky blue
    "#5EC6E8",  # light blue
    "#003865",  # navy
    "#6DD0F0",  # pale blue
    "#0076BE",  # medium blue
    "#1ED7AA",  # teal accent
]
_COLOR_SEQ_STR = json.dumps(_SNOWFLAKE_COLORS)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _sanitize_sheet_name(raw_name: str) -> tuple[str, bool]:
    """Return (clean_display_name, should_skip).
    should_skip=True for image zones, text-only zones, etc."""
    # Skip image file paths
    if any(raw_name.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.bmp')):
        return raw_name, True
    # Skip blank / whitespace-only names
    if not raw_name.strip():
        return raw_name, True
    # Bracket notation: [datasource].[qualifier:FIELD_NAME:type]
    if raw_name.startswith('[') and '].' in raw_name:
        after_dot = raw_name.split('].', 1)[-1]
        match = re.search(r'\[(?:none:)?([^:\]]+)', after_dot)
        if match:
            return match.group(1).replace('_', ' ').title(), False
    # Plain bracket-wrapped name
    clean = re.sub(r'^\[|\]$', '', raw_name).strip()
    if clean:
        return clean, False
    return raw_name, True


def _field_list(fields_by_ds: dict) -> list[str]:
    """Flatten fields_by_datasource into a plain list of field name strings."""
    out: list[str] = []
    for vals in fields_by_ds.values():
        for v in vals:
            out.append(v if isinstance(v, str) else v.get("name", ""))
    return [f for f in out if f]


def _build_ds_table_map(inventory: dict) -> dict[str, str]:
    """Build a mapping of datasource name -> fully qualified table name."""
    db = inventory.get("snowflake_target", {}).get("database", "DB")
    sch = inventory.get("snowflake_target", {}).get("schema", "SCHEMA")
    ds_map: dict[str, str] = {}
    for t in inventory.get("tables", []):
        ds_name = t.get("datasource", "")
        tname = t.get("snowflake_name") or t.get("name", "TABLE")
        fqn = f"{db}.{sch}.{tname}"
        if ds_name and ds_name not in ds_map:
            ds_map[ds_name] = fqn
        # Also map by physical table name
        pname = t.get("physical_table", "")
        if pname and pname not in ds_map:
            ds_map[pname] = fqn
    return ds_map


def _resolve_table_for_sheet(sheet: dict, ds_table_map: dict[str, str],
                              fallback_table: str) -> str:
    """Pick the best table for a sheet by matching its datasource keys."""
    for ds_key in sheet.get("fields_by_datasource", {}).keys():
        if ds_key in ds_table_map:
            return ds_table_map[ds_key]
        # Try substring match (datasource names can be verbose)
        for map_key, fqn in ds_table_map.items():
            if map_key in ds_key or ds_key in map_key:
                return fqn
    return fallback_table


def _resolve_query_fields(sheet: dict, dims: list[dict], measures: list[dict],
                           table_name: str, chart_type: str) -> dict:
    """
    Choose x/y columns and build a SQL snippet for a given sheet.
    Returns dict with: sql, x_col, y_col, color_col, measure_col, dim_col.
    """
    sheet_fields = _field_list(sheet.get("fields_by_datasource", {}))
    # Strip bracket notation from field names for matching
    clean_fields = set()
    for f in sheet_fields:
        clean = re.sub(r'^\[|\]$', '', f).upper()
        # Also strip Calculation_ prefix for matching
        if not clean.startswith('CALCULATION_'):
            clean_fields.add(clean)

    # Find dims and measures that appear in this sheet
    sheet_dim_names = [d["name"] for d in dims
                       if d.get("name", "").upper() in clean_fields
                       or d.get("name", "") in sheet_fields][:3]
    sheet_measure_names = [m["name"] for m in measures
                           if m.get("name", "").upper() in clean_fields
                           or m.get("name", "") in sheet_fields][:2]

    # Fallback: if nothing matched, use first available dim/measure
    if not sheet_dim_names and dims:
        sheet_dim_names = [dims[0]["name"]]
    if not sheet_measure_names and measures:
        sheet_measure_names = [measures[0]["name"]]

    dim_col = sheet_dim_names[0].upper() if sheet_dim_names else "CATEGORY"
    measure_col = sheet_measure_names[0].upper() if sheet_measure_names else "VALUE"
    color_col = sheet_dim_names[1].upper() if len(sheet_dim_names) > 1 else None

    all_cols = list(dict.fromkeys(sheet_dim_names + sheet_measure_names))
    select_cols = ", ".join(f'"{c.upper()}"' for c in all_cols) if all_cols else "*"

    group_dims = ", ".join(f'"{c.upper()}"' for c in sheet_dim_names)

    if chart_type in ("table", "metric", "metric_table"):
        sql = f'SELECT {select_cols} FROM {table_name} LIMIT 500'
    elif group_dims:
        agg_col = f'SUM("{measure_col}") AS "{measure_col}"'
        dim_select = ", ".join(f'"{c.upper()}"' for c in sheet_dim_names)
        sql = (
            f'SELECT {dim_select}, '
            f'{agg_col} FROM {table_name} WHERE 1=1 '
            f'{{filter_clause}} GROUP BY {group_dims} '
            f'ORDER BY "{measure_col}" DESC LIMIT 500'
        )
    else:
        sql = f'SELECT * FROM {table_name} LIMIT 500'

    return {
        "sql": sql,
        "x_col": dim_col,
        "y_col": measure_col,
        "color_col": color_col,
        "dim_col": dim_col,
        "measure_col": measure_col,
    }


def _chart_code(sheet: dict, df_var: str, field_info: dict, chart_type: str,
                sheet_name: str) -> str:
    """Generate the Plotly chart call and st.plotly_chart for one sheet."""
    spec = plotly_render_spec(chart_type)
    fn = spec.get("fn", "bar")
    x = field_info["x_col"]
    y = field_info["y_col"]
    color = field_info.get("color_col")
    title = sheet_name.replace('"', "'")
    color_arg = f', color="{color}"' if color else ""

    base_kwargs = (
        f'title="{title}", '
        f'color_discrete_sequence={_COLOR_SEQ_STR}, '
        f'template="plotly_white"'
    )

    if fn == "dataframe":
        return f'    st.dataframe({df_var}, use_container_width=True, hide_index=True)\n'

    if fn == "metric":
        return (
            f'    if not {df_var}.empty:\n'
            f'        _val = {df_var}["{y}"].iloc[0]\n'
            f'        st.metric(label="{title}", value=f"{{_val:,.2f}}")\n'
        )

    if fn in ("bar",):
        orientation = spec.get("orientation", "v")
        barmode = spec.get("barmode", "")
        barmode_arg = f', barmode="{barmode}"' if barmode else ""
        if orientation == "h":
            return (
                f'    _fig = px.bar({df_var}, x="{y}", y="{x}"{color_arg}, '
                f'orientation="h", {base_kwargs}{barmode_arg})\n'
                f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
                f'    st.plotly_chart(_fig, use_container_width=True)\n'
            )
        return (
            f'    _fig = px.bar({df_var}, x="{x}", y="{y}"{color_arg}, '
            f'{base_kwargs}{barmode_arg})\n'
            f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "line":
        return (
            f'    _fig = px.line({df_var}, x="{x}", y="{y}"{color_arg}, '
            f'{base_kwargs})\n'
            f'    _fig.update_traces(line_width=2)\n'
            f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "area":
        return (
            f'    _fig = px.area({df_var}, x="{x}", y="{y}"{color_arg}, '
            f'{base_kwargs})\n'
            f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "pie":
        hole_arg = f', hole={spec["hole"]}' if "hole" in spec else ""
        return (
            f'    _fig = px.pie({df_var}, names="{x}", values="{y}"{hole_arg}, '
            f'title="{title}", color_discrete_sequence={_COLOR_SEQ_STR})\n'
            f'    _fig.update_traces(textposition="inside", textinfo="percent+label")\n'
            f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "scatter":
        return (
            f'    _fig = px.scatter({df_var}, x="{x}", y="{y}"{color_arg}, '
            f'{base_kwargs})\n'
            f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "funnel":
        return (
            f'    _fig = px.funnel({df_var}, x="{y}", y="{x}", '
            f'{base_kwargs})\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "treemap":
        return (
            f'    _fig = px.treemap({df_var}, path=["{x}"], values="{y}", '
            f'title="{title}", color_discrete_sequence={_COLOR_SEQ_STR})\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "waterfall":
        return (
            f'    # Waterfall chart — using go.Waterfall\n'
            f'    import plotly.graph_objects as go\n'
            f'    _fig = go.Figure(go.Waterfall(\n'
            f'        x={df_var}["{x}"].tolist(),\n'
            f'        y={df_var}["{y}"].tolist(),\n'
            f'        connector={{"line": {{"color": "#29B5E8"}}}},\n'
            f'    ))\n'
            f'    _fig.update_layout(title="{title}", template="plotly_white", font_family="Inter")\n'
            f'    st.plotly_chart(_fig, use_container_width=True)\n'
        )

    if fn == "indicator":
        return (
            f'    if not {df_var}.empty:\n'
            f'        import plotly.graph_objects as go\n'
            f'        _val = float({df_var}["{y}"].iloc[0])\n'
            f'        _fig = go.Figure(go.Indicator(mode="gauge+number", value=_val, '
            f'title={{"text": "{title}"}}))\n'
            f'        st.plotly_chart(_fig, use_container_width=True)\n'
        )

    # fallback
    return (
        f'    _fig = px.bar({df_var}, x="{x}", y="{y}", {base_kwargs})\n'
        f'    _fig.update_layout(margin=dict(t=40, b=20), font_family="Inter")\n'
        f'    st.plotly_chart(_fig, use_container_width=True)\n'
    )


def _filter_code(filter_fields: list[dict]) -> str:
    """Generate sidebar filter widget code."""
    lines = []
    for f in filter_fields:
        name = f["field_name"]
        label = f["label"]
        wtype = f["widget_type"]
        var = f"_filter_{_slugify(name)}"

        if wtype == "date_range":
            lines.append(
                f'    {var} = st.date_input("{label}", value=None, key="{var}")\n'
            )
        elif wtype == "checkbox":
            lines.append(
                f'    {var} = st.checkbox("{label}", value=True, key="{var}")\n'
            )
        elif wtype == "selectbox":
            lines.append(
                f'    {var} = st.selectbox("{label}", options=[], key="{var}")\n'
            )
        else:  # multiselect
            lines.append(
                f'    {var} = st.multiselect("{label}", options=[], key="{var}")\n'
            )
    return "".join(lines) or '    pass  # No filters detected\n'


def _agent_chat_sidebar(agent_fqn: str | None) -> str:
    if not agent_fqn:
        return ""
    return textwrap.dedent(f"""\
        st.sidebar.divider()
        st.sidebar.subheader("Ask your data")
        _agent_query = st.sidebar.text_area("Ask a question in plain English", height=80,
                                             key="agent_query")
        if st.sidebar.button("Ask", type="primary", key="agent_ask") and _agent_query:
            with st.sidebar.spinner("Thinking..."):
                try:
                    # Cortex Agent REST API call
                    import requests, json as _json
                    _token = session.connection.rest.token
                    _host  = session.connection.host
                    _resp = requests.post(
                        f"https://{{_host}}/api/v2/cortex/agent:run",
                        headers={{"Authorization": f"Bearer {{_token}}",
                                  "Content-Type": "application/json"}},
                        json={{
                            "agent": "{agent_fqn}",
                            "messages": [{{"role": "user", "content": _agent_query}}],
                            "stream": False,
                        }},
                        timeout=60,
                    )
                    _resp.raise_for_status()
                    _answer = _resp.json().get("choices", [{{}}])[0].get("message", {{}}).get("content", "No response.")
                except Exception as _e:
                    _answer = f"Error calling agent: {{_e}}"
            st.sidebar.markdown(_answer)
    """)


def _generate_dashboard_page(
    dashboard: dict,
    inventory: dict,
    table_name: str,
    semantic_view: str | None,
    embed_agent: str | None,
    ds_table_map: dict[str, str] | None = None,
    visuals: dict | None = None,
) -> str:
    """Generate the full Python source for one dashboard page."""
    name = dashboard.get("name", "Dashboard")
    slug = _slugify(name)
    vis = visuals or {}
    # Normalize: sheets may be plain strings (zone names with no field metadata)
    # or full dicts produced by extract_dashboard_field_usage.  Always work with dicts.
    sheets = [
        s if isinstance(s, dict) else {"name": s, "fields_by_datasource": {}}
        for s in dashboard.get("sheets", [])
    ]

    dims = inventory.get("dimensions", [])
    measures = inventory.get("measures", []) + inventory.get("metrics", []) + inventory.get("facts", [])

    # Collect filter fields from inventory-level filters (if present)
    raw_filters = inventory.get("parameters", []) or inventory.get("filters", [])
    filter_widgets = infer_filter_widgets(raw_filters) if raw_filters else []

    # Use semantic view as query target when available
    fallback_target = semantic_view or table_name or "TARGET_TABLE  -- TODO: replace with actual table"
    _ds_map = ds_table_map or {}

    lines: list[str] = [
        '"""',
        f'Dashboard: {name}',
        f'Generated by bi-modernization skill.',
        '"""',
        'import streamlit as st',
        'import plotly.express as px',
        'import plotly.graph_objects as go',
        'import pandas as pd',
        'from snowflake.snowpark.context import get_active_session',
        '',
        '_session = get_active_session()',
        '',
        'st.set_page_config(',
        f'    page_title="{name}",',
        '    layout="wide",',
        '    initial_sidebar_state="expanded",',
        ')',
        '',
    ]

    # ── Inject extracted visual metadata as constants ───────────────────
    color_maps = vis.get("color_mappings", {})
    col_aliases = vis.get("column_aliases", {})
    bg_color = vis.get("global_styles", {}).get("background_color", "")
    params = vis.get("parameters", [])

    if color_maps:
        lines.append(f'# Color mappings extracted from source BI file')
        lines.append(f'_COLOR_MAPS = {json.dumps(color_maps)}')
        lines.append('')
    if col_aliases:
        lines.append(f'# Column display name aliases extracted from source BI file')
        lines.append(f'_COL_ALIASES = {json.dumps(col_aliases)}')
        lines.append('')
    if bg_color:
        lines.append(f'# Background color from source')
        lines.append(f'st.markdown(\'<style>.stApp {{background-color: {bg_color};}}</style>\', unsafe_allow_html=True)')
        lines.append('')

    lines += [
        '# ── Header ───────────────────────────────────────────────────────────────',
        '_hdr_col1, _hdr_col2 = st.columns([5, 1])',
        'with _hdr_col1:',
        f'    st.title("{name}")',
        'with _hdr_col2:',
        '    st.caption("Powered by Snowflake Cortex")',
        '',
        '# ── Sidebar Filters ──────────────────────────────────────────────────────',
        'with st.sidebar:',
        '    st.header("Filters")',
    ]

    # Generate parameter-based filters from visuals.json
    if params:
        for param in params:
            pname = param.get("name", "")
            vals = param.get("values", [])
            default = param.get("default", "")
            aliases_map = param.get("aliases", {})
            if vals:
                display_vals = [v.get("alias", v.get("value", "")) for v in vals]
                lines.append(f'    _{_slugify(pname)} = st.selectbox("{pname}", {json.dumps(display_vals)})')

    lines.append(_filter_code(filter_widgets))

    if embed_agent:
        lines.append(_agent_chat_sidebar(embed_agent))

    lines.append('')
    lines.append('# ── Charts ───────────────────────────────────────────────────────────────')

    # Layout: 2 columns for narrow charts, 1 column for tables/wide charts
    col_index = 0
    open_cols = False

    for i, sheet in enumerate(sheets):
        raw_sname = sheet.get("name", f"Sheet {i+1}")
        sname, skip = _sanitize_sheet_name(raw_sname)
        if skip:
            continue
        chart_type = sheet.get("chart_type_plotly") or "bar"

        # Resolve per-datasource table
        query_target = _resolve_table_for_sheet(sheet, _ds_map, fallback_target)

        # Tables and metrics get full width
        full_width = chart_type in ("table", "metric", "metric_table", "filter",
                                    "map_scatter", "choropleth", "treemap",
                                    "bar_line", "waterfall", "timeline")

        if full_width:
            if open_cols:
                lines.append('')
                open_cols = False
                col_index = 0
            lines.append(f'# Sheet: {sname}')
            lines.append(f'with st.container():')
            if chart_type not in ("metric", "metric_table"):
                lines.append(f'    st.subheader("{sname}")')
        else:
            if col_index % 2 == 0:
                if open_cols:
                    lines.append('')
                lines.append(f'# Sheets: row {col_index // 2 + 1}')
                lines.append(f'_col_{col_index}, _col_{col_index+1} = st.columns(2)')
                open_cols = True
            lines.append(f'with _col_{col_index}:')
            lines.append(f'    st.subheader("{sname}")')
            col_index += 1

        df_var = f'_df_{_slugify(sname)}'
        field_info = _resolve_query_fields(sheet, dims, measures, query_target, chart_type)
        sql = field_info["sql"].replace("{filter_clause}", "")

        lines.append(f'    {df_var} = _session.sql("""')
        for sql_line in sql.split("\n"):
            lines.append(f'        {sql_line}')
        lines.append(f'    """).to_pandas()')
        lines.append(f'    if {df_var}.columns.str.islower().any():')
        lines.append(f'        {df_var}.columns = {df_var}.columns.str.upper()')

        chart_block = _chart_code(sheet, df_var, field_info, chart_type, sname)
        lines.append(chart_block.rstrip())

    lines.append('')
    return "\n".join(lines) + "\n"


def _generate_home(dashboard_names: list[str]) -> str:
    """Generate the home.py entry point using st.radio sidebar (Streamlit 1.24+ compat)."""
    lines = [
        '"""',
        'Streamlit app home — generated by bi-modernization skill.',
        'Uses st.radio sidebar navigation for broad Streamlit version compatibility.',
        '"""',
        'import streamlit as st',
        'import importlib',
        '',
        'st.set_page_config(page_title="Dashboard", layout="wide", page_icon="\U0001f4ca")',
        '',
        '# Sidebar navigation',
        'with st.sidebar:',
        '    page = st.radio("Navigate", [',
    ]
    for name in dashboard_names:
        lines.append(f'        "{name}",')
    lines += [
        '    ], label_visibility="collapsed")',
        '',
        '# Page routing',
    ]
    for i, name in enumerate(dashboard_names):
        slug = _slugify(name)
        cond = 'if' if i == 0 else 'elif'
        lines.append(f'{cond} page == "{name}":')
        lines.append(f'    import dashboard_{slug}')
        lines.append(f'    dashboard_{slug}.render()')
    lines.append('')
    return "\n".join(lines)


def generate(
    inventory_path: str,
    output_dir: str,
    semantic_view: str | None = None,
    embed_agent: str | None = None,
    dashboards_filter: list[str] | None = None,
    visuals_path: str | None = None,
) -> dict[str, Any]:
    """
    Main entry point called by cli.py.

    Returns structured result dict with list of generated files.
    """
    with open(inventory_path) as fh:
        inventory = json.load(fh)

    # Load visual metadata if provided
    visuals: dict = {}
    if visuals_path:
        with open(visuals_path) as fh:
            visuals = json.load(fh)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    dashboards = inventory.get("dashboards", [])
    if dashboards_filter:
        dashboards = [d for d in dashboards if d.get("name") in dashboards_filter]

    if not dashboards:
        # Build minimal dashboard from worksheets
        dashboards = [{"name": "Dashboard", "sheets": inventory.get("worksheets", [])}]

    # Build per-datasource table map + primary fallback
    ds_table_map = _build_ds_table_map(inventory)
    tables = inventory.get("tables", [])
    primary_table = ""
    if tables:
        t = tables[0]
        db = inventory.get("snowflake_target", {}).get("database", "DB")
        sch = inventory.get("snowflake_target", {}).get("schema", "SCHEMA")
        tname = t.get("snowflake_name") or t.get("name", "TABLE")
        primary_table = f"{db}.{sch}.{tname}"

    generated: list[dict] = []

    for dash in dashboards:
        slug = _slugify(dash.get("name", "dashboard"))
        fname = f"dashboard_{slug}.py"
        fpath = out / fname
        code = _generate_dashboard_page(
            dash, inventory, primary_table, semantic_view, embed_agent,
            ds_table_map=ds_table_map, visuals=visuals,
        )
        fpath.write_text(code, encoding="utf-8")
        generated.append({
            "path": str(fpath),
            "dashboard": dash.get("name"),
            "sheet_count": len(dash.get("sheets", [])),
        })

    # home.py
    home_path = out / "home.py"
    home_path.write_text(
        _generate_home([d.get("name", "") for d in dashboards]),
        encoding="utf-8",
    )
    generated.append({"path": str(home_path), "dashboard": "home", "sheet_count": 0})

    # requirements.txt for SiS
    req_path = out / "requirements.txt"
    req_path.write_text("plotly>=5.22.0\npandas>=2.0.0\n", encoding="utf-8")

    return {
        "status": "ok",
        "command": "generate-streamlit",
        "output_dir": output_dir,
        "generated_files": generated,
        "dashboard_count": len(dashboards),
        "embed_agent": embed_agent,
        "semantic_view": semantic_view,
        "visuals_loaded": bool(visuals),
    }
