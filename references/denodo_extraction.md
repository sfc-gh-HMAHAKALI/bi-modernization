# Denodo Extraction Reference

## Access Methods

| Method | Description | Prerequisites |
|--------|-------------|---------------|
| **VQL Export file** | `.vql` text file exported from Denodo Design Studio or CLI. Contains full DDL for all views, data sources, and folders. Most portable — no live connection needed. | Design Studio access or `DESC VQL DATABASE` privilege. Export via Design Studio → File → Export or VQL Shell. |
| **JDBC connection to VDP** | Connect to Virtual DataPort server via JDBC or Python (using `jaydebeapi`). Run catalog stored procedures to query metadata in real time. | VDP server hostname/port, credentials, `jaydebeapi` Python lib, Denodo JDBC driver `.jar`. Requires `EXECUTE` privilege on catalog stored procedures. |
| **Data Catalog / Data Marketplace REST API** | Denodo Data Catalog exposes a REST API for browsing, synchronizing, and exporting metadata including business tags, descriptions, and categories assigned by data stewards. | Data Catalog deployed and synchronized with VDP. Credentials with Catalog Admin or Developer role. URL to the Data Catalog server. |

## Denodo Model Structure Overview

Virtual DataPort (VDP) organizes objects in this hierarchy:

```
VDP Server
└── Database (e.g., "customer_analytics")
    ├── Folder (e.g., "/sales", "/finance")
    │   ├── Base View         ← maps directly to a physical source (JDBC, file, web service)
    │   ├── Derived View      ← SELECT/JOIN/UNION/aggregation over other views
    │   │   ├── Selection View   (WHERE or column subset of one view)
    │   │   ├── Join View        (JOIN between 2+ views)
    │   │   ├── Union View       (UNION ALL of multiple views)
    │   │   ├── Intersection/Minus View
    │   │   ├── Flatten View     (unnests array/register fields)
    │   │   └── Aggregation View (GROUP BY)
    │   └── Interface View    ← abstraction over one implementation view (published API surface)
    └── Data Sources (JDBC, LDAP, Web Service, Delimited File, etc.)
```

**Key difference from Tableau/Power BI**: Denodo has **no native dimension/measure concept**. Every column is simply a typed field. Classification must be inferred from data type, naming conventions, and whether the column appears in aggregation expressions.

Column data types in VDP: `VARCHAR`, `TEXT`, `CHAR`, `INTEGER`, `LONG`, `DECIMAL`, `DOUBLE`, `FLOAT`, `BOOLEAN`, `DATE`, `TIMESTAMP`, `TIMESTAMPTZ`, `TIME`, `INTERVAL_YEAR_MONTH`, `INTERVAL_DAY_SECOND`, `ARRAY`, `REGISTER` (struct), `BLOB`, `XML`.

## Extraction Code Patterns

### Path A: VQL Export Parsing

Run from VQL Shell or Design Studio to export a full database:

```sql
DESC VQL DATABASE customer_analytics (
  'includeCreateDatabase'   = 'yes',
  'includeStatistics'       = 'yes',
  'dropElements'            = 'no',
  'replaceExistingElements' = 'yes'
);
```

Save the output as `export.vql`. This file contains `CREATE VIEW`, `CREATE DATASOURCE`, `CREATE FOLDER`, and `CREATE INTERFACE` statements that can be parsed with Python.

