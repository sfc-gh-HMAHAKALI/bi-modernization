# Snowflake Semantic View YAML Specification

## YAML Structure

```yaml
name: SEMANTIC_VIEW_NAME

tables:
  - name: LOGICAL_TABLE_NAME          # Used in expressions (e.g., ORDERS.amount)
    base_table:
      database: DATABASE_NAME
      schema: SCHEMA_NAME
      table: PHYSICAL_TABLE_NAME
    primary_key: COLUMN_NAME           # Required for relationships

relationships:
  - name: relationship_name
    left_table: LEFT_LOGICAL_NAME
    right_table: RIGHT_LOGICAL_NAME
    relationship_columns:
      - left_column: LEFT_COL
        right_column: RIGHT_COL
    relationship_type: many_to_one     # many_to_one | one_to_many | one_to_one | many_to_many
    join_type: left_outer              # left_outer (default) | inner | full_outer

dimensions:
  - name: DIMENSION_NAME
    synonyms:
      - "business friendly name"
      - "alternate name"
    expr: LOGICAL_TABLE.COLUMN         # SQL expression referencing logical table names
    data_type: VARCHAR                 # VARCHAR | NUMBER | DATE | TIMESTAMP | BOOLEAN
    description: "What this dimension represents"
    sample_values:                     # Optional but improves Cortex Analyst accuracy
      - "Value 1"
      - "Value 2"

facts:
  - name: FACT_NAME                    # Raw, non-aggregated numeric columns
    synonyms:
      - "alternate name"
    expr: LOGICAL_TABLE.COLUMN
    data_type: NUMBER
    description: "What this fact represents"

metrics:
  - name: METRIC_NAME                  # Aggregated expressions
    synonyms:
      - "business name"
    expr: SUM(LOGICAL_TABLE.COLUMN) / NULLIF(SUM(LOGICAL_TABLE.OTHER_COL), 0)
    data_type: NUMBER
    description: "What this metric calculates"
    default_aggregation: sum           # Optional: sum | avg | count | min | max

filters:
  - name: FILTER_NAME
    synonyms:
      - "filter alias"
    expr: LOGICAL_TABLE.COLUMN = 'value'
    description: "When to apply this filter"
```

## Complete Example

```yaml
name: PROVIDER_REVENUE_ANALYSIS

tables:
  - name: CLAIMS
    base_table:
      database: ANALYTICS
      schema: BILLING
      table: FACT_CLAIMS
    primary_key: CLAIM_ID
  - name: PROVIDERS
    base_table:
      database: ANALYTICS
      schema: CRM
      table: DIM_PROVIDERS
    primary_key: PROVIDER_ID

relationships:
  - name: claims_to_providers
    left_table: CLAIMS
    right_table: PROVIDERS
    relationship_columns:
      - left_column: PROVIDER_ID
        right_column: PROVIDER_ID
    relationship_type: many_to_one

dimensions:
  - name: PROVIDER_NAME
    synonyms: ["physician name", "doctor name"]
    expr: PROVIDERS.PROVIDER_NAME
    data_type: VARCHAR
  - name: CLAIM_STATUS
    synonyms: ["billing status", "payment status"]
    expr: CLAIMS.CLAIM_STATUS
    data_type: VARCHAR
  - name: CLAIM_DATE
    synonyms: ["service date", "date of service"]
    expr: CLAIMS.SERVICE_DATE
    data_type: DATE

facts:
  - name: BILLED_AMOUNT
    synonyms: ["charge amount", "total billed"]
    expr: CLAIMS.BILLED_AMOUNT
    data_type: NUMBER
  - name: PAID_AMOUNT
    synonyms: ["reimbursement", "payment received"]
    expr: CLAIMS.PAID_AMOUNT
    data_type: NUMBER

metrics:
  - name: REVENUE_PER_HOUR
    synonyms: ["rev per hour", "hourly revenue"]
    expr: SUM(CLAIMS.PAID_AMOUNT) / NULLIF(SUM(CLAIMS.WORK_HOURS), 0)
    data_type: NUMBER
    description: "Total paid amount divided by total work hours"
  - name: NET_COLLECTION_RATE
    synonyms: ["collection rate", "NCR"]
    expr: SUM(CLAIMS.PAID_AMOUNT) / NULLIF(SUM(CLAIMS.BILLED_AMOUNT), 0)
    data_type: NUMBER
    description: "Ratio of collected to billed revenue"
```

## Deployment

### From a Stage

```sql
-- Upload YAML to stage
PUT file:///path/to/semantic_view.yaml @my_stage/semantic_views/ AUTO_COMPRESS=FALSE;

-- Create semantic view from YAML
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'DATABASE.SCHEMA.VIEW_NAME',
  '@my_stage/semantic_views/semantic_view.yaml'
);
```

