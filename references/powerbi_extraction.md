# Power BI Extraction Reference

## File Formats

| Format | Extension | Description |
|--------|-----------|-------------|
| **PBIX** | `.pbix` | Power BI report file. ZIP archive containing model, report layout, and imported data. Data model stored with Xpress9 compression (not directly readable). |
| **PBIT** | `.pbit` | Power BI template file. ZIP archive like PBIX but **without data**. Contains `DataModelSchema` as readable JSON. Easiest to parse. |
| **PBIP** | `.pbip` | Power BI Project format (Developer Mode). Folder-based, Git-friendly. Contains `<name>.SemanticModel/model.bim` in JSON (TOM format). **Best for extraction.** |
| **BIM** | `.bim` | Tabular Model JSON (TOM/TMSL). Single JSON file describing the entire model. Can be exported via Tabular Editor or `pbi-tools`. |
| **TMDL** | `.tmdl` | Tabular Model Definition Language. YAML-like format, one file per object. Newer alternative to BIM. |

### Extraction Priority

1. **`.pbip` project folder** — already decomposed, `model.bim` is plain JSON
2. **`.pbit` template** — unzip, read `DataModelSchema` as JSON
3. **`.bim` file** — already JSON, parse directly
4. **`pbi-tools extract`** — decompose `.pbix` into JSON model files
5. **`PBIXRay` Python library** — parse `.pbix` directly (handles Xpress9)
6. **XMLA endpoint / DMVs** — query live model metadata via API
7. **Power BI REST API** — workspace-level metadata (less detailed)

## Tabular Model JSON Structure (BIM / DataModelSchema)

The core model JSON follows the Tabular Object Model (TOM) schema:

```json
{
  "compatibilityLevel": 1567,
  "model": {
    "tables": [
      {
        "name": "Sales",
        "columns": [
          {
            "name": "SalesAmount",
            "dataType": "double",
            "sourceColumn": "SalesAmount",
            "summarizeBy": "sum"
          },
          {
            "name": "OrderDate",
            "dataType": "dateTime",
            "sourceColumn": "OrderDate"
          }
        ],
        "measures": [
          {
            "name": "Total Revenue",
            "expression": "SUMX(Sales, Sales[Quantity] * RELATED(Product[Price]))",
            "formatString": "$#,##0.00",
            "displayFolder": "Revenue Metrics",
            "description": "Total revenue across all products"
          }
        ],
        "partitions": [
          {
            "name": "Sales",
            "source": {
              "type": "m",
              "expression": "let Source = Snowflake.Databases(\"account.snowflakecomputing.com\", \"WAREHOUSE\"), ... in #\"Filtered Rows\""
            }
          }
        ],
        "hierarchies": [...],
        "annotations": [...]
      }
    ],
    "relationships": [
      {
        "name": "auto-generated-guid",
        "fromTable": "Sales",
        "fromColumn": "ProductID",
        "toTable": "Products",
        "toColumn": "ProductID",
        "crossFilteringBehavior": "oneDirection"
      }
    ],
    "dataSources": [...],
    "roles": [...],
    "cultures": [...]
  }
}
```

### Key TOM Objects to Extract

| Object | Path | Semantic View Equivalent |
|--------|------|--------------------------|
| `model.tables[].name` | Table name | `tables[].name` |
| `model.tables[].columns[]` | Columns (name, dataType, sourceColumn) | `dimensions[]` or `facts[]` |
| `model.tables[].measures[]` | DAX measures (name, expression) | `metrics[]` |
| `model.tables[].partitions[].source` | M/Power Query source expression | Connection/table resolution |
| `model.tables[].hierarchies[]` | Drill-down hierarchies | Not directly mapped (flatten) |
| `model.relationships[]` | Table relationships | `relationships[]` |
| `model.tables[].columns[].summarizeBy` | Default aggregation | `default_aggregation` on facts |

## Python Extraction Patterns

### From .pbit file (template)

```python
import zipfile, json

def extract_from_pbit(pbit_path):
    with zipfile.ZipFile(pbit_path, 'r') as z:
        # DataModelSchema is the model JSON
        raw = z.read('DataModelSchema')
        # May have BOM — strip it
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        model = json.loads(raw)
    return model
```

### From .pbip project folder

