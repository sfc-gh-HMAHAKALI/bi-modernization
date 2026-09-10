# Calculation translation — Tableau to Snowflake SQL and pandas

Every table below has three columns: the Tableau expression, the **Snowflake SQL** translation (the default), and the **pandas** translation (for result sets already small enough that a round trip is not worth it).

**Why SQL is the default.** The data is in Snowflake. A pandas translation of a FIXED LOD requires `SELECT *` into app memory before the aggregation runs, so app latency and memory scale with the table rather than with what you display. Push aggregation into the warehouse and pull the result set. Use pandas when you have already aggregated and are shaping a few hundred rows for display, or when the logic is genuinely hard to express in SQL.

The pandas column in the tables below exists for that second case. It is a fallback, not a menu of equals — if you find yourself using it for a LOD over a full table, you have reintroduced the problem this skill exists to prevent.

Read the **order of operations** section last but treat it as the most important one — it is where migrated dashboards silently disagree with the workbook they replaced.

## Row-level and aggregate formulas

| Tableau | Snowflake SQL | pandas |
|---|---|---|
| `IF [Profit] >= 0 THEN "Profitable" ELSE "Loss" END` | `CASE WHEN profit >= 0 THEN 'Profitable' ELSE 'Loss' END` | `np.where(df.profit >= 0, 'Profitable', 'Loss')` |
| Nested `IF`/`ELSEIF` chain | `CASE WHEN … THEN … WHEN … THEN … ELSE … END` | `np.select([c1, c2], [v1, v2], default=v3)` |
| `ZN([Sales])` | `ZEROIFNULL(sales)` | `df.sales.fillna(0)` |
| `IFNULL([a], [b])` | `COALESCE(a, b)` | `df.a.fillna(df.b)` |
| `SUM([Profit]) / SUM([Sales])` | `SUM(profit) / NULLIF(SUM(sales), 0)` | `df.profit.sum() / df.sales.sum()` |
| `COUNTD([Customer ID])` | `COUNT(DISTINCT customer_id)` | `df.customer_id.nunique()` |
| `DATETRUNC('month', [Order Date])` | `DATE_TRUNC('month', order_date)` | `df.order_date.dt.to_period('M')` |
| `DATEDIFF('day', [a], [b])` | `DATEDIFF('day', a, b)` | `(df.b - df.a).dt.days` |
| `[Region] = "West"` | `region = 'West'` | `df.region.eq('West')` |

### Functions whose name survives translation but whose meaning does not

These are the mappings that produce a working query and a wrong number. Check each one you carry over.

| Tableau | Snowflake SQL | pandas | Why it bites |
|---|---|---|---|
| `LOG([Sales])` | `LN(sales)` | `np.log(df.sales)` | **Tableau's `LOG` is base 10 only when given a second argument; bare `LOG(x)` is base 10.** Snowflake's `LOG(x)` requires two arguments — `LOG(base, x)`. Natural log is `LN`. Map Tableau `LOG(x)` → `LOG(10, x)` and Tableau `LN(x)` → `LN(x)`. Getting this wrong scales every value silently. |
| `ATTR([Rep Email])` | `MIN(rep_email)` | `df.groupby(...).rep_email.min()` | `ATTR` returns the value if unique in the group and `*` otherwise. `MIN` never signals ambiguity. If the workbook relied on seeing `*`, add `CASE WHEN COUNT(DISTINCT rep_email) > 1 THEN '*' ELSE MIN(rep_email) END`. |
| `SIZE()` | `COUNT(*) OVER (PARTITION BY <viz grain>)` | `df.groupby(keys).transform('size')` | `SIZE()` is a table calculation — it counts rows *in the partition of the rendered viz*, not in the table. The `PARTITION BY` must mirror the marks-card grain, not the source grain. |
| `RANK_PERCENTILE([Sales])` | `PERCENT_RANK() OVER (ORDER BY SUM(sales))` | `df.sales.rank(pct=True)` | Percentile of rank, not of value. Do not substitute `PERCENTILE_CONT`. |
| `INT([Sales] / [Denom])` | `FLOOR(SUM(sales) / NULLIF(SUM(denom), 0))` | `np.floor(...)` | Tableau's `INT` truncates toward zero; `FLOOR` rounds toward negative infinity. They disagree on negatives — use `TRUNC` when values can be negative. |
| `MEDIAN([Sales])` | `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sales)` | `df.sales.median()` | Needs `WITHIN GROUP`, and windowed form needs `... OVER (PARTITION BY region)` — it cannot take a bare `OVER` without `WITHIN GROUP`. |