```python
import re

def parse_vql_export(vql_text):
    """Parse a Denodo VQL export file into structured view metadata."""
    views = {}

    # Match each CREATE OR REPLACE VIEW block
    view_pattern = re.compile(
        r'CREATE OR REPLACE VIEW\s+(\w+)\s*\(([^)]+)\)\s*'
        r'(?:DESCRIPTION\s+\'([^\']*)\'\s*)?'
        r'AS\s+SELECT\s+(.*?)(?=CREATE OR REPLACE|$)',
        re.DOTALL | re.IGNORECASE
    )

    # Match field definitions inside the column list: name type [description 'text']
    field_pattern = re.compile(
        r'(\w+)\s+(VARCHAR|TEXT|INTEGER|LONG|DECIMAL|DOUBLE|FLOAT|BOOLEAN|DATE|TIMESTAMP\w*|TIME\w*)',
        re.IGNORECASE
    )

    # Match field descriptions (attached as column annotations)
    field_desc_pattern = re.compile(
        r'(\w+)\s+\w+.*?DESCRIPTION\s+\'([^\']+)\'',
        re.DOTALL | re.IGNORECASE
    )

    for match in view_pattern.finditer(vql_text):
        view_name  = match.group(1)
        col_block  = match.group(2)
        view_desc  = match.group(3) or ''
        select_sql = match.group(4).strip()

        columns = []
        for f in field_pattern.finditer(col_block):
            col = {'name': f.group(1), 'type': f.group(2).upper(), 'description': ''}
            columns.append(col)

        # Overlay descriptions
        for fd in field_desc_pattern.finditer(col_block):
            for col in columns:
                if col['name'].upper() == fd.group(1).upper():
                    col['description'] = fd.group(2)

        views[view_name] = {
            'view_name':    view_name,
            'description':  view_desc,
            'columns':      columns,
            'select_sql':   select_sql,
            'joins':        extract_joins_from_sql(select_sql),
            'group_by':     extract_group_by_cols(select_sql),
            'aggregations': extract_aggregations(select_sql),
        }
    return views


def extract_joins_from_sql(sql):
    """Extract JOIN clauses from a VQL SELECT statement."""
    join_pattern = re.compile(
        r'(INNER|LEFT OUTER|RIGHT OUTER|FULL OUTER|CROSS)?\s*JOIN\s+(\w+)\s+(?:\w+\s+)?ON\s+([^\n]+)',
        re.IGNORECASE
    )
    joins = []
    for m in join_pattern.finditer(sql):
        joins.append({
            'join_type':  (m.group(1) or 'INNER').strip().upper(),
            'right_view': m.group(2),
            'condition':  m.group(3).strip()
        })
    return joins


def extract_group_by_cols(sql):
    """Extract column names from GROUP BY clause."""
    m = re.search(r'GROUP\s+BY\s+([\w\s,\.]+?)(?:HAVING|ORDER|$)', sql, re.IGNORECASE)
    if not m:
        return []
    return [c.strip().split('.')[-1] for c in m.group(1).split(',')]


def extract_aggregations(sql):
    """Extract aggregated expressions from SELECT clause."""
    agg_pattern = re.compile(r'(SUM|AVG|COUNT|MIN|MAX)\s*\([^)]+\)', re.IGNORECASE)
    return [m.group(0) for m in agg_pattern.finditer(sql)]
```

### Path B: JDBC / VQL Metadata Queries

Denodo exposes catalog stored procedures callable via JDBC or any SQL client. Use `jaydebeapi` with the Denodo JDBC driver, or connect via Snowflake's JDBC bridge.

