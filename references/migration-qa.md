# Migration QA — traceability, parity verification, and anti-patterns

A migrated dashboard that renders is not a migrated dashboard. It is done when the numbers match the workbook it replaces, every source object has a known fate, and the differences that remain are deliberate and written down.

Users who know the old dashboard spot a 2% discrepancy immediately, and one wrong total costs more trust than a plain-looking chart.

## Every source object gets a disposition

The inventory from Step 1 is a checklist. Nothing on it may be silently dropped — an object the user cared about that quietly vanished is the failure mode that erodes confidence in the whole migration. Assign exactly one of:

| Disposition | Meaning |
|---|---|
| `exact` | Same semantics, materially the same behavior and appearance |
| `equivalent` | Different implementation, same user-observable analytical result |
| `improved` | Deliberate, user-benefiting difference that preserves analytical meaning |
| `partial` | The important behavior exists but not fully — state precisely what is missing |
| `blocked` | Required evidence, access, or platform capability is unavailable |
| `intentionally-omitted` | An explicit scope decision — record who approved it |

**Disposition is not a test result.** An untested mapping is never `exact` or `equivalent`; it is a claim you have not checked. Track validation separately, per dimension, using `not-run`, `passed`, `failed`, `partial`, or `blocked`:

| Source ID | Disposition | Semantic | Functional | Visual |
|---|---|---|---|---|
| `worksheet:monthly-trend` | exact | passed | passed | passed |
| `calculation:nested-cust-value` | partial | passed | n/a | n/a |
| `action:region-highlight` | equivalent | n/a | passed | not-run |

A blocked visual check must not hide a passed semantic test, and a semantic pass never implies visual parity. Avoid rolling these into one percentage — a single score hides exactly the failure you most need to see.

Use stable IDs derived from the object kind and its Tableau name (`dashboard:executive-overview`, `calculation:profit-ratio`), never a list index, so the ledger survives re-running the inventory.

Keep the ledger in the conversation as you work. When the migration is finished, **offer** to write it up:

> Reconciliation complete: 14 objects — 11 exact, 2 partial, 1 blocked. Want this written up as `migration/parity_report.md` for sign-off?

Write the file only if the user says yes. Some organizations need that artifact for a cutover decision; others just want the app.

## Reconciliation procedure

Work outside-in: totals first, then each grain the app displays, then the interaction states.

**Reconcile the SQL, not the app.** If the queries live in `sql/*.sql` (see `app-structure.md`), each one runs standalone — execute the file with the same parameter values the app would pass and compare its output to the workbook. That removes Streamlit, caching, and render code from the comparison entirely, so a discrepancy points at the translation rather than at three possible layers. It also means someone who knows the data but not Streamlit can verify your work, which is usually the person whose sign-off you actually need.

Reconcile through the app afterwards, not instead: matching SQL with a mis-wired filter still produces a wrong dashboard.

### 1. Grand totals, unfiltered

Compare each measure's total against the workbook with all filters cleared, taking the Tableau number from the workbook itself rather than from memory.

```sql
SELECT SUM(sales) AS sales, SUM(profit) AS profit, COUNT(*) AS rows,
       COUNT(DISTINCT customer_id) AS customers
FROM analytics.public.orders
```

A mismatch here is a data problem, not a translation problem — wrong table, a datasource filter you did not carry over, or an extract refreshed on a different date than the Snowflake table. Fix it before looking at anything else, because every downstream number inherits it.

### 2. Each displayed grain

| Symptom | Likely cause |
|---|---|
| Totals match, per-category values do not | Grain error — `GROUP BY` does not match the shelves |
| Per-category values match, total does not | Aggregating an aggregate: averaging averages instead of recomputing at the total grain |
| Row counts differ | Join fan-out, or a filter applied at the wrong stage |
| Distinct counts differ | `COUNT(DISTINCT …)` after a join that duplicated rows |

### 3. LOD values

