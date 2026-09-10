# SAP Business Objects Extraction Reference

## File Formats

| Format | Description | Extraction Method |
|--------|-------------|-------------------|
| `.unv` | Universe Design Tool (UDT) format. Proprietary binary — **not directly parseable** by standard tools. Must be exported from UDT or converted via third-party tools before any programmatic access. | Export via UDT → File → Export as `.unv`, then use `starschema/business-objects-universe-extractor` Java tool to convert to JSON. |
| `.unx` | Information Design Tool (IDT) format. Binary with structured metadata but not plain-text readable. The current format for BI 4.x universes. | Parse via SAP Semantic Layer Java SDK (programmatic), `biclever UnxDoc` (exports to Excel), or `starschema/business-objects-universe-extractor`. |
| `.biar` | Business Intelligence Archive Resource. ZIP-like container that can hold universes, reports, connections, and other BI objects. Exported from the Central Management Console (CMC). | Unzip with standard tools; extract `.unv`/`.unx` files inside, then process as above. |
| Repository (CMS) | Central Management Server — the live runtime repository where all universe and report objects are stored. Authoritative source. | Access via SAP Semantic Layer Java SDK or SAP RESTful Web Service SDK (limited metadata). |
| JSON export | Structured JSON output from `starschema/business-objects-universe-extractor` Java tool. Parses `.unv`/`.unx` files and outputs classes, objects, joins, and contexts in a machine-readable format. | **Recommended path.** Parse JSON with Python using the patterns in this reference. |
| Excel export | Output of `biclever UnxDoc` tool, which parses `.unx` files and writes business layer objects to Excel sheets. | Read with `openpyxl` or `pandas`. Useful for quick audits; less structured than JSON. |

### Extracting .biar Contents

```python
import zipfile, os, glob

def extract_biar(biar_path, out_dir='extracted_biar'):
    """Unpack a .biar archive and find any universe files inside."""
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(biar_path, 'r') as z:
        z.extractall(out_dir)
    unv_files = glob.glob(os.path.join(out_dir, '**', '*.unv'), recursive=True)
    unx_files = glob.glob(os.path.join(out_dir, '**', '*.unx'), recursive=True)
    return {'unv': unv_files, 'unx': unx_files}
```

## Universe Structure Overview

SAP BO universes have two layers separated by a clear boundary: the **Data Foundation** (physical/technical) and the **Business Layer** (semantic/user-facing). This separation is a strength for extraction — the business classification is explicit.

```
CMS Repository
└── Universe (.unv or .unx)
    ├── Connection (database driver, server, credentials, schema)
    ├── Data Foundation  (physical/technical layer)
    │   ├── Physical Tables       (tables from the source database)
    │   ├── Joins                 (join expressions with cardinality: 1:1, 1:N, N:N)
    │   ├── Contexts              (named join paths to resolve schema loops/ambiguity)
    │   └── Derived Tables        (SQL-defined virtual tables, like a CTE)
    └── Business Layer  (semantic/user-facing layer)
        ├── Class  (folder — organizes objects by business domain)
        │   ├── Dimension Object  (type=dimension, SELECT expression returning a value)
        │   ├── Detail Object     (type=detail, dependent on a parent Dimension)
        │   ├── Measure Object    (type=measure, SELECT expression with aggregate function)
        │   ├── Filter            (predefined WHERE clause exposed as a reusable condition)
        │   └── Sub-Class         (nested folder for further organization)
        ├── Hierarchy             (ordered drill path across Dimension objects)
        └── List of Values (LOV)  (enumerated allowed values for a Dimension or prompt)
```

**Key advantage over Denodo/Tableau**: SAP BO universes **natively classify** every object as Dimension, Detail, or Measure. There is no need for heuristic inference — the type is an explicit, author-assigned property. This significantly reduces classification uncertainty during extraction.

**Key distinction**: The Data Foundation is the join graph and physical table layer. The Business Layer is what users see. Always extract from the Business Layer for semantic view output; use the Data Foundation only for resolving physical table names, join conditions, and derived table SQL.

## Extraction Code Patterns

### Path A: starschema/business-objects-universe-extractor (Recommended)

The `starschema/business-objects-universe-extractor` is a Java CLI tool that reads `.unv` or `.unx` files and outputs structured JSON. It does not require a live CMS connection or an SAP SDK license.