```python
import jaydebeapi

def connect_vdp(host, port, database, user, password, driver_jar):
    """Connect to Denodo VDP via JDBC."""
    url = f"jdbc:vdb://{host}:{port}/{database}"
    conn = jaydebeapi.connect(
        'com.denodo.vdp.jdbc.Driver',
        url,
        [user, password],
        driver_jar
    )
    return conn

def get_all_view_columns(conn, database):
    """Return all columns for all views in a database with types and descriptions."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM CATALOG_VDP_METADATA_VIEWS() WHERE input_database_name = ?",
        [database]
    )
    cols = [d[0].lower() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
    # Returns: database_name, view_name, column_name, column_vdp_type,
    #          column_precision, column_scale, column_remarks, ...

def get_view_dependencies(conn, database, view_name):
    """Return lineage for a specific view — which views/tables feed it."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM VIEW_DEPENDENCIES() "
        "WHERE input_view_database_name = ? AND input_view_name = ?",
        [database, view_name]
    )
    cols = [d[0].lower() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
    # Returns: view_name, dependency_name, dependency_database, dependency_type,
    #          join_type (Inner/Left outer/Right outer/Full outer), join_method

def get_view_column_detail(conn, database, view_name):
    """Return detailed column info including column_remarks (descriptions)."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM GET_VIEW_COLUMNS() "
        "WHERE input_database_name = ? AND input_view_name = ?",
        [database, view_name]
    )
    cols = [d[0].lower() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def get_all_elements(conn, database):
    """List all objects in a database: views, data sources, interface views, etc."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM GET_ELEMENTS() WHERE input_database_name = ?",
        [database]
    )
    cols = [d[0].lower() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
    # element_type: VIEW, DATASOURCE, WEBSERVICE, WIDGET, etc.
    # element_subtype: BASE, DERIVED, INTERFACE, UNION, JOIN, SELECTION, FLATTEN, etc.
```

### Path C: Data Catalog REST API

```python
import requests

BASE_URL = "https://<catalog-host>:9443/denodo-data-catalog"

def catalog_auth_basic(user, password):
    """Returns requests auth tuple for HTTP Basic auth."""
    return (user, password)

def synchronize_catalog(auth, server_id=None):
    """Trigger a sync from VDP to Data Catalog."""
    body = {"allServers": "true", "priority": "server_with_local_changes"}
    if server_id:
        body = {"serverId": server_id, "allDatabases": "true",
                "priority": "server_with_local_changes"}
    resp = requests.post(
        f"{BASE_URL}/apirest/synchronize",
        json=body,
        auth=auth,
        verify=False   # use True in production with proper cert
    )
    resp.raise_for_status()
    return resp.json()

def browse_databases(auth, server_id, database_name=None):
    """List databases and their views from Data Catalog."""
    params = {"serverId": server_id}
    if database_name:
        params["databaseName"] = database_name
    resp = requests.get(
        f"{BASE_URL}/public/api/browse/databases",
        params=params,
        auth=auth,
        verify=False
    )
    resp.raise_for_status()
    return resp.json()
    # Returns: name, description, tags, categories, elements (views list)

def export_catalog_metadata(auth):
    """Export all catalog metadata to a ZIP of JSON files."""
    resp = requests.post(
        f"{BASE_URL}/apirest/admin/export",
        auth=auth,
        verify=False
    )
    resp.raise_for_status()
    with open("catalog_export.zip", "wb") as f:
        f.write(resp.content)
    return "catalog_export.zip"
```

**Note**: The Data Catalog export ZIP contains JSON files per view including business descriptions, tags, and category assignments not present in raw VQL. Always merge Catalog metadata over VQL metadata when both are available — Catalog has richer business context.

## Column Classification Heuristics

Denodo has no `dimension`/`measure` flag. Apply these rules in order; first match wins.

### Data Type Heuristics

| VDP Type | Default Classification | Notes |
|----------|----------------------|-------|
| `VARCHAR`, `TEXT`, `CHAR` | Dimension | |
| `DATE`, `TIMESTAMP`, `TIMESTAMPTZ`, `TIME` | Dimension (time) | |
| `BOOLEAN` | Dimension | |
| `INTEGER`, `LONG` | Check naming (below) | Could be FK/PK (dimension) or count (metric) |
| `DECIMAL`, `DOUBLE`, `FLOAT` | Check naming (below) | Almost always fact/metric |
| `ARRAY`, `REGISTER` | Flag as manual | Complex types — flatten or skip |

### Naming Convention Patterns

