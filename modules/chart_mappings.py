"""
Chart type lookup tables mapping BI tool types to Plotly and ECharts render targets.

No logic here — pure dicts for use by chart_extractor.py and the generators.
"""

# ---------------------------------------------------------------------------
# Tableau mark class → render targets
# ---------------------------------------------------------------------------

TABLEAU_TO_PLOTLY = {
    "bar":       "bar",
    "line":      "line",
    "area":      "area",
    "circle":    "scatter",
    "shape":     "scatter",
    "square":    "scatter",
    "pie":       "pie",
    "text":      "table",
    "polygon":   "scatter",
    "gantt":     "timeline",
    "automatic": None,  # resolved by heuristics.infer_chart_type
}

TABLEAU_TO_ECHARTS = {
    "bar":       "bar",
    "line":      "line",
    "area":      "line_area",   # line + areaStyle
    "circle":    "scatter",
    "shape":     "scatter",
    "square":    "scatter",
    "pie":       "pie",
    "text":      "table",
    "polygon":   "scatter",
    "gantt":     "custom",
    "automatic": None,
}

# Tableau mark class is CamelCase in the XML — normalise to lower for lookup
def tableau_mark_to_plotly(mark_class: str) -> str | None:
    return TABLEAU_TO_PLOTLY.get(mark_class.lower())

def tableau_mark_to_echarts(mark_class: str) -> str | None:
    return TABLEAU_TO_ECHARTS.get(mark_class.lower())


# ---------------------------------------------------------------------------
# Power BI visualType → render targets
# ---------------------------------------------------------------------------

POWERBI_TO_PLOTLY = {
    # Bar / Column
    "barchart":                         "bar_h",    # horizontal
    "clusteredbarchart":                "bar_h",
    "stackedbarchart":                  "bar_h_stacked",
    "hundredpercentstackedbarchart":    "bar_h_pct",
    "columnchart":                      "bar",      # vertical
    "clusteredcolumnchart":             "bar",
    "stackedcolumnchart":               "bar_stacked",
    "hundredpercentstackedcolumnchart": "bar_pct",
    # Line / Area
    "linechart":                        "line",
    "areachart":                        "area",
    "stackedareachart":                 "area_stacked",
    "lineclusteredcolumncombovisual":   "bar_line",
    "linestackedcolumncombovisual":     "bar_line",
    # Pie / Donut
    "piechart":                         "pie",
    "donutchart":                       "donut",
    # Scatter / Bubble
    "scatterchart":                     "scatter",
    "bubblechart":                      "scatter_bubble",
    # Table / Matrix
    "tableex":                          "table",
    "pivottable":                       "table",
    # Cards / KPI
    "card":                             "metric",
    "multirowcard":                     "metric_table",
    "kpivisual":                        "metric",
    # Slicer / Filter
    "slicer":                           "filter",
    # Map
    "map":                              "map_scatter",
    "filledmap":                        "choropleth",
    "azuremap":                         "map_scatter",
    # Waterfall / Funnel
    "waterfallchart":                   "waterfall",
    "funnelchart":                      "funnel",
    # Treemap / Decomposition
    "treemap":                          "treemap",
    "decompositiontreevisual":          "treemap",
    # Gauge
    "gauge":                            "gauge",
    # Ribbon
    "ribbonchart":                      "bar_stacked",
}

POWERBI_TO_ECHARTS = {
    "barchart":                         "bar_h",
    "clusteredbarchart":                "bar_h",
    "stackedbarchart":                  "bar_h_stacked",
    "hundredpercentstackedbarchart":    "bar_h_pct",
    "columnchart":                      "bar",
    "clusteredcolumnchart":             "bar",
    "stackedcolumnchart":               "bar_stacked",
    "hundredpercentstackedcolumnchart": "bar_pct",
    "linechart":                        "line",
    "areachart":                        "line_area",
    "stackedareachart":                 "line_area_stacked",
    "lineclusteredcolumncombovisual":   "bar_line",
    "linestackedcolumncombovisual":     "bar_line",
    "piechart":                         "pie",
    "donutchart":                       "pie_donut",
    "scatterchart":                     "scatter",
    "bubblechart":                      "scatter_bubble",
    "tableex":                          "table",
    "pivottable":                       "table",
    "card":                             "metric",
    "multirowcard":                     "metric",
    "kpivisual":                        "metric",
    "slicer":                           "filter",
    "map":                              "map",
    "filledmap":                        "map_geo",
    "waterfallchart":                   "custom_waterfall",
    "funnelchart":                      "funnel",
    "treemap":                          "treemap",
    "decompositiontreevisual":          "treemap",
    "gauge":                            "gauge",
    "ribbonchart":                      "bar_stacked",
}

def powerbi_visual_to_plotly(visual_type: str) -> str | None:
    return POWERBI_TO_PLOTLY.get(visual_type.lower())

def powerbi_visual_to_echarts(visual_type: str) -> str | None:
    return POWERBI_TO_ECHARTS.get(visual_type.lower())


# ---------------------------------------------------------------------------
# Looker tile type → render targets
# ---------------------------------------------------------------------------

