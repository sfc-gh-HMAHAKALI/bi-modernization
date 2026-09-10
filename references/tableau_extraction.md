# Tableau Extraction Reference

## File Formats

| Format | Description | Extraction Method |
|--------|-------------|-------------------|
| `.twb` | Tableau Workbook (XML) | Direct XML parsing |
| `.tds` | Tableau Data Source (XML) | Direct XML parsing |
| `.twbx` | Packaged Workbook (ZIP) | Unzip → parse `.twb` inside |
| `.tdsx` | Packaged Data Source (ZIP) | Unzip → parse `.tds` inside |

### Unzipping Packaged Files

```python
import zipfile, glob

with zipfile.ZipFile('workbook.twbx', 'r') as z:
    z.extractall('extracted/')

twb_files = glob.glob('extracted/**/*.twb', recursive=True)
```

## XML Structure Overview

A Tableau `.twb` file is XML with this high-level structure:

```
<workbook>
  <datasources>
    <datasource name="..." caption="...">
      <connection class="..." server="..." dbname="..." schema="...">
        <named-connections>
          <named-connection name="...">
            <connection server="..." dbname="..." schema="..." />
          </named-connection>
        </named-connections>
        <relation type="table" table="[schema].[table]" />
        <!-- or for joins: -->
        <relation type="join" join="inner|left|right">
          <clause type="join">
            <expression op="=">
              <expression op="[left_table].[col]" />
              <expression op="[right_table].[col]" />
            </expression>
          </clause>
          <relation type="table" table="[schema].[left_table]" />
          <relation type="table" table="[schema].[right_table]" />
        </relation>
      </connection>
      <column caption="Display Name" datatype="string" name="[internal_name]" role="dimension|measure">
        <calculation class="tableau" formula="..." />
      </column>
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name="Sheet Name">
      <table>
        <view>
          <datasources>
            <datasource name="ref_to_datasource" />
          </datasources>
          <datasource-dependencies datasource="ref">
            <column name="[field_name]" />
          </datasource-dependencies>
        </view>
      </table>
    </worksheet>
  </worksheets>
  <dashboards>
    <dashboard name="Dashboard Name">
      <zones>
        <zone name="Sheet Name" type="..." />
      </zones>
    </dashboard>
  </dashboards>
</workbook>
```

## Extraction Code Patterns

### Extract Data Source Connection Info

```python
import xml.etree.ElementTree as ET

tree = ET.parse("workbook.twb")
root = tree.getroot()

for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', 'unknown'))
    conn = ds.find('.//connection')
    if conn is not None:
        print(f"Data Source: {ds_name}")
        print(f"  Server: {conn.attrib.get('server', 'N/A')}")
        print(f"  Database: {conn.attrib.get('dbname', 'N/A')}")
        print(f"  Schema: {conn.attrib.get('schema', 'N/A')}")

    # Get all base tables
    for rel in ds.findall('.//relation[@type="table"]'):
        table = rel.attrib.get('table', 'N/A')
        print(f"  Table: {table}")
```

### Extract All Calculated Fields

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for col in ds.findall('.//column[@caption]'):
        calc = col.find('.//calculation[@formula]')
        if calc is not None:
            name = col.attrib.get('caption', col.attrib.get('name', ''))
            formula = calc.attrib.get('formula', '')
            role = col.attrib.get('role', 'unknown')
            datatype = col.attrib.get('datatype', 'unknown')

            # Classify complexity
            complexity = 'simple'
            if '{FIXED' in formula or '{INCLUDE' in formula or '{EXCLUDE' in formula:
                complexity = 'manual_required'  # LOD expression
            elif 'LOOKUP(' in formula or 'RUNNING_' in formula or 'WINDOW_' in formula:
                complexity = 'manual_required'  # Table calculation
            elif any(f in formula for f in ['ZN(', 'ATTR(', 'IIF(', 'DATETRUNC(']):
                complexity = 'needs_translation'

            print(f"[{ds_name}] {name}: {formula} (role={role}, complexity={complexity})")
```

### Extract All Dimensions and Measures (Non-Calculated)

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for col in ds.findall('.//column'):
        calc = col.find('.//calculation[@formula]')
        if calc is None:  # Not a calculated field
            name = col.attrib.get('caption', col.attrib.get('name', ''))
            role = col.attrib.get('role', 'unknown')
            datatype = col.attrib.get('datatype', 'unknown')
            if name and role in ('dimension', 'measure'):
                print(f"[{ds_name}] {name}: role={role}, datatype={datatype}")
```

