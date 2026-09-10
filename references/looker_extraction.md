# Looker / LookML Extraction Reference

## LookML Project Structure

A typical LookML project:

```
project/
├── manifest.lkml            # Project settings, imports from other projects
├── ecommerce.model.lkml     # Model: connection, explores, joins
├── orders.view.lkml          # View: table, dimensions, measures
├── users.view.lkml           # View: table, dimensions, measures
├── products.view.lkml        # View: table, dimensions, measures
└── dashboards/
    └── sales.dashboard.lkml  # Dashboard definitions
```

**Key files:**
- `.model.lkml` — declares `connection:`, `explore:` blocks with joins. This is where table relationships live.
- `.view.lkml` — declares `sql_table_name:`, dimensions, measures, dimension_groups. This is where field definitions live.
- `.dashboard.lkml` — declares dashboard layout, tiles, field references.
- `manifest.lkml` — project-level config, may import from other projects.

## LookML is NOT YAML

LookML uses a custom DSL with `;;` as the statement terminator. Do NOT try to parse it with a YAML parser. Use the `lkml` Python package (official parser by Looker):

```bash
pip install lkml
```

## Extraction Code Patterns

### Parse a View File

```python
import lkml

with open('orders.view.lkml', 'r') as f:
    parsed = lkml.load(f.read())

for view in parsed.get('views', []):
    view_name = view['name']
    sql_table_name = view.get('sql_table_name', 'UNKNOWN')
    print(f"View: {view_name} → Table: {sql_table_name}")

    # Extract dimensions
    for dim in view.get('dimensions', []):
        name = dim['name']
        dim_type = dim.get('type', 'string')
        sql = dim.get('sql', '')
        label = dim.get('label', '')
        description = dim.get('description', '')
        primary_key = dim.get('primary_key', False)
        hidden = dim.get('hidden', False)
        print(f"  Dimension: {name} (type={dim_type}, pk={primary_key}, hidden={hidden})")
        print(f"    SQL: {sql}")

    # Extract dimension groups
    for dg in view.get('dimension_groups', []):
        name = dg['name']
        dg_type = dg.get('type', 'time')
        timeframes = dg.get('timeframes', [])
        sql = dg.get('sql', '')
        print(f"  Dimension Group: {name} (type={dg_type})")
        print(f"    Timeframes: {timeframes}")
        print(f"    SQL: {sql}")

    # Extract measures
    for measure in view.get('measures', []):
        name = measure['name']
        m_type = measure.get('type', 'number')
        sql = measure.get('sql', '')
        label = measure.get('label', '')
        description = measure.get('description', '')
        filters = measure.get('filters', [])
        print(f"  Measure: {name} (type={m_type})")
        print(f"    SQL: {sql}")
        if filters:
            print(f"    Filters: {filters}")
```

### Parse a Model File (Explores & Joins)

```python
with open('ecommerce.model.lkml', 'r') as f:
    parsed = lkml.load(f.read())

connection = parsed.get('connection', 'UNKNOWN')
print(f"Connection: {connection}")

for explore in parsed.get('explores', []):
    explore_name = explore['name']
    from_view = explore.get('from', explore_name)  # 'from' overrides base view
    label = explore.get('label', '')
    description = explore.get('description', '')
    print(f"Explore: {explore_name} (from: {from_view})")

    for join in explore.get('joins', []):
        join_name = join['name']
        join_type = join.get('type', 'left_outer')
        relationship = join.get('relationship', 'many_to_one')
        sql_on = join.get('sql_on', '')
        print(f"  Join: {join_name}")
        print(f"    Type: {join_type}, Relationship: {relationship}")
        print(f"    SQL ON: {sql_on}")
```

### Parse Dashboard Files

```python
with open('dashboards/sales.dashboard.lkml', 'r') as f:
    parsed = lkml.load(f.read())

for dashboard in parsed.get('dashboards', []):
    db_name = dashboard['name']
    title = dashboard.get('title', db_name)
    print(f"Dashboard: {title}")

    for element in dashboard.get('elements', []):
        elem_name = element.get('name', 'unnamed')
        elem_title = element.get('title', elem_name)
        explore = element.get('explore', '')
        model = element.get('model', '')
        fields = element.get('fields', [])
        filters = element.get('filters', {})
        print(f"  Tile: {elem_title} (explore={explore})")
        print(f"    Fields: {fields}")
```

### Process a Full LookML Project