```python
import re

DIMENSION_PATTERNS = re.compile(
    r'(_id|_key|_code|_num|_name|_desc|_description|_label|'
    r'_type|_status|_category|_date|_time|_timestamp|_at|'
    r'^is_|^has_|^flag_)',
    re.IGNORECASE
)

METRIC_PATTERNS = re.compile(
    r'(_amount|_amt|_qty|_quantity|_count|_total|_sum|_avg|'
    r'_pct|_percent|_rate|_price|_cost|_revenue|_profit|'
    r'_margin|_balance|_weight|_score)',
    re.IGNORECASE
)

def classify_column_by_name(col_name, vdp_type):
    if DIMENSION_PATTERNS.search(col_name):
        return 'dimension'
    if METRIC_PATTERNS.search(col_name):
        return 'fact'
    # Fall back to type heuristic
    if vdp_type.upper() in ('VARCHAR', 'TEXT', 'CHAR', 'BOOLEAN', 'DATE',
                             'TIMESTAMP', 'TIMESTAMPTZ', 'TIME'):
        return 'dimension'
    return 'fact'  # DECIMAL, DOUBLE, FLOAT, INTEGER, LONG default to fact
```

### Aggregation Detection

- Column appears in `extract_aggregations()` result (wrapped in SUM/AVG/COUNT/MIN/MAX) → **metric**
- Column appears in `extract_group_by_cols()` result → **dimension**
- Column in a non-aggregating derived view SELECT → apply type/naming heuristics

### Data Catalog Tags

When catalog metadata is available, check `tags` and `description` fields:

```python
METRIC_TAG_HINTS  = {'measure', 'metric', 'kpi', 'fact', 'aggregate'}
DIM_TAG_HINTS     = {'dimension', 'attribute', 'category', 'filter'}
METRIC_DESC_HINTS = re.compile(r'\b(total|count of|average|sum of|rate of)\b', re.IGNORECASE)

def classify_with_catalog(col_name, vdp_type, tags, description):
    tag_set = {t.lower() for t in (tags or [])}
    if tag_set & METRIC_TAG_HINTS:
        return 'fact'
    if tag_set & DIM_TAG_HINTS:
        return 'dimension'
    if description and METRIC_DESC_HINTS.search(description):
        return 'fact'
    return classify_column_by_name(col_name, vdp_type)
```

**User Override**: Always accept an explicit mapping file that overrides heuristic classification:

```python
# override_map.json: {"VIEW_NAME.COLUMN_NAME": "dimension|fact|metric|skip"}
import json
with open('override_map.json') as f:
    overrides = json.load(f)

def apply_override(view_name, col_name, default_class):
    key = f"{view_name}.{col_name}"
    return overrides.get(key, default_class)
```

## VQL-to-Snowflake Function Translation Table

