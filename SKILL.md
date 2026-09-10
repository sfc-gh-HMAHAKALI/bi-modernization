---
name: bi-modernization
description: >
  End-to-end BI modernization suite. Takes Tableau, Power BI, Looker, Denodo, or SAP BO
  source files and generates Snowflake Semantic Views, Cortex Agents, a Streamlit-in-Snowflake
  app, and/or a React/SPCS enterprise dashboard — with optional agent chat embedded in the app.
  Self-contained: source parsing and semantic YAML generation are built in. Delegates to
  agent-studio, developing-with-streamlit-in-snowflake, snowflake-apps, and
  sar-actions-desktop for deployment.
---

# BI Modernization Suite

You are orchestrating a complete BI modernization workflow. You will parse BI sources, generate
Snowflake Semantic Views, and then let the user choose from: Cortex Agents, Streamlit-in-Snowflake
app, React/SPCS enterprise app, or any combination — with optional Cortex Agent chat embedded
in the app layer.

Source extraction is part of this skill — there is no separate skill to install. It delegates
only for *deployment*, and you must invoke those skills at the appropriate steps using the
skill tool.

## Setup

```bash
BIM_DIR="$HOME/.snowflake/cortex/skills/bi-modernization"
cd "$BIM_DIR"
```

Install dependencies if a parse fails on a missing module:
```bash
python3 -m pip install -r requirements.txt
```

Everything runs from one CLI. `python3 -m modules.cli --help` lists all commands; the two
halves are extraction (`crawl`, `parse`, `classify`, `generate-yaml`, `report`, `compare`,
`seed-data`, `si-agent`, `test-connection`) and generation (`enrich-charts`,
`extract-visuals`, `generate-streamlit`, `generate-react`, `build-agent`, `preview`).

---

## Step 1: Source Detection

Ask the user what they're starting from:

```
ask_user_question:
  - "Are you starting from BI source files or an existing inventory.json?"
    type: options
    options:
      - label: "BI source files (Tableau, Power BI, Looker, etc.)"
        description: "I'll parse the files and generate the inventory from scratch."
      - label: "Existing inventory.json"
        description: "Skip straight to generation."

  - "Which BI tool(s) are the source files from?"
    type: options (only ask if source files chosen)
    multiSelect: true
    options:
      - label: "Tableau (.twb / .twbx / .tds / .tdsx)"
      - label: "Power BI (.pbix / .pbit / .pbip)"
      - label: "Looker (LookML project folder)"
      - label: "Denodo (VQL export)"
      - label: "SAP Business Objects (.unx / .unv JSON)"
```

Ask for the file/folder paths.

**If the user gave a directory rather than specific files**, find the sources first rather
than guessing:
```bash
python3 -m modules.cli crawl "<directory>" --type tableau
```

### Source routing

Extraction depth differs by source, and it changes what you can promise. Tell the user which
tier they are in before building:

| Source | Extractor | Depth |
|---|---|---|
| Tableau | `modules/tableau/inspector.py` | Deepest. LOD classification, untranslatable-function detection, table calcs, filter order-of-operations staging, dashboard layout ratios, per-member colours, field reach. Use `--format markdown` for a migration report. |
| Power BI | `modules/powerbi/` (pbixray) | Model, DAX measures, report-page visuals and field bindings. No DAX-to-SQL prescriptions. |
| Looker | `modules/looker/` (lkml) | Views, explores, dimensions, measures, joins. LookML is already close to a semantic layer. |
| Denodo | `modules/denodo/vql_parser.py` | VQL views and derived-view lineage. |
| SAP BO | `modules/businessobjects/` | Universe objects from a JSON export; requires an export step outside this skill. |

For **Tableau**, run the inspector's markdown report and surface its warnings before doing
anything else — see Step 8's "Deep analysis of the source". For the other four, expect to
read the source's own expression language yourself; only Tableau has a prescription engine.

Mixed sources are supported: parse each separately, then `compare` or merge the inventories.

---


## Step 2: Parse BI Sources (skip if existing inventory.json provided)

Run the parse:
```bash
cd "$BIM_DIR"
python3 -m modules.cli parse "<source_path>" --type <tableau|powerbi|looker|denodo|businessobjects> \
  -o /tmp/bim_inventory.json
```

For Tableau this uses the inspector automatically, and attaches its richer detail to the
inventory under `tableau_inspection` (translation prescriptions, filter staging, layout
ratios, visual styles, field reach). `BIM_TABLEAU_PARSER=legacy` forces the older parser if
a workbook ever regresses.

