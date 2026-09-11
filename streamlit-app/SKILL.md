---
name: streamlit-app
description: >
  Generate a multi-page Streamlit-in-Snowflake app to the Enterprise Dashboard Standard,
  translate calculations, reconcile numbers, and deploy. Load from the bi-modernization
  router.
---

## Step 8: Generate Streamlit App (if selected)

Build the app directly from the enriched inventory and the source BI file, following the
Enterprise Dashboard Standard below. Do NOT run the `generate-streamlit` CLI first — it
produces usable output only for simple single-datasource workbooks with explicit mark types
and no custom SQL, which is rare in practice. Complex workbooks (federated datasources,
custom SQL, calculated fields, crosstab layouts) produce sparse or broken output that always
fails validation and wastes a round-trip. The standard below IS the generator.

### Deep analysis of the source (do this BEFORE writing any code)

**From the enriched inventory** (`/tmp/bim_enriched.json`):
- Dashboard names and the worksheets they contain
- For each worksheet: `fields_by_datasource`, the mark type, and chart type
- All dimensions, measures/facts, and their names/expressions
- Parameters and their allowed values
- The `source_type` (tableau, powerbi, etc.)

**From the original source file** (TWB, PBIX, etc.) — re-read it directly to extract
what the parser missed:

For **Tableau (.twb / .twbx / .tds / .tdsx)** — do NOT hand-parse the XML. Run the
inspector, which already models all of it and reports what a naive parse misses:

```bash
# Human-readable migration report — read this first, it leads with the warnings
python3 -m modules.tableau.inspector "<source>" --format markdown

# Full structured detail
python3 -m modules.tableau.inspector "<source>" --format json > /tmp/bim_inspect.json
```

`parse --type tableau` uses the inspector automatically, so its output is also on the
inventory under `tableau_inspection`. Read these keys:

| Key | What it gives you |
|---|---|
| `translation` | Per-calculation LOD kind, table calcs, blockers, and a prescribed action |
| `filters` | Filter definitions with their order-of-operations stage |
| `layout` | Per-dashboard container tree with `st.columns` width ratios |
| `visual_styles` | Palettes, per-member colours, dashboard background colours |
| `worksheet_marks` | Mark type, rows/cols shelves, and the suggested Streamlit chart |
| `field_usage` | Which fields actually reach a dashboard, and which are stranded |

The inspector's warnings are the highest-value output. Surface them to the user before
building: data blending, non-Snowflake connections, orphan worksheets, high-complexity
calculations, member aliases, hand-written colour legends in text boxes, and fields that
never reach a dashboard. On the FBR reference workbook it reported that 117 of 222 fields
reach no dashboard — translating those would have created parity obligations for things
nobody looks at.

For **Power BI (.pbix / .pbit)**:
- Parse report page visuals for chart types and field bindings
- Extract DAX measures and their expressions

### Translate calculations before building anything

Numbers that disagree with the source are the only migration failure users care about.
Layout can be corrected later; a wrong total destroys trust immediately. So translate and
reconcile calculations FIRST.

**Load `references/calculation-translation.md` now and treat it as the authority.** It
carries the order-of-operations stages, the per-function translation table, and the SQL
forms, all compile- and value-checked on Snowflake. Do not translate from memory.

Work the `tableau_inspection.translation` list in descending `complexity_score`: that
ordering front-loads the calculations most likely to be wrong.

Three rules the reference does not cover, which decide whether the numbers land:

**Filter timing.** Tableau's order of operations is not SQL's: context filters and FIXED
LODs resolve BEFORE ordinary dimension filters, so a sidebar filter that looks like it
should narrow a FIXED LOD does not. If you apply the app's filters to the frame before
computing a FIXED-LOD equivalent, the number will differ and the difference will look
random. Decide per calculation which stage its filters belong to, and record that in the
fidelity report.

**Untranslatable functions.** When the inspector reports a `blockers` entry there is no SQL
equivalent — typically `SCRIPT_REAL`/`SCRIPT_STR` (R/Python integration), `MODEL_QUANTILE`,
`MODEL_PERCENTILE`, or a spatial function. Do not invent an approximation and ship it as
parity. Name the calculation, state that it is not translatable, and ask the user whether
to drop the field, precompute it upstream, or accept a documented gap.