```bash
# Run the extractor
java -jar bo-universe-extractor.jar -i universe.unx -o universe_output.json

# For a .unv file (UDT format)
java -jar bo-universe-extractor.jar -i universe.unv -o universe_output.json
```

The output JSON has this structure:

```json
{
  "universeName": "Sales Universe",
  "connection": {
    "driver": "Snowflake",
    "server": "myaccount.snowflakecomputing.com",
    "database": "SALES_DB",
    "schema": "PUBLIC"
  },
  "dataFoundation": {
    "tables": [
      { "name": "FACT_SALES", "alias": "FACT_SALES" }
    ],
    "joins": [
      {
        "leftTable": "FACT_SALES", "leftColumn": "PRODUCT_ID",
        "rightTable": "DIM_PRODUCT", "rightColumn": "PRODUCT_ID",
        "joinType": "INNER", "cardinality": "N:1"
      }
    ],
    "contexts": [
      { "name": "Sales Context", "joins": ["FACT_SALES-DIM_PRODUCT", "FACT_SALES-DIM_DATE"] }
    ],
    "derivedTables": [
      { "name": "DT_RECENT_ORDERS", "sql": "SELECT * FROM FACT_ORDERS WHERE order_date >= DATEADD('DAY',-90,CURRENT_DATE)" }
    ]
  },
  "businessLayer": {
    "classes": [
      {
        "name": "Product",
        "objects": [
          { "name": "Product Name", "type": "dimension", "select": "DIM_PRODUCT.PRODUCT_NAME", "where": null, "description": "The full product name" },
          { "name": "Product SKU",  "type": "dimension", "select": "DIM_PRODUCT.SKU", "where": null, "description": "Stock-keeping unit" },
          { "name": "Category",     "type": "dimension", "select": "DIM_PRODUCT.CATEGORY", "where": null, "description": null },
          { "name": "Revenue",      "type": "measure",   "select": "SUM(FACT_SALES.REVENUE)", "where": null, "description": "Total revenue" },
          { "name": "Unit Cost",    "type": "detail",    "select": "DIM_PRODUCT.UNIT_COST", "where": null, "description": "Cost per unit, dependent on Product SKU" }
        ],
        "subClasses": []
      }
    ]
  }
}
```

```python
import json

def load_bo_json(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_bo_inventory(bo_json):
    """
    Parse the starschema extractor JSON into a flat inventory of objects
    with type, SELECT expression, and complexity classification.
    """
    inventory = []
    connection = bo_json.get('connection', {})
    classes = bo_json.get('businessLayer', {}).get('classes', [])

    def process_class(cls, parent_path=''):
        path = f"{parent_path}/{cls['name']}" if parent_path else cls['name']
        for obj in cls.get('objects', []):
            select_expr = obj.get('select', '')
            where_expr  = obj.get('where', '')
            obj_type    = obj.get('type', 'dimension').lower()
            complexity  = classify_bo_complexity(select_expr, obj_type, where_expr)
            inventory.append({
                'class_path':   path,
                'name':         obj['name'],
                'type':         obj_type,       # 'dimension' | 'detail' | 'measure'
                'select':       select_expr,
                'where':        where_expr,
                'description':  obj.get('description') or '',
                'complexity':   complexity,
            })
        for sub in cls.get('subClasses', []):
            process_class(sub, path)

    for cls in classes:
        process_class(cls)

    return {
        'connection':  connection,
        'objects':     inventory,
        'joins':       bo_json.get('dataFoundation', {}).get('joins', []),
        'contexts':    bo_json.get('dataFoundation', {}).get('contexts', []),
        'derived_tables': bo_json.get('dataFoundation', {}).get('derivedTables', []),
    }
```

### Path B: Semantic Layer Java SDK

For live CMS access — retrieve universes directly from the repository without exporting files. Requires SAP BusinessObjects BI Platform installation and Java SDK access. Use pseudocode below as a structural guide.

