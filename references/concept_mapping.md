# Concept Mapping: BI Tools → Snowflake Semantic Views

## Tableau → Snowflake Semantic View

| Tableau Concept | XML Element / API Field | Snowflake Semantic View | Notes |
|----------------|------------------------|------------------------|-------|
| Data Source / Connection | `<datasource>`, `<named-connection>` (server, dbname, schema) | `TABLES` clause (`database`, `schema`, `table`) | Extract `server`, `dbname`, `schema`, `table` from connection attributes |
| Joins / Relationships | `<relation type="join">`, `<clause>`, `<expression>` | `RELATIONSHIPS` (`left_table`, `right_table`, `relationship_columns`, `relationship_type`) | Map `join="inner\|left\|right"` to relationship_type. Tableau doesn't declare cardinality — infer from primary keys |
| Dimensions (discrete fields) | `<column>` with `role="dimension"` | `DIMENSIONS` | Discrete, groupable fields |
| Measures (continuous fields) | `<column>` with `role="measure"` | `FACTS` (raw column) or `METRICS` (if aggregated) | If the measure is a simple column reference → FACTS. If it contains SUM/AVG/COUNT → METRICS |
| Calculated Fields | `<column>` containing `<calculation formula="...">` | `METRICS` (if aggregate) or `DIMENSIONS` (if row-level) | Must translate Tableau formula syntax to Snowflake SQL |
| Field Captions / Aliases | `caption` attribute on `<column>` | `SYNONYMS` list + `COMMENT` | Caption becomes a synonym; description becomes the comment |
| Parameters | `<parameter>` elements | Not directly supported | Flag — parameters may need to become filters or session variables |
| Sets | `<group>` elements with `type="set"` | `DIMENSIONS` (as CASE expressions) | Convert to `CASE WHEN field IN (...) THEN 'In Set' ELSE 'Out of Set' END` |
| Bins | `<bin>` elements | `DIMENSIONS` (as CASE expressions) | Convert bin boundaries to CASE WHEN ranges |
| Table Calculations | formulas with `LOOKUP`, `RUNNING_SUM`, etc. | Not supported in semantic views | Flag as manual — these depend on viz-level sort/partition |
| LOD Expressions | `{FIXED [...] : AGG(...)}` | Not auto-convertible | Flag as manual. May become subqueries, window functions, or separate CTEs |
| Data Blending | Multiple `<datasource>` elements linked | Multiple tables in TABLES clause | Each blended source becomes a separate table entry |

## Looker → Snowflake Semantic View

### Core Concepts

| LookML Concept | LookML Syntax | Snowflake Semantic View | Notes |
|---------------|---------------|------------------------|-------|
| View / sql_table_name | `view: name { sql_table_name: schema.table ;; }` | `TABLES` clause | Resolve `${TABLE}` → actual table name from `sql_table_name` |
| Derived Table (SQL) | `view: name { derived_table: { sql: ... ;; } }` | Create as a Snowflake VIEW first, then reference in TABLES | Derived tables need to be materialized before semantic view can reference them |
| Derived Table (NDT) | `derived_table: { explore_source: ... }` | Create as Snowflake VIEW | Native derived tables use Looker query syntax — extract the generated SQL |
| Persistent Derived Table | `derived_table: { sql: ... ;; datagroup_trigger: ... }` | Snowflake VIEW or TABLE | PDTs are pre-computed; create as table/view, note refresh schedule |
| Explore / Joins | `explore: name { join: other { sql_on: ... ;; relationship: many_to_one } }` | `RELATIONSHIPS` | `relationship:` maps directly. `sql_on` needs `${view.field}` resolved to `TABLE.COLUMN` |
| Explore (extends) | `explore: +name { extends: [base] ... }` | Merge joins/fields from base + extension | Process in topological order |
| Explore (sql_always_where) | `explore: name { sql_always_where: ${TABLE}.deleted = false ;; }` | Document as required filter context | Always-on filter — semantic view doesn't support this natively; add as note or WHERE in underlying view |
| Explore (always_filter) | `explore: name { always_filter: { filters: [field: "value"] } }` | Document — user-facing default filter | Unlike sql_always_where, users can change this in Looker. Flag for documentation |
| Explore (access_filter) | `explore: name { access_filter: { field: user_attr user_attribute: region } }` | Not supported | Row-level security — flag and document |
| Connection | `connection: "name"` in model file | Informs which Snowflake database/schema to target | Connection config maps to Snowflake account — user provides the actual target |

### Dimension Types

| LookML Concept | LookML Syntax | Snowflake Semantic View | Notes |
|---------------|---------------|------------------------|-------|
| Dimension (string) | `dimension: name { type: string sql: ${TABLE}.col ;; }` | `DIMENSIONS` | Resolve `${TABLE}` to actual table reference |
| Dimension (number) | `dimension: name { type: number sql: ${TABLE}.col ;; }` | `DIMENSIONS` or `FACTS` | Numeric dimensions may be facts if aggregable |
| Dimension (yesno) | `dimension: name { type: yesno sql: ${TABLE}.active = 1 ;; }` | `DIMENSIONS` with `CASE WHEN expr THEN 'Yes' ELSE 'No' END` | Convert to explicit CASE |
| Dimension (tier) | `dimension: name { type: tier tiers: [0,10,20] sql: ${TABLE}.amount ;; }` | `DIMENSIONS` with CASE WHEN generating tier buckets | Generate `CASE WHEN amount < 10 THEN '0-9' WHEN amount < 20 THEN '10-19' ELSE '20+' END` |
| Dimension (case) | `dimension: name { case: { when: { sql: ... ;; label: "..." } } }` | `DIMENSIONS` with CASE WHEN | Direct mapping of each `when` clause |
| Dimension (location) | `dimension: name { type: location sql_latitude: ... sql_longitude: ... }` | Two `DIMENSIONS` (lat + lon) | Split into separate lat/lon dimensions |
| Dimension (zipcode) | `dimension: name { type: zipcode sql: ${TABLE}.zip ;; }` | `DIMENSIONS` (string) | Treat as string dimension |
| Dimension (duration) | `dimension: name { type: duration sql_start: ... sql_end: ... intervals: [hour, day] }` | Multiple `DIMENSIONS` via `DATEDIFF` | Generate `DATEDIFF('HOUR', start, end)`, `DATEDIFF('DAY', start, end)` |
| Dimension Group (time) | `dimension_group: created { type: time timeframes: [date, week, month, year] sql: ${TABLE}.created_at ;; }` | Multiple `DIMENSIONS` — one per timeframe | Generate: `created_date` → `DATE(created_at)`, `created_week` → `DATE_TRUNC('WEEK', created_at)`, etc. |
| Dimension Group (duration) | `dimension_group: wait { type: duration sql_start: ... sql_end: ... intervals: [...] }` | Multiple `DIMENSIONS` via `DATEDIFF` | Same as duration dimension |
| Primary Key | `dimension: id { primary_key: yes }` | `primary_key` in table definition | Direct mapping |
| Dimension (bin) | `dimension: name { type: bin bins: [0,18,36,65] sql: ${TABLE}.age ;; }` | `DIMENSIONS` with CASE WHEN generating bin ranges | Alias for `type: tier` — identical behavior |
| Dimension (distance) | `dimension: d { type: distance start_location_field: a.loc end_location_field: b.loc units: miles }` | `DIMENSIONS` with `HAVERSINE(lat1, lon1, lat2, lon2)` | **Manual** — requires resolving both location dims to lat/lon |
| Dimension (date) | `dimension: name { type: date sql: ${TABLE}.event_date ;; }` | `DIMENSIONS` | Standalone date dimension (not dimension_group) |
| Dimension (date_time) | `dimension: name { type: date_time sql: ${TABLE}.created_at ;; }` | `DIMENSIONS` | Standalone datetime dimension |
| Dimension Group (custom_calendar) | `dimension_group: fiscal { type: custom_calendar ... }` | Multiple `DIMENSIONS` via custom offsets | **Translation** — generates custom fiscal/calendar dimensions |

### Measure Types