```python
import json, glob, os

def extract_from_pbip(pbip_folder):
    # Find model.bim in the SemanticModel subfolder
    bim_files = glob.glob(os.path.join(pbip_folder, '**', 'model.bim'), recursive=True)
    if not bim_files:
        raise FileNotFoundError("No model.bim found in .pbip project")
    with open(bim_files[0], 'r', encoding='utf-8') as f:
        model = json.load(f)
    return model
```

### From .bim file directly

```python
import json

def extract_from_bim(bim_path):
    with open(bim_path, 'r', encoding='utf-8') as f:
        model = json.load(f)
    return model
```

### Using PBIXRay (for .pbix files)

```python
# pip install pbixray
from pbixray import PBIXRay

model = PBIXRay("report.pbix")

# Tables
tables = model.tables          # list of table names
schema = model.schema           # DataFrame: TableName, ColumnName, PandasDataType

# DAX measures
measures = model.dax_measures   # DataFrame: TableName, Name, Expression, DisplayFolder, Description

# Calculated columns
calc_cols = model.dax_columns   # DataFrame: TableName, ColumnName, Expression

# Calculated tables
calc_tables = model.dax_tables  # DataFrame: TableName, Expression

# Power Query / M code
pq = model.power_query          # DataFrame: TableName, Expression

# Relationships (available via model metadata)
metadata = model.metadata
```

### Extracting all semantic objects from TOM JSON