FIXED LODs are the most common silent drift, and a FIXED value can be wrong on every row while the total still reconciles. So spot-check individual entities, not just aggregates:

- Pick three or four specific keys and compare the LOD value directly against Tableau.
- **Verify the context-filter interaction explicitly.** Apply a context filter in Tableau, note how the FIXED value moves, apply the same filter in the app. If it does not move, the context filter is being applied after the FIXED window instead of before.
- Confirm a *dimension* filter does **not** change FIXED values. If it does, that filter is in the wrong CTE.

### 4. Table calculations

- Compare the first and last points of any running total or moving average — off-by-one window bounds surface at the boundaries first.
- Find a tie in the data and confirm the rank flavor matches (`RANK` / `DENSE_RANK` / `ROW_NUMBER`). A Top-N view over tied values is exactly where the flavors diverge.
- Confirm the partition: a calc that restarts per category in Tableau needs `PARTITION BY category`, and a missing partition yields a plausible monotonic line that is simply wrong.

### 5. Interaction states

Build a row per critical *state*, not per page:

| ID | View | Initial state | Action | Tableau result | App result | Status |
|---|---|---|---|---|---|---|

Cover at minimum: default load; each filter and parameter independently; the filter combinations people actually use; single-select, multi-select, clear, and reset; chart selections and their downstream effects; navigation; sort, drill, tabs, and downloads; and the no-data, null, and error states. A filter applied at the wrong pipeline stage often reconciles alone and diverges only in combination.

Check the empty state deliberately — Tableau shows a blank viz for a filter combination with no rows, and the app must not raise instead.

### 6. Numeric tolerance

Decide tolerance from the source format and computation, and state it:

- **Exact** for counts, identifiers, and distinct counts.
- **Defined absolute or relative tolerance** for floating-point measures.
- Normalize only known presentational differences — thousands separators, currency symbols. **Never round away a discrepancy you cannot explain.**

For each failure, name the cause: source freshness, export grain, calculation translation, filter timing, timezone or locale, rounding, or an actual defect. "Close enough" is not a cause.

### 7. Visual comparison

Capture both sides under controlled conditions, or the comparison means nothing: same viewport dimensions and device pixel ratio, same data snapshot and filter state, same theme, animations finished, tooltips and dialogs captured as separate named states, lossless PNG.

```bash
# Pillow is a QA-time dependency only: this runs on a laptop, never inside
# Streamlit in Snowflake, so it does not affect what the deployed app needs.
python3 -m pip install Pillow
python3 <SKILL_DIR>/scripts/visual_compare.py \
  evidence/tableau/default.png evidence/streamlit/default.png \
  --output-dir evidence/visual/default
```

The script reports dimensions and a normalized pixel difference, and writes `side-by-side.png`, `overlay.png`, and `difference.png`. Use these to **locate** differences, never to decide correctness — a pixel score is not evidence of semantic parity, and a low score on a chart showing wrong numbers is worse than useless.

Review in this order: information architecture and reading order; zone geometry, alignment, and clipping; chart type, marks, scales, axes, legends; color domains, typography, number and date formatting; controls, selections, and empty/error states; then responsive behavior.

Label a deliberate improvement rather than trying to drive its pixel difference to zero.

When visual evidence is missing: request a reference image for each dashboard and state that lacks one, mark appearance `blocked`, and keep validating semantics meanwhile. XML zone geometry is not rendered visual evidence. Never synthesize a "reference" image from your own implementation, and never cite a run against unrelated images as parity evidence — that proves only that the tool executes.

### 8. Formatting

Compare currency symbols, decimal places, percent scaling (`0.42` versus `42%`), thousands separators, date granularity labels, and axis ranges. To someone reading a dashboard they know well, these are not cosmetic.

## Row-level security

Tableau user filters and `USERNAME()`-style calculations are **security controls**, not display logic. Reimplementing one as a `WHERE` clause in app code moves the boundary from the database to the client and creates a real data-exposure bug — anyone who can reach the app or its cache can reach data the filter was meant to hide.