| LookML Concept | LookML Syntax | Snowflake Semantic View | Notes |
|---------------|---------------|------------------------|-------|
| Measure (sum) | `measure: total { type: sum sql: ${TABLE}.amount ;; }` | `METRICS` with `SUM(TABLE.AMOUNT)` | Direct mapping |
| Measure (count) | `measure: count { type: count }` | `METRICS` with `COUNT(*)` or `COUNT(pk)` | If no `sql:` specified, counts the primary key or `*` |
| Measure (count_distinct) | `measure: unique_users { type: count_distinct sql: ${TABLE}.user_id ;; }` | `METRICS` with `COUNT(DISTINCT TABLE.USER_ID)` | Direct mapping |
| Measure (average) | `measure: avg_amount { type: average sql: ${TABLE}.amount ;; }` | `METRICS` with `AVG(TABLE.AMOUNT)` | Direct mapping |
| Measure (average_distinct) | `measure: avg { type: average_distinct sql_distinct_key: ${TABLE}.id sql: ${TABLE}.val ;; }` | Complex — flag as manual | Deduplicates before averaging; no simple SQL equivalent |
| Measure (min / max) | `measure: m { type: min sql: ${TABLE}.col ;; }` | `METRICS` with `MIN/MAX(TABLE.COL)` | Direct mapping |
| Measure (median) | `measure: m { type: median sql: ${TABLE}.col ;; }` | `METRICS` with `MEDIAN(TABLE.COL)` | Snowflake supports MEDIAN natively |
| Measure (sum_distinct) | `measure: m { type: sum_distinct sql_distinct_key: ... sql: ... ;; }` | Complex — flag as manual | Deduplicates before summing |
| Measure (number) | `measure: ratio { type: number sql: ${total_revenue} / NULLIF(${total_orders}, 0) ;; }` | `METRICS` with resolved SQL expression | Resolve `${measure}` references to their SQL |
| Measure (string) | `measure: m { type: string sql: ... ;; }` | Non-aggregate expression | Rare — pass through |
| Measure (yesno) | `measure: m { type: yesno sql: ... ;; }` | `CASE WHEN expr THEN 'Yes' ELSE 'No' END` | Boolean display |
| Measure (list) | `measure: m { type: list list_field: name ;; }` | `LISTAGG(column, ', ')` | Snowflake equivalent |
| Measure (date) | `measure: m { type: date sql: MAX(${TABLE}.date) ;; }` | `METRICS` with `MAX(TABLE.DATE)` | Date-typed aggregate |
| Measure (percentile) | `measure: m { type: percentile percentile: 95 sql: ${TABLE}.val ;; }` | `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY TABLE.VAL)` | Translation |
| Measure (median_distinct) | `measure: m { type: median_distinct sql_distinct_key: ... sql: ... ;; }` | Complex — flag as manual | Deduplicates before computing median; uses `sql_distinct_key` |
| Measure (percentile_distinct) | `measure: m { type: percentile_distinct sql_distinct_key: ... percentile: 95 sql: ... ;; }` | Complex — flag as manual | Deduplicates before percentile; uses `sql_distinct_key` |
| Measure (period_over_period) | `measure: m { type: period_over_period based_on: orders.count based_on_time: orders.created_month period: month kind: previous }` | `LAG()` window function or date-offset CTE | **Manual** — new Looker PoP feature; requires resolving `based_on` measure and time dimension |
| Measure (filtered) | `measure: us_rev { type: sum sql: ${TABLE}.revenue ;; filters: [country: "US"] }` | `SUM(CASE WHEN TABLE.COUNTRY = 'US' THEN TABLE.REVENUE END)` | Translation — convert filter to CASE WHEN inside aggregate |
| Measure (percent_of_total) | `measure: m { type: percent_of_total sql: ${TABLE}.amount ;; }` | Requires window function: `SUM(col) / SUM(col) OVER ()` | **Manual** |
| Measure (percent_of_previous) | `measure: m { type: percent_of_previous sql: ${TABLE}.amount ;; }` | Requires `LAG()` window function | **Manual** |
| Measure (running_total) | `measure: m { type: running_total sql: ${TABLE}.amount ;; }` | Requires `SUM() OVER (ORDER BY ...)` | **Manual** |

### Metadata & Presentation

| LookML Concept | LookML Syntax | Snowflake Semantic View | Notes |
|---------------|---------------|------------------------|-------|
| Label / Description | `label:` and `description:` parameters | `SYNONYMS` + `COMMENT` | Label text becomes a synonym entry |
| Group Label | `group_label: "Revenue Metrics"` | Organizational grouping | Use to split large models into focused semantic views |
| Group Item Label | `group_item_label: "Total"` | Refines label within group | Combine with group_label for full display name |
| Tags | `tags: ["finance", "quarterly"]` | Document as metadata | Helps categorize fields for semantic view organization |
| Hidden | `hidden: yes` | Omit from semantic view | Hidden fields are internal — typically skip unless referenced by visible measures |
| Value Format | `value_format_name: usd` or `value_format: "$#,##0.00"` | Document as metadata | Format strings inform the `COMMENT` but don't map to semantic view properties |
| HTML / Link | `html: ... ;;` or `link: { ... }` | Not supported | Presentation-only — skip |
| Drill Fields | `drill_fields: [id, name, ...]` | Not supported | Exploration hint — document but don't map |
| Can Filter | `can_filter: no` | Not supported | Access control — document but don't map |
| Suggest Dimension/Explore | `suggest_dimension: ...` / `suggest_explore: ...` | Not supported | Autocomplete hint — skip |
| Required Access Grants | `required_access_grants: [grant_name]` | Not supported | Row/field-level security — flag and document |
| Datagroup | `datagroup: name { sql_trigger: ... ;; max_cache_age: "24 hours" }` | Not directly mapped | Caching/refresh policy — document for context |
| Parameter | `parameter: name { type: string allowed_values: [...] }` | Not directly mapped | Runtime parameter — flag; user must decide how to handle |
| Liquid SQL | `sql: {% if ... %} ... {% endif %} ;;` | Not auto-convertible | Flag as manual — Liquid templates are runtime conditional logic |
| `{% parameter %}` | `sql: {% parameter my_param %} ;;` | Not auto-convertible | Parameter injection in SQL — flag as manual |
| `${field}` references | `sql: ${view_name.field_name} ;;` | Resolved to `TABLE_ALIAS.COLUMN_NAME` | Must resolve through the view's sql_table_name + field's sql expression |
| Sets | `set: detail { fields: [id, name, email] }` | Organizational grouping | Use for drill-down or field lists — document but don't directly map |

### LookML Complexity Classification

| Complexity | Criteria | Action |
|-----------|----------|--------|
| **Simple** | Basic measure types (`sum`, `count`, `count_distinct`, `average`, `min`, `max`, `median`), simple dimensions (`string`, `number`, `zipcode`, `date`, `date_time`), dimension groups (`type: time`) with standard timeframes, `${TABLE}.column` references, `primary_key`, `label`, `description`, `hidden`, `tags` | Auto-convert |
| **Translation** | `type: tier`/`type: bin`, `type: case`, `type: duration`, `type: location`, `type: custom_calendar`, `type: date` (measure), `type: percentile`, `type: list`, `type: number` (measure with `${measure}` refs), filtered measures, `type: yesno` (dimensions and measures), `value_format`, `group_label` | Apply translation patterns |
| **Manual** | Liquid SQL (`{% if %}`, `{% parameter %}`), `type: percent_of_total`, `type: percent_of_previous`, `type: running_total`, `type: sum_distinct`, `type: average_distinct`, `type: median_distinct`, `type: percentile_distinct`, `type: period_over_period`, `type: distance`, derived tables (SQL or NDT), `sql_always_where`, `sql_always_having`, `access_filter`, `required_access_grants`, `extends` with complex overrides, `${view.field}` cross-view references in measures with nested logic | Flag for manual review |

## Function Translation: Tableau → Snowflake SQL

### Conditional & Logical

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `IF cond THEN a ELSEIF cond2 THEN b ELSE c END` | `CASE WHEN cond THEN a WHEN cond2 THEN b ELSE c END` | Translation | Multi-branch |
| `IIF(cond, a, b)` | `IFF(cond, a, b)` or `CASE WHEN cond THEN a ELSE b END` | Translation | |
| `CASE expr WHEN val1 THEN r1 ... END` | `CASE expr WHEN val1 THEN r1 ... END` | Simple | Identical syntax |
| `ZN(expr)` | `COALESCE(expr, 0)` | Translation | Zero-null |
| `IFNULL(expr, alt)` | `COALESCE(expr, alt)` | Simple | |
| `ISNULL(expr)` | `expr IS NULL` | Simple | |
| `ISDATE(string)` | `TRY_TO_DATE(string) IS NOT NULL` | Translation | |
| `AND` / `OR` / `NOT` | `AND` / `OR` / `NOT` | Simple | Identical |
| `ATTR(field)` | `MIN(field)` | Translation | Approximate — returns `*` if values differ |

### String Functions

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `CONTAINS(string, sub)` | `CONTAINS(string, sub)` | Simple | |
| `STARTSWITH(string, sub)` | `STARTSWITH(string, sub)` | Simple | |
| `ENDSWITH(string, sub)` | `ENDSWITH(string, sub)` | Simple | |
| `LEFT(string, n)` | `LEFT(string, n)` | Simple | |
| `RIGHT(string, n)` | `RIGHT(string, n)` | Simple | |
| `MID(string, start, len)` | `SUBSTR(string, start, len)` | Translation | |
| `LEN(string)` | `LENGTH(string)` | Translation | Different name |
| `LTRIM(string)` | `LTRIM(string)` | Simple | |
| `RTRIM(string)` | `RTRIM(string)` | Simple | |
| `TRIM(string)` | `TRIM(string)` | Simple | |
| `UPPER(string)` | `UPPER(string)` | Simple | |
| `LOWER(string)` | `LOWER(string)` | Simple | |
| `REPLACE(string, old, new)` | `REPLACE(string, old, new)` | Simple | |
| `FIND(string, sub, start)` | `POSITION(sub, string, start)` | Translation | Argument order differs |
| `SPLIT(string, delim, index)` | `SPLIT_PART(string, delim, index)` | Translation | |
| `SPACE(n)` | `REPEAT(' ', n)` | Translation | |
| `CHAR(n)` | `CHAR(n)` | Simple | |
| `ASCII(char)` | `ASCII(char)` | Simple | |
| `MIN(a, b)` (string) | `LEAST(a, b)` | Translation | Tableau scalar MIN |
| `MAX(a, b)` (string) | `GREATEST(a, b)` | Translation | Tableau scalar MAX |
| `STR(num)` | `TO_VARCHAR(num)` | Translation | |
| `FLOAT(string)` | `TRY_CAST(string AS FLOAT)` | Translation | |
| `INT(num)` | `TRUNC(num, 0)` | Translation | Truncates toward zero |
| `REGEXP_MATCH(string, pattern)` | `REGEXP_LIKE(string, pattern)` | Translation | Different name |
| `REGEXP_EXTRACT(string, pattern)` | `REGEXP_SUBSTR(string, pattern)` | Translation | Different name |
| `REGEXP_EXTRACT_NTH(string, pattern, n)` | `REGEXP_SUBSTR(string, pattern, 1, n)` | Translation | |
| `REGEXP_REPLACE(string, pattern, repl)` | `REGEXP_REPLACE(string, pattern, repl)` | Simple | |
| `FINDNTH(string, sub, occurrence)` | `REGEXP_INSTR(string, REGEXP_REPLACE(sub,'[.*+?]','\\\\\\0'), 1, occurrence)` | Translation | Nth occurrence — use regex or iterative POSITION |
| `PROPER(string)` | `INITCAP(string)` | Translation | Title case |
| `DATE(expression)` | `TRY_TO_DATE(expression)` | Translation | Type conversion |
| `DATETIME(expression)` | `TRY_TO_TIMESTAMP(expression)` | Translation | Type conversion |