For multiple source types, run parse once per type, then merge or `compare` the inventories.

Review the parse output for errors. If there are critical errors, show them to the user and ask
whether to proceed with partial results.

---

## Step 3: Enrich with Chart Types

Run the enrich-charts command from this skill:
```bash
cd "$BIM_DIR"
python3 -m modules.cli enrich-charts /tmp/bim_inventory.json \
  --source-files "<original_source_file_paths>" \
  -o /tmp/bim_enriched.json
```

For Power BI sources, `--source-files` is optional because chart types are already in the
inventory from the parse step.

Show the chart type coverage summary from the output:
```
Chart type coverage: X% explicit (from source files), Y% inferred via heuristics
```

---

## Step 3b: Extract Visual Metadata

Extract color schemes, layout coordinates, column aliases, number formats, parameters,
and filter configurations from the original source file. This produces a `visuals.json`
sidecar that generators use for pixel-accurate reproduction.

```bash
cd "$BIM_DIR"
python3 -m modules.cli extract-visuals "<original_source_file>" \
  -o /tmp/bim_visuals.json
```

Review the output summary:
```
Visual metadata extracted: /tmp/bim_visuals.json
  Color mappings:    N value→color pairs (e.g., RRC: L=#59a14f, M=#4e79a7)
  Column aliases:    N (e.g., Calculation_123 → "Recommended CL")
  Dashboard layouts: N (zone coordinates per dashboard)
  Worksheet filters: N filter configurations
  Parameters:        N (with allowed values and aliases)
```

**Present the color mappings to the user** for confirmation — these drive the entire visual
styling of the generated app:

```
Extracted color scheme from source file:
  Field: RRC
    L → #59a14f (green)
    M → #4e79a7 (blue)
    H → #f28e2b (orange)
    C → #e15759 (red)
  Background: #f5f5f5
  Accent: #0077c0

Does this match the original dashboard? Any corrections?
```

Pass `--visuals /tmp/bim_visuals.json` to all subsequent generate commands.

---

## Step 4: Preview What Was Found

Run the agent assessment to generate the domain structure:
```bash
cd "$BIM_DIR"
python3 -m modules.cli build-agent /tmp/bim_enriched.json --assess-only
```

Present the results to the user in a structured summary:

```
Found in your BI sources:
  Dashboards:  N  (list their names)
  Data domains: M  (e.g., Revenue, Pipeline, Marketing, User Engagement)
  Dimensions:  X
  Metrics:     Y
  Relationships: Z

Proposed Cortex Agent structure:
  Cortex Analyst tools: M (one per domain)
    1. QueryRevenueData        → Revenue domain       (T tables, N metrics)
    2. QueryPipelineData       → Pipeline domain      (T tables, N metrics)
    ...
  Cortex Search tools: 0 (can add if you have Cortex Search services)

Proposed Streamlit app: N pages (one per dashboard)
Proposed React app: N pages with ECharts visualizations
```

Ask the user to review and optionally rename domains or reassign tables.

---

## Step 5: Ask What to Build

Use ask_user_question with multiSelect:

```
"What would you like to generate? Select all that apply."
type: options
multiSelect: true
options:
  - label: "Snowflake Semantic Views"
    description: "Deploy the YAML semantic views — foundation for Cortex Analyst and everything else."
  - label: "Cortex Agent"
    description: "Conversational AI over your data. Includes Cortex Analyst tools per domain."
  - label: "Streamlit-in-Snowflake app"
    description: "Multi-page dashboard app with Plotly charts, quick to deploy inside Snowflake."
  - label: "React/SPCS app (enterprise)"
    description: "Next.js + Apache ECharts hosted on SPCS. Enterprise-grade visuals with animations and drill-down."
  - label: "Embed Cortex Agent chat in the app"
    description: "Adds a floating chat panel to the Streamlit or React app powered by your Cortex Agent."
```

Also ask:
```
"Do you have any Cortex Search services to attach to the agent?"
type: options
options:
  - label: "No, just Cortex Analyst tools"
  - label: "Yes — I'll provide the service FQNs"
```

If yes, ask for the Cortex Search service fully-qualified names (e.g., DB.SCHEMA.SERVICE_NAME).

---

## Step 6: Generate Semantic Views (if selected)

Generate the YAML files. Note the command is `generate-yaml`, named to stay unambiguous
alongside `generate-streamlit` and `generate-react`:
```bash
cd "$BIM_DIR"
python3 -m modules.cli generate-yaml /tmp/bim_inventory.json -o /tmp/bim_yaml/
```