| VQL Function | Snowflake SQL | Complexity | Notes |
|---|---|---|---|
| `ADDDAY(date, n)` | `DATEADD('DAY', n, date)` | needs_translation | |
| `ADDMONTH(date, n)` | `DATEADD('MONTH', n, date)` | needs_translation | |
| `ADDYEAR(date, n)` | `DATEADD('YEAR', n, date)` | needs_translation | |
| `ADDHOUR(ts, n)` | `DATEADD('HOUR', n, ts)` | needs_translation | |
| `ADDMINUTE(ts, n)` | `DATEADD('MINUTE', n, ts)` | needs_translation | |
| `ADDSECOND(ts, n)` | `DATEADD('SECOND', n, ts)` | needs_translation | |
| `GETDAY(date)` | `DAY(date)` | needs_translation | |
| `GETMONTH(date)` | `MONTH(date)` | needs_translation | |
| `GETYEAR(date)` | `YEAR(date)` | needs_translation | |
| `GETHOUR(ts)` | `HOUR(ts)` | needs_translation | |
| `GETMINUTE(ts)` | `MINUTE(ts)` | needs_translation | |
| `GETSECOND(ts)` | `SECOND(ts)` | needs_translation | |
| `FORMATDATE('yyyy-MM-dd', date)` | `TO_VARCHAR(date, 'YYYY-MM-DD')` | needs_translation | Format string syntax differs |
| `GETDAYOFWEEK(date)` | `DAYOFWEEK(date)` | needs_translation | VQL: 1=Monday; Snowflake: 0=Sunday — adjust with `MOD(DAYOFWEEK(date)+6,7)+1` |
| `GETDAYOFYEAR(date)` | `DAYOFYEAR(date)` | needs_translation | |
| `GETWEEK(date)` | `WEEKOFYEAR(date)` | needs_translation | |
| `GETQUARTER(date)` | `QUARTER(date)` | needs_translation | |
| `FIRSTDAYOFMONTH(date)` | `DATE_TRUNC('MONTH', date)` | needs_translation | |
| `LASTDAYOFMONTH(date)` | `LAST_DAY(date)` | needs_translation | |
| `FIRSTDAYOFWEEK(date)` | `DATE_TRUNC('WEEK', date)` | needs_translation | Snowflake week starts Sunday |
| `LASTDAYOFWEEK(date)` | `DATEADD('DAY', 6, DATE_TRUNC('WEEK', date))` | needs_translation | |
| `TRUNC(date, 'MONTH')` | `DATE_TRUNC('MONTH', date)` | needs_translation | |
| `COALESCE(a, b)` | `COALESCE(a, b)` | simple | Identical |
| `NVL(a, b)` | `NVL(a, b)` | simple | Identical |
| `NULLIF(a, b)` | `NULLIF(a, b)` | simple | Identical |
| `CAST(x AS type)` | `CAST(x AS type)` | needs_translation | Type names differ: `LONG` → `BIGINT`, `DOUBLE` → `FLOAT`, `VARCHAR (n)` compatible |
| `SUBSTRING(s, start, len)` | `SUBSTRING(s, start, len)` | simple | Identical |
| `TRIM(s)` / `LTRIM(s)` / `RTRIM(s)` | Same | simple | Identical |
| `UPPER(s)` / `LOWER(s)` | Same | simple | Identical |
| `REPLACE(s, old, new)` | `REPLACE(s, old, new)` | simple | Identical |
| `CONCAT(a, b)` | `CONCAT(a, b)` | simple | Identical |
| `REGEXP(s, pattern)` | `REGEXP_LIKE(s, pattern)` | needs_translation | VQL pattern dialect may differ |
| `REMOVEACCENTS(s)` | *(no native equivalent)* | manual_required | Must implement via custom UDF or normalize upstream |
| `SIMILARITY(a, b)` | *(no native equivalent)* | manual_required | Requires custom UDF (edit distance / Jaro-Winkler) |
| `NEST(field)` | *(no native equivalent)* | manual_required | Denodo array aggregation — no Snowflake SQL built-in |
| `UNNEST(array_field)` | `LATERAL FLATTEN(input => col)` | manual_required | Syntax completely different; semantics may differ |
| `FLATTEN(col)` | `LATERAL FLATTEN(input => col)` | manual_required | Verify field path mapping |
| `ROW_NUMBER() OVER (...)` | Same | simple | Identical |
| `RANK() OVER (...)` | Same | simple | Identical |
| `DENSE_RANK() OVER (...)` | Same | simple | Identical |
| `LAG(col, n) OVER (...)` | Same | simple | Identical |
| `LEAD(col, n) OVER (...)` | Same | simple | Identical |
| `CASE WHEN ... END` | Same | simple | Identical |
| `SUM()`, `AVG()`, `COUNT()`, `MIN()`, `MAX()` | Same | simple | Identical |

## View Type Handling