### Date & Time Functions

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `TODAY()` | `CURRENT_DATE()` | Simple | |
| `NOW()` | `CURRENT_TIMESTAMP()` | Simple | |
| `DATETRUNC('month', date)` | `DATE_TRUNC('MONTH', date)` | Simple | |
| `DATEDIFF('day', start, end)` | `DATEDIFF('DAY', start, end)` | Simple | |
| `DATEADD('month', n, date)` | `DATEADD('MONTH', n, date)` | Simple | |
| `DATEPART('year', date)` | `DATE_PART('YEAR', date)` or `YEAR(date)` | Simple | |
| `DATENAME('month', date)` | `MONTHNAME(date)` or `TO_VARCHAR(date, 'MMMM')` | Translation | |
| `DATEPARSE('format', string)` | `TRY_TO_TIMESTAMP(string, format)` | Translation | Format tokens differ |
| `MAKEDATE(year, month, day)` | `DATE_FROM_PARTS(year, month, day)` | Translation | |
| `MAKEDATETIME(date, time)` | `TIMESTAMP_FROM_PARTS(date, time)` | Translation | |
| `MAKETIME(hour, min, sec)` | `TIME_FROM_PARTS(hour, min, sec)` | Translation | |
| `YEAR(date)` | `YEAR(date)` | Simple | |
| `MONTH(date)` | `MONTH(date)` | Simple | |
| `DAY(date)` | `DAY(date)` | Simple | |
| `WEEK(date)` | `WEEKOFYEAR(date)` | Translation | |
| `QUARTER(date)` | `QUARTER(date)` | Simple | |
| `ISOYEAR(date)` | `YEAROFWEEKISO(date)` | Translation | ISO 8601 year |
| `ISOWEEK(date)` | `WEEKISO(date)` | Translation | ISO 8601 week |
| `ISOWEEKDAY(date)` | `DAYOFWEEKISO(date)` | Translation | ISO 8601 weekday (1=Mon) |
| `ISOQUARTER(date)` | `CEIL(WEEKISO(date) / 13.0)` | Translation | ISO quarter — no native function |

### Math Functions

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `ABS(n)` | `ABS(n)` | Simple | |
| `ROUND(n, d)` | `ROUND(n, d)` | Simple | |
| `CEILING(n)` | `CEIL(n)` | Simple | |
| `FLOOR(n)` | `FLOOR(n)` | Simple | |
| `SIGN(n)` | `SIGN(n)` | Simple | |
| `SQRT(n)` | `SQRT(n)` | Simple | |
| `POWER(base, exp)` | `POWER(base, exp)` | Simple | |
| `LOG(n)` | `LN(n)` | Translation | Tableau LOG is natural log |
| `LOG(n, base)` (Prep) | `LOG(base, n)` | Translation | |
| `LN(n)` | `LN(n)` | Simple | |
| `EXP(n)` | `EXP(n)` | Simple | |
| `DIV(a, b)` | `DIV0(a, b)` or `TRUNC(a / b)` | Translation | Integer division |
| `MOD(n, d)` | `MOD(n, d)` | Simple | |
| `PI()` | `PI()` | Simple | |
| `RADIANS(deg)` | `RADIANS(deg)` | Simple | |
| `DEGREES(rad)` | `DEGREES(rad)` | Simple | |
| `SIN/COS/TAN/ASIN/ACOS/ATAN` | Same names | Simple | |
| `HEXBINX(x, y)` / `HEXBINY(x, y)` | No direct equivalent | **Manual** | Geo-binning |
| `SQUARE(n)` | `POWER(n, 2)` | Simple | |
| `ATAN2(y, x)` | `ATAN2(y, x)` | Simple | |
| `COT(n)` | `COT(n)` | Simple | |

### Aggregate Functions

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `SUM(expr)` | `SUM(expr)` | Simple | |
| `AVG(expr)` | `AVG(expr)` | Simple | |
| `COUNT(expr)` | `COUNT(expr)` | Simple | |
| `COUNTD(field)` | `COUNT(DISTINCT field)` | Translation | Tableau shorthand |
| `MIN(expr)` | `MIN(expr)` | Simple | Aggregate context |
| `MAX(expr)` | `MAX(expr)` | Simple | Aggregate context |
| `MEDIAN(expr)` | `MEDIAN(expr)` | Simple | |
| `PERCENTILE(expr, k)` | `PERCENTILE_CONT(k) WITHIN GROUP (ORDER BY expr)` | Translation | |
| `STDEV(expr)` | `STDDEV_SAMP(expr)` | Translation | |
| `STDEVP(expr)` | `STDDEV_POP(expr)` | Translation | |
| `VAR(expr)` | `VAR_SAMP(expr)` | Translation | |
| `VARP(expr)` | `VAR_POP(expr)` | Translation | |
| `CORR(expr1, expr2)` | `CORR(expr1, expr2)` | Simple | |
| `COVAR(expr1, expr2)` | `COVAR_SAMP(expr1, expr2)` | Translation | |
| `COVARP(expr1, expr2)` | `COVAR_POP(expr1, expr2)` | Translation | |
| `COLLECT(expr)` | `ARRAY_AGG(expr)` | Translation | Spatial → array |

### Table Calculations (Manual — viz-dependent)

| Tableau Function | Snowflake SQL | Complexity | Notes |
|-----------------|---------------|-----------|-------|
| `LOOKUP(expr, offset)` | Not supported in semantic views | **Manual** | Viz-level offset |
| `RUNNING_SUM(expr)` | `SUM(expr) OVER (ORDER BY ...)` | **Manual** | Requires partition/order context |
| `RUNNING_AVG(expr)` | `AVG(expr) OVER (ORDER BY ...)` | **Manual** | Requires partition/order context |
| `RUNNING_COUNT(expr)` | `COUNT(expr) OVER (ORDER BY ...)` | **Manual** | Requires partition/order context |
| `RUNNING_MIN(expr)` / `RUNNING_MAX(expr)` | `MIN/MAX(expr) OVER (ORDER BY ...)` | **Manual** | |
| `WINDOW_SUM(expr, start, end)` | `SUM(expr) OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_AVG(expr, start, end)` | `AVG(expr) OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_COUNT(expr, start, end)` | `COUNT(expr) OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_MAX(expr, start, end)` / `WINDOW_MIN` | `MAX/MIN(expr) OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_MEDIAN(expr, start, end)` | No window equivalent | **Manual** | |
| `WINDOW_PERCENTILE(expr, k, start, end)` | No window equivalent | **Manual** | |
| `WINDOW_STDEV(expr, start, end)` / `WINDOW_VAR` | `STDDEV/VAR OVER (ROWS BETWEEN ...)` | **Manual** | |
| `INDEX()` | `ROW_NUMBER() OVER (...)` | **Manual** | Partition depends on viz |
| `FIRST()` / `LAST()` | Relative position in partition | **Manual** | Viz-dependent |
| `SIZE()` | `COUNT(*) OVER (PARTITION BY ...)` | **Manual** | Partition size |
| `RANK(expr)` | `RANK() OVER (ORDER BY expr)` | **Manual** | Tableau RANK is table calc |
| `RANK_DENSE(expr)` | `DENSE_RANK() OVER (ORDER BY expr)` | **Manual** | |
| `RANK_MODIFIED(expr)` | No direct equivalent | **Manual** | |
| `RANK_PERCENTILE(expr)` | `PERCENT_RANK() OVER (ORDER BY expr)` | **Manual** | |
| `RANK_UNIQUE(expr)` | `ROW_NUMBER() OVER (ORDER BY expr)` | **Manual** | |
| `TOTAL(expr)` | `SUM(expr) OVER ()` | **Manual** | Grand total in viz |
| `PREVIOUS_VALUE(initial)` | No direct equivalent | **Manual** | Recursive calc |
| `RAWSQL_BOOL/DATE/INT/REAL/STR(sql)` | Pass-through SQL | Translation | Embed directly |
| `RAWSQLAGG_BOOL/DATE/INT/REAL/STR(sql)` | Pass-through aggregate SQL | Translation | Embed directly in aggregate context |
| `WINDOW_CORR(expr1, expr2, start, end)` | `CORR(expr1, expr2) OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_COVAR(expr1, expr2, start, end)` | `COVAR_SAMP OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_COVARP(expr1, expr2, start, end)` | `COVAR_POP OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_STDEVP(expr, start, end)` | `STDDEV_POP OVER (ROWS BETWEEN ...)` | **Manual** | |
| `WINDOW_VARP(expr, start, end)` | `VAR_POP OVER (ROWS BETWEEN ...)` | **Manual** | |
| `MODEL_PERCENTILE(target, predictors)` | No equivalent | **Manual** | Analytics Extensions — predictive model |
| `MODEL_QUANTILE(quantile, target, predictors)` | No equivalent | **Manual** | Analytics Extensions — predictive model |
| `SCRIPT_BOOL/INT/REAL/STRING(script, args)` | No equivalent | **Manual** | Analytics Extensions — R/Python/MATLAB |
| `MAKEPOINT(lat, lon)` | No spatial support in semantic views | **Manual** | Spatial function |
| `MAKELINE(point1, point2)` | No spatial support in semantic views | **Manual** | Spatial function |