**Aggregation grain and fan-out.** A join that duplicates rows inflates every `SUM`
downstream, and it looks like a plausible number rather than an error. When a plan or quota
table joins one-to-many against a fact, carry the plan value on exactly ONE row per group
and assert it — the reference app's test suite checks `plan carried on exactly one row per
month`. Ratios must be computed from re-aggregated numerator and denominator, never
averaged from per-row ratios.


### Build the Streamlit app — Enterprise Dashboard Standard

These rules are the codified output of building and browser-testing a 13-dashboard reference
app (FBR Finance). Every rule exists because a specific defect shipped without it, was found
by automated or visual testing, and took at least one fix round to close. Follow all of them
on every generation; they are not optional polish.

**File structure**: Generate `app.py` (shell, navigation, theme), `data.py` (all data loading
and aggregation), `pages_impl.py` (page rendering functions), and `metrics.py` (safe ratio
arithmetic). Keep `bim_ui/` as a sibling package containing reusable components. This
separation means a chart, a KPI card, and a grid cell cannot disagree about formatting,
because they all call the same code.

**1. Data layer**

If the user's Snowflake account has the source tables, generate `session.sql()` queries. If
not (demo mode), generate `@st.cache_data` functions with synthetic data matching the schema.
Synthetic data MUST be seeded per-team/per-dimension so reruns do not reshuffle numbers
(which makes cross-filtering look broken).

For plan/target columns stored at a different grain than detail rows, carry the value on
exactly ONE row per group (e.g. the first product row per month), not on every row. A
`SUM(plan)` over the detail frame must equal the monthly plan, not plan * product_count.
Validate this with: `df[df.plan > 0].groupby("month_label").size().unique()` must be `[1]`.

**2. Navigation**

Use two `st.radio()` groups in the sidebar — one for category, one for dashboard — not
`st.navigation()`/`st.Page()` (requires Streamlit 1.36+ and breaks on older builds). Do NOT
use separator strings ("---") as radio options; they render as clickable blank entries.

**3. Cross-filtering with exclude-self (CRITICAL)**

This is the single biggest gap between a static report and a BI tool. Implement it using a
FilterStore pattern backed by `st.session_state`:

- Each chart declares the dimension it EMITS (e.g. "team") and has a stable `key`.
- When asked for a filtered frame, apply every active filter EXCEPT the requesting chart's
  own dimension. This is "exclude-self": the Team chart stays fully visible and clickable
  even while it is filtering KPIs, trends, and grids by the selected team.
- Without exclude-self, clicking a bar collapses the chart to one bar and strands the user.
- Wire chart selections via `st.plotly_chart(fig, on_select="rerun", key=...)`. The returned
  event contains the selected points. Fingerprint each event and only write state when it
  differs from last time — writing unconditionally creates infinite rerun loops.
- KPIs and detail grids consume ALL filters (no exclude-self for non-emitting panels).
- Render active filters as visible chips with per-filter remove buttons and a clear-all.
- Give the user a Reset All Filters button in the sidebar.

**4. URL-shareable filter state**

Sync page, target parameter mode, and all filter selections to `st.query_params` so a filtered
view is a shareable link. On load, hydrate the FilterStore from the URL. Use a compat layer
for `st.query_params` vs `st.experimental_get_query_params` on older builds.

**5. Drill-down**

Support ordered dimension paths (Team -> Rep, Year -> Quarter -> Month). Track the drill path
in session state. Render a breadcrumb (Home > BC Sales) with clickable segments. Drilling pins
the parent value as a filter and switches the display grain to the child dimension.

**6. Tables — use native `st.dataframe`, NEVER hand-rolled HTML**

The first-generation app used HTML tables (`st.markdown(unsafe_allow_html=True)`) and burned
three fix rounds on sticky columns, unreachable Total columns, and sort/search. All of those
are free with `st.dataframe`:

- Derive `column_config` automatically from dtypes plus a format registry:
  `st.column_config.NumberColumn` for measures, `ProgressColumn` for attainment/percent
  columns, `LineChartColumn` for inline sparkline columns.
- Pin the label column (team, product) via `column_order` so it stays visible while scrolling.
- Add a Total row: SUM measures, AVERAGE ratios (summing four teams at 90% is not 360%).
- List-typed sparkline columns MUST be homogeneous: the Total row must use an empty list `[]`,
  not an empty string `""`, or PyArrow raises "cannot mix list and non-list, non-null values"
  and the grid crashes.
- When building sparkline series from month-name columns, sort by CALENDAR order (Jan, Feb,
  Mar...), not alphabetical order (Apr, Aug, Dec...). Alphabetical sort puts Feb before Jan
  and produces a sparkline showing the wrong trend direction. Detect month names and apply
  calendar ordering; accept an explicit `order` argument for non-obvious sequences like
  quarters.
- Add CSV and Excel download buttons below every grid (use `st.download_button`).

**7. Charts — Plotly with reference lines, not competing bar series**

- Use `plotly.graph_objects`, not `plotly.express`, for all dashboard charts. GO gives
  explicit control over traces, reference lines, and label positioning.
- Render plan/target as a dashed step-line (`shape="hv"`) overlaid on the actual bars, NOT
  as a second bar series. A second bar series doubles the mark count and makes "am I above
  plan" a height comparison instead of a glance.
- Add horizontal reference lines for thresholds (100% attainment line, quota targets).
- COMPUTE label headroom from the data: set `yaxis.range = [0, max_value * 1.18]` so outside
  value labels cannot be clipped by the plot edge. Also set `cliponaxis=False` on bar traces
  as a second line of defence. This was the single most common visual defect.
- Use semantic colors from the palette (actual, target, good, warn, bad) — never hardcode hex.
- Color bars by attainment status (green/amber/red) when the chart shows performance vs target.
- Set `dragmode=False` — dashboard charts should not pan on drag.
- Set `scrollZoom=False` in the config — scroll zoom on a dashboard hijacks page scrolling.
- Collapse long tails: more than 8 categories in a bar chart is unreadable. Aggregate the
  tail into an "Other" bucket, preserving the total. `max_categories=N` means N total bars
  including Other.
- For share-of-total, use horizontal ranked bars instead of donuts. Humans compare bar lengths
  far more accurately than pie angles, and donut outside-labels clip at the chart edge.

**8. KPI cards — styled HTML with sparkline and progress bar**

- Render as styled HTML `<div>` cards with CSS class `bim-kpi`, not plain `st.metric()`.
- Each card shows: label, value, delta vs target with semantic color (green up, red down),
  an inline SVG sparkline (9 data points for YTD trend), and an optional progress bar
  (thin SVG) showing attainment vs target.
- Support `higher_is_better=False` for metrics where an increase is bad (churn, cost).
  Getting this wrong produces a green arrow on a worsening number.
- Layout KPIs in rows of 4 (not 7). Seven equal columns squeezed values until they clipped.
  Four keeps values readable with the sidebar expanded at 900px viewport width.
- Labels MUST wrap (`overflow-wrap: break-word`), never truncate with ellipsis. A truncated
  label ("MTD BOOKI...") cannot be read; a wrapped label ("MTD BOOKINGS" on two lines) can.

**9. Provenance banner**

Every dashboard must show where its data came from: a strip below the header displaying
query time, row count, source tables, warehouse, and semantic view. In demo mode, it must
say "SYNTHETIC DATA — not connected to a live source" prominently. An enterprise viewer will
not act on a number they cannot trace.

**10. Empty and error states**

A filter combination that excludes everything must render "No data matches the current
filters" with a Clear Filters button — not a blank panel, not a raw traceback. Every chart
and grid rendering function must guard its input: if the frame is empty, render the empty
state and return. Use `states.guard(df, on_clear=store.clear)` as the standard opening.

**11. Theme and color system**

- Resolve colors from the source BI file's custom palette (e.g. Tebra's 24-color palette).
- Filter the palette for usability: reject colors below 3:1 contrast ratio against the
  background (invisible bars), reject greys and near-black (read as text/axis, not data),
  enforce perceptual separation between accepted colors (so adjacent categories in a stacked
  bar are distinguishable), and keep series colors clear of the status colors (a category
  should never be painted the same red the app uses to mean "missing target").
- When the brand palette cannot supply enough usable colors, top up from a curated extension
  palette first, then from muted hue rotation — never neon, never unsaturated.
- Status colors (good/warn/bad) are NOT drawn from the brand palette. Use tuned pairs:
  light-background (#1B7F3B, #B45309, #B4232C) and dark-background (#4ADE80, #FBBF24,
  #F87171). Verify WCAG AA contrast for every foreground/background pair.
- Emit `.streamlit/config.toml` for base theming (primaryColor, backgroundColor,
  secondaryBackgroundColor, textColor) so widgets and native dataframes are themed.
  Use a distinct `secondaryBackgroundColor` tinted from the primary so the sidebar reads
  as a separate surface — NOT the same color as the page background.
- Keep injected CSS minimal: only what `config.toml` cannot express (header band, KPI card
  structure, chips, provenance strip).

**12. Formatting — one code path, everywhere**

Build a format registry seeded from `visuals.json` `number_formats`, with name-based inference
as fallback. Every surface — KPI card, axis tick, tooltip, grid cell — formats through the
same registry. This prevents "$4.2M" in the KPI, "4200" on the axis, and "4,200.00" in the
grid for the same number.

- Date-like field names (CLOSED_MONTH, CLOSE_DATE, Period) must be detected and treated as
  text labels, not formatted as counts. The regex `(date|month|quarter|year|period|timestamp)`
  should guard before numeric inference.
- `$0` not `$0.0` for zero values in compact currency.
- Division that can hit zero (attainment, ACV, average deal) must use safe arithmetic that
  returns 0.0 rather than NaN, inf, or ZeroDivisionError. Centralise this in a `safe_ratio()`
  function. Do NOT use `df[col].replace(0, pd.NA).fillna(0.0)` — it triggers a pandas
  FutureWarning about object-dtype downcast that will become an error.

**13. Version tolerance (CRITICAL for local preview)**

The app targets the SiS container runtime (Streamlit 1.50+), but gets previewed locally in
whatever the developer has. A hard `AttributeError: module 'streamlit' has no attribute
'fragment'` at import time is the worst possible outcome. Build a compat layer:

- `st.fragment` -> `st.experimental_fragment` -> passthrough (no-op decorator). Losing
  fragments costs performance, never correctness.
- `st.query_params` -> `st.experimental_get_query_params`. Losing this disables URL state.
- `st.plotly_chart(on_select=...)` -> drop `on_select` kwarg. Losing this disables cross-
  filtering; the chart still renders.
- `st.dataframe(on_select=...)` -> same pattern.
- Material icon shortcodes (`:material/info:`) in `st.info(icon=...)` crash on pre-1.31
  builds. Drop the `icon` kwarg on those versions. Wrap ALL alert calls (info, warning,
  error) through the compat layer so the degradation notice itself cannot be the thing
  that crashes the app.
- `st.columns(gap=...)` -> drop `gap` kwarg on older builds.
- `st.rerun()` -> `st.experimental_rerun()` on pre-1.27 builds.
- Show a compatibility notice in the sidebar listing what is unavailable, but ONLY when
  features are actually missing — silent on the target runtime.

**14. Plotly compatibility floor (Plotly 5.9+)**

These rules prevent runtime errors in older Streamlit+Plotly environments:

- No `cornerradius` in bar markers (added in 5.19).
- No `legendgrouptitle`, `marker.pattern.fgopacity`, `minor` axis properties (5.15+).
- No duplicate keyword arguments in `update_layout()`. Use a `_base_layout()` function that
  accepts `**overrides` and merges them, so the collision cannot be expressed. Never
  `fig.update_layout(**BASE, showlegend=False)` when BASE already has `showlegend`.
- CSS `background-clip: text` renders as invisible text in Streamlit's webview. Never use it.
- For geographic data (Multipolygon marks), use `px.choropleth()`.
- For Square/Circle marks with color encoding, use `go.Heatmap()`.

**15. `st.fragment` for panel-scoped reruns**

Wrap each independent panel (the monthly chart, the breakdown charts, the detail grid) in
`@st.fragment` so interacting with one (clicking a bar, selecting a grid row) reruns only
that panel, not all dashboards. Global filters that affect every chart belong outside
fragments and trigger full reruns.

### Preview

Save the app files to `/tmp/bim_streamlit/` and launch a local preview:

```bash
cd /tmp/bim_streamlit && python3 -m streamlit run app.py --server.port 8501 --server.headless true
```

Use `python3 -m streamlit` rather than bare `streamlit` — the PATH may resolve to an older
build (e.g. miniconda's Streamlit 1.27 while pip installed 1.63).

Open the browser and verify:
1. All dashboards render without tracebacks (visit every page in every category)
2. KPI values are real numbers (not NaN, not blank, not "$0.0")
3. Grids render with sparklines and progress bars (no Arrow serialization crash)
4. Cross-filtering works: click a bar, chips appear, other charts update, the clicked chart
   stays fully visible (exclude-self)
5. The compat notice appears if on an older Streamlit, or is absent on 1.50+
6. Toggle the parameter switch — labels and chart titles update consistently

If issues are found, fix them before presenting to the user.

**ALWAYS offer a local preview before deployment.**

### Step 8c: Reconcile the numbers (gate — do this before deploying)

A dashboard that looks right and totals wrong is worse than no dashboard, because people
act on it. Reconcile before deploying, and follow `references/migration-qa.md` for the full
procedure. Reconciliation is a numeric exercise; a visual comparison cannot substitute for it.

**Run the automated render gate first.** It is cheap and catches the failures that would
otherwise waste a review cycle:

```bash
cd "$BIM_DIR"
BIM_APP_DIR=<generated app dir> python3 tests/run_all.py
```

This executes every page against a mocked Streamlit and asserts: no page raises, no KPI is
NaN or Inf, every page survives a filter that matches zero rows, cross-filtered and
drilled states render, grids carry a derived `column_config`, exports are non-empty, month
axes are in calendar order, and value labels do not collide. A page that raises here would
have raised in front of the user.

**Then reconcile the numbers.** For each dashboard, pick the figures a user would check
first — the headline KPIs, one total per grain, and the worst-performing row — and compare
against the source with filters in a stated, identical state:

| Check | Why it is the one that catches drift |
|---|---|
| Grand total per measure, no filters | Catches join fan-out and duplicated rows |
| One total per dimension grain | Catches a wrong `GROUP BY` or a lost filter stage |
| A ratio (attainment, share, margin) | Catches per-row averaging instead of re-aggregation |
| A period-over-period delta | Catches an off-by-one on the date axis or a boundary rule |
| A row with a NULL or zero denominator | Catches `DIV0` masquerading as a real zero |
| The single largest and smallest row | Catches ordering, ties, and top-N filter semantics |
| A FIXED-LOD-derived figure with a dimension filter active | Catches the order-of-operations trap |

Record actual numbers, not verdicts. "Matches" is not evidence; `4,182,566 vs 4,182,566`
is. For every mismatch, name the cause — source freshness, export grain, calculation
translation, filter timing, timezone or locale, rounding, or a genuine defect. "Close
enough" is not a cause.

**Expect legitimate differences, and say so.** Masking policies and row-access policies
apply per executing role. If the Tableau extract was built by a broad service account and
the Streamlit app runs as the viewer's role, the app can correctly show different numbers.
That is governance working, not a migration defect — but it must be stated explicitly in
the fidelity report, or the first user to notice will file it as a bug.

**Visual comparison, after the numbers agree:**

```bash
python3 -m pip install Pillow   # QA-time only, not a deployment dependency
python3 scripts/visual_compare.py \
  evidence/tableau/<dashboard>.png evidence/streamlit/<dashboard>.png \
  --output-dir evidence/visual/<dashboard>