Every SQL form in the table above was compile-checked and value-checked on Snowflake, including that `FLOOR(SUM(sales) / NULLIF(SUM(denom), 0))` yields null rather than raising when the denominator sums to zero.

**Division is a parity trap, and the obvious Snowflake helper is the wrong one.** Tableau returns null when a denominator is zero; Snowflake raises *Division by zero*. The fix is `a / NULLIF(b, 0)`, which yields null and matches Tableau.

Do **not** reach for `DIV0` or `DIV0NULL` here: both return **0**, not null (`DIV0(10, 0)` = 0, `DIV0NULL(10, 0)` = 0 — `DIV0NULL` only additionally treats a null divisor as zero). Substituting zero for a blank changes every downstream aggregate, because a zero is counted and averaged while a null is skipped. Use `DIV0` only when the workbook genuinely wanted zero.

## LOD expressions

A LOD expression computes at a grain other than the visualization's. In SQL that is a window function when the result broadcasts back to rows, and a subquery when the grain is genuinely different.

### FIXED — a grain independent of the viz

```
{ FIXED [Customer ID] : MIN([Order Date]) }
```

```sql
MIN(order_date) OVER (PARTITION BY customer_id) AS customer_acquisition_date
```

```python
df['customer_acquisition_date'] = df.groupby('customer_id')['order_date'].transform('min')
```

Multiple dimensions extend the partition:

```
{ FIXED [Region], [Category] : AVG([Discount]) }
```

```sql
AVG(discount) OVER (PARTITION BY region, category) AS regional_cat_avg_discount
```

```python
df['regional_cat_avg_discount'] = df.groupby(['region', 'category'])['discount'].transform('mean')
```

FIXED ignores dimension filters but **respects context filters**. That is not a detail — see order of operations.

### INCLUDE — a finer grain, then aggregated up

```
{ INCLUDE [Customer ID] : SUM([Sales]) }     -- viz is at Region grain
```

Compute at the finer grain, then re-aggregate at the viz grain:

```sql
WITH finer AS (
  SELECT region, customer_id, SUM(sales) AS customer_sales
  FROM orders
  GROUP BY region, customer_id
)
SELECT region, AVG(customer_sales) AS avg_sales_per_customer
FROM finer
GROUP BY region
```

```python
finer = df.groupby(['region', 'customer_id'], as_index=False)['sales'].sum()
viz = finer.groupby('region', as_index=False)['sales'].mean()
```

The outer aggregate matters: `AVG({INCLUDE …: SUM(…)})` and `SUM({INCLUDE …: SUM(…)})` differ. Read which one the workbook wraps the LOD in.

### EXCLUDE — a coarser grain than the viz

```
{ EXCLUDE [Sub-Category] : SUM([Sales]) }    -- viz is at Region + Sub-Category grain
```

Aggregate over the viz grain, then window over the reduced dimension set — this broadcasts the coarser total onto each viz row, which is what Tableau shows:

```sql
SELECT region, sub_category,
       SUM(sales)                                AS sales,
       SUM(SUM(sales)) OVER (PARTITION BY region) AS region_sales,
       SUM(sales) / NULLIF(SUM(SUM(sales)) OVER (PARTITION BY region), 0) AS pct_of_region
FROM orders
GROUP BY region, sub_category
```

The nested `SUM(SUM(...))` is correct and intentional: the inner aggregate collapses to the viz grain, the outer window sums those group totals across the partition.

That idiom only works **inside a single `GROUP BY` query**, where the inner `SUM` is still an aggregate the outer window can consume. If you have already aggregated in an earlier CTE, the column is a plain value there — window it directly as `SUM(sales) OVER (PARTITION BY region)` and drop the inner wrapper.