```python
import os
import lkml

def parse_lookml_project(project_dir):
    """Parse all LookML files in a project directory."""
    result = {
        'connection': None,
        'views': [],
        'explores': [],
        'dashboards': [],
        'models': []
    }

    for root_dir, dirs, files in os.walk(project_dir):
        for fname in files:
            fpath = os.path.join(root_dir, fname)
            with open(fpath, 'r') as f:
                try:
                    parsed = lkml.load(f.read())
                except Exception as e:
                    print(f"Warning: Failed to parse {fpath}: {e}")
                    continue

            if fname.endswith('.model.lkml'):
                if 'connection' in parsed:
                    result['connection'] = parsed['connection']
                result['models'].append({'file': fpath, 'parsed': parsed})
                for explore in parsed.get('explores', []):
                    explore['_source_file'] = fpath
                    result['explores'].append(explore)

            elif fname.endswith('.view.lkml'):
                for view in parsed.get('views', []):
                    view['_source_file'] = fpath
                    result['views'].append(view)

            elif fname.endswith('.dashboard.lkml'):
                for dashboard in parsed.get('dashboards', []):
                    dashboard['_source_file'] = fpath
                    result['dashboards'].append(dashboard)

    return result
```

## Resolving LookML References

### `${TABLE}` Resolution

In LookML, `${TABLE}` refers to the view's `sql_table_name`. When converting to Snowflake Semantic View YAML, replace `${TABLE}` with the actual table name:

```python
def resolve_table_ref(sql_expr, sql_table_name):
    """Replace ${TABLE} with actual table name."""
    return sql_expr.replace('${TABLE}', sql_table_name)
```

### `${view_name.field_name}` Resolution

Cross-view references like `${orders.user_id}` need to be resolved to the actual column:

```python
def resolve_field_ref(sql_expr, views_dict):
    """Resolve ${view.field} references to TABLE.COLUMN."""
    import re
    pattern = r'\$\{(\w+)\.(\w+)\}'
    def replacer(match):
        view_name = match.group(1)
        field_name = match.group(2)
        view = views_dict.get(view_name, {})
        # Find the field's SQL expression
        for dim in view.get('dimensions', []) + view.get('measures', []):
            if dim['name'] == field_name:
                return dim.get('sql', f'{view_name}.{field_name}').replace('${TABLE}', view.get('sql_table_name', view_name))
        return f'{view_name}.{field_name}'
    return re.sub(pattern, replacer, sql_expr)
```

### Handling `extends` and `refinements`

LookML views can extend other views:

```
view: base_orders { ... }
view: +orders { extends: [base_orders] ... }  # refinement
view: special_orders { extends: [base_orders] ... }  # extension
```

**Strategy**: Process views in dependency order:
1. Parse all views
2. Build an inheritance graph from `extends:` declarations
3. Resolve in topological order — base views first
4. Merge fields: child fields override parent fields with same name

### Handling Liquid Templates

LookML `sql:` fields can contain Liquid (`{% if ... %}`):

```
dimension: region {
  sql: {% if _user_attributes['region'] == 'US' %}
         ${TABLE}.us_region
       {% else %}
         ${TABLE}.global_region
       {% endif %} ;;
}
```

**These cannot be auto-converted.** Flag as `complexity: manual_required` with the original Liquid template shown to the user. Suggest they pick one branch or create multiple dimensions.

### Handling `parameter` Blocks

LookML parameters define runtime user inputs:

```
parameter: date_granularity {
  type: string
  allowed_values: {
    label: "Day"    value: "day"
  }
  allowed_values: {
    label: "Month"  value: "month"
  }
}

dimension: dynamic_date {
  sql: {% if date_granularity._parameter_value == "'day'" %}
         DATE(${TABLE}.created_at)
       {% elsif date_granularity._parameter_value == "'month'" %}
         DATE_TRUNC('MONTH', ${TABLE}.created_at)
       {% endif %} ;;
}
```

**Extraction:**
```python
for view in parsed.get('views', []):
    for param in view.get('parameters', []):
        name = param['name']
        param_type = param.get('type', 'string')
        allowed = param.get('allowed_values', [])
        default = param.get('default_value', '')
        print(f"  Parameter: {name} (type={param_type}, default={default})")
        for av in allowed:
            print(f"    Allowed: {av.get('label', '')} = {av.get('value', '')}")
```

**Mapping:** Parameters cannot map to semantic views. Flag them and document which dimensions/measures reference them via `{% parameter %}` or `_parameter_value`. User must choose a fixed value or create multiple semantic view variants.

### Extract Derived Tables