```java
// Key SDK classes (com.businessobjects.sdk.plugin.desktop.universe.*)
// IUniverse, IDataFoundation, IBusinessLayer, IClass, IDimension, IMeasure, IJoin

// Connect to CMS
IEnterpriseSession session = CrystalEnterprise.getSessionMgr()
    .logon("user", "pass", "CMSServer", "secEnterprise");
IInfoStore store = (IInfoStore) session.getService("InfoStore");

// Query for universes
IInfoObjects universes = store.query(
    "SELECT SI_NAME, SI_ID FROM CI_APPOBJECTS WHERE SI_PROGID='CrystalEnterprise.Universe'"
);

// For each universe, open and iterate
for (IInfoObject obj : universes) {
    IUniverse universe = (IUniverse) obj;
    IBusinessLayer bl = universe.getBusinessLayer();

    // Iterate classes and objects
    for (IClass cls : bl.getClasses()) {
        for (IObject busObj : cls.getObjects()) {
            String name   = busObj.getName();
            String select = busObj.getSelect();
            String where  = busObj.getWhere();
            // Type: IDimension, IMeasure, IDetail
            boolean isDimension = busObj instanceof IDimension;
            boolean isMeasure   = busObj instanceof IMeasure;
        }
    }

    // Iterate joins from Data Foundation
    IDataFoundation df = universe.getDataFoundation();
    for (IJoin join : df.getJoins()) {
        String expr        = join.getExpression();  // e.g. "T1.COL = T2.COL"
        String cardinality = join.getCardinality(); // e.g. "1,N"
        boolean isOuter    = join.isOuter();
    }
}
```

**Note**: Path B requires the SAP BusinessObjects BI Platform Java SDK, which is bundled with the BI platform server installation. It is not publicly downloadable. Use Path A when live SDK access is unavailable.

### Path C: RESTful Web Service SDK (Inventory Only)

SAP provides a REST API for BI platform metadata. Useful for building a universe inventory but does **not** expose full object SELECT expressions or join conditions — it is a catalog API, not a schema API.

```python
import requests

BO_REST_BASE = "http://<bo-server>:6405/biprws"

def bo_rest_logon(username, password, auth_type='secEnterprise'):
    """Authenticate and return a session token."""
    payload = {
        'userName': username,
        'password': password,
        'auth':     auth_type   # secEnterprise | secLDAP | secSAPR3
    }
    resp = requests.post(f"{BO_REST_BASE}/logon/long", json=payload,
                         headers={'Content-Type': 'application/json',
                                  'Accept': 'application/json'})
    resp.raise_for_status()
    return resp.json()['logonToken']

def list_universes(token):
    """List all universes in the repository (name + ID only)."""
    resp = requests.get(
        f"{BO_REST_BASE}/raylight/v1/universes",
        headers={'X-SAP-LogonToken': token, 'Accept': 'application/json'}
    )
    resp.raise_for_status()
    return resp.json().get('universes', {}).get('universe', [])
    # Returns: id, name, description, type (UNX/UNV)

def get_universe_details(token, universe_id):
    """Get universe details — limited metadata, no SQL expressions."""
    resp = requests.get(
        f"{BO_REST_BASE}/raylight/v1/universes/{universe_id}",
        headers={'X-SAP-LogonToken': token, 'Accept': 'application/json'}
    )
    resp.raise_for_status()
    return resp.json()
```

**Limitation**: Path C is appropriate for discovery (listing all universes, counting objects, identifying candidates). It does not return SELECT expressions or join SQL. For deep extraction, use Path A or B.

### Path D: Manual IDT/UDT Export

When no Java tooling is available, the Information Design Tool (IDT) can export business layer metadata to a human-readable format.

1. Open IDT → File → Export Universe to Local Folder → saves `.unx`
2. In IDT, right-click the Business Layer → "Publish to Local Folder" for a `.blx` sidecar file
3. Some BO versions allow File → Export → Save As XML (partial — not all versions support this)
4. Use the "Show Object Properties" report in IDT to manually inspect SELECT/WHERE expressions

**Note**: Path D produces files for manual review. There is no reliable automated parser for IDT's native export format across all BO versions. Treat Path D as a last resort — gather the exports and convert manually to the inventory format, then use Path A JSON structure as the target schema.

## @Function Translation Table

SAP BO universe objects frequently use `@functions` — BO-specific macros embedded in SELECT and WHERE expressions. These must be resolved before the SQL can be used in a Snowflake Semantic View.