### Extract Joins

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for relation in ds.findall('.//relation[@type="join"]'):
        join_type = relation.attrib.get('join', 'inner')
        # Get the tables being joined
        tables = relation.findall('./relation[@type="table"]')
        left_table = tables[0].attrib.get('table', '') if len(tables) > 0 else ''
        right_table = tables[1].attrib.get('table', '') if len(tables) > 1 else ''
        # Get join clauses
        clauses = relation.findall('.//clause')
        for clause in clauses:
            expr = clause.find('.//expression')
            if expr is not None:
                expr_str = ET.tostring(expr, encoding='unicode')
                print(f"[{ds_name}] JOIN {join_type}: {left_table} ↔ {right_table}")
                print(f"  Clause: {expr_str}")
```

### Extract Dashboard → Sheet → Field Usage

```python
# Map dashboards to sheets
dashboards = {}
for db in root.findall('.//dashboard'):
    db_name = db.attrib.get('name', '')
    sheets = []
    for zone in db.findall('.//zone'):
        zone_name = zone.attrib.get('name', '')
        if zone_name:
            sheets.append(zone_name)
    dashboards[db_name] = sheets

# Map sheets to fields
sheet_fields = {}
for ws in root.findall('.//worksheet'):
    ws_name = ws.attrib.get('name', '')
    fields = []
    for dep in ws.findall('.//datasource-dependencies'):
        for col in dep.findall('.//column'):
            col_name = col.attrib.get('name', '')
            if col_name:
                fields.append(col_name)
    sheet_fields[ws_name] = fields

# Combine: dashboard → fields
for db_name, sheets in dashboards.items():
    all_fields = set()
    for sheet in sheets:
        all_fields.update(sheet_fields.get(sheet, []))
    print(f"Dashboard: {db_name}")
    print(f"  Sheets: {sheets}")
    print(f"  Fields: {all_fields}")
```

### Extract Parameters

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for param in ds.findall('.//column[@param-domain-type]'):
        name = param.attrib.get('caption', param.attrib.get('name', ''))
        datatype = param.attrib.get('datatype', 'string')
        param_type = param.attrib.get('param-domain-type', '')
        default_value = param.attrib.get('value', '')
        
        # Get allowed values (list or range)
        members = []
        for member in param.findall('.//member'):
            members.append({
                'value': member.attrib.get('value', ''),
                'alias': member.attrib.get('alias', '')
            })
        
        range_info = {}
        range_elem = param.find('.//range')
        if range_elem is not None:
            range_info = {
                'min': range_elem.attrib.get('min', ''),
                'max': range_elem.attrib.get('max', ''),
                'granularity': range_elem.attrib.get('granularity', ''),
            }
        
        print(f"[{ds_name}] Parameter: {name} (type={datatype}, domain={param_type})")
        print(f"  Default: {default_value}")
        if members:
            print(f"  Allowed values: {members}")
        if range_info:
            print(f"  Range: {range_info}")
```

**Note:** Parameters cannot be directly mapped to semantic views. Flag them and document their usage in calculated fields — the user must decide how to handle (hardcode a value, create separate semantic views, or omit).

### Extract Groups (Sets)

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for group in ds.findall('.//group'):
        group_name = group.attrib.get('caption', group.attrib.get('name', ''))
        group_type = group.attrib.get('name-style', '')  # 'set' or empty
        
        members = []
        for member in group.findall('.//groupfilter'):
            member_val = member.attrib.get('member', '')
            if member_val:
                members.append(member_val)
        
        # Also check for nested groupfilter with function="union"
        for gf in group.findall('.//groupfilter[@function="union"]'):
            for sub in gf.findall('.//groupfilter[@member]'):
                members.append(sub.attrib.get('member', ''))
        
        print(f"[{ds_name}] Group/Set: {group_name} ({len(members)} members)")
        print(f"  Members: {members[:10]}{'...' if len(members) > 10 else ''}")
```

**Mapping:** Groups/Sets → `DIMENSIONS` as `CASE WHEN field IN ('val1', 'val2', ...) THEN 'In Set' ELSE 'Out of Set' END`

### Extract Bins

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    for col in ds.findall('.//column'):
        bin_elem = col.find('.//bin')
        if bin_elem is not None:
            name = col.attrib.get('caption', col.attrib.get('name', ''))
            source_col = bin_elem.attrib.get('source-column', '')  # e.g. [Amount]
            bin_size = bin_elem.attrib.get('size', '')
            print(f"[{ds_name}] Bin: {name} (source={source_col}, size={bin_size})")
```

**Mapping:** Bins → `DIMENSIONS` as `FLOOR(column / bin_size) * bin_size`