### LOD Expressions (Manual — requires restructuring)

| Tableau Pattern | Snowflake SQL | Complexity | Notes |
|----------------|---------------|-----------|-------|
| `{FIXED [dim] : AGG(measure)}` | Subquery or window function | **Manual** | Independent of viz dimensions |
| `{INCLUDE [dim] : AGG(measure)}` | Window function with additional partition | **Manual** | Adds dimension to viz grain |
| `{EXCLUDE [dim] : AGG(measure)}` | Window function removing partition | **Manual** | Removes dimension from viz grain |

### Tableau Complexity Classification

| Complexity | Criteria | Action |
|-----------|----------|--------|
| **Simple** | Basic aggregates (`SUM`, `AVG`, `COUNT`, `MIN`, `MAX`, `MEDIAN`), 1:1 functions (`UPPER`, `LOWER`, `TRIM`, `LEFT`, `RIGHT`, `REPLACE`, `ABS`, `ROUND`, `FLOOR`, `CEIL`, `YEAR`, `MONTH`, `DAY`, `DATETRUNC`, `DATEDIFF`, `DATEADD`, `DATEPART`, `CONTAINS`, `STARTSWITH`, `ENDSWITH`, `SQRT`, `POWER`, `SQUARE`, `SIGN`, `MOD`, `PI`, `LN`, `EXP`, `SIN/COS/TAN/ASIN/ACOS/ATAN`, `ATAN2`, `COT`, `RADIANS`, `DEGREES`, `CORR`, `REGEXP_REPLACE`), basic conditionals (`CASE WHEN`), null checks (`ISNULL`, `IFNULL`, `COALESCE`) | Auto-convert |
| **Translation** | `ZN`, `IIF`, `ATTR`, `COUNTD`, `STR`, `FLOAT`, `INT`, `DATE`, `DATETIME`, `PROPER`, `FINDNTH`, `LEN`, `MID`, `FIND`, `SPLIT`, `SPACE`, `DATEPARSE`, `MAKETIME/DATE/DATETIME`, `ISOYEAR/ISOWEEK/ISOWEEKDAY/ISOQUARTER`, `DATENAME`, `WEEK`, `REGEXP_MATCH/EXTRACT`, `STDEV/STDEVP/VAR/VARP/COVAR/COVARP`, `PERCENTILE`, `RAWSQL_*`, `RAWSQLAGG_*`, `LOG`, `DIV` | Apply translation table |
| **Manual** | All table calculations (`RUNNING_*`, `WINDOW_*`, `LOOKUP`, `INDEX`, `FIRST`, `LAST`, `SIZE`, `RANK*`, `TOTAL`, `PREVIOUS_VALUE`), all LOD expressions (`{FIXED}`, `{INCLUDE}`, `{EXCLUDE}`), `HEXBINX/Y`, `MAKEPOINT/MAKELINE`, `MODEL_PERCENTILE/QUANTILE`, `SCRIPT_*` (Analytics Extensions), `COLLECT` (spatial) | Flag for manual conversion |

## Function Translation: LookML Measure Types → Snowflake SQL

| LookML Measure Type | Snowflake SQL Pattern | Notes |
|---------------------|----------------------|-------|
| `type: sum` | `SUM(column)` | |
| `type: count` | `COUNT(*)` or `COUNT(pk)` | |
| `type: count_distinct` | `COUNT(DISTINCT column)` | |
| `type: average` | `AVG(column)` | |
| `type: min` | `MIN(column)` | |
| `type: max` | `MAX(column)` | |
| `type: median` | `MEDIAN(column)` | Snowflake supports MEDIAN natively |
| `type: sum_distinct` | `SUM(DISTINCT column)` | Rare but supported |
| `type: number` | Custom SQL expression | Pass-through — the `sql:` field contains the expression |
| `type: string` | Custom SQL expression | Non-aggregate measure (rare) |
| `type: yesno` | `CASE WHEN expr THEN 'Yes' ELSE 'No' END` | Boolean display |
| `type: list` | `LISTAGG(column, ', ')` | Snowflake equivalent |
| `type: percent_of_total` | Requires window function | Flag — needs `SUM(col) / SUM(col) OVER ()` |
| `type: percent_of_previous` | Requires window function | Flag — needs LAG() |
| `type: running_total` | Requires window function | Flag — needs `SUM() OVER (ORDER BY ...)` |
| `type: percentile` | `PERCENTILE_CONT(k) WITHIN GROUP (ORDER BY col)` | Requires `percentile:` subparam (e.g. `percentile: 95`) |
| `type: median_distinct` | Complex — deduplicate then median | Manual — like `average_distinct` but for median |
| `type: percentile_distinct` | Complex — deduplicate then percentile | Manual — uses `sql_distinct_key` |
| `type: average_distinct` | Complex — deduplicate then average | Manual — uses `sql_distinct_key` |
| `type: sum_distinct` (with `sql_distinct_key`) | Complex — deduplicate then sum | Manual — simple `SUM(DISTINCT)` is insufficient; requires CTE |
| `type: period_over_period` | `LAG()` or date-offset join | Manual — new Looker feature with `based_on`, `based_on_time`, `period`, `kind` |
| `type: date` | `MAX(date_col)` or custom | Non-aggregate — passes through sql expression |

## LookML Dimension Group Timeframe → Snowflake Expression

| Timeframe | Snowflake Expression | Example Output |
|-----------|---------------------|----------------|
| `raw` | Column as-is (TIMESTAMP) | `2024-03-15 14:30:00` |
| `time` | `TO_TIMESTAMP(col)` | `2024-03-15 14:30:00` |
| `date` | `DATE(col)` | `2024-03-15` |
| `week` | `DATE_TRUNC('WEEK', col)` | `2024-03-11` |
| `month` | `DATE_TRUNC('MONTH', col)` | `2024-03-01` |
| `quarter` | `DATE_TRUNC('QUARTER', col)` | `2024-01-01` |
| `year` | `DATE_TRUNC('YEAR', col)` | `2024-01-01` |
| `day_of_week` | `DAYOFWEEK(col)` | `5` |
| `day_of_month` | `DAYOFMONTH(col)` | `15` |
| `month_num` | `MONTH(col)` | `3` |
| `month_name` | `MONTHNAME(col)` | `Mar` |
| `fiscal_month_num` | `MONTH(DATEADD('MONTH', -offset, col))` | Depends on fiscal year start |
| `hour` | `HOUR(col)` | `14` |
| `hour_of_day` | `HOUR(col)` | `14` |

## Power BI → Snowflake Semantic View

| Power BI Concept | TOM JSON Location | Snowflake Semantic View | Notes |
|-----------------|-------------------|------------------------|-------|
| Table | `model.tables[].name` | `TABLES` clause | Map to Snowflake table via M/Power Query source expression in partitions |
| Column (data) | `model.tables[].columns[]` with `type: data` | `DIMENSIONS` or `FACTS` | If `summarizeBy != "none"` → FACTS; otherwise → DIMENSIONS |
| Column (calculated) | `model.tables[].columns[]` with `type: calculated` | `DIMENSIONS` (if row-level) or `METRICS` (if aggregate) | DAX expression must be translated to SQL |
| Measure | `model.tables[].measures[]` | `METRICS` | DAX expression in `expression` field. Complexity varies widely |
| Relationship | `model.relationships[]` | `RELATIONSHIPS` | `fromTable/fromColumn → toTable/toColumn`. Map cardinality directly |
| Hierarchy | `model.tables[].hierarchies[]` | Flatten to individual `DIMENSIONS` | Each hierarchy level becomes a separate dimension |
| Display Folder | `measures[].displayFolder` | Group into separate semantic views if large | Organizational hint for splitting views |
| Description | `columns[]/measures[].description` | `COMMENT` | Direct mapping |
| Format String | `measures[].formatString` | Not directly mapped | Document in mapping file for reference |
| Data Source (M) | `partitions[].source.expression` (M/Power Query) | Snowflake target table resolution | Parse M to find source database.schema.table |
| Calculated Table | `partitions[].source.type == "calculated"` | Must create as Snowflake VIEW first | Flag — no physical source table |
| RLS Roles | `model.roles[]` | Not supported in semantic views | Document and flag |
| Inactive Relationship | `relationships[].isActive == false` | Flag for review | Activated via `USERELATIONSHIP()` in DAX |
| Partition (M query) | `partitions[].source.expression` | Source table identification | Parse M expression for Snowflake source |

## Function Translation: DAX → Snowflake SQL