| BO @Function | Purpose | Snowflake Translation | Complexity |
|---|---|---|---|
| `@Aggregate_Aware(agg1, agg2, ...)` | Returns first available pre-aggregated table (aggregate navigation). Arguments listed from most- to least-aggregated. | Use the most granular (last) argument expression; document skipped alternatives as comments. Verify aggregate tables exist in Snowflake. | `needs_translation` |
| `@Prompt('Label','type','lov',mono/multi,free/constrained)` | Runtime user input prompt. Injects a user-supplied value into the WHERE clause. | Remove — becomes a semantic view filter parameter or a documented session variable. Flag for user decision. | `needs_translation` |
| `@Select(Class\Object)` | References another object's SELECT expression by path. Creates an implicit dependency chain. | Inline the referenced object's SQL expression. Recursively resolve nested `@Select` chains. | `needs_translation` |
| `@Variable('BOUSER')` | SAP BO session variable. `BOUSER` = current username. | `CURRENT_USER()`. Other BO variables (`DBUSER`, `BOLOCALE`) require manual mapping or session context UDFs. | `needs_translation` |
| `@Derived_Table(DT_name)` | References a Derived Table defined in the Data Foundation. | Replace with the Derived Table's actual SQL (as a CTE or Snowflake VIEW). | `needs_translation` |
| `@Where(Class\Object)` | References another object's WHERE clause by path. | Inline the referenced filter condition. Recursively resolve if nested. | `needs_translation` |
| `@Script('language','script',args)` | Embeds a VBScript or JavaScript expression for custom logic (legacy feature). | No equivalent. Flag as `manual_required` — rewrite logic in SQL or a Snowflake UDF. | `manual_required` |

### @Select / @Where Resolver

```python
import re

def resolve_at_functions(expr, object_index):
    """
    Resolve @Select(Class\\Object) and @Where(Class\\Object) references
    by inlining expressions from the object index.

    object_index: dict mapping 'ClassName\\ObjectName' → {'select': ..., 'where': ...}
    """
    def inline_select(m):
        path = m.group(1).replace('/', '\\')
        ref = object_index.get(path, {})
        return ref.get('select', f'/* UNRESOLVED @Select({path}) */')

    def inline_where(m):
        path = m.group(1).replace('/', '\\')
        ref = object_index.get(path, {})
        return ref.get('where', f'/* UNRESOLVED @Where({path}) */')

    # Iteratively resolve up to 5 levels of nesting
    for _ in range(5):
        new_expr = re.sub(r'@Select\(([^)]+)\)', inline_select, expr, flags=re.IGNORECASE)
        new_expr = re.sub(r'@Where\(([^)]+)\)',  inline_where,  new_expr, flags=re.IGNORECASE)
        if new_expr == expr:
            break
        expr = new_expr

    return expr

def strip_at_prompt(expr):
    """Remove @Prompt() calls, leaving surrounding SQL intact."""
    return re.sub(r'@Prompt\([^)]*\)', '/* PROMPT_REMOVED */', expr, flags=re.IGNORECASE)

def resolve_at_variable(expr):
    """Translate known @Variable() calls to Snowflake equivalents."""
    replacements = {
        "'BOUSER'":   'CURRENT_USER()',
        '"BOUSER"':   'CURRENT_USER()',
        "'DBUSER'":   'CURRENT_USER()',
        '"DBUSER"':   'CURRENT_USER()',
        "'BOLOCALE'": "'en_US' /* BOLOCALE — hardcode or use session variable */"
    }
    for bo_var, sf_expr in replacements.items():
        expr = re.sub(
            rf'@Variable\s*\(\s*{re.escape(bo_var)}\s*\)',
            sf_expr, expr, flags=re.IGNORECASE
        )
    # Flag any unrecognized @Variable calls
    expr = re.sub(r'@Variable\([^)]*\)',
                  lambda m: f'/* UNRESOLVED {m.group(0)} */', expr, flags=re.IGNORECASE)
    return expr
```

## Object Type Mapping Table