### Extract Aliases and Folders

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    
    # Column aliases (caption != internal name)
    for col in ds.findall('.//column[@caption]'):
        internal = col.attrib.get('name', '')
        caption = col.attrib.get('caption', '')
        desc = col.attrib.get('desc', '')
        default_format = col.attrib.get('default-format', '')
        semantic_role = col.attrib.get('semantic-role', '')  # e.g. '[Country].[ISO3166_2]'
        
        if caption and caption != internal:
            print(f"[{ds_name}] Alias: {internal} → '{caption}'")
        if desc:
            print(f"  Description: {desc}")
        if semantic_role:
            print(f"  Semantic role: {semantic_role}")
    
    # Folder organization
    for folder in ds.findall('.//folder'):
        folder_name = folder.attrib.get('name', '')
        folder_role = folder.attrib.get('role', '')
        folder_items = [fi.attrib.get('name', '') for fi in folder.findall('.//folder-item')]
        print(f"[{ds_name}] Folder: {folder_name} (role={folder_role})")
        print(f"  Fields: {folder_items}")
```

**Mapping:** 
- `caption` → `SYNONYMS` list on the semantic view dimension/metric
- `desc` → `COMMENT` on the semantic view column
- `semantic-role` → helps infer column semantics (geo, time, etc.)
- `folder` → organizational grouping; use to split large models into focused semantic views

### Extract Data Source Filters

```python
for ds in root.findall('.//datasource'):
    ds_name = ds.attrib.get('caption', ds.attrib.get('name', ''))
    
    # Data source level filters
    for filt in ds.findall('.//filter[@column]'):
        col = filt.attrib.get('column', '')
        
        # Check for list filter
        members = []
        for gf in filt.findall('.//groupfilter[@member]'):
            members.append(gf.attrib.get('member', ''))
        
        # Check for range filter  
        range_min = filt.findall('.//groupfilter[@from]')
        range_max = filt.findall('.//groupfilter[@to]')
        
        print(f"[{ds_name}] Filter on: {col}")
        if members:
            print(f"  Include: {members[:5]}{'...' if len(members) > 5 else ''}")
```

**Note:** Data source filters should be documented in the semantic view mapping as context — they indicate the workbook was pre-filtered and the semantic view may need corresponding WHERE clauses or documentation.

### Classify Tableau Formula Complexity

```python
def classify_tableau_complexity(formula):
    """Classify Tableau formula complexity for conversion to Snowflake SQL."""
    if not formula:
        return 'simple'
    
    f = formula.upper()
    
    # Manual required — viz-dependent or no SQL equivalent
    manual_patterns = [
        # LOD expressions
        '{FIXED', '{INCLUDE', '{EXCLUDE',
        # Table calculations
        'LOOKUP(', 'RUNNING_SUM(', 'RUNNING_AVG(', 'RUNNING_COUNT(',
        'RUNNING_MIN(', 'RUNNING_MAX(',
        'WINDOW_SUM(', 'WINDOW_AVG(', 'WINDOW_COUNT(',
        'WINDOW_MAX(', 'WINDOW_MIN(', 'WINDOW_MEDIAN(',
        'WINDOW_PERCENTILE(', 'WINDOW_STDEV(', 'WINDOW_VAR(',
        'WINDOW_STDEVP(', 'WINDOW_VARP(',
        'WINDOW_CORR(', 'WINDOW_COVAR(', 'WINDOW_COVARP(',
        'INDEX(', 'FIRST(', 'LAST(', 'SIZE(',
        'RANK(', 'RANK_DENSE(', 'RANK_MODIFIED(', 'RANK_PERCENTILE(', 'RANK_UNIQUE(',
        'TOTAL(', 'PREVIOUS_VALUE(',
        'HEXBINX(', 'HEXBINY(',
        'MODEL_PERCENTILE(', 'MODEL_QUANTILE(',
        'MODEL_EXTENSION_BOOL(', 'MODEL_EXTENSION_INT(', 'MODEL_EXTENSION_REAL(', 'MODEL_EXTENSION_STRING(',
        'SCRIPT_BOOL(', 'SCRIPT_INT(', 'SCRIPT_REAL(', 'SCRIPT_STRING(',
        'MAKEPOINT(', 'MAKELINE(',  # Spatial — no semantic view support
    ]
    if any(p in f for p in manual_patterns):
        return 'manual_required'
    
    # Needs translation — has Snowflake equivalent but syntax differs
    translation_patterns = [
        'ZN(', 'ATTR(', 'IIF(',
        'COUNTD(', 'STR(', 'FLOAT(', 'INT(',
        'LEN(', 'MID(', 'FIND(', 'FINDNTH(', 'SPLIT(',
        'SPACE(', 'ISDATE(', 'PROPER(',
        'DATE(', 'DATETIME(',  # Type conversion functions
        'DATEPARSE(', 'MAKEDATE(', 'MAKEDATETIME(', 'MAKETIME(',
        'DATENAME(', 'WEEK(',
        'ISOYEAR(', 'ISOWEEK(', 'ISOWEEKDAY(', 'ISOQUARTER(',
        'LOG(',  # Tableau LOG = natural log
        'DIV(',
        'REGEXP_MATCH(', 'REGEXP_EXTRACT(', 'REGEXP_EXTRACT_NTH(',
        'STDEV(', 'STDEVP(', 'VAR(', 'VARP(',
        'COVAR(', 'COVARP(',
        'PERCENTILE(',
        'COLLECT(',
        'RAWSQL_BOOL(', 'RAWSQL_DATE(', 'RAWSQL_INT(', 'RAWSQL_REAL(', 'RAWSQL_STR(',
        'RAWSQLAGG_BOOL(', 'RAWSQLAGG_DATE(', 'RAWSQLAGG_INT(', 'RAWSQLAGG_REAL(', 'RAWSQLAGG_STR(',
    ]
    if any(p in f for p in translation_patterns):
        return 'needs_translation'
    
    # Simple — 1:1 mapping or basic constructs
    simple_patterns = [
        'SUM(', 'AVG(', 'COUNT(', 'MIN(', 'MAX(', 'MEDIAN(',
        'UPPER(', 'LOWER(', 'TRIM(', 'LTRIM(', 'RTRIM(',
        'LEFT(', 'RIGHT(', 'REPLACE(', 'CONTAINS(', 'STARTSWITH(', 'ENDSWITH(',
        'ABS(', 'ROUND(', 'CEILING(', 'FLOOR(', 'SIGN(', 'SQRT(', 'POWER(', 'SQUARE(',
        'LN(', 'EXP(', 'MOD(', 'PI(',
        'YEAR(', 'MONTH(', 'DAY(', 'QUARTER(',
        'TODAY(', 'NOW(',
        'DATETRUNC(', 'DATEDIFF(', 'DATEADD(', 'DATEPART(',
        'ISNULL(', 'IFNULL(', 'COALESCE(',
        'CORR(',
        'REGEXP_REPLACE(',
        'ASCII(', 'CHAR(',
        'RADIANS(', 'DEGREES(',
        'SIN(', 'COS(', 'TAN(', 'ASIN(', 'ACOS(', 'ATAN(', 'ATAN2(', 'COT(',
    ]
    if any(p in f for p in simple_patterns):
        return 'simple'
    
    # Check for CASE WHEN (native Tableau syntax)
    if 'CASE ' in f or 'IF ' in f:
        return 'simple'
    
    return 'needs_translation'  # Default for unrecognized formulas