```

Capture both sides under the same viewport, data snapshot, filter state, and theme, or the
comparison means nothing. Use the pixel score to *locate* differences, never to decide
correctness — a low score on a chart showing wrong numbers is worse than useless. Never
synthesize a reference image from your own implementation.

**Deploy prerequisites — check these before promising a deployment:**

- SPCS must be available in the user's cloud and region. Confirm it before designing
  around the container runtime.
- `SYSTEM_COMPUTE_POOL_CPU` exists by default and permits Streamlit workloads, so a simple
  deployment needs no new compute pool.
- Size `MAX_NODES` against *concurrent* apps, not deployed apps. Ten deployed dashboards
  used by two people at a time is not a ten-node workload.
- The app's role needs `SELECT` on every underlying table and `USAGE` on any semantic view.
  A missing grant surfaces as an empty chart, not an error, which is why the kit renders a
  deliberate no-permission state instead of a blank panel.

### Step 8d: Deploy (after preview and reconciliation are approved)

Then invoke the Streamlit skill for deployment:
```python
skill(command="developing-with-streamlit-in-snowflake")
```

Tell it: "I have a Streamlit app at /tmp/bim_streamlit/.
Please help me deploy it to Snowflake. The entry point is app.py (or home.py if multi-file)."

### Step 8e: Fidelity Report

**ALWAYS present a fidelity report before deployment.** This tells the user exactly
what is faithful to the original and what is approximated.

Read `/tmp/bim_visuals.json` and the generated app code, then present:

```
Visual Fidelity Report