Review the YAML files. For deployment, invoke the agent-studio skill which handles
CREATE SEMANTIC VIEW deployment and validation:

```python
skill(command="agent-studio")
```

Tell the agent-studio skill: "I have generated semantic view YAML files at /tmp/bim_yaml/
and I need to deploy them to Snowflake. The files are: [list files]."

---

## Step 7: Build Cortex Agent (if selected)

Run the build-agent command:
```bash
cd "$BIM_DIR"
python3 -m modules.cli build-agent /tmp/bim_enriched.json \
  --database <DB> --schema <SCHEMA> \
  --agent-name <AGENT_NAME> \
  [--search-services "DB.SCH.SVC1,DB.SCH.SVC2"] \
  -o /tmp/bim_agent/
```

This produces:
- `/tmp/bim_agent/<agent_name>_spec.json` — the agent specification
- `/tmp/bim_agent/<agent_name>_deploy.sql` — deployment SQL

Show the user the agent preview from the JSON output. Allow them to edit domain names,
tool descriptions, or the list of search services before deploying.

Then invoke the agent-studio skill for deployment:
```python
skill(command="agent-studio")
```

Tell it: "I have an agent specification at /tmp/bim_agent/<name>_spec.json and deployment SQL
at /tmp/bim_agent/<name>_deploy.sql. Please help me deploy and test this agent."

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
reconcile calculations FIRST, and treat `references/calculation-translation.md` as the
authority. Work the `tableau_inspection.translation` list in descending `complexity_score`,
because that ordering front-loads the calculations most likely to be wrong.

**Tableau's order of operations is not SQL's.** Tableau applies nine stages in a fixed
sequence, and three of them are where numbers silently drift:

1. Extract filters
2. Datasource filters
3. **Context filters** — these run BEFORE FIXED LODs, so a FIXED LOD sees context-filtered
   data but ignores ordinary dimension filters
4. Sets
5. **FIXED LODs**
6. **Dimension filters** — these do NOT affect a FIXED LOD computed at stage 5
7. INCLUDE / EXCLUDE LODs
8. Measure filters
9. Table calculations — these run LAST, on the aggregated result

The trap: a dimension filter in the app's sidebar looks like it should narrow a FIXED LOD,
and in Tableau it does not. If you apply your Streamlit filters to the frame before
computing a FIXED-LOD equivalent, your number will differ from Tableau's and the difference
will look random. Decide per calculation which stage its filters belong to, and say so in
the fidelity report.

**Translation patterns:**

| Tableau | Snowflake | Note |
|---|---|---|
| `{FIXED [Dim] : SUM([M])}` | `SUM(m) OVER (PARTITION BY dim)` | Not affected by dimension filters |
| `{INCLUDE [Dim] : SUM([M])}` | Subquery at the finer grain, then re-aggregate | Adds a grain below the viz |
| `{EXCLUDE [Dim] : SUM([M])}` | `SUM(m) OVER (PARTITION BY <viz dims minus Dim>)` | Coarser than the viz grain |
| `WINDOW_SUM`, `RUNNING_SUM`, `INDEX()`, `RANK()` | Window functions with an explicit `ORDER BY` | Tableau's default addressing is the *table layout*; state it explicitly |
| `LOOKUP([M], -1)` | `LAG(m) OVER (ORDER BY ...)` | Partition must match Tableau's addressing |
| `TOTAL([M])` | `SUM(m) OVER ()` | Whole-partition total |
| `IIF`, `IF/THEN/ELSEIF` | `IFF`, `CASE WHEN` | |
| `ZN([M])` | `COALESCE(m, 0)` | |
| `DATETRUNC('month', d)` | `DATE_TRUNC('MONTH', d)` | |
| `DATEDIFF('day', a, b)` | `DATEDIFF('day', a, b)` | Tableau counts boundary crossings; verify on a known pair |
| `[a] / [b]` | `a / NULLIF(b, 0)` | See below — this one matters |

**Division parity.** Tableau returns null when the denominator is zero; Snowflake raises a
division-by-zero error. Always write `a / NULLIF(b, 0)`.

Do NOT use `DIV0` or `DIV0NULL`. Both return 0, and a zero is not a null: it gets counted in
a `COUNT`, dragged through an `AVG`, and rendered as "0%" attainment where the source showed
a blank. That is a silent, plausible-looking wrong answer, which is the worst kind.