| BO Concept | BO Structure | Snowflake Semantic View | Notes |
|---|---|---|---|
| Universe Connection | `connection` block (driver, server, database, schema) | `TABLES` clause — database, schema, table names | Parse connection string to confirm target Snowflake account/database/schema. |
| Physical Table | Data Foundation table entry (name or alias) | `TABLES` entry with `base_table` reference | Direct mapping if table exists in Snowflake. |
| Derived Table | SQL-defined virtual table in Data Foundation | Create as a Snowflake `VIEW`, then reference in `TABLES` | Must materialize the SQL as a view first — derived tables cannot be inlined into a semantic view directly. |
| Join (1:1 or 1:N) | Data Foundation join (tables, expression, cardinality, outer flag) | `RELATIONSHIPS` entry | Map cardinality: `1:1` → `one_to_one`, `1:N` → `many_to_one`, `N:N` → flag for review. |
| Join (N:N) | Data Foundation many-to-many join | Not directly supported | Flag — requires bridge table pattern or denormalization. |
| Context | Named join path (activates a subset of joins) | Document — may require separate semantic views | One semantic view per context is the cleanest mapping for schemas with multiple fact tables. |
| Class | Business Layer folder | Organizational grouping only | Use class hierarchy to split large universes into focused semantic views by domain. |
| Dimension Object | `type: dimension`, SELECT expression | `DIMENSIONS` | Direct mapping. Resolve any `@functions` in SELECT first. |
| Detail Object | `type: detail`, SELECT expression, linked to parent Dimension | `DIMENSIONS` (dependent column) | Include alongside parent dimension. Add parent relationship in `COMMENT`. Detail objects without a parent Dimension in the same semantic view should be flagged. |
| Measure Object | `type: measure`, SELECT with aggregate function | `METRICS` (if SELECT contains aggregate) or `FACTS` (if simple column reference) | Check if SELECT contains `SUM`, `COUNT`, `AVG`, `MIN`, `MAX`. If yes → `METRICS`. If bare column → `FACTS` with `default_aggregation`. |
| Filter (predefined) | WHERE clause exposed as a named object | `FILTERS` in semantic view | Map condition directly. Strip `@Prompt` calls. |
| Hierarchy | Ordered list of Dimension objects for drill-down | Document only — no direct semantic view equivalent | Note the drill path in `COMMENT` fields on the relevant dimensions. |
| List of Values (LOV) | Enumerated values for a Dimension | `SYNONYMS` (if distinct values are aliases) or documentation | Useful for filter hint documentation in `COMMENT`. |

## `classify_bo_complexity()` Function

```python
import re

BO_AT_SIMPLE = re.compile(
    r'@(Select|Where|Variable|Derived_Table)\s*\(',
    re.IGNORECASE
)

BO_AT_TRANSLATION = re.compile(
    r'@(Aggregate_Aware|Prompt)\s*\(',
    re.IGNORECASE
)

BO_AT_MANUAL = re.compile(
    r'@Script\s*\(',
    re.IGNORECASE
)

BO_STANDARD_AGG = re.compile(
    r'\b(SUM|AVG|COUNT|MIN|MAX)\s*\(',
    re.IGNORECASE
)

BO_BO_DATE_FUNCS = re.compile(
    r'\b(ToDate|DatesBetween|DaysBetween|MonthsBetween|YearsBetween|'
    r'LastDayOfMonth|Quarter|DayOfWeek|NumberOfDays)\s*\(',
    re.IGNORECASE
)

BO_SNOWFLAKE_COMPAT = re.compile(
    r'\b(UPPER|LOWER|TRIM|SUBSTR|SUBSTRING|NVL|COALESCE|REPLACE|CONCAT|'
    r'LENGTH|TO_CHAR|TO_DATE|TO_NUMBER|TRUNC|ROUND|ABS|MOD|SIGN|FLOOR|CEIL|'
    r'NULLIF|CASE|DECODE|YEAR|MONTH|DAY)\s*[(\s]',
    re.IGNORECASE
)


def classify_bo_complexity(select_expr: str, obj_type: str, where_expr: str = None) -> str:
    """
    Classify a SAP BO universe object for migration complexity.

    Parameters:
        select_expr  (str): The object's SELECT expression (may contain @functions).
        obj_type     (str): BO object type: 'dimension' | 'detail' | 'measure' | 'filter'.
        where_expr   (str): The object's WHERE clause, if any.

    Returns:
        str: 'simple' | 'needs_translation' | 'manual_required'
    """
    expr = (select_expr or '') + ' ' + (where_expr or '')

    # --- Manual required ---

    # @Script() has no SQL equivalent
    if BO_AT_MANUAL.search(expr):
        return 'manual_required'

    # @Prompt with multi-value or complex LOV binding
    complex_prompt = re.search(
        r'@Prompt\([^)]*multi[^)]*\)|@Prompt\([^)]*constrained[^)]*LOV[^)]*\)',
        expr, re.IGNORECASE
    )
    if complex_prompt:
        return 'manual_required'

    # Nested @Select chains (more than one level deep)
    select_depth = len(re.findall(r'@Select\s*\(', expr, re.IGNORECASE))
    if select_depth >= 2:
        return 'manual_required'

    # Complex @Aggregate_Aware with 3+ arguments (multi-level aggregate navigation)
    aa_match = re.search(r'@Aggregate_Aware\(([^)]+)\)', expr, re.IGNORECASE)
    if aa_match and aa_match.group(1).count(',') >= 2:
        return 'manual_required'

    # Subquery in WHERE — correlated or complex
    if where_expr and re.search(r'\bSELECT\b', where_expr, re.IGNORECASE):
        return 'manual_required'

    # --- Needs translation ---

    # Any remaining @function (except @Script already caught above)
    if BO_AT_SIMPLE.search(expr) or BO_AT_TRANSLATION.search(expr):
        return 'needs_translation'

    # DECODE with more than 3 branches
    decode_match = re.search(r'DECODE\s*\(([^)]+)\)', expr, re.IGNORECASE)
    if decode_match and decode_match.group(1).count(',') > 4:
        return 'needs_translation'

    # BO-specific date/time functions
    if BO_BO_DATE_FUNCS.search(expr):
        return 'needs_translation'

    # NVL2 (Snowflake uses IFF or CASE instead)
    if re.search(r'\bNVL2\s*\(', expr, re.IGNORECASE):
        return 'needs_translation'

    # CASE with deep nesting (3+ levels)
    if len(re.findall(r'\bCASE\b', expr, re.IGNORECASE)) >= 3:
        return 'needs_translation'

    # --- Simple ---

    # Direct table.column reference (no function call)
    if re.fullmatch(r'\s*\w+\.\w+\s*', (select_expr or '').strip()):
        return 'simple'

    # Standard aggregation over a column reference
    if BO_STANDARD_AGG.search(expr) and not BO_AT_SIMPLE.search(expr):
        return 'simple'

    # Standard SQL scalar functions with 1:1 Snowflake equivalents
    if BO_SNOWFLAKE_COMPAT.search(expr):
        return 'simple'

    # Basic CASE WHEN or DECODE with few branches
    if re.search(r'\bCASE\b|\bDECODE\s*\(', expr, re.IGNORECASE):
        return 'simple'

    # Simple arithmetic
    if re.search(r'[\+\-\*\/]', expr) and not re.search(r'\b\w+\s*\(', expr):
        return 'simple'

    # Default: assume translation needed
    return 'needs_translation'
```