```

## Tableau Metadata API (GraphQL)

For content published to Tableau Server or Tableau Cloud.

### Authentication

```python
import requests

# Step 1: Sign in to REST API
auth_url = "https://<server>/api/3.x/auth/signin"
body = {
    "credentials": {
        "personalAccessTokenName": "<token_name>",
        "personalAccessTokenSecret": "<token_secret>",
        "site": {"contentUrl": "<site_name>"}
    }
}
resp = requests.post(auth_url, json=body)
token = resp.json()['credentials']['token']
site_id = resp.json()['credentials']['site']['id']
```

### Query All Calculated Fields with Lineage

```graphql
{
  calculatedFields {
    name
    description
    dataType
    formula
    referencedByCalculations { name formula }
    upstreamDatabases { name }
    upstreamTables { name schema }
    downstreamSheets { name }
    downstreamDashboards { name }
    downstreamWorkbooks { name projectName }
  }
}
```

### Query All Data Sources

```graphql
{
  embeddedDatasources {
    name
    hasExtracts
    upstreamDatabases { name connectionType }
    upstreamTables { name schema database { name } }
    fields {
      name
      description
      isCalculated
      formula
      role
      dataType
    }
    downstreamWorkbooks { name projectName }
  }
}
```

### Query All Dashboards

```graphql
{
  dashboards {
    name
    projectName
    sheets { name }
    workbook { name projectName }
    upstreamDatasources { name }
  }
}
```

### Execute GraphQL Query

```python
graphql_url = f"https://<server>/metadata/graphql/"
headers = {"X-Tableau-Auth": token, "Content-Type": "application/json"}

query = """{ calculatedFields { name formula dataType } }"""
resp = requests.post(graphql_url, json={"query": query}, headers=headers)
data = resp.json()['data']
```

## Autopilot Integration Guidance

After completing the audit and qualification steps, for each qualified Tableau data source:

1. Identify the specific `.twb` or `.tds` file containing that data source
2. In Snowsight: navigate to **AI & ML → Cortex Analyst → Create Semantic View**
3. Enter a name and description (use the inferred description from the audit)
4. Select **Tableau Files** and upload the `.twb`/`.tds`
5. Autopilot will parse and generate a draft semantic view
6. Review against the audit mapping file to verify completeness
7. Refine as needed using the Semantic View Wizard

**Autopilot limitations:**
- One file at a time (no batch)
- File must be under 50 MB
- No published data sources
- No LOD expressions
- No large extracts in `.twbx` files