| Denodo View Type | Extraction Approach | Semantic View Mapping |
|---|---|---|
| **Base View** | Maps to a physical table. Extract the data source connection info (`database`, `schema`, `tableName` from VQL or `GET_VIEW_COLUMNS()`). | Add directly to `tables:` block with `base_table.database / schema / table`. |
| **Selection View** | Derived view with WHERE filter and/or column subset over one view. Extract filter conditions. | Map underlying base to `tables:`, add the WHERE clause as a `filters:` entry or document it as a pre-filter caveat. |
| **Join View** | Two or more views joined. Extract join type and ON conditions from `VIEW_DEPENDENCIES()` (`join_type`: Inner / Left outer / Right outer / Full outer). | Add both underlying tables to `tables:`, create a `relationships:` entry with the join columns. Map join type: Inner → `inner`, Left outer → `left_outer`, Full outer → `full_outer`. |
| **Union View** | Multiple views combined with UNION ALL. No JOIN conditions. | Create a Snowflake `VIEW` or `DYNAMIC TABLE` performing the union, then reference it in `tables:`. Flag for manual review. |
| **Intersection / Minus View** | Set operations (INTERSECT / EXCEPT). | Create a Snowflake `VIEW` performing the set operation, then reference. Flag as manual. |
| **Flatten View** | Unnests `ARRAY` or `REGISTER` (struct) typed columns. Complex type handling. | Flag as **manual_required**. Snowflake uses `LATERAL FLATTEN` with different path syntax. Requires a custom Snowflake view first. |
| **Aggregation View** | Derived view with GROUP BY. Column-level aggregations exist. | Aggregated columns → `metrics:`. GROUP BY columns → `dimensions:`. Use aggregation expressions (SUM, AVG, COUNT, etc.) directly in metric `expr`. |
| **Interface View** | Abstraction over one implementation view. Functions as the published API. | Extract the underlying implementation view instead. Interface view name becomes the semantic view name — it is the user-facing label. |

## `classify_denodo_complexity()` Function