## Join & Context Handling

### Why Contexts Exist

BO universes that model star schemas with multiple fact tables contain **loops** in the join graph — a shared dimension (e.g., `DIM_DATE`) joins to both `FACT_SALES` and `FACT_ORDERS`. Without disambiguation, any query spanning both facts would produce a Cartesian product.

**Contexts** are BO's solution: a named subset of joins that defines one unambiguous traversal path through the join graph. A context named "Sales" activates only the joins relevant to `FACT_SALES`; a context named "Orders" activates only those for `FACT_ORDERS`.

### Extracting and Documenting Contexts

```python
def extract_context_inventory(bo_json):
    """
    Build a context-to-joins map and identify which physical tables
    are active per context. Used to decide if separate semantic views
    are needed.
    """
    df = bo_json.get('dataFoundation', {})
    all_joins = df.get('joins', [])
    contexts  = df.get('contexts', [])

    # Index joins by a generated key
    join_index = {}
    for j in all_joins:
        key = f"{j['leftTable']}-{j['rightTable']}"
        join_index[key] = j

    context_inventory = []
    for ctx in contexts:
        active_joins  = [join_index[k] for k in ctx.get('joins', []) if k in join_index]
        active_tables = set()
        for j in active_joins:
            active_tables.add(j['leftTable'])
            active_tables.add(j['rightTable'])

        context_inventory.append({
            'context_name':   ctx['name'],
            'active_joins':   active_joins,
            'active_tables':  sorted(active_tables),
            'join_count':     len(active_joins),
        })

    return context_inventory


def print_context_summary(context_inventory):
    """Print a decision table for context → semantic view mapping."""
    print(f"{'Context':<30} {'Tables':<50} {'Joins':>5}")
    print("-" * 90)
    for ctx in context_inventory:
        tables_str = ', '.join(ctx['active_tables'][:4])
        if len(ctx['active_tables']) > 4:
            tables_str += f" (+{len(ctx['active_tables'])-4} more)"
        print(f"{ctx['context_name']:<30} {tables_str:<50} {ctx['join_count']:>5}")
    print(f"\nRecommendation: create one semantic view per context if contexts share dimension tables.")
```