```python
def extract_semantics(model_json):
    """Extract all semantic objects from a TOM model JSON."""
    model = model_json.get('model', model_json)
    
    tables = []
    dimensions = []
    measures = []
    relationships = []
    flagged = []
    
    for table in model.get('tables', []):
        table_name = table['name']
        # Skip internal tables
        if table_name.startswith('DateTableTemplate') or table_name.startswith('LocalDateTable'):
            continue
        
        table_info = {
            'name': table_name,
            'columns': [],
            'measures': [],
            'source_query': None
        }
        
        # Extract source query from partitions
        for partition in table.get('partitions', []):
            source = partition.get('source', {})
            if source.get('type') == 'm':
                table_info['source_query'] = source.get('expression', '')
            elif source.get('type') == 'calculated':
                table_info['source_query'] = source.get('expression', '')
                flagged.append({
                    'item': f"{table_name} (calculated table)",
                    'reason': 'calculated_table',
                    'expression': source.get('expression', '')
                })
        
        # Columns
        for col in table.get('columns', []):
            if col.get('isHidden'):
                continue
            col_info = {
                'name': col['name'],
                'data_type': col.get('dataType', 'string'),
                'source_column': col.get('sourceColumn'),
                'summarize_by': col.get('summarizeBy', 'none'),
                'type': col.get('type', 'data')  # 'data', 'calculated', 'calculatedTableColumn'
            }
            
            # Calculated columns have DAX expressions
            if col.get('type') == 'calculated':
                col_info['expression'] = col.get('expression', '')
                complexity = classify_dax_complexity(col_info['expression'])
                col_info['complexity'] = complexity
                if complexity == 'manual_required':
                    flagged.append({
                        'item': f"{table_name}.{col['name']}",
                        'reason': 'complex_dax_calculated_column',
                        'expression': col_info['expression']
                    })
            
            table_info['columns'].append(col_info)
            
            # Classify as dimension or fact based on summarizeBy
            if col_info['summarize_by'] != 'none':
                dimensions.append({**col_info, 'table': table_name, 'semantic_type': 'fact'})
            else:
                dimensions.append({**col_info, 'table': table_name, 'semantic_type': 'dimension'})
        
        # Measures
        for measure in table.get('measures', []):
            expr = measure.get('expression', '')
            complexity = classify_dax_complexity(expr)
            measure_info = {
                'name': measure['name'],
                'expression': expr,
                'format_string': measure.get('formatString', ''),
                'display_folder': measure.get('displayFolder', ''),
                'description': measure.get('description', ''),
                'table': table_name,
                'complexity': complexity
            }
            measures.append(measure_info)
            table_info['measures'].append(measure_info)
            
            if complexity == 'manual_required':
                flagged.append({
                    'item': f"{table_name}.[{measure['name']}]",
                    'reason': 'complex_dax_measure',
                    'expression': expr
                })
        
        tables.append(table_info)
    
    # Relationships
    for rel in model.get('relationships', []):
        relationships.append({
            'from_table': rel.get('fromTable'),
            'from_column': rel.get('fromColumn'),
            'to_table': rel.get('toTable'),
            'to_column': rel.get('toColumn'),
            'cross_filter': rel.get('crossFilteringBehavior', 'oneDirection'),
            'is_active': rel.get('isActive', True),
            'cardinality': rel.get('fromCardinality', 'many') + '-to-' + rel.get('toCardinality', 'one')
        })
    
    return {
        'tables': tables,
        'dimensions': dimensions,
        'measures': measures,
        'relationships': relationships,
        'flagged': flagged
    }


def classify_dax_complexity(expression):
    """Classify DAX expression complexity for conversion to SQL."""
    if not expression:
        return 'simple'
    
    expr_upper = expression.upper()
    
    # Manual required — complex DAX patterns
    manual_patterns = [
        'CALCULATE(',       # Filter context manipulation
        'CALCULATETABLE(',
        'ALL(',             # Context removal
        'ALLEXCEPT(',
        'ALLSELECTED(',
        'RANKX(',           # Ranking with context
        'EARLIER(',         # Row context reference
        'EARLIEST(',
        'ISINSCOPE(',
        'USERELATIONSHIP(', # Inactive relationship activation
        'CROSSFILTER(',
        'TREATAS(',
        'SUMMARIZECOLUMNS(',
        'ADDCOLUMNS(',
        'SELECTCOLUMNS(',
        'GENERATE(',
        'GENERATESERIES(',
        'PATH(',            # Parent-child hierarchy
        'PATHITEM(',
        'PATHLENGTH(',
        'HASONEVALUE(',     # Filter context inspection
        'HASONEFILTER(',
        'ISFILTERED(',
        'ISCROSSFILTERED(',
        'SELECTEDVALUE(',
        'PARALLELPERIOD(',  # Complex time intelligence
        'OPENINGBALANCEMONTH(', 'OPENINGBALANCEQUARTER(', 'OPENINGBALANCEYEAR(',
        'CLOSINGBALANCEMONTH(', 'CLOSINGBALANCEQUARTER(', 'CLOSINGBALANCEYEAR(',
        'CALENDAR(',        # Calculated tables
        'CALENDARAUTO(',
        'PERCENTILEX.INC(', 'PERCENTILEX.EXC(',  # Iterator statistics
        'MEDIANX(',
        'NPV(', 'XNPV(', 'IRR(', 'XIRR(',      # Financial functions (no SQL built-in)
        'PMT(', 'PV(', 'FV(',
        'GROUPBY(',
        'NATURALINNERJOIN(', 'NATURALLEFTOUTERJOIN(',
    ]
    
    if any(p in expr_upper for p in manual_patterns):
        return 'manual_required'
    
    # Needs translation — functions with Snowflake equivalents requiring adjustment
    translation_patterns = [
        'SUMX(',            # Iterator aggregations
        'AVERAGEX(',
        'COUNTX(',
        'MINX(',
        'MAXX(',
        'RELATED(',         # Simple join reference
        'RELATEDTABLE(',
        'DIVIDE(',          # Safe division
        'IF(',              # Conditional logic
        'SWITCH(',
        'IFERROR(',
        'FORMAT(',          # Formatting
        'DATESYTD(',        # Time intelligence
        'DATESMTD(',
        'DATESQTD(',
        'DATEADD(',
        'DATESBETWEEN(',
        'DATESINPERIOD(',
        'SAMEPERIODLASTYEAR(',
        'TOTALYTD(',
        'TOTALMTD(',
        'TOTALQTD(',
        'PREVIOUSMONTH(', 'PREVIOUSQUARTER(', 'PREVIOUSYEAR(',
        'NEXTMONTH(', 'NEXTQUARTER(', 'NEXTYEAR(',
        'PREVIOUSDAY(', 'NEXTDAY(',
        'DISTINCTCOUNT(',
        'COALESCE(',
        'CONCATENATEX(',
        'FIRSTDATE(',
        'LASTDATE(',
        'ISBLANK(',
        'LOOKUPVALUE(',     # Scalar lookup (becomes subquery)
        # Text functions
        'MID(', 'SUBSTITUTE(', 'REPLACE(', 'SEARCH(', 'FIND(',
        'VALUE(', 'FIXED(', 'COMBINEVALUES(',
        # Math functions
        'ROUNDUP(', 'ROUNDDOWN(', 'INT(', 'CEILING(', 'FLOOR(',
        'LOG(', 'LOG10(', 'RAND(', 'RANDBETWEEN(',
        'EVEN(', 'ODD(', 'QUOTIENT(', 'CURRENCY(', 'MROUND(',
        # Information functions
        'ISERROR(', 'ISNUMBER(', 'ISTEXT(', 'ISLOGICAL(', 'ISNONTEXT(',
        'USERNAME(', 'USERPRINCIPALNAME(',
        # Statistical functions
        'PERCENTILE.INC(', 'PERCENTILE.EXC(',
        'NORM.DIST(',
    ]
    
    if any(p in expr_upper for p in translation_patterns):
        return 'needs_translation'
    
    # Simple — basic aggregations and direct Snowflake equivalents
    simple_patterns = [
        'SUM(', 'COUNT(', 'COUNTROWS(', 'AVERAGE(', 'MIN(', 'MAX(', 'DISTINCTCOUNT(',
        # Text (1:1 mapping)
        'CONCATENATE(', 'LEFT(', 'RIGHT(', 'LEN(', 'UPPER(', 'LOWER(', 'TRIM(',
        'EXACT(', 'REPT(', 'UNICHAR(', 'UNICODE(',
        # Math (1:1 mapping)
        'ABS(', 'ROUND(', 'MOD(', 'POWER(', 'SQRT(', 'LN(', 'EXP(', 'SIGN(', 'PI(', 'TRUNC(',
        # Date (1:1 mapping)
        'YEAR(', 'MONTH(', 'DAY(', 'HOUR(', 'MINUTE(', 'SECOND(', 'TODAY(', 'NOW(',
        'DATE(', 'TIME(', 'WEEKDAY(', 'WEEKNUM(', 'EOMONTH(',
        # Statistical (1:1 mapping)
        'STDEV.S(', 'STDEV.P(', 'VAR.S(', 'VAR.P(', 'MEDIAN(',
        # Logical (1:1 mapping)
        'AND(', 'OR(', 'NOT(', 'TRUE(', 'FALSE(', 'COALESCE(',
        'BLANK(',
        # Start/End of period (1:1 via DATE_TRUNC/LAST_DAY)
        'STARTOFMONTH(', 'STARTOFQUARTER(', 'STARTOFYEAR(',
        'ENDOFMONTH(', 'ENDOFQUARTER(', 'ENDOFYEAR(',
    ]
    if any(p in expr_upper for p in simple_patterns):
        return 'simple'
    
    return 'needs_translation'  # default to translation for unknown patterns
```