```python
for view in parsed.get('views', []):
    view_name = view['name']
    dt = view.get('derived_table', {})
    if dt:
        sql = dt.get('sql', '')
        explore_source = dt.get('explore_source', '')
        datagroup = dt.get('datagroup_trigger', '')
        persist_for = dt.get('persist_for', '')
        
        if sql:
            print(f"  Derived Table (SQL): {view_name}")
            print(f"    SQL: {sql[:200]}...")
            print(f"    Persistence: datagroup={datagroup}, persist_for={persist_for}")
        elif explore_source:
            print(f"  Derived Table (NDT): {view_name} from explore: {explore_source}")
        
        # Flag: derived tables must be materialized as Snowflake VIEWs
        # before the semantic view can reference them
```

**Mapping:** 
- SQL derived tables: extract `sql`, resolve `${view.field}` references, create as Snowflake VIEW
- NDT (explore_source): flag as manual — requires understanding the Looker query to generate equivalent SQL
- PDTs with `datagroup_trigger`/`persist_for`: note the refresh cadence for documentation

### Extract Explore-Level Filters

```python
for explore in parsed.get('explores', []):
    explore_name = explore['name']
    
    # sql_always_where — mandatory, user cannot remove
    sql_always_where = explore.get('sql_always_where', '')
    if sql_always_where:
        print(f"[{explore_name}] sql_always_where: {sql_always_where}")
    
    # always_filter — default filter, user can change
    always_filter = explore.get('always_filter', {})
    if always_filter:
        filters = always_filter.get('filters', [])
        print(f"[{explore_name}] always_filter: {filters}")
    
    # access_filter — row-level security
    access_filters = explore.get('access_filters', [])
    for af in access_filters:
        print(f"[{explore_name}] access_filter: field={af.get('field', '')} user_attribute={af.get('user_attribute', '')}")
    
    # conditionally_filter
    cond_filter = explore.get('conditionally_filter', {})
    if cond_filter:
        print(f"[{explore_name}] conditionally_filter: {cond_filter}")
```

### Extract Sets

```python
for view in parsed.get('views', []):
    for s in view.get('sets', []):
        set_name = s['name']
        fields = s.get('fields', [])
        print(f"  Set: {set_name} = {fields}")
```

Sets are organizational — they define named field lists used by `drill_fields`, `required_fields`, etc. Document them but don't directly map.

### Extract Duration Dimensions

```python
for view in parsed.get('views', []):
    for dim in view.get('dimensions', []):
        if dim.get('type') == 'duration':
            name = dim['name']
            sql_start = dim.get('sql_start', '')
            sql_end = dim.get('sql_end', '')
            intervals = dim.get('intervals', ['day', 'hour', 'minute'])
            print(f"  Duration: {name}")
            print(f"    Start: {sql_start}, End: {sql_end}")
            print(f"    Intervals: {intervals}")
    
    # Duration dimension groups
    for dg in view.get('dimension_groups', []):
        if dg.get('type') == 'duration':
            name = dg['name']
            sql_start = dg.get('sql_start', '')
            sql_end = dg.get('sql_end', '')
            intervals = dg.get('intervals', ['day', 'hour', 'minute'])
            print(f"  Duration Group: {name}")
            print(f"    Start: {sql_start}, End: {sql_end}")
            print(f"    Intervals: {intervals}")
```

**Mapping:** For each interval, generate a dimension:
- `{name}_hours` → `DATEDIFF('HOUR', start_expr, end_expr)`
- `{name}_days` → `DATEDIFF('DAY', start_expr, end_expr)`
- `{name}_minutes` → `DATEDIFF('MINUTE', start_expr, end_expr)`

### Classify LookML Complexity

```python
def classify_lookml_complexity(field_dict, view_dict=None):
    """Classify a LookML dimension or measure for conversion to Snowflake SQL.
    
    field_dict: parsed LookML dimension or measure dict
    view_dict: optional parent view dict for context (derived table, etc.)
    """
    field_type = field_dict.get('type', 'string')
    sql = field_dict.get('sql', '')
    filters = field_dict.get('filters', [])
    
    # Check for Liquid templates — always manual
    if sql and ('{%' in sql or '{{' in sql):
        return 'manual_required'
    
    # Check for cross-view references beyond simple ${TABLE}
    import re
    cross_view_refs = re.findall(r'\$\{(?!TABLE\b)(\w+)\.(\w+)\}', sql)
    
    # Manual required patterns
    manual_types = [
        'percent_of_total', 'percent_of_previous', 'running_total',
        'sum_distinct', 'average_distinct',
        'median_distinct', 'percentile_distinct',  # Fanout-safe aggregates
        'period_over_period',  # New Looker PoP measure type
        'distance',  # Requires resolving two location dims to lat/lon
    ]
    if field_type in manual_types:
        return 'manual_required'
    
    # Check if parent view uses derived table
    if view_dict and view_dict.get('derived_table'):
        dt = view_dict['derived_table']
        if dt.get('explore_source'):
            return 'manual_required'  # NDT — can't auto-resolve
    
    # Translation needed patterns
    translation_types = [
        'tier', 'bin', 'case', 'duration', 'location', 'percentile',
        'list', 'date',  # date measure type
        'custom_calendar',  # Custom fiscal calendar dimension groups
    ]
    if field_type in translation_types:
        return 'needs_translation'
    
    if field_type == 'yesno':
        return 'needs_translation'
    
    if field_type == 'number' and cross_view_refs:
        # Number measure with cross-view references (e.g. ${total_revenue} / ${total_orders})
        return 'needs_translation'
    
    if filters:
        return 'needs_translation'  # Filtered measures
    
    # Simple patterns
    simple_types = [
        'string', 'number', 'sum', 'count', 'count_distinct',
        'average', 'min', 'max', 'median', 'zipcode',
        'date_time',  # Standalone datetime dimension
    ]
    if field_type in simple_types:
        return 'simple'
    
    # Dimension groups — time type is simple, duration needs translation
    if field_type == 'time':
        return 'simple'
    
    return 'needs_translation'  # Default for unrecognized types
```