```python
agg = df.groupby(['region', 'sub_category'], as_index=False)['sales'].sum()
agg['region_sales'] = agg.groupby('region')['sales'].transform('sum')
```

EXCLUDE depends on the viz's dimensions, so it must be recomputed per chart. It cannot be precomputed once like FIXED.

### Nested LOD — inner grain first

```
{ FIXED [Customer ID] : SUM({ INCLUDE [Order ID] : AVG([Sales]) }) }
```

```sql
WITH order_grain AS (
  SELECT customer_id, order_id, AVG(sales) AS avg_order_sales
  FROM orders
  GROUP BY customer_id, order_id
)
SELECT customer_id, SUM(avg_order_sales) AS nested_lod
FROM order_grain
GROUP BY customer_id
```

```python
inner = df.groupby(['customer_id', 'order_id'], as_index=False)['sales'].mean()
outer = inner.groupby('customer_id', as_index=False)['sales'].sum()
df = df.merge(outer.rename(columns={'sales': 'nested_lod'}), on='customer_id', how='left')
```

Evaluate innermost first, always. A nested LOD flattened into a single `GROUP BY` produces a plausible-looking wrong number.

## User functions are security, not formulas

Tableau's user functions look like ordinary calculations but usually implement access control, so they translate to Snowflake identity functions — and, where they gate rows, to a Snowflake policy rather than to app code.

| Tableau | Snowflake |
|---|---|
| `USERNAME()` | `CURRENT_USER()` |
| `FULLNAME()` | `CURRENT_USER()`, or a users table lookup |
| `ISMEMBEROF("group")` | `IS_ROLE_IN_SESSION('ROLE')`, or a membership mapping table |
| `USERDOMAIN()` | No direct equivalent — derive from the user or account if genuinely needed |

Streamlit in Snowflake queries with the caller's identity, so `CURRENT_USER()` resolves per viewer without any wiring.

A calculation like `IF USERNAME() = [Rep Email] THEN [Sales] END`, or a Tableau user filter, is a row-level security rule. Reimplementing it as a predicate in the app's SQL moves a security boundary into the client — see the row-level security section of `migration-qa.md` before translating one.

## Table calculations

Table calculations run on **already-aggregated** data, after the query. In SQL they are window functions over the aggregated result; the partition mirrors Tableau's "compute using" scope and the ordering mirrors the addressing direction.

| Tableau | Snowflake SQL | pandas |
|---|---|---|
| `RUNNING_SUM(SUM([Sales]))` | `SUM(sales) OVER (ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` | `df.sales.cumsum()` |
| `WINDOW_AVG(SUM([Sales]), -6, 0)` | `AVG(sales) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)` | `df.sales.rolling(7, min_periods=1).mean()` |
| `WINDOW_SUM(SUM([Sales]), FIRST(), LAST())` | `SUM(sales) OVER ()` | `df.sales.sum()` |
| `TOTAL(SUM([Sales]))` | `SUM(sales) OVER ()` | `df.sales.sum()` |
| `SUM([Sales]) / TOTAL(SUM([Sales]))` | `sales / NULLIF(SUM(sales) OVER (), 0)` | `df.sales / df.sales.sum()` |
| `LOOKUP(SUM([Sales]), -1)` | `LAG(sales) OVER (ORDER BY d)` | `df.sales.shift(1)` |
| `LOOKUP(SUM([Sales]), 1)` | `LEAD(sales) OVER (ORDER BY d)` | `df.sales.shift(-1)` |
| Percent change vs prior period | `(sales - LAG(sales) OVER (ORDER BY d)) / NULLIF(ABS(LAG(sales) OVER (ORDER BY d)), 0)` | `df.sales.pct_change()` |
| `RANK(SUM([Sales]), 'desc')` | `RANK() OVER (ORDER BY sales DESC)` | `df.sales.rank(ascending=False, method='min')` |
| `RANK_DENSE(SUM([Sales]), 'desc')` | `DENSE_RANK() OVER (ORDER BY sales DESC)` | `df.sales.rank(ascending=False, method='dense')` |
| `RANK_UNIQUE(SUM([Sales]), 'desc')` | `ROW_NUMBER() OVER (ORDER BY sales DESC)` | `df.sales.rank(ascending=False, method='first')` |
| `INDEX()` | `ROW_NUMBER() OVER (ORDER BY d)` | `np.arange(1, len(df) + 1)` |
| `PERCENTILE([Sales], 0.95)` | `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY sales) OVER (PARTITION BY <scope>)` | `df.sales.quantile(0.95)` |
| `WINDOW_STDEV(SUM([Sales]), FIRST(), LAST())` | `STDDEV(sales) OVER ()` | `df.sales.std()` |