- Recreate row-level security with Snowflake-native controls — a row access policy on the table, or a secure view — so the restriction holds regardless of what the app asks for.
- `USERNAME()` maps to `CURRENT_USER()`, and group membership (`ISMEMBEROF()`) to `IS_ROLE_IN_SESSION()` or a mapping table. Streamlit in Snowflake queries with the caller's identity, so these resolve per viewer.
- Never put a user-scoped predicate only in the app's SQL and call it equivalent.
- **Include every security-affecting input in the cache key.** A `@st.cache_data` function whose key omits the viewer will serve one user's rows to another. This is the most likely way a migration introduces a data leak.
- Verify with more than one test identity where authorized identities exist.

Note also that SiS viewers need `USAGE` on the `STREAMLIT` object *and* `SELECT` on the underlying tables. Tableau's extract-based permissions did not work that way, so someone who could see the Tableau dashboard may not be able to see the Streamlit one. Check before announcing a cutover; see the scaffolding skill's `operations.md` for the grants.

## When to deviate deliberately

Parity is the default, not a religion. Make an improvement only when it preserves analytical meaning **and** at least one of these holds: it fixes accessibility or responsive behavior; it removes duplicated controls or clutter; it makes loading, empty, or error states clearer; it improves performance without changing results; it replaces a desktop-only interaction with a discoverable equivalent; or the user asked for it.

Record the Tableau behavior, the new behavior, why it is better, and how to reverse it. Mark it `improved`, not `exact`. An unrecorded "improvement" is indistinguishable from a bug to whoever validates the numbers.

## Anti-patterns

| Anti-pattern | Correct pattern | Why |
|---|---|---|
| `df.apply(lambda row: …, axis=1)` | `np.where` / `np.select`, or a SQL `CASE` | Row-at-a-time Python; orders of magnitude slower and freezes the app on real data |
| `for i, row in df.iterrows():` | Vectorized assignment, or SQL | Same cost, and usually a sign the logic belongs in the query |
| `SELECT *` then aggregate in pandas | Aggregate in SQL, pull the result | Latency scales with the table instead of the view |
| Uncached query in the script body | `@st.cache_data(ttl=…)` on the query function | Re-queries the warehouse on every interaction |
| `@st.cache_data` on `st.connection` | Leave it alone | Already cached; wrapping it breaks the connection lifecycle |
| Cache key omitting the viewer identity | Include every security-affecting input | Serves one user's rows to another |
| f-string interpolation of widget values into SQL | Bind parameters as `conn.query`'s second argument | Injection, and it defeats result caching |
| Tableau user filter reimplemented as an app `WHERE` | Snowflake row access policy or secure view | Moves a security boundary into the client |
| Collapsing context and dimension filters into one `WHERE` | Separate CTEs, context before FIXED windows | Changes every FIXED LOD value |
| Flattening a nested LOD into one `GROUP BY` | Inner grain in a CTE, then the outer grain | Produces a wrong number that looks reasonable |
| `a / b` for a Tableau ratio | `a / NULLIF(b, 0)` | Snowflake raises on divide-by-zero where Tableau returns null. `DIV0`/`DIV0NULL` both return **0**, which is counted and averaged downstream where a null would be skipped |
| Reading uppercase Snowflake columns as-is | `df.columns = df.columns.str.lower()` after each query | Silent `KeyError` far from the query |
| `use_container_width=True` | `width="stretch"` | Deprecated |
| Color list built by row position | Color keyed by category value, with an explicit scale domain | Colors reshuffle when a filter changes the category set |
| `st.session_state` write plus unconditional `st.rerun()` | Guard with an equality check | Infinite rerun loop |
| Hand-rolled `st.query_params` around a widget | `bind="query-params"` with a `key` | Stale values on the run after a change |
| Missing `st.session_state` initialization | `if "k" not in st.session_state:` guard | `KeyError` on first load |
| Every widget triggering a full rerun | `@st.fragment` for chart-local controls | Whole-dashboard recompute on a local toggle |
| Expensive work inside `st.tabs` / `st.expander` | Gate on open state | Hidden content still computes |
| Adding Plotly without settling the EAI | Ask the user which integration to use before writing the charts | Deploy fails at runtime on package resolution |
| Dropping a needed dependency to avoid asking about an EAI | Ask; a missing package is not a simplification | Silent feature loss, or an ImportError at runtime |
| Files missing from `snowflake.yml` `artifacts` | Run the pre-flight check | Deploys clean, then dies on first import |
| Embedding the Tableau dashboard, or scraping its DOM | Rebuild from the workbook definition | An iframe is not a migration and inherits every Tableau dependency |