**Untranslatable functions.** When the inspector reports a `blockers` entry, there is no SQL
equivalent — typically `SCRIPT_REAL`/`SCRIPT_STR` (R/Python integration), `MODEL_QUANTILE`,
`MODEL_PERCENTILE`, or a spatial function. Do not invent an approximation and ship it as
parity. Name the calculation, state that it is not translatable, and ask the user whether to
drop the field, precompute it upstream, or accept a documented gap.

**Aggregation grain and fan-out.** A join that duplicates rows inflates every `SUM` downstream,
and it will look like a plausible number rather than an error. When a plan or quota table joins
one-to-many against a fact, carry the plan value on exactly ONE row per group and assert it —
the reference app's test suite checks `plan carried on exactly one row per month`. Ratios must
be computed from re-aggregated numerator and denominator, never averaged from per-row ratios.


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

- SPCS must be available in the customer's cloud and region. Confirm it before designing
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

## Step 9: Generate React/SPCS App (if selected)

Run the React generator:
```bash
cd "$BIM_DIR"
python3 -m modules.cli generate-react /tmp/bim_enriched.json \
  --app-name "<App Name>" \
  [--semantic-view DB.SCHEMA.SEMANTIC_VIEW] \
  [--embed-agent DB.SCHEMA.AGENT_NAME] \
  -o /tmp/bim_react/
```

Show the user the generated project structure:
```
Generated React app: /tmp/bim_react/
  app.yml                     — Snowflake App Runtime manifest
  package.json                — Next.js 14, echarts-for-react, tailwindcss
  src/app/
    page.tsx                  — Dashboard navigation home
    dashboard/[name]/page.tsx — Dynamic dashboard routes (N dashboards)
  src/components/charts/      — ECharts-based chart components
  src/components/AgentChat.tsx — Agent chat drawer (if embed-agent selected)
```

**ALWAYS offer a local preview before deployment** — this is the default behaviour.
Ask the user:

```
ask_user_question: "Would you like to run the React app locally first so you can
  see how it looks before deploying to SPCS? I'll inject synthetic data and run
  it on http://localhost:3000 — npm and Node.js must be installed locally."
type: options
options:
  - label: "Yes, run local preview first" (recommended)
  - label: "Skip preview and deploy to SPCS"
```

If the user wants a preview:
```bash
cd "$BIM_DIR"
python3 -m modules.cli preview /tmp/bim_enriched.json \
  --app-dir /tmp/bim_react/ \
  --type react \
  --rows 30
```

This will:
1. Generate `src/lib/mock-data.ts` with synthetic JSON for every table
2. Replace `src/lib/snowflake.ts` with a mock client (production version backed up as `snowflake.prod.ts`)
3. Write `.env.local` with `NEXT_PUBLIC_PREVIEW=true`
4. Run `npm install` (first time only) then `npm run dev` on http://localhost:3000

Note: If the user does not have Node.js/npm installed, offer the Streamlit preview instead as a
quicker option, or use `--generate-only` to inject the mock data without launching the server.

Tell the user: "Your React app is running at http://localhost:3000. The charts display synthetic
data matching your BI field structure. Press Ctrl+C to stop. When you're happy with the layout,
I'll restore the production Snowflake client and deploy to SPCS."

After the user confirms the preview is acceptable:
```bash
cd "$BIM_DIR"
python3 -m modules.cli restore-react /tmp/bim_react/
```
This restores `snowflake.ts` from the backup before deployment.

Then invoke both the Snowflake Apps and SAR actions skills:
```python
skill(command="snowflake-apps")
```
Tell it: "I have a Next.js app at /tmp/bim_react/ with an app.yml manifest. 
Please help me install dependencies and deploy it to Snowflake App Runtime (SPCS)."

If on desktop (CoCo Desktop environment):
```python
skill(command="sar-actions-desktop")
```

---

## Step 10: Summary

Present a final summary showing:
- What was generated (semantic views, agent, Streamlit app, React app)
- File locations for generated artifacts
- Next steps (deployment commands, links to relevant Snowflake docs)
- Tip: "Re-run the parse after adding new BI dashboards to keep your
  Snowflake layer in sync."

---

## Stopping Points

- Step 2: Parse errors — confirm user wants to proceed with partial results
- Step 4: Domain structure — confirm domain grouping before proceeding
- Step 5: Selection confirmed — all choices locked in before generation
- Step 7: Agent preview — user reviews and approves agent config before deployment