### Deploy Multiple (Sequential Loop)

```python
# No bulk API — must deploy one at a time
import snowflake.connector

conn = snowflake.connector.connect(connection_name='my_connection')
cursor = conn.cursor()

yaml_files = ['view1.yaml', 'view2.yaml', 'view3.yaml']
for yaml_file in yaml_files:
    view_name = yaml_file.replace('.yaml', '').upper()
    fqn = f'DATABASE.SCHEMA.{view_name}'
    try:
        cursor.execute(f"""
            CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
                '{fqn}',
                '@stage/semantic_views/{yaml_file}'
            )
        """)
        print(f"Deployed: {fqn}")
    except Exception as e:
        print(f"Failed: {fqn} — {e}")
```

### From DDL (Alternative)

```sql
CREATE OR REPLACE SEMANTIC VIEW DATABASE.SCHEMA.VIEW_NAME
  TABLES (
    CLAIMS AS DATABASE.SCHEMA.FACT_CLAIMS PRIMARY KEY (CLAIM_ID),
    PROVIDERS AS DATABASE.SCHEMA.DIM_PROVIDERS PRIMARY KEY (PROVIDER_ID)
  )
  RELATIONSHIPS (
    claims_to_providers AS
      CLAIMS (PROVIDER_ID) REFERENCES PROVIDERS (PROVIDER_ID)
  )
  DIMENSIONS (
    PROVIDERS.PROVIDER_NAME AS provider_name,
    CLAIMS.CLAIM_STATUS AS claim_status
  )
  METRICS (
    SUM(CLAIMS.PAID_AMOUNT) AS total_paid,
    COUNT(DISTINCT CLAIMS.CLAIM_ID) AS claim_count
  );
```

## Validation

### Using cortex reflect

```bash
cortex reflect semantic_view.yaml
```

This validates YAML syntax and semantic model structure before deployment.

### Post-Deployment Validation

```sql
-- Export to YAML to verify what was deployed
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DATABASE.SCHEMA.VIEW_NAME');

-- Test with Cortex Analyst
-- Use cortex analyst query or Snowsight to ask natural language questions

-- Query via Standard SQL
SELECT
  provider_name,
  AGG(total_paid) as revenue
FROM DATABASE.SCHEMA.VIEW_NAME
GROUP BY ALL
ORDER BY revenue DESC
LIMIT 10;
```

## Constraints & Gotchas

### Column Limit
Semantic views work best with **~50-100 total columns** across all tables for Cortex Analyst. Large BI data sources should be split into focused, domain-specific semantic views.

**Splitting strategy**: Group by business domain (e.g., Revenue, Utilization, Compliance) rather than by source table.

### No Bulk API
No batch `CREATE SEMANTIC VIEW` — must call `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` one at a time in a scripted loop. Snowflake has confirmed no plans to add batch support.

### Expression Requirements
- All expressions must reference **logical table names** (the `name` in the tables clause), not physical table names
- Metric expressions must be aggregated (SUM, COUNT, AVG, MIN, MAX, etc.)
- Dimension expressions must be non-aggregated (row-level)
- Use `NULLIF(denominator, 0)` in division metrics to prevent divide-by-zero
- String literals in expressions need proper quoting

### Synonyms Best Practices
- Include business-friendly names users might actually say
- Include abbreviations (e.g., "NCR" for Net Collection Rate)
- Include alternative phrasings ("how much was paid" → PAID_AMOUNT)
- 2-5 synonyms per field is typical

### Sample Values
Adding `sample_values` to dimensions significantly improves Cortex Analyst accuracy:
```yaml
dimensions:
  - name: REGION
    sample_values: ["North America", "EMEA", "APAC", "LATAM"]
```

### dbt Integration
The `Snowflake-Labs/dbt_semantic_view` package (v1.0.3+) adds a `semantic_view` materialization to dbt:
```yaml
# In dbt model config
config:
  materialized: semantic_view
```

### Bidirectional with Tableau
After deploying a semantic view, generate a `.tds` file for Tableau consumption:
```sql
SELECT SYSTEM$EXPORT_TDS_FROM_SEMANTIC_VIEW('DATABASE.SCHEMA.VIEW_NAME');
```

### Permissions Required
```sql
-- To create semantic views:
GRANT CREATE SEMANTIC VIEW ON SCHEMA database.schema TO ROLE my_role;

-- To query semantic views:
GRANT USAGE ON SEMANTIC VIEW database.schema.view_name TO ROLE analyst_role;
-- Plus SELECT on the underlying tables

-- To use Cortex Analyst with semantic views:
-- Users need USAGE on the semantic view + SELECT on underlying tables
```