## Pre-deploy checklist

1. App runs locally against Snowflake, connection set via `SNOWFLAKE_DEFAULT_CONNECTION_NAME`.
2. Every inventoried object has a disposition; nothing silently dropped.
3. Reconciliation complete for every in-scope grain, with tolerances stated.
4. Row-level security reimplemented with Snowflake-native controls, and cache keys include viewer identity.
5. No anti-pattern from the table above present.
6. Every runtime file listed under `artifacts:`, verified by the pre-flight check in the scaffolding skill's `snowflake-deployment.md`.
7. `compute_pool` resolved from the account default and confirmed against `SHOW COMPUTE POOLS`.
8. A dependency file is present — `pyproject.toml` (recommended) or `requirements.txt`. If it lists anything beyond `streamlit[snowflake]`, `external_access_integrations` names a real integration the **user chose**, not an invented name.
9. Post-deploy `SHOW STREAMLITS LIKE '<name>' IN ACCOUNT` returns exactly one row.
10. The app opens in Snowsight and every chart renders with data.
11. **A viewer who is not you can open it and see data.** SiS viewers need `USAGE` on the `STREAMLIT` object *and* `SELECT` on the underlying tables. An app that works for the migrator and fails for the whole audience is the most common cutover surprise, because Tableau's extract-based permissions did not work this way. Check with a second identity, or at minimum enumerate the grants the audience's role needs.

## Offer the reconciliation as a Tableau workbook

Numeric reconciliation is more persuasive to the people signing off if they can see it in
the tool they already trust. After the numbers agree, **offer** to emit an audit workbook —
a small `.twb` whose worksheets query Snowflake directly and reproduce the aggregates at
each grain the app displays.

Why it changes the conversation:

- It removes you from the loop. The dashboard owner opens Tableau, points it at Snowflake,
  and sees the same totals — no need to trust a markdown table you wrote.
- It compares Tableau-to-Snowflake, not Tableau-to-your-Python. If the audit workbook and
  the original workbook agree, the SQL translation is right regardless of what the app does
  with the result.
- It survives the migration. Kept alongside the app, it re-runs later and catches drift
  after a schema change.

Keep it minimal and honest about what it proves: one worksheet per reconciled grain, a
crosstab of the grouping keys and the measures, pointed at the same table the app queries.
It verifies **the query translation**, not the app — chart rendering, filter wiring, and
interaction state are still verified by the procedure above.

This is an offer, not a step. Build it when the migration needs a sign-off artifact or when
the user is skeptical of the numbers; skip it for a small dashboard whose totals the user
already checked themselves. Never present it as a replacement for the reconciliation — it
is the same reconciliation in a form the audience can re-run.

## Calling it done

- **complete** — all critical semantics and flows passed; only immaterial visual differences remain.
- **complete-with-differences** — usable and verified, with documented intentional or partial differences and no implied claim of exact parity.
- **blocked** — missing access, evidence, or capability prevents a critical semantic or security validation.

Do not report completion because the app launches or resembles the default screenshot. When full parity is impossible, finish the best verified migration and state exactly what remains, why, and the smallest next action.

One operational note to pass on: SiS exposes no application logs. Runtime errors surface only in the app's own error banner, so anything you want to debug later must be logged into a table the app owns.