LOOKER_TO_PLOTLY = {
    "looker_area":                      "area",
    "looker_bar":                       "bar_h",
    "looker_boxplot":                   "box",
    "looker_column":                    "bar",
    "looker_donut_multiples":           "donut",
    "looker_funnel":                    "funnel",
    "looker_gauge":                     "gauge",
    "looker_geo_choropleth":            "choropleth",
    "looker_geo_coordinates":           "map_scatter",
    "looker_grid":                      "table",
    "looker_line":                      "line",
    "looker_pie":                       "pie",
    "looker_scatter":                   "scatter",
    "looker_single_value":              "metric",
    "looker_timeline":                  "timeline",
    "looker_treemap":                   "treemap",
    "looker_waterfall":                 "waterfall",
    "looker_word_cloud":                "table",    # fallback — no easy Plotly equiv
    "table":                            "table",
    "text":                             "metric",
}

LOOKER_TO_ECHARTS = {
    "looker_area":                      "line_area",
    "looker_bar":                       "bar_h",
    "looker_boxplot":                   "boxplot",
    "looker_column":                    "bar",
    "looker_donut_multiples":           "pie_donut",
    "looker_funnel":                    "funnel",
    "looker_gauge":                     "gauge",
    "looker_geo_choropleth":            "map_geo",
    "looker_geo_coordinates":           "map",
    "looker_grid":                      "table",
    "looker_line":                      "line",
    "looker_pie":                       "pie",
    "looker_scatter":                   "scatter",
    "looker_single_value":              "metric",
    "looker_timeline":                  "custom_timeline",
    "looker_treemap":                   "treemap",
    "looker_waterfall":                 "custom_waterfall",
    "looker_word_cloud":                "table",
    "table":                            "table",
    "text":                             "metric",
}

def looker_tile_to_plotly(tile_type: str) -> str | None:
    return LOOKER_TO_PLOTLY.get(tile_type.lower())

def looker_tile_to_echarts(tile_type: str) -> str | None:
    return LOOKER_TO_ECHARTS.get(tile_type.lower())


# ---------------------------------------------------------------------------
# Plotly render type → px function / graph_objects call
# ---------------------------------------------------------------------------

PLOTLY_RENDER = {
    "bar":             {"fn": "bar",       "orientation": "v"},
    "bar_h":           {"fn": "bar",       "orientation": "h"},
    "bar_stacked":     {"fn": "bar",       "orientation": "v", "barmode": "stack"},
    "bar_h_stacked":   {"fn": "bar",       "orientation": "h", "barmode": "stack"},
    "bar_pct":         {"fn": "bar",       "orientation": "v", "barmode": "stack", "barnorm": "percent"},
    "bar_h_pct":       {"fn": "bar",       "orientation": "h", "barmode": "stack", "barnorm": "percent"},
    "bar_line":        {"fn": "bar_line",  "note": "combo — use make_subplots"},
    "line":            {"fn": "line"},
    "area":            {"fn": "area"},
    "area_stacked":    {"fn": "area",      "groupnorm": ""},
    "scatter":         {"fn": "scatter"},
    "scatter_bubble":  {"fn": "scatter",   "size_col": True},
    "pie":             {"fn": "pie"},
    "donut":           {"fn": "pie",       "hole": 0.4},
    "table":           {"fn": "dataframe", "note": "st.dataframe / HTML table"},
    "metric":          {"fn": "metric",    "note": "st.metric / MetricCard"},
    "metric_table":    {"fn": "dataframe"},
    "filter":          {"fn": "filter",    "note": "sidebar filter widget"},
    "map_scatter":     {"fn": "scatter_mapbox"},
    "choropleth":      {"fn": "choropleth"},
    "waterfall":       {"fn": "waterfall"},
    "funnel":          {"fn": "funnel"},
    "treemap":         {"fn": "treemap"},
    "box":             {"fn": "box"},
    "gauge":           {"fn": "indicator", "mode": "gauge+number"},
    "timeline":        {"fn": "timeline"},
}

def plotly_render_spec(plotly_type: str) -> dict:
    return PLOTLY_RENDER.get(plotly_type, {"fn": "bar"})


# ---------------------------------------------------------------------------
# ECharts type → component name (for react_generator)
# ---------------------------------------------------------------------------

ECHARTS_COMPONENT = {
    "bar":                 "BarChart",
    "bar_h":               "BarChart",
    "bar_stacked":         "BarChart",
    "bar_h_stacked":       "BarChart",
    "bar_pct":             "BarChart",
    "bar_h_pct":           "BarChart",
    "bar_line":            "ComboChart",
    "line":                "LineChart",
    "line_area":           "AreaChart",
    "line_area_stacked":   "AreaChart",
    "scatter":             "ScatterChart",
    "scatter_bubble":      "ScatterChart",
    "pie":                 "PieChart",
    "pie_donut":           "PieChart",
    "table":               "DataTable",
    "metric":              "MetricCard",
    "filter":              None,        # becomes a FilterPanel control
    "map":                 "MapChart",
    "map_geo":             "MapChart",
    "choropleth":          "MapChart",
    "treemap":             "TreemapChart",
    "funnel":              "FunnelChart",
    "gauge":               "GaugeChart",
    "boxplot":             "BoxPlotChart",
    "custom_waterfall":    "WaterfallChart",
    "custom_timeline":     "TimelineChart",
}

def echarts_component_name(echarts_type: str) -> str:
    return ECHARTS_COMPONENT.get(echarts_type, "BarChart")