### Resolving Power Query / M Source Tables

Power BI tables get their data from M (Power Query) expressions in `partitions[].source.expression`. To find the Snowflake source table:

```python
import re

def extract_source_from_m(m_expression):
    """Attempt to extract source table from M/Power Query expression."""
    if not m_expression:
        return None
    
    # Pattern: Snowflake.Databases("account", "warehouse")
    snowflake_match = re.search(
        r'Snowflake\.Databases\("([^"]+)",\s*"([^"]+)"\)', m_expression
    )
    
    # Pattern: {[Name="DATABASE"]}[Data] -> {[Name="SCHEMA"]}[Data] -> {[Name="TABLE"]}[Data]
    name_matches = re.findall(r'\{?\[Name="([^"]+)"\]\}?\[Data\]', m_expression)
    
    if name_matches and len(name_matches) >= 3:
        return {
            'database': name_matches[0],
            'schema': name_matches[1],
            'table': name_matches[2]
        }
    
    # Pattern: direct SQL query
    sql_match = re.search(r'Value\.NativeQuery\([^,]+,\s*"([^"]+)"', m_expression)
    if sql_match:
        return {'native_query': sql_match.group(1)}
    
    return None
```

## Power BI REST API Patterns

### Authentication

```python
import requests

def get_pbi_token(client_id, client_secret, tenant_id):
    """Get OAuth token for Power BI REST API using service principal."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }
    response = requests.post(url, data=payload)
    response.raise_for_status()
    return response.json()['access_token']
```

### Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1.0/myorg/groups` | GET | List workspaces |
| `/v1.0/myorg/groups/{groupId}/datasets` | GET | List datasets (semantic models) in workspace |
| `/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/tables` | GET | List tables in dataset |
| `/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeQueries` | POST | Execute DAX query (including `INFO.*` DMVs) |
| `/v1.0/myorg/groups/{groupId}/reports` | GET | List reports |
| `/v1.0/myorg/groups/{groupId}/reports/{reportId}/pages` | GET | List report pages |
| `/v1.0/myorg/admin/workspaces/getInfo` | POST | Scanner API — bulk metadata with DAX expressions |

### Extracting Metadata via DAX DMVs (executeQueries endpoint)

The most powerful approach — run DAX queries against the model to extract metadata:

```python
def execute_dax_query(token, group_id, dataset_id, dax_query):
    """Execute a DAX query against a Power BI dataset."""
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{group_id}/datasets/{dataset_id}/executeQueries"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'queries': [{'query': dax_query}],
        'serializerSettings': {'includeNulls': True}
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()['results'][0]['tables'][0]['rows']

# Get all measures with expressions
measures = execute_dax_query(token, gid, did, "EVALUATE INFO.VIEW.MEASURES()")

# Get all columns
columns = execute_dax_query(token, gid, did,
    "EVALUATE SELECTCOLUMNS(INFO.COLUMNS(), \"TableID\", [TableID], \"Name\", [ExplicitName], \"Type\", [Type], \"DataType\", [DataType], \"Expression\", [Expression])"
)

# Get all tables
tables = execute_dax_query(token, gid, did,
    "EVALUATE SELECTCOLUMNS(INFO.TABLES(), \"ID\", [ID], \"Name\", [Name])"
)

# Get all relationships
relationships = execute_dax_query(token, gid, did,
    "EVALUATE INFO.RELATIONSHIPS()"
)

# Combined: all DAX expressions (measures + calculated columns + calculated tables)
all_expressions = execute_dax_query(token, gid, did, """
EVALUATE
VAR _measures = SELECTCOLUMNS(INFO.MEASURES(), "ID", [ID], "TableID", [TableID], "Name", [Name], "Expression", [Expression], "Type", "Measure")
VAR _calc_cols = SELECTCOLUMNS(INFO.COLUMNS("Type", 2), "ID", [ID], "TableID", [TableID], "Name", [ExplicitName], "Expression", [Expression], "Type", "Calculated column")
VAR _calc_tables = SELECTCOLUMNS(INFO.PARTITIONS("Type", 2), "ID", [TableID], "TableID", BLANK(), "Name", BLANK(), "Expression", [QueryDefinition], "Type", "Calculated table")
RETURN UNION(_measures, _calc_cols, _calc_tables)
""")
```

### Scanner API (Workspace-level bulk extraction)

For tenant-wide extraction across many workspaces:

```python
def scan_workspaces(token, workspace_ids):
    """Use Scanner API to extract metadata from multiple workspaces."""
    url = "https://api.powerbi.com/v1.0/myorg/admin/workspaces/getInfo"
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    
    # Request with DAX expressions and schema
    params = {'datasetExpressions': 'true', 'datasetSchema': 'true'}
    payload = {'workspaces': workspace_ids}  # max 100 per call
    
    response = requests.post(url, headers=headers, json=payload, params=params)
    scan_id = response.json()['id']
    
    # Poll for results
    import time
    time.sleep(5)  # adjust for workspace size
    
    results_url = f"https://api.powerbi.com/v1.0/myorg/admin/workspaces/scanResult/{scan_id}"
    results = requests.get(results_url, headers=headers)
    return results.json()
```

**Requirements**: Fabric Admin or Power Platform Admin role. Tenant settings must enable:
- "Enhance admin APIs responses with detailed metadata"
- "Enhance admin APIs responses with DAX and mashup expressions"

## XMLA Endpoint

For Premium/Fabric workspaces, the XMLA endpoint provides full Analysis Services access:

- **Connection string**: `powerbi://api.powerbi.com/v1.0/myorg/<workspace_name>`
- **DMV queries** via SSMS, DAX Studio, or Python (`adomd` / `ssas-api`):
  - `$SYSTEM.TMSCHEMA_MEASURES` — all measures
  - `$SYSTEM.TMSCHEMA_COLUMNS` — all columns
  - `$SYSTEM.TMSCHEMA_TABLES` — all tables
  - `$SYSTEM.TMSCHEMA_RELATIONSHIPS` — all relationships
  - `$SYSTEM.MDSCHEMA_MEASURES` — OLAP-style measure metadata

## Relationship Type Mapping

| Power BI | Snowflake Semantic View |
|----------|------------------------|
| Many-to-One (default) | `relationship_type: many_to_one` |
| One-to-One | `relationship_type: one_to_one` |
| Many-to-Many | Not directly supported — flag for review |
| Single direction cross-filter | Default |
| Both directions cross-filter | Flag — may need explicit join logic |
| Inactive relationships | Flag — activated via `USERELATIONSHIP()` in DAX |

## Report Layout Extraction

Report pages and visuals are stored separately from the model:

- In `.pbix`/`.pbit`: `Report/Layout` file (JSON)
- In `.pbip`: `<name>.Report/report.json`

```python
def extract_report_pages(layout_json):
    """Extract report pages and visual field mappings."""
    pages = []
    for section in layout_json.get('sections', []):
        page = {
            'name': section.get('displayName', section.get('name', 'Unknown')),
            'visuals': []
        }
        for container in section.get('visualContainers', []):
            config = json.loads(container.get('config', '{}'))
            visual_type = config.get('singleVisual', {}).get('visualType', 'unknown')
            
            # Extract field references from projections
            projections = config.get('singleVisual', {}).get('projections', {})
            fields = []
            for role, bindings in projections.items():
                for binding in bindings:
                    query_ref = binding.get('queryRef', '')
                    fields.append({'role': role, 'field': query_ref})
            
            page['visuals'].append({
                'type': visual_type,
                'fields': fields
            })
        pages.append(page)
    return pages
```

## pbi-tools CLI

`pbi-tools` is a .NET CLI tool that decomposes `.pbix` files into source-control-friendly files:

```bash
# Install (Windows .NET tool)
dotnet tool install --global pbi-tools

# Extract .pbix to folder
pbi-tools extract "report.pbix"
# Creates: report/Model/model.bim (or TMDL), Report/*, ...

# Extract with raw model serialization (single BIM file)
pbi-tools extract "report.pbix" -modelSerialization Raw

# Generate BIM from extracted folder
pbi-tools generate-bim "report/"
```

**Note**: `pbi-tools` is Windows/.NET only. On macOS/Linux, use `PBIXRay` (Python) or extract from `.pbit`/`.pbip` instead.

## Gotchas and Limitations

1. **Xpress9 compression**: `.pbix` files compress the data model with Xpress9 — cannot be unzipped with standard tools. Use `pbi-tools`, `PBIXRay`, or convert to `.pbit` first.
2. **No Autopilot support**: Snowflake Semantic View Autopilot does NOT support Power BI files. Must generate YAML directly.
3. **DAX filter context**: DAX's `CALCULATE()` + `ALL()`/`FILTER()` pattern has no direct SQL equivalent. These measures must be flagged for manual conversion.
4. **Row context iteration**: `SUMX(table, expr)` patterns work differently than SQL `SUM(expr)`. Simple cases map cleanly; complex nested iterations don't.
5. **Inactive relationships**: Power BI models can have multiple relationships between tables with only one active. `USERELATIONSHIP()` activates alternatives — flag these.
6. **Many-to-Many relationships**: Not supported in Snowflake semantic views. Must be restructured (bridge table or denormalization).
7. **Calculated tables**: Tables created entirely from DAX (e.g., `Calendar = CALENDAR(...)`) have no physical Snowflake source. Must create equivalent views/tables.
8. **RLS (Row-Level Security)**: Power BI roles with DAX filter expressions don't translate to semantic view filters directly. Document and flag.
9. **Report-level measures**: Measures defined at the report level (not the model) are not visible via model extraction. Use Scanner API or report layout parsing.
10. **Power Query / M**: Source transformations in M code are a separate layer from DAX. The M expressions show how data is loaded, not how it's calculated. Parse M for source table identification only.