| DAX Function / Pattern | Snowflake SQL | Complexity | Notes |
|-----------------------|---------------|-----------|-------|
| `SUM(Table[Column])` | `SUM(COLUMN)` | Simple | Direct mapping |
| `COUNT(Table[Column])` | `COUNT(COLUMN)` | Simple | Direct mapping |
| `COUNTROWS(Table)` | `COUNT(*)` | Simple | Direct mapping |
| `DISTINCTCOUNT(Table[Column])` | `COUNT(DISTINCT COLUMN)` | Simple | Direct mapping |
| `AVERAGE(Table[Column])` | `AVG(COLUMN)` | Simple | Direct mapping |
| `MIN(Table[Column])` | `MIN(COLUMN)` | Simple | Direct mapping |
| `MAX(Table[Column])` | `MAX(COLUMN)` | Simple | Direct mapping |
| `DIVIDE(a, b)` | `DIV0NULL(a, b)` or `CASE WHEN b = 0 THEN NULL ELSE a / b END` | Translation | `DIVIDE` returns BLANK on div-by-zero |
| `DIVIDE(a, b, alt)` | `CASE WHEN b = 0 THEN alt ELSE a / b END` | Translation | With alternate value |
| `IF(cond, true, false)` | `CASE WHEN cond THEN true ELSE false END` | Translation | Direct |
| `SWITCH(expr, val1, result1, ..., default)` | `CASE expr WHEN val1 THEN result1 ... ELSE default END` | Translation | Direct |
| `SWITCH(TRUE(), cond1, r1, ...)` | `CASE WHEN cond1 THEN r1 ... END` | Translation | Searched CASE |
| `ISBLANK(expr)` | `expr IS NULL` | Translation | BLANK = NULL in SQL |
| `COALESCE(a, b)` | `COALESCE(a, b)` | Simple | Same function |
| `BLANK()` | `NULL` | Simple | Direct |
| `FORMAT(date, "yyyy-MM")` | `TO_VARCHAR(date, 'YYYY-MM')` | Translation | Format string syntax differs |
| `RELATED(Table[Column])` | Direct column reference (via JOIN) | Translation | Assumes relationship exists; map to joined table column |
| `SUMX(Table, expr)` | `SUM(expr)` (if simple) | Translation | Simple iterator → aggregate. Complex iterators → manual |
| `AVERAGEX(Table, expr)` | `AVG(expr)` (if simple) | Translation | Same pattern as SUMX |
| `COUNTX(Table, expr)` | `COUNT(expr)` (if simple) | Translation | Same pattern |
| `CONCATENATEX(Table, col, sep)` | `LISTAGG(col, sep)` | Translation | Direct equivalent |
| `YEAR(date)` | `YEAR(date)` | Simple | Same |
| `MONTH(date)` | `MONTH(date)` | Simple | Same |
| `DAY(date)` | `DAY(date)` | Simple | Same |
| `TODAY()` | `CURRENT_DATE()` | Simple | |
| `NOW()` | `CURRENT_TIMESTAMP()` | Simple | |
| `DATEADD(DateTable[Date], -1, YEAR)` | `DATEADD('YEAR', -1, date)` | Translation | Argument order differs |
| `DATEDIFF(d1, d2, DAY)` | `DATEDIFF('DAY', d1, d2)` | Translation | Argument order differs |
| `TOTALYTD(measure, DateTable[Date])` | `SUM(CASE WHEN date >= DATE_TRUNC('YEAR', CURRENT_DATE()) AND date <= CURRENT_DATE() THEN col END)` | Translation | Year-to-date filter |
| `SAMEPERIODLASTYEAR(DateTable[Date])` | `DATEADD('YEAR', -1, date)` filter | Translation | Prior year comparison |
| `CALCULATE(measure, filter1, ...)` | Not auto-convertible | **Manual** | Filter context manipulation — core DAX concept with no SQL equivalent |
| `CALCULATE(measure, ALL(Table))` | Not auto-convertible | **Manual** | Removes all filters — requires understanding intended semantics |
| `CALCULATE(measure, FILTER(Table, cond))` | `SUM(CASE WHEN cond THEN col END)` (simple cases) | **Manual** | Simple filter → CASE WHEN; complex → manual |
| `ALL(Table)` / `ALL(Column)` | Not auto-convertible | **Manual** | Context removal |
| `ALLEXCEPT(Table, col)` | Not auto-convertible | **Manual** | Selective context removal |
| `ALLSELECTED(Table)` | Not auto-convertible | **Manual** | Respects outer filter context only |
| `RANKX(Table, expr)` | `RANK() OVER (ORDER BY expr)` (approximate) | **Manual** | Window function, but DAX RANKX has context nuances |
| `TOPN(n, Table, expr)` | `QUALIFY ROW_NUMBER() OVER (ORDER BY expr) <= n` | **Manual** | Approximate — TOPN is context-aware |
| `EARLIER(column)` / `EARLIEST(column)` | Not auto-convertible | **Manual** | Nested row context — no SQL parallel |
| `USERELATIONSHIP(col1, col2)` | Not auto-convertible | **Manual** | Activates inactive relationship |
| `CROSSFILTER(col1, col2, direction)` | Not auto-convertible | **Manual** | Changes cross-filter direction |
| `PATH(child, parent)` | `SYS_CONNECT_BY_PATH` or recursive CTE | **Manual** | Parent-child hierarchy |
| `TREATAS(values, column)` | Not auto-convertible | **Manual** | Virtual relationship |
| `SELECTEDVALUE(column)` | Not auto-convertible | **Manual** | Depends on slicer context |
| `HASONEVALUE(column)` | Not auto-convertible | **Manual** | Depends on filter context |
| `VALUES(column)` | `SELECT DISTINCT column` (approximate) | **Manual** | Context-dependent distinct list |

## DAX Complexity Classification