**Rank flavors are not interchangeable.** Tableau's default `RANK()` is competition ranking (1, 2, 2, 4) — SQL `RANK()`. `RANK_DENSE()` is (1, 2, 2, 3) — SQL `DENSE_RANK()`. A "Top N" filter built on the wrong one returns a different row count when values tie, which is exactly the case a Top-N view is most likely to hit.

**`WINDOW_AVG(..., -6, 0)` is a 7-row window, not 6.** The bounds are inclusive of the current row. Off-by-one here shifts every point on a moving-average line.

**`PERCENTILE_CONT` needs an explicit partition to behave like Tableau.** Written bare as `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY sales)` it is an ordered-set aggregate over the whole result. Tableau's `PERCENTILE` evaluates within the "compute using" scope, so add `OVER (PARTITION BY …)` matching that scope — omit the partition only when the workbook computes across the entire table.

**`ROWS` versus `RANGE` matters for dates with gaps.** `ROWS BETWEEN 6 PRECEDING` counts rows; if the series is missing days, Tableau's row-based window also counts rows, so `ROWS` is usually right. Use `RANGE BETWEEN INTERVAL '6 days' PRECEDING` only when the workbook densifies the date axis.

## Sets, groups, and bins

These three appear in the workbook XML as their own element types rather than as calculations, so the inspector reports them separately. All three translate to plain SQL — the work is finding them, not writing them.

**A constant set** is a saved list of members. It becomes a boolean expression, and it participates in order of operations at stage 4 — *after* context filters and before dimension filters:

```sql
CASE WHEN sub_category IN ('Chairs', 'Phones') THEN 'In Set' ELSE 'Out of Set' END AS top_subcategories_set
```

**A computed set** is a set whose membership is a rule ("top 10 by sales"). It becomes a window function plus a predicate, and it must be evaluated at the set's own grain, not the viz grain:

```sql
WITH ranked AS (
    SELECT sub_category, SUM(sales) AS total,
           DENSE_RANK() OVER (ORDER BY SUM(sales) DESC) AS rk
    FROM orders GROUP BY sub_category
)
SELECT sub_category, CASE WHEN rk <= 10 THEN 'In Set' ELSE 'Out of Set' END AS top_10_set
FROM ranked
```

Use `DENSE_RANK` unless the workbook's "Top N" was configured to break ties — Tableau's default top-N keeps ties, and plain `RANK` skips numbers after a tie, which changes which members land inside the cutoff.

**A group** is a manual remap of dimension members. It becomes a `CASE` with the members enumerated, or a small join against a mapping table when the group has more than a dozen members — inline `CASE` arms past that point are a maintenance liability, not a translation.

**A bin** is a fixed-width bucket over a continuous measure. Tableau bins align to a bin size and an origin:

```sql
FLOOR(raw_val / 10) * 10 AS raw_val_bin      -- bin size 10, origin 0
```

Use `WIDTH_BUCKET(raw_val, 0, 100, 10)` only when you want a bucket *ordinal* rather than a bucket *floor*. The two produce different labels for the same data, and dashboards usually display the floor.

## Semi-additive measures do not survive a naive `SUM`

If a measure is a balance, a headcount, an inventory level, or any snapshot quantity, it is **semi-additive**: it sums across every dimension except time, where it must be taken at a point instead. Tableau workbooks usually enforce this with a `LAST()` table calculation, a context filter pinning the latest date, or a `{FIXED : MAX([Date])}` guard — none of which look like special handling in the XML.