## Looker API Extraction

For content on Looker instances (alternative to file parsing).

### Authentication

```python
import requests

base_url = "https://<instance>.cloud.looker.com"
auth = requests.post(f"{base_url}/api/4.0/login", data={
    "client_id": "<client_id>",
    "client_secret": "<client_secret>"
})
token = auth.json()['access_token']
headers = {"Authorization": f"Bearer {token}"}
```

### Get All Models and Explores

```python
# List all models
models = requests.get(f"{base_url}/api/4.0/lookml_models", headers=headers).json()

for model in models:
    model_name = model['name']
    print(f"Model: {model_name}")
    for explore in model.get('explores', []):
        explore_name = explore['name']
        print(f"  Explore: {explore_name}")
```

### Get Fully Resolved Explore (with all fields)

```python
# This returns the explore with ALL fields resolved (after extends, refinements, includes)
explore_detail = requests.get(
    f"{base_url}/api/4.0/lookml_models/{model_name}/explores/{explore_name}",
    headers=headers
).json()

# Dimensions
for field in explore_detail.get('fields', {}).get('dimensions', []):
    print(f"  Dimension: {field['name']}")
    print(f"    SQL: {field.get('sql', '')}")
    print(f"    Type: {field.get('type', '')}")
    print(f"    Label: {field.get('label', '')}")
    print(f"    Description: {field.get('description', '')}")

# Measures
for field in explore_detail.get('fields', {}).get('measures', []):
    print(f"  Measure: {field['name']}")
    print(f"    SQL: {field.get('sql', '')}")
    print(f"    Type: {field.get('type', '')}")

# Joins (from the explore definition)
for join in explore_detail.get('joins', []):
    print(f"  Join: {join['name']}")
    print(f"    Type: {join.get('type', '')}")
    print(f"    Relationship: {join.get('relationship', '')}")
    print(f"    SQL ON: {join.get('sql_on', '')}")
```

### Get All Dashboards and Their Elements

```python
dashboards = requests.get(f"{base_url}/api/4.0/dashboards", headers=headers).json()

for db in dashboards:
    db_id = db['id']
    db_title = db.get('title', 'Untitled')
    print(f"Dashboard: {db_title}")

    # Get dashboard elements (tiles)
    detail = requests.get(f"{base_url}/api/4.0/dashboards/{db_id}", headers=headers).json()
    for elem in detail.get('dashboard_elements', []):
        elem_title = elem.get('title', 'Untitled tile')
        query = elem.get('query', {})
        if query:
            model = query.get('model', '')
            explore = query.get('view', '')  # 'view' in API = explore name
            fields = query.get('fields', [])
            filters = query.get('filters', {})
            print(f"  Tile: {elem_title} (model={model}, explore={explore})")
            print(f"    Fields: {fields}")
            if filters:
                print(f"    Filters: {filters}")
```

## LookML Relationship Type Mapping

| LookML `relationship:` | Snowflake `relationship_type` |
|------------------------|------------------------------|
| `many_to_one` | `many_to_one` |
| `one_to_many` | `one_to_many` |
| `one_to_one` | `one_to_one` |
| `many_to_many` | `many_to_many` |

| LookML `type:` (join) | Notes |
|----------------------|-------|
| `left_outer` | Default. Maps to standard LEFT JOIN semantics |
| `inner` | INNER JOIN |
| `full_outer` | FULL OUTER JOIN |
| `cross` | CROSS JOIN — rare in semantic views |