```python
import re

VQL_DATE_FUNCS = re.compile(
    r'\b(ADDDAY|ADDMONTH|ADDYEAR|ADDHOUR|ADDMINUTE|ADDSECOND|'
    r'GETDAY|GETMONTH|GETYEAR|GETHOUR|GETMINUTE|GETSECOND|'
    r'FORMATDATE|GETDAYOFWEEK|GETDAYOFYEAR|GETWEEK|GETQUARTER|'
    r'FIRSTDAYOFMONTH|LASTDAYOFMONTH|FIRSTDAYOFWEEK|LASTDAYOFWEEK|'
    r'TRUNC)\s*\(',
    re.IGNORECASE
)

VQL_MANUAL_FUNCS = re.compile(
    r'\b(REMOVEACCENTS|SIMILARITY|NEST|UNNEST|FLATTEN)\s*\(',
    re.IGNORECASE
)

VQL_CAST_DENODO_TYPES = re.compile(
    r'CAST\s*\([^)]+AS\s+(LONG|DOUBLE|VARCHAR\s*\()',
    re.IGNORECASE
)

STANDARD_AGG = re.compile(
    r'\b(SUM|AVG|COUNT|MIN|MAX)\s*\(',
    re.IGNORECASE
)

STANDARD_SCALAR = re.compile(
    r'\b(COALESCE|NVL|NULLIF|UPPER|LOWER|TRIM|LTRIM|RTRIM|'
    r'SUBSTRING|REPLACE|CONCAT|ABS|ROUND|FLOOR|CEIL|MOD|POWER|SQRT|'
    r'ROW_NUMBER|RANK|DENSE_RANK|LAG|LEAD)\s*\(',
    re.IGNORECASE
)


def classify_denodo_complexity(field_name, field_expr, view_type, vql_functions_used):
    """
    Classify a Denodo field's migration complexity.

    Parameters:
        field_name          (str): Column name (used for naming heuristics).
        field_expr          (str): VQL expression for this field, or empty string
                                   if it is a direct column reference.
        view_type           (str): Denodo view subtype — 'BASE', 'SELECTION',
                                   'JOIN', 'UNION', 'FLATTEN', 'AGGREGATION',
                                   'INTERFACE'.
        vql_functions_used  (list[str]): Function names found in this field's expr
                                         (pre-extracted from VQL parser).

    Returns:
        str: 'simple' | 'needs_translation' | 'manual_required'
    """
    expr = field_expr or ''
    expr_upper = expr.upper()

    # --- Manual required ---

    # Complex type operations — no direct Snowflake SQL equivalent
    if view_type.upper() == 'FLATTEN':
        return 'manual_required'

    if VQL_MANUAL_FUNCS.search(expr):
        return 'manual_required'

    # Custom Java wrappers, stored procedures, web service data sources
    if re.search(r'\b(CUSTOM|JAVA|WEBSERVICE|BEJAVA)\s*\(', expr, re.IGNORECASE):
        return 'manual_required'

    # CONTEXT clause — Denodo execution hints with no Snowflake equivalent
    if re.search(r'\bCONTEXT\s*\(', expr, re.IGNORECASE):
        return 'manual_required'

    # Views with 3+ levels of derived view nesting (depth signal)
    nesting_depth = len(re.findall(r'\bSELECT\b', expr, re.IGNORECASE))
    if nesting_depth >= 3:
        return 'manual_required'

    # UNION views — need to be materialized as a Snowflake view first
    if view_type.upper() == 'UNION':
        return 'manual_required'

    # --- Needs translation ---

    # VQL-specific date/time functions with Snowflake equivalents requiring rewrite
    if VQL_DATE_FUNCS.search(expr):
        return 'needs_translation'

    # CAST with Denodo-specific type names
    if VQL_CAST_DENODO_TYPES.search(expr):
        return 'needs_translation'

    # REGEXP — syntax may differ
    if re.search(r'\bREGEXP\s*\(', expr, re.IGNORECASE):
        return 'needs_translation'

    # View parameters ($params) — must be resolved or hardcoded
    if re.search(r'\$\w+', expr):
        return 'needs_translation'

    # FLATTEN with field path references (partial flatten case)
    if re.search(r'\bFLATTEN\b', expr, re.IGNORECASE):
        return 'needs_translation'

    # --- Simple ---

    # Direct column reference (no expression)
    if not expr or re.fullmatch(r'[\w.]+', expr.strip()):
        return 'simple'

    # Standard SQL aggregations
    if STANDARD_AGG.search(expr):
        return 'simple'

    # Standard scalar functions with 1:1 Snowflake mapping
    if STANDARD_SCALAR.search(expr):
        return 'simple'

    # CASE WHEN
    if re.search(r'\bCASE\b', expr_upper):
        return 'simple'

    # Basic arithmetic or comparison without function calls
    if re.search(r'[\+\-\*\/\%]', expr) and not re.search(r'\b\w+\s*\(', expr):
        return 'simple'

    # Default: assume translation needed for unrecognized patterns
    return 'needs_translation'
```

## View Inventory (Equivalent of Dashboard Inventory)

Denodo has no dashboards. The equivalent organizational structure is the **folder hierarchy** + **Interface Views** (the published API surface). Use this Python function to build a complete inventory.