Extracted from source (faithful):
   - Color scheme: [list each field->color mapping with hex codes]
   - Background color and sidebar tint
   - Parameters: [list each with values]
   - Column aliases: [count] resolved
   - Number formats: [count] applied via format registry
   - Dashboard layout proportions
   - Filter configurations

Interactive features (enterprise standard):
   - Cross-filtering: click a chart bar to filter the page (with exclude-self)
   - URL-shareable filter state (page, parameter mode, filter selections)
   - Drill-down: [list drill paths, e.g. Team -> Rep]
   - Native grids with sort, search, pin, progress bars, inline sparklines, export
   - Plan/target as reference lines (not competing bar series)
   - 100% attainment reference line on performance charts
   - Data provenance banner (source tables, warehouse, freshness, row count)
   - Empty states with Clear Filters button
   - Version tolerance: runs on Streamlit 1.27+ through 1.63+

Approximated (close but not exact):
   - Chart type: [note if inferred via heuristic vs. explicit mark]
   - Font family: [note if not specified in source]
   - Column ordering (may differ from original worksheet row/col encoding)
   - KPI layout in rows of 4 (Tableau may have used different grouping)

Cannot reproduce in Streamlit:
   - Tooltip rich formatting on hover (Plotly tooltips are simpler)
   - Tableau Server/Cloud auth integration
   - Dynamic LOD expressions (if any were flagged as manual)
   - Tableau storyboard tab animation

Numeric reconciliation:
   - Render gate: [N] pages, [N] raised, [N] KPIs checked for NaN/Inf
   - Figures compared: [list each as "<label>: <source value> vs <app value>"]
   - Mismatches: [each with its named cause, or "none"]
   - Not translatable: [each calculation with a blocker, and what was agreed]
   - Legitimate differences: [e.g. masking or row-access policy applies per role,
     so the app shows the viewer's entitled slice rather than the extract's]
```

Ask the user: "Does this look acceptable? Any colors or layouts to adjust before deployment?"

---

## Reference reading

Load these when the condition applies. Do not read them all up front.

- Load `references/calculation-translation.md` when translating ANY calculated field, LOD, or table calculation — this is the authority.
- Load `references/viz-mapping.md` when choosing a chart type for a source mark, or a mark has no obvious equivalent.
- Load `references/interactivity-state.md` when wiring cross-filtering, drill-down, parameters, or URL state.
- Load `references/app-structure.md` when laying out pages, containers and column ratios.
- Load `references/migration-qa.md` when reconciling numbers or comparing visuals — this is the authority for Step 8c.

---

## When this path is done

Return to the bi-modernization router (`SKILL.md`) and load the next sub-skill the
user selected, or present the summary if this was the last one.