A migration that replaces such a measure with `SUM(balance) GROUP BY month` triples a quarterly figure. Take the snapshot explicitly:

```sql
QUALIFY snapshot_date = MAX(snapshot_date) OVER (PARTITION BY account_id, DATE_TRUNC('month', snapshot_date))
```

Then sum the surviving rows across the non-time dimensions. When you see a measure named like a balance or a level, ask the user whether it is additive over time before translating it — this is one of the few cases where guessing is worse than a one-line question.

## Order of operations

Tableau applies filters and calculations in a fixed sequence. Reproducing it is the difference between a migration that matches and one that does not.

| # | Tableau stage | Where it goes in a query pipeline |
|---|---|---|
| 1 | Extract filters | Not applicable — data is live in Snowflake |
| 2 | Data source filters | `WHERE` in the base CTE, or baked into a view |
| 3 | **Context filters** | `WHERE` in a CTE that runs **before** any FIXED window |
| 4 | Sets, and Top N filters bound to a set | Resolve in the same CTE as context filters, before the FIXED windows |
| 5 | **FIXED LODs** | Window functions over the context-filtered CTE |
| 6 | Dimension filters | `WHERE` **after** the FIXED windows — a later CTE |
| 7 | INCLUDE / EXCLUDE LODs | Aggregation in the chart-level query |
| 8 | Measure filters | `HAVING`, or a `WHERE` on the aggregated result |
| 9 | Table calculations | Window functions over the aggregated result |

Stages 3 → 5 → 6 are the ones that break. A context filter narrows the population a FIXED LOD sees; a dimension filter does not. Collapse them into one `WHERE` and every FIXED value changes.

Sets sit between them (stage 4): if the inventory flags `<set>` or `<group>` elements, resolve them alongside the context filters rather than with the dimension filters, or the FIXED values shift again.

The skeleton that preserves the sequence:

```sql
WITH base AS (                      -- 2: data source filters
    SELECT * FROM analytics.public.orders
    WHERE order_date >= '2020-01-01'
),
context_filtered AS (               -- 3 + 4: context filters (bind params), sets
    SELECT * FROM base
    WHERE region IN (SELECT VALUE FROM TABLE(FLATTEN(input => PARSE_JSON(?))))
),
with_fixed AS (                     -- 5: FIXED LODs
    SELECT *,
           MIN(order_date) OVER (PARTITION BY customer_id) AS acquisition_date
    FROM context_filtered
),
dim_filtered AS (                   -- 6: dimension filters
    SELECT * FROM with_fixed
    WHERE category = ?
),
aggregated AS (                     -- 7: viz-grain aggregation
    SELECT DATE_TRUNC('month', order_date) AS month,
           SUM(sales) AS sales
    FROM dim_filtered
    GROUP BY 1
    HAVING SUM(sales) > ?           -- 8: measure filters
)
SELECT month, sales,                -- 9: table calculations
       AVG(sales) OVER (ORDER BY month ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma_7
FROM aggregated
ORDER BY month
```

When a workbook has no context filters, stages 3 and 6 merge safely and the pipeline collapses — but confirm that from the inventory (`<filter context='true'>`) rather than assuming it.

## Passing filter values safely

Filter values come from widgets, so they are user input. Pass them as bind parameters — the second argument to `conn.query` — never by string interpolation:

```python
conn = st.connection("snowflake")
df = conn.query(SQL, params=[json.dumps(selected_regions), category, min_sales])
df.columns = df.columns.str.lower()
```

For multi-select filters, either bind a JSON array and flatten it in SQL as above, or generate the right number of `?` placeholders for the selection length. Both keep the values out of the SQL text.

## Caching the query layer

Wrap query functions in `@st.cache_data` with a `ttl` so repeated widget interactions do not re-hit the warehouse. Cache the expensive source query and apply cheap filters to the result when the result set is small; cache per parameter set when the filter belongs in the `WHERE` clause. Do not wrap `st.connection` — it is already cached.