```python
def build_view_inventory(conn, database):
    """
    Build a complete view inventory for a Denodo database.
    Returns a list of view records with folder path, type, column count,
    and dependency depth — analogous to a dashboard/report inventory.
    """
    elements = get_all_elements(conn, database)
    all_cols = get_all_view_columns(conn, database)

    # Index column counts by view name
    col_count = {}
    for row in all_cols:
        vn = row['view_name']
        col_count[vn] = col_count.get(vn, 0) + 1

    # Build dependency map
    dep_graph = {}
    for el in elements:
        if el.get('element_type', '').upper() == 'VIEW':
            vn = el['element_name']
            deps = get_view_dependencies(conn, database, vn)
            dep_graph[vn] = [d['dependency_name'] for d in deps]

    def compute_depth(view_name, visited=None):
        if visited is None:
            visited = set()
        if view_name in visited:
            return 0  # cycle guard
        visited.add(view_name)
        deps = dep_graph.get(view_name, [])
        if not deps:
            return 0
        return 1 + max(compute_depth(d, visited.copy()) for d in deps)

    inventory = []
    for el in elements:
        if el.get('element_type', '').upper() != 'VIEW':
            continue

        vn      = el['element_name']
        subtype = el.get('element_subtype', 'UNKNOWN').upper()
        folder  = el.get('folder', '/')

        inventory.append({
            'view_name':        vn,
            'folder_path':      folder,
            'view_subtype':     subtype,
            'is_interface':     subtype == 'INTERFACE',
            'column_count':     col_count.get(vn, 0),
            'dependency_depth': compute_depth(vn),
            'direct_deps':      dep_graph.get(vn, []),
        })

    # Sort: Interface views first (published API surface), then folder, then depth
    inventory.sort(
        key=lambda v: (0 if v['is_interface'] else 1, v['folder_path'], v['dependency_depth'])
    )
    return inventory


def print_inventory_summary(inventory):
    """Print a summary table of the view inventory."""
    print(f"{'View Name':<40} {'Type':<12} {'Folder':<30} {'Cols':>4} {'Depth':>5}")
    print("-" * 95)
    for v in inventory:
        flag = " *" if v['is_interface'] else ""
        print(
            f"{v['view_name']:<40} {v['view_subtype']:<12} "
            f"{v['folder_path']:<30} {v['column_count']:>4} {v['dependency_depth']:>5}{flag}"
        )
    print(f"\n* = Interface View (prioritize for semantic view extraction)")
    print(f"\nTotal views: {len(inventory)}")
    print(f"Interface views: {sum(1 for v in inventory if v['is_interface'])}")
    print(f"Deep views (depth >= 3): {sum(1 for v in inventory if v['dependency_depth'] >= 3)}")
```

**Prioritization guidance**: Focus on **Interface Views** first — they represent the stable, published API that downstream consumers already use. Folder structure maps to business domains; use it to split large inventories into focused semantic views per domain. Views with `dependency_depth >= 3` are high-complexity candidates for manual review before attempting automated extraction.

## Gotchas and Limitations

1. **No dimension/measure metadata**: Denodo VQL carries zero role information for columns. All classification is inferred. Always validate the inventory with a Denodo data steward before finalizing.
2. **VQL export is database-scoped**: A single VQL export covers one database. Multi-database Denodo environments require one export per database.
3. **Base View source tables may be virtual**: Some Base Views point to other Denodo servers via VDP Federation or to REST/SOAP web services. These cannot be mapped to `base_table:` in a semantic view — flag them as manual.
4. **CONTEXT clause has no Snowflake equivalent**: Denodo's `CONTEXT ('i18n' = 'us_pst', 'cache_preload' = 'true', ...)` is a runtime execution hint. Strip it before translating SQL.
5. **View parameters (`$param`)**: Derived views can be parameterized (`WHERE region = $regionParam`). These must be hardcoded, converted to `filters:` in the semantic view, or exposed as separate semantic views per parameter value.
6. **ARRAY / REGISTER types**: Denodo supports complex columnar types. These cannot be mapped directly to semantic view columns. Create a Snowflake view using `LATERAL FLATTEN` first, then reference the flattened view.
7. **Data Catalog descriptions are separate**: Business-friendly column descriptions and tags live only in Data Catalog, not in VQL exports. Always merge Catalog metadata when available.
8. **Circular view dependencies**: Denodo allows parameterized views with circular references. The `compute_depth()` function includes a cycle guard, but flag these views for manual inspection.
9. **No Autopilot support**: Snowflake Semantic View Autopilot does not support Denodo files. Generate YAML directly using Path A, B, or C extraction above.
10. **GETDAYOFWEEK offset**: VQL `GETDAYOFWEEK` returns 1=Monday. Snowflake `DAYOFWEEK` returns 0=Sunday by default. Apply `MOD(DAYOFWEEK(date)+6,7)+1` to align when migrating day-of-week logic.