Based on the [official Microsoft DAX Reference](https://learn.microsoft.com/en-us/dax/) (250+ functions across 13 categories), every DAX function is classified into one of three tiers:

| Complexity | Criteria | Action | Coverage |
|-----------|----------|--------|----------|
| **Simple** | Direct 1:1 Snowflake equivalent. Basic aggregations (`SUM`, `COUNT`, `AVG`, `MIN`, `MAX`, `DISTINCTCOUNT`), text (`LEFT`, `RIGHT`, `UPPER`, `LOWER`, `TRIM`, `LEN`), math (`ABS`, `ROUND`, `MOD`, `POWER`, `SQRT`), date parts (`YEAR`, `MONTH`, `DAY`), statistics (`STDEV.S/P`, `VAR.S/P`, `MEDIAN`), logical (`AND`, `OR`, `NOT`, `TRUE`, `FALSE`), period boundaries (`STARTOFMONTH/QUARTER/YEAR`, `ENDOFMONTH/QUARTER/YEAR`) | Auto-convert to SQL | ~80 functions |
| **Translation** | Snowflake equivalent exists but requires syntax/argument adjustment. Includes `DIVIDE`, `IF`/`SWITCH`/`IFERROR`, `FORMAT`, `RELATED`, `LOOKUPVALUE`, iterators (`SUMX`/`AVERAGEX`/`COUNTX`), time intelligence (`TOTALYTD/MTD/QTD`, `SAMEPERIODLASTYEAR`, `DATEADD`, `DATESYTD/MTD/QTD`), text (`MID`, `SUBSTITUTE`, `SEARCH`, `FIND`, `COMBINEVALUES`), math (`ROUNDUP/DOWN`, `LOG`, `RAND`, `CEILING/FLOOR`), info (`ISERROR`, `ISNUMBER`, `USERNAME`) | Apply translation table, present for review | ~70 functions |
| **Manual Required** | No direct SQL equivalent — depends on DAX filter/row context. Includes `CALCULATE`/`CALCULATETABLE`, `ALL`/`ALLEXCEPT`/`ALLSELECTED`, `RANKX`, `EARLIER`, `USERELATIONSHIP`, `CROSSFILTER`, `PATH`/`PATHITEM`, `TREATAS`, `SELECTEDVALUE`, `HASONEVALUE`, `ISINSCOPE`, `PARALLELPERIOD`, `OPENINGBALANCE*`/`CLOSINGBALANCE*`, `CALENDAR`/`CALENDARAUTO`, all financial functions (`NPV`, `IRR`, `PMT`, `PV`, `FV`), table manipulation (`GROUPBY`, `NATURALINNERJOIN`) | Flag — present original DAX with explanation, user provides Snowflake SQL | ~60 functions |

## DAX Function Translation: Additional Categories

The tables above cover the most commonly encountered DAX functions. This section covers additional categories from the [official DAX reference](https://learn.microsoft.com/en-us/dax/) to ensure comprehensive translation coverage.

### Text Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `CONCATENATE(a, b)` | `CONCAT(a, b)` or `a \|\| b` | Simple |
| `a & b` (string concat operator) | `a \|\| b` | Simple |
| `LEFT(text, n)` | `LEFT(text, n)` | Simple |
| `RIGHT(text, n)` | `RIGHT(text, n)` | Simple |
| `MID(text, start, n)` | `SUBSTR(text, start, n)` | Translation |
| `LEN(text)` | `LENGTH(text)` | Simple |
| `UPPER(text)` | `UPPER(text)` | Simple |
| `LOWER(text)` | `LOWER(text)` | Simple |
| `TRIM(text)` | `TRIM(text)` | Simple |
| `SUBSTITUTE(text, old, new)` | `REPLACE(text, old, new)` | Translation |
| `REPLACE(text, start, n, new)` | `INSERT(text, start, n, new)` | Translation |
| `SEARCH(find, text, start)` | `POSITION(find IN text)` or `CHARINDEX(find, text, start)` | Translation |
| `FIND(find, text, start)` | `POSITION(find IN text)` (case-sensitive) | Translation |
| `EXACT(a, b)` | `a = b` (Snowflake default is case-sensitive for strings) | Simple |
| `REPT(text, n)` | `REPEAT(text, n)` | Simple |
| `VALUE(text)` | `TRY_CAST(text AS NUMBER)` | Translation |
| `FIXED(number, decimals)` | `TO_VARCHAR(number, format)` | Translation |
| `UNICHAR(n)` | `CHAR(n)` | Simple |
| `UNICODE(char)` | `ASCII(char)` | Simple |
| `COMBINEVALUES(sep, val1, val2, ...)` | `CONCAT_WS(sep, val1, val2, ...)` | Translation |

### Math and Trig Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `ABS(n)` | `ABS(n)` | Simple |
| `ROUND(n, digits)` | `ROUND(n, digits)` | Simple |
| `ROUNDUP(n, digits)` | `CEIL(n * POWER(10, digits)) / POWER(10, digits)` | Translation |
| `ROUNDDOWN(n, digits)` | `TRUNC(n, digits)` | Translation |
| `INT(n)` | `FLOOR(n)` | Translation |
| `CEILING(n, sig)` | `CEIL(n / sig) * sig` | Translation |
| `FLOOR(n, sig)` | `FLOOR(n / sig) * sig` | Translation |
| `MOD(n, divisor)` | `MOD(n, divisor)` | Simple |
| `POWER(base, exp)` | `POWER(base, exp)` | Simple |
| `SQRT(n)` | `SQRT(n)` | Simple |
| `LOG(n, base)` | `LOG(base, n)` | Translation (argument order differs) |
| `LOG10(n)` | `LOG(10, n)` | Translation |
| `LN(n)` | `LN(n)` | Simple |
| `EXP(n)` | `EXP(n)` | Simple |
| `SIGN(n)` | `SIGN(n)` | Simple |
| `PI()` | `PI()` | Simple |
| `RAND()` | `UNIFORM(0::FLOAT, 1::FLOAT, RANDOM())` | Translation |
| `RANDBETWEEN(lo, hi)` | `UNIFORM(lo, hi, RANDOM())` | Translation |
| `TRUNC(n)` | `TRUNC(n)` | Simple |
| `EVEN(n)` | `CEIL(n / 2) * 2` | Translation |
| `ODD(n)` | `CEIL((n - 1) / 2) * 2 + 1` | Translation |
| `QUOTIENT(num, denom)` | `TRUNC(num / denom)` | Translation |
| `CURRENCY(value)` | `CAST(value AS DECIMAL(19,4))` | Translation |
| `MROUND(n, multiple)` | `ROUND(n / multiple) * multiple` | Translation |

### Information Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `ISBLANK(expr)` | `expr IS NULL` | Simple |
| `ISERROR(expr)` | `TRY_CAST(expr ...) IS NULL` (approximate) | Translation |
| `ISNUMBER(expr)` | `TRY_CAST(expr AS NUMBER) IS NOT NULL` | Translation |
| `ISTEXT(expr)` | `TYPEOF(expr) = 'VARCHAR'` (approximate) | Translation |
| `ISLOGICAL(expr)` | `TYPEOF(expr) = 'BOOLEAN'` (approximate) | Translation |
| `ISNONTEXT(expr)` | `TYPEOF(expr) != 'VARCHAR'` | Translation |
| `HASONEVALUE(col)` | Not auto-convertible | **Manual** |
| `HASONEFILTER(col)` | Not auto-convertible | **Manual** |
| `ISFILTERED(col)` | Not auto-convertible | **Manual** |
| `ISCROSSFILTERED(col)` | Not auto-convertible | **Manual** |
| `ISINSCOPE(col)` | Not auto-convertible | **Manual** |
| `USERNAME()` / `USERPRINCIPALNAME()` | `CURRENT_USER()` | Translation |
| `LOOKUPVALUE(result_col, search_col, search_val, ...)` | Subquery: `(SELECT result_col FROM table WHERE search_col = search_val)` | Translation |

### Logical Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `IF(cond, true, false)` | `CASE WHEN cond THEN true ELSE false END` | Translation |
| `SWITCH(expr, val1, r1, ..., default)` | `CASE expr WHEN val1 THEN r1 ... ELSE default END` | Translation |
| `AND(a, b)` | `a AND b` | Simple |
| `OR(a, b)` | `a OR b` | Simple |
| `NOT(expr)` | `NOT expr` | Simple |
| `TRUE()` | `TRUE` | Simple |
| `FALSE()` | `FALSE` | Simple |
| `IFERROR(expr, alt)` | `COALESCE(TRY_CAST(...), alt)` or `IFF(TRY_... IS NULL, alt, expr)` | Translation |
| `COALESCE(a, b, ...)` | `COALESCE(a, b, ...)` | Simple |

### Time Intelligence Functions (Extended)

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `TOTALYTD(measure, dates)` | Filter: `date >= DATE_TRUNC('YEAR', CURRENT_DATE()) AND date <= CURRENT_DATE()` | Translation |
| `TOTALMTD(measure, dates)` | Filter: `date >= DATE_TRUNC('MONTH', CURRENT_DATE()) AND date <= CURRENT_DATE()` | Translation |
| `TOTALQTD(measure, dates)` | Filter: `date >= DATE_TRUNC('QUARTER', CURRENT_DATE()) AND date <= CURRENT_DATE()` | Translation |
| `SAMEPERIODLASTYEAR(dates)` | `DATEADD('YEAR', -1, date)` | Translation |
| `DATEADD(dates, n, interval)` | `DATEADD(interval, n, date)` — note arg order differs | Translation |
| `DATESYTD(dates)` | `date BETWEEN DATE_TRUNC('YEAR', CURRENT_DATE()) AND CURRENT_DATE()` | Translation |
| `DATESMTD(dates)` | `date BETWEEN DATE_TRUNC('MONTH', CURRENT_DATE()) AND CURRENT_DATE()` | Translation |
| `DATESQTD(dates)` | `date BETWEEN DATE_TRUNC('QUARTER', CURRENT_DATE()) AND CURRENT_DATE()` | Translation |
| `DATESBETWEEN(dates, start, end)` | `date BETWEEN start AND end` | Translation |
| `DATESINPERIOD(dates, start, n, interval)` | `date BETWEEN start AND DATEADD(interval, n, start)` | Translation |
| `PARALLELPERIOD(dates, n, interval)` | Shifted date range via `DATEADD` | **Manual** |
| `PREVIOUSMONTH/QUARTER/YEAR(dates)` | `DATEADD('MONTH/QUARTER/YEAR', -1, date)` range | Translation |
| `NEXTMONTH/QUARTER/YEAR(dates)` | `DATEADD('MONTH/QUARTER/YEAR', 1, date)` range | Translation |
| `OPENINGBALANCEMONTH/QUARTER/YEAR(measure, dates)` | Not auto-convertible (filter context dependent) | **Manual** |
| `CLOSINGBALANCEMONTH/QUARTER/YEAR(measure, dates)` | Not auto-convertible (filter context dependent) | **Manual** |
| `STARTOFMONTH/QUARTER/YEAR(dates)` | `DATE_TRUNC('MONTH/QUARTER/YEAR', date)` | Simple |
| `ENDOFMONTH/QUARTER/YEAR(dates)` | `LAST_DAY(date, 'MONTH/QUARTER/YEAR')` | Simple |
| `CALENDAR(start, end)` | `SELECT seq_date FROM TABLE(GENERATOR(ROWCOUNT => DATEDIFF('DAY', start, end) + 1))` | **Manual** (calculated table) |
| `CALENDARAUTO()` | Not auto-convertible | **Manual** (calculated table) |

### Statistical Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `STDEV.S(col)` | `STDDEV_SAMP(col)` | Simple |
| `STDEV.P(col)` | `STDDEV_POP(col)` | Simple |
| `VAR.S(col)` | `VAR_SAMP(col)` | Simple |
| `VAR.P(col)` | `VAR_POP(col)` | Simple |
| `MEDIAN(col)` | `MEDIAN(col)` | Simple |
| `PERCENTILE.INC(col, k)` | `PERCENTILE_CONT(k) WITHIN GROUP (ORDER BY col)` | Translation |
| `PERCENTILE.EXC(col, k)` | `PERCENTILE_CONT(k) WITHIN GROUP (ORDER BY col)` (approx) | Translation |
| `PERCENTILEX.INC(table, expr, k)` | Requires subquery with `PERCENTILE_CONT` | **Manual** |
| `NORM.DIST(x, mean, stdev, cumulative)` | `NORMAL_CDF(mean, stdev, x)` for cumulative | Translation |

### Financial Functions

| DAX Function | Snowflake SQL | Complexity |
|-------------|---------------|-----------|
| `NPV(rate, cashflows)` | No built-in equivalent | **Manual** |
| `XNPV(rate, cashflows, dates)` | No built-in equivalent | **Manual** |
| `IRR(cashflows)` | No built-in equivalent | **Manual** |
| `XIRR(cashflows, dates)` | No built-in equivalent | **Manual** |
| `PMT(rate, nper, pv)` | No built-in equivalent | **Manual** |
| `PV(rate, nper, pmt)` | No built-in equivalent | **Manual** |
| `FV(rate, nper, pmt)` | No built-in equivalent | **Manual** |

Financial functions should be flagged for manual implementation as Snowflake UDFs or inline SQL formulas.

### DAX Variables (VAR / RETURN)

DAX `VAR` / `RETURN` blocks are a scoping construct, not a function. They create named intermediate results within a measure. In SQL, these map to CTEs or inline expressions:

```
-- DAX:
-- VAR TotalQty = SUM(Sales[Quantity])
-- RETURN IF(TotalQty > 1000, TotalQty * 0.95, TotalQty * 1.25)

-- Snowflake SQL metric expression:
-- CASE WHEN SUM(QUANTITY) > 1000 THEN SUM(QUANTITY) * 0.95 ELSE SUM(QUANTITY) * 1.25 END
```

For semantic view metrics, VAR/RETURN patterns should be inlined into a single SQL expression. If the DAX VAR references different aggregations that can't be combined, flag as manual.

### DAX Operators

| DAX Operator | Snowflake SQL | Notes |
|-------------|---------------|-------|
| `&` (text concat) | `\|\|` | |
| `&&` (logical AND) | `AND` | |
| `\|\|` (logical OR) | `OR` | |
| `<>` (not equal) | `<>` or `!=` | Same |
| `IN` | `IN` | Same |
| `=` (comparison) | `=` | Same, but DAX `=` is case-insensitive for text; Snowflake default is case-sensitive |
| `EXACT(a, b)` | `a = b` | Use when case-sensitive match intended |

## Denodo → Snowflake Semantic View

### Core Concepts

| Denodo Concept | VQL / Metadata Source | Snowflake Semantic View | Notes |
|---|---|---|---|
| Database (VDP) | `DESC VQL DATABASE db_name` | Organizational boundary | Each VDP database may map to one or more semantic views |
| Base View | `CREATE ... BASE VIEW` / `GET_VIEW_COLUMNS()` | `TABLES` clause (`base_table`) | Maps to a physical source table. Extract connection info for database/schema/table. |
| Derived View (Selection) | `CREATE ... VIEW` with WHERE clause | `TABLES` + `FILTERS` | Underlying base → table entry; WHERE → filter or documented pre-condition |
| Derived View (Join) | `CREATE ... VIEW` with JOIN clauses / `VIEW_DEPENDENCIES()` | `TABLES` + `RELATIONSHIPS` | Map join type: Inner → `inner`, Left outer → `left_outer`, Full outer → `full_outer` |
| Derived View (Union) | `CREATE ... VIEW` with UNION ALL | Must materialize as Snowflake `VIEW` first | Flag for review — union views cannot be represented as a single table entry |
| Derived View (Intersection/Minus) | `CREATE ... VIEW` with INTERSECT / EXCEPT | Must materialize as Snowflake `VIEW` first | Flag as manual |
| Derived View (Flatten) | `CREATE ... VIEW` with FLATTEN/UNNEST | Must materialize as Snowflake `VIEW` with `LATERAL FLATTEN` | Flag as **manual_required** — syntax and semantics differ |
| Derived View (Aggregation) | `CREATE ... VIEW` with GROUP BY | Aggregated cols → `METRICS`; GROUP BY cols → `DIMENSIONS` | Aggregation expressions map directly to metric `expr` |
| Interface View | Abstraction over implementation views | Use as the semantic view name; extract the underlying impl view | Interface is the user-facing published API |
| Column (VARCHAR/TEXT/CHAR/BOOLEAN/DATE) | `GET_VIEW_COLUMNS()` or VQL parse | `DIMENSIONS` | Heuristic: string/date/boolean types default to dimension |
| Column (DECIMAL/DOUBLE/FLOAT) | `GET_VIEW_COLUMNS()` or VQL parse | `FACTS` or `METRICS` | Heuristic: numeric types with metric naming patterns → fact; with aggregation → metric |
| Column (INTEGER/LONG) | `GET_VIEW_COLUMNS()` or VQL parse | Check naming conventions | `_id`/`_key`/`_code` → dimension; `_count`/`_total`/`_qty` → fact |
| Column (ARRAY/REGISTER) | `GET_VIEW_COLUMNS()` | Flag as **manual_required** | Complex types need `LATERAL FLATTEN` or skip |
| View Description | `DESCRIPTION` clause in VQL or Data Catalog | `COMMENT` on semantic view | Catalog descriptions are richer than VQL descriptions |
| Data Catalog Tags | Data Catalog REST API `/browse` | Classification hint | Tags like "measure", "metric", "kpi" → fact; "dimension", "attribute" → dimension |
| Data Catalog Categories | Data Catalog REST API | Organizational grouping | Use to split large databases into focused semantic views |
| VDP Connection | `CREATE DATASOURCE` in VQL | Informs target Snowflake database/schema | Parse JDBC URL or connection parameters for target resolution |

### Denodo Column Classification (No Native Dim/Measure)

Denodo has no built-in dimension/measure distinction. Classification is inferred via heuristics applied in priority order:

| Priority | Signal | Classification Rule |
|----------|--------|-------------------|
| 1 | User override JSON | Explicit mapping always wins |
| 2 | Data Catalog tags | `measure`/`metric`/`kpi`/`fact` → FACTS; `dimension`/`attribute`/`category` → DIMENSIONS |
| 3 | Aggregation context | Column in GROUP BY → DIMENSIONS; column in SUM/AVG/COUNT → METRICS |
| 4 | Naming conventions | `_id`/`_key`/`_code`/`_name`/`_date`/`_type`/`_status`/`is_`/`has_` → DIMENSIONS; `_amount`/`_qty`/`_total`/`_pct`/`_price`/`_revenue`/`_cost` → FACTS |
| 5 | Data type fallback | VARCHAR/TEXT/CHAR/BOOLEAN/DATE/TIMESTAMP → DIMENSIONS; DECIMAL/DOUBLE/FLOAT → FACTS; INTEGER/LONG → check naming first |

### VQL View Type → Semantic View Mapping

| Denodo View Type | Semantic View Pattern | Complexity |
|---|---|---|
| Base View | Direct `tables:` entry with `base_table` | Simple |
| Selection View | `tables:` entry + `filters:` for WHERE clause | Simple |
| Join View | Multiple `tables:` entries + `relationships:` | Simple |
| Union View | Materialize as Snowflake VIEW, then reference | Manual |
| Intersection/Minus View | Materialize as Snowflake VIEW, then reference | Manual |
| Flatten View | Materialize with `LATERAL FLATTEN`, then reference | Manual |
| Aggregation View | GROUP BY cols → `dimensions:`, agg cols → `metrics:` | Translation |
| Interface View | Use interface name; extract underlying impl | Simple |

## Function Translation: VQL → Snowflake SQL

### Date & Time Functions

| VQL Function | Snowflake SQL | Complexity | Notes |
|---|---|---|---|
| `ADDDAY(date, n)` | `DATEADD('DAY', n, date)` | Translation | Arg order differs |
| `ADDMONTH(date, n)` | `DATEADD('MONTH', n, date)` | Translation | |
| `ADDYEAR(date, n)` | `DATEADD('YEAR', n, date)` | Translation | |
| `ADDHOUR(ts, n)` | `DATEADD('HOUR', n, ts)` | Translation | |
| `ADDMINUTE(ts, n)` | `DATEADD('MINUTE', n, ts)` | Translation | |
| `ADDSECOND(ts, n)` | `DATEADD('SECOND', n, ts)` | Translation | |
| `GETDAY(date)` | `DAY(date)` | Translation | |
| `GETMONTH(date)` | `MONTH(date)` | Translation | |
| `GETYEAR(date)` | `YEAR(date)` | Translation | |
| `GETHOUR(ts)` | `HOUR(ts)` | Translation | |
| `GETMINUTE(ts)` | `MINUTE(ts)` | Translation | |
| `GETSECOND(ts)` | `SECOND(ts)` | Translation | |
| `FORMATDATE('yyyy-MM-dd', date)` | `TO_VARCHAR(date, 'YYYY-MM-DD')` | Translation | Format tokens differ |
| `GETDAYOFWEEK(date)` | `MOD(DAYOFWEEK(date)+6, 7)+1` | Translation | VQL: 1=Monday; Snowflake: 0=Sunday |
| `GETDAYOFYEAR(date)` | `DAYOFYEAR(date)` | Translation | |
| `GETWEEK(date)` | `WEEKOFYEAR(date)` | Translation | |
| `GETQUARTER(date)` | `QUARTER(date)` | Translation | |
| `FIRSTDAYOFMONTH(date)` | `DATE_TRUNC('MONTH', date)` | Translation | |
| `LASTDAYOFMONTH(date)` | `LAST_DAY(date)` | Translation | |
| `FIRSTDAYOFWEEK(date)` | `DATE_TRUNC('WEEK', date)` | Translation | Snowflake week starts Sunday by default |
| `LASTDAYOFWEEK(date)` | `DATEADD('DAY', 6, DATE_TRUNC('WEEK', date))` | Translation | |
| `TRUNC(date, 'MONTH')` | `DATE_TRUNC('MONTH', date)` | Translation | |

### String & Scalar Functions

| VQL Function | Snowflake SQL | Complexity | Notes |
|---|---|---|---|
| `COALESCE(a, b)` | `COALESCE(a, b)` | Simple | Identical |
| `NVL(a, b)` | `NVL(a, b)` | Simple | Identical |
| `NULLIF(a, b)` | `NULLIF(a, b)` | Simple | Identical |
| `SUBSTRING(s, start, len)` | `SUBSTRING(s, start, len)` | Simple | Identical |
| `TRIM(s)` / `LTRIM(s)` / `RTRIM(s)` | Same | Simple | Identical |
| `UPPER(s)` / `LOWER(s)` | Same | Simple | Identical |
| `REPLACE(s, old, new)` | `REPLACE(s, old, new)` | Simple | Identical |
| `CONCAT(a, b)` | `CONCAT(a, b)` | Simple | Identical |
| `CAST(x AS LONG)` | `CAST(x AS BIGINT)` | Translation | Type name differs |
| `CAST(x AS DOUBLE)` | `CAST(x AS FLOAT)` | Translation | Type name differs |
| `REGEXP(s, pattern)` | `REGEXP_LIKE(s, pattern)` | Translation | VQL pattern dialect may differ |

### Aggregate & Window Functions

| VQL Function | Snowflake SQL | Complexity | Notes |
|---|---|---|---|
| `SUM()` / `AVG()` / `COUNT()` / `MIN()` / `MAX()` | Same | Simple | Identical |
| `ROW_NUMBER() OVER (...)` | Same | Simple | Identical |
| `RANK() OVER (...)` | Same | Simple | Identical |
| `DENSE_RANK() OVER (...)` | Same | Simple | Identical |
| `LAG(col, n) OVER (...)` | Same | Simple | Identical |
| `LEAD(col, n) OVER (...)` | Same | Simple | Identical |
| `CASE WHEN ... END` | Same | Simple | Identical |

### Manual-Required Functions

| VQL Function | Snowflake Equivalent | Notes |
|---|---|---|
| `REMOVEACCENTS(s)` | No native equivalent | Requires custom UDF |
| `SIMILARITY(a, b)` | No native equivalent | Requires UDF (edit distance / Jaro-Winkler) |
| `NEST(field)` | No native equivalent | Denodo array aggregation |
| `UNNEST(array_field)` | `LATERAL FLATTEN(input => col)` | Syntax completely different |
| `FLATTEN(col)` | `LATERAL FLATTEN(input => col)` | Verify field path mapping |

### Denodo Complexity Classification

| Complexity | Criteria | Action |
|---|---|---|
| **Simple** | Direct column references, standard aggregates (`SUM`/`AVG`/`COUNT`/`MIN`/`MAX`), 1:1 functions (`COALESCE`/`NVL`/`NULLIF`/`UPPER`/`LOWER`/`TRIM`/`SUBSTRING`/`REPLACE`/`CONCAT`), `CASE WHEN`, basic arithmetic, window functions (`ROW_NUMBER`/`RANK`/`DENSE_RANK`/`LAG`/`LEAD`), Base/Selection/Join views | Auto-convert |
| **Translation** | VQL date functions (`ADDDAY`/`GETMONTH`/`FORMATDATE`/etc.), `CAST` with Denodo-specific types (`LONG`→`BIGINT`, `DOUBLE`→`FLOAT`), `REGEXP`, parameterized views (`$parameter` references), Aggregation views (GROUP BY mapping) | Apply translation table |
| **Manual** | `FLATTEN`/`UNNEST`/`NEST` operations, `REMOVEACCENTS`/`SIMILARITY`, custom Java/VBScript functions, `CONTEXT` clause usage, 3+ nested subqueries, Union/Intersection/Minus views, WEBSERVICE/CUSTOM data sources | Flag for manual review |

## SAP Business Objects → Snowflake Semantic View

### Core Concepts

| BO Concept | BO Structure | Snowflake Semantic View | Notes |
|---|---|---|---|
| Universe Connection | Data Foundation connection (driver, server, database, schema) | `TABLES` clause — database, schema, table | Parse connection string to confirm target Snowflake database/schema |
| Physical Table | Data Foundation table entry | `TABLES` entry with `base_table` | Direct mapping if table exists in Snowflake |
| Derived Table | SQL-defined virtual table in Data Foundation | Create as Snowflake `VIEW`, then reference in `TABLES` | Must materialize — cannot inline into semantic view |
| Join (1:1 or 1:N) | Data Foundation join (expression, cardinality, outer flag) | `RELATIONSHIPS` entry | `1:1` → `one_to_one`, `1:N` → `many_to_one` |
| Join (N:N) | Many-to-many join | Not directly supported | Flag — requires bridge table or denormalization |
| Context | Named join path (activates subset of joins) | May require separate semantic views | One semantic view per context is cleanest for multi-fact schemas |
| Class | Business Layer folder | Organizational grouping | Use class hierarchy to split large universes into focused semantic views |
| Dimension Object | `type: dimension`, SELECT expression | `DIMENSIONS` | Direct mapping. Resolve `@functions` in SELECT first |
| Detail Object | `type: detail`, linked to parent Dimension | `DIMENSIONS` (dependent column) | Include alongside parent dimension. Add parent reference in `COMMENT` |
| Measure Object | `type: measure`, SELECT with aggregate | `METRICS` (if aggregate) or `FACTS` (if bare column) | Check for SUM/COUNT/AVG/MIN/MAX in SELECT |
| Filter (predefined) | Named WHERE clause object | `FILTERS` in semantic view | Map condition directly. Strip `@Prompt` calls |
| Hierarchy | Ordered list of Dimensions for drill-down | Document in `COMMENT` fields | No direct semantic view equivalent for drill paths |
| List of Values (LOV) | Enumerated values for a Dimension | Documentation or `SYNONYMS` | Useful for filter hint documentation |

### @Function Translation

BO universe objects use `@functions` — BO-specific macros in SELECT and WHERE expressions that must be resolved before generating Snowflake SQL.

| BO @Function | Purpose | Snowflake Translation | Complexity |
|---|---|---|---|
| `@Aggregate_Aware(agg1, agg2, ...)` | Aggregate navigation — returns first available pre-aggregated table | Use most granular (last) argument; document alternatives | Translation |
| `@Prompt('Label','type','lov',mono/multi,...)` | Runtime user input prompt | Remove — becomes a semantic view filter or session variable | Translation (simple) / Manual (multi-value) |
| `@Select(Class\Object)` | References another object's SELECT by path | Inline the referenced SQL. Recursively resolve nested chains | Translation (1 level) / Manual (2+ levels) |
| `@Variable('BOUSER')` | BO session variable | `CURRENT_USER()` for BOUSER/DBUSER. Other variables require manual mapping | Translation |
| `@Derived_Table(DT_name)` | References a Data Foundation Derived Table | Replace with actual SQL as CTE or Snowflake VIEW | Translation |
| `@Where(Class\Object)` | References another object's WHERE clause | Inline the referenced filter condition | Translation |
| `@Script('language','script',args)` | VBScript/JavaScript custom logic (legacy) | No equivalent — rewrite in SQL or Snowflake UDF | **Manual** |

### BO SQL Functions → Snowflake SQL

Many BO universe SELECT expressions use database-vendor SQL functions. The translation depends on the original universe's target RDBMS. Common patterns:

| BO / Source SQL Function | Snowflake SQL | Complexity | Notes |
|---|---|---|---|
| `TO_CHAR(date, 'format')` | `TO_VARCHAR(date, 'format')` | Translation | Oracle-style |
| `NVL(a, b)` | `NVL(a, b)` | Simple | Identical |
| `DECODE(expr, val1, r1, ..., default)` | `CASE expr WHEN val1 THEN r1 ... ELSE default END` | Translation | Oracle-style |
| `SUBSTR(s, start, len)` | `SUBSTR(s, start, len)` | Simple | Identical |
| `SYSDATE` | `CURRENT_TIMESTAMP()` | Translation | Oracle-style |
| `GETDATE()` | `CURRENT_TIMESTAMP()` | Translation | SQL Server-style |
| `DATEADD(interval, n, date)` | `DATEADD(interval, n, date)` | Simple | SQL Server-style — identical in Snowflake |
| `DATEDIFF(interval, d1, d2)` | `DATEDIFF(interval, d1, d2)` | Simple | SQL Server-style — identical |
| `CONVERT(type, expr)` | `CAST(expr AS type)` | Translation | SQL Server-style |
| `ISNULL(expr, alt)` | `NVL(expr, alt)` or `COALESCE(expr, alt)` | Translation | SQL Server-style |
| `ToDate(string, 'format')` | `TO_DATE(string, 'format')` | Translation | BO-specific |
| `DaysBetween(d1, d2)` | `DATEDIFF('DAY', d1, d2)` | Translation | BO-specific |
| `MonthsBetween(d1, d2)` | `DATEDIFF('MONTH', d1, d2)` | Translation | BO-specific |
| `YearsBetween(d1, d2)` | `DATEDIFF('YEAR', d1, d2)` | Translation | BO-specific |
| `LastDayOfMonth(date)` | `LAST_DAY(date)` | Translation | BO-specific |
| `Quarter(date)` | `QUARTER(date)` | Translation | BO-specific |
| `DayOfWeek(date)` | `DAYOFWEEK(date)` | Translation | BO-specific |
| `SUM()` / `AVG()` / `COUNT()` / `MIN()` / `MAX()` | Same | Simple | Identical |
| `UPPER()` / `LOWER()` / `TRIM()` | Same | Simple | Identical |

### BO Complexity Classification

| Complexity | Criteria | Action |
|---|---|---|
| **Simple** | Direct column references, standard aggregates (`SUM`/`AVG`/`COUNT`/`MIN`/`MAX`), Snowflake-compatible scalars (`UPPER`/`LOWER`/`TRIM`/`SUBSTR`/`NVL`/`COALESCE`/`REPLACE`/`CONCAT`/`ROUND`/`ABS`/`CASE`), simple `DECODE` (≤3 branches), Dimension/Detail objects with bare column SELECT | Auto-convert |
| **Translation** | `@Select`/`@Where`/`@Variable`/`@Derived_Table` (single-level), `@Aggregate_Aware` (≤2 arguments), simple `@Prompt` (mono-value), `DECODE` (4+ branches → CASE), BO-specific date functions (`ToDate`/`DaysBetween`/`MonthsBetween`/`YearsBetween`/`LastDayOfMonth`), Oracle-style functions (`TO_CHAR`/`SYSDATE`/`DECODE`), SQL Server-style functions (`GETDATE`/`CONVERT`/`ISNULL`), Measures with standard aggregation wrappers | Apply translation table |
| **Manual** | `@Script()` (VBScript/JavaScript), `@Prompt` with multi-value or constrained LOV, nested `@Select` chains (2+ levels), `@Aggregate_Aware` with 3+ arguments, correlated subqueries in WHERE, Contexts requiring separate semantic views, N:N joins requiring bridge tables, Derived Tables with complex SQL (UNION/subqueries), expressions referencing BO-specific runtime variables beyond BOUSER | Flag for manual review |