### Context → Semantic View Strategy

| Scenario | Recommendation |
|----------|----------------|
| Single context (or no contexts) | One semantic view covering all joins. |
| Multiple contexts, separate fact tables, shared dimensions | One semantic view per context; shared dimensions appear in each. |
| Multiple contexts, overlapping join sets | Flag for manual review — resolve with Snowflake VIEWs that pre-join the relevant tables. |
| Objects valid across all contexts | Include in every generated semantic view with the same definition. |

## Gotchas and Limitations

1. **`.unv` files are binary and not directly parseable.** They require either the UDT application, the `starschema/business-objects-universe-extractor` Java tool, or export to `.unx` via IDT before any automated processing is possible. Do not attempt to read `.unv` as XML or ZIP.

2. **`.unx` files require the SAP Semantic Layer Java SDK for programmatic access** via the live CMS. The SDK is not publicly downloadable — it ships with the BI platform server. If SDK access is unavailable, use Path A (JSON extractor) or Path D (manual IDT export).

3. **`@Aggregate_Aware()` hides complexity.** An object with `@Aggregate_Aware(SUM(AGG_SALES.REVENUE), SUM(FACT_SALES.REVENUE))` looks like a simple SUM but implies aggregate navigation across multiple physical tables. Always expand the arguments and document which aggregate tables exist in Snowflake before mapping.

4. **Universe security (row-level restrictions) does not transfer.** BO supports universe-level security profiles that apply row-level WHERE filters per user group. These restrictions are stored in the CMS security repository, not in the `.unv`/`.unx` file itself. They are invisible during file-based extraction. Always ask the BI team whether security profiles are in use before declaring extraction complete.

5. **Cascading prompts (dependent LOVs) have no semantic view equivalent.** BO supports `@Prompt` chains where the values available for one prompt depend on the selection of another (e.g., Country prompt constrains the City prompt). There is no equivalent in Snowflake Semantic Views. Flag these and document the dependency for downstream filter design.

6. **Detail objects are dependent on parent Dimensions.** A Detail object (e.g., "Employee Email" as a detail of "Employee Name") is only meaningful in queries that also include its parent Dimension. When extracting, always identify the parent Dimension and include both together in the semantic view. A Detail without its parent in the same semantic view should be promoted to a standalone Dimension or flagged for review.

7. **Derived Tables may use BO-specific or source-dialect SQL.** Derived Table SQL in the Data Foundation is written against the source database dialect — which may be Oracle, SAP HANA, or an older Snowflake-connected universe. Review each Derived Table's SQL for dialect-specific functions (`ROWNUM`, `SYSDATE`, `NVL2`, `TO_CHAR` with Oracle format strings) before creating the equivalent Snowflake VIEW.

8. **Multi-source universes (federated) may join across databases.** Enterprise universes sometimes federate data from multiple connection strings — e.g., a join between a table from Oracle and a table from SQL Server. These cross-database joins cannot be represented in a single Snowflake Semantic View. Each source must be landed in Snowflake first.

9. **"Incompatible objects" flag must be respected.** BO tracks pairs of objects that cannot coexist in a single query because they belong to different contexts or produce ambiguous SQL (e.g., two measures from different fact tables). These pairs are marked as incompatible in the Business Layer. Extract this metadata and document it — they map directly to objects that cannot appear in the same semantic view.

10. **Connection server names in `.unv`/`.unx` may not match current Snowflake endpoints.** The connection stored in the universe was configured at design time and may reference a legacy ODBC DSN, an old account identifier format, or a non-Snowflake source. Always verify the connection against the current Snowflake account URL before assuming the physical tables are directly accessible.

11. **No Autopilot support.** Snowflake Semantic View Autopilot does not support SAP BO universe files. Generate YAML directly using the extraction inventory from Path A, B, or C above.

12. **LOV (List of Values) queries can be complex SQL.** Some LOVs are backed by custom SQL queries (not just `SELECT DISTINCT col FROM table`). These queries may contain joins, filters, or `@Prompt` references. Extract and review LOV SQL separately — they often reveal which columns are used as filter axes and which dimension values are expected by end users.
