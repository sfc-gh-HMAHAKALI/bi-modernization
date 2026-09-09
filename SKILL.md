---
name: bi-modernization
description: >
  End-to-end BI modernization suite. Takes Tableau, Power BI, Looker, Denodo, or SAP BO
  source files and generates Snowflake Semantic Views, Cortex Agents, a Streamlit-in-Snowflake
  app, and/or a React/SPCS enterprise dashboard — with optional agent chat embedded in the app.
  Orchestrates semantic-extraction, agent-studio, developing-with-streamlit-in-snowflake,
  snowflake-apps, and sar-actions-desktop sub-skills.
---

# BI Modernization Suite

You are orchestrating a complete BI modernization workflow. You will parse BI sources, generate
Snowflake Semantic Views, and then let the user choose from: Cortex Agents, Streamlit-in-Snowflake
app, React/SPCS enterprise app, or any combination — with optional Cortex Agent chat embedded
in the app layer.

This skill DELEGATES to other CoCo skills for deployment steps. You must invoke those skills
at the appropriate steps using the skill tool.

## Prerequisites Check

Before starting, verify the semantic-extraction skill is installed:
```bash
ls ~/.snowflake/cortex/skills/semantic-extraction/modules/cli.py
```

If it does not exist, tell the user to install it first and stop.

Set a variable for convenient reuse:
```bash
SEM_EX_DIR="$HOME/.snowflake/cortex/skills/semantic-extraction"
BIM_DIR="$HOME/.snowflake/cortex/skills/bi-modernization"
```

---

## Step 1: Source Detection

Ask the user what they're starting from:

```
ask_user_question:
  - "Are you starting from BI source files or an existing inventory.json from semantic-extraction?"
    type: options
    options:
      - label: "BI source files (Tableau, Power BI, Looker, etc.)"
        description: "I'll parse the files and generate the inventory from scratch."
      - label: "Existing inventory.json"
        description: "I already ran semantic-extraction — skip straight to generation."

  - "Which BI tool(s) are the source files from?"
    type: options (only ask if source files chosen)
    multiSelect: true
    options:
      - label: "Tableau (.twb / .twbx)"
      - label: "Power BI (.pbix / .pbit / .pbip)"
      - label: "Looker (LookML project folder)"
      - label: "Denodo (VQL export)"
      - label: "SAP Business Objects (.unx / .unv JSON)"
```

Ask for the file/folder paths.

---

## Step 2: Parse BI Sources (skip if existing inventory.json provided)

**Load the semantic-extraction skill first**, then run the parse:

```python
skill(command="semantic-extraction")
```

After loading the skill, use its parse command:
```bash
cd "$SEM_EX_DIR"
python3 -m modules.cli parse "<source_path>" --type <tableau|powerbi|looker|denodo|businessobjects> \
  -o /tmp/bim_inventory.json
```

For multiple source types, run parse once per type and merge by hand (combine the JSON arrays).

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

Generate the YAML files using semantic-extraction:
```bash
cd "$SEM_EX_DIR"
python3 -m modules.cli generate /tmp/bim_inventory.json -o /tmp/bim_yaml/
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

Run the Streamlit generator:
```bash
cd "$BIM_DIR"
python3 -m modules.cli generate-streamlit /tmp/bim_enriched.json \
  [--semantic-view DB.SCHEMA.SEMANTIC_VIEW] \
  [--embed-agent DB.SCHEMA.AGENT_NAME] \
  [--visuals /tmp/bim_visuals.json] \
  -o /tmp/bim_streamlit/
```

### Step 8a: Quality Gate

**CRITICAL — before showing the output to the user, validate it.**

Read each generated `.py` file and check for these degeneracies:

1. **Identical SQL queries** — read all the SQL strings (the text inside `_session.sql(""" ... """)` blocks). If >50% of sheets have the exact same SQL query, the field resolution failed.
2. **Raw XML sheet names** — check if any `st.subheader()` or chart title contains bracket notation (`[...].[...]`), file paths (`.jpg`, `.png`), or Tableau-internal strings like `none:FIELD:nk`.
3. **All bar charts** — if every single chart is `px.bar` and the enriched inventory shows all marks were "Automatic", the chart type detection failed. The workbook is likely tabular/crosstab.
4. **No filters** — check if the sidebar section only contains `pass  # No filters detected`. Compare against `inventory["parameters"]` and worksheet-level filters.
5. **Uses `st.navigation`** — this requires Streamlit >= 1.36. Many environments have older versions.
6. **Placeholder table names** — SQL targeting `TARGET_DB.PUBLIC.EXTRACT` or `DB.SCHEMA.TABLE` means table resolution failed.
7. **Plotly compatibility** — check for `cornerradius` in any `marker=dict(...)` call (breaks Plotly <5.19), duplicate keyword arguments in `update_layout()` (e.g., spreading `**PLOTLY_LAYOUT` that has `margin` AND passing `margin=` again), or CSS `-webkit-background-clip: text` (invisible text in Streamlit webview).

**If ANY of these degeneracies are found, do NOT present the generated files to the user. Proceed to Step 8b instead.**

If all checks pass, proceed to show the user the generated pages and offer a preview.

### Step 8b: LLM-Driven App Generation (fallback)

When the automated generator produces low-quality output, build the Streamlit app manually.
This produces significantly better results for complex workbooks with custom SQL, crosstab
layouts, calculated fields, or role-based views.

**Step 8b.1: Deep analysis of the enriched inventory**

Read `/tmp/bim_enriched.json` and extract:
- Dashboard names and the worksheets they contain
- For each worksheet: `fields_by_datasource` (which datasources it uses and which fields)
- All dimensions, measures/facts, and their names/expressions
- Parameters and their allowed values
- The `source_type` (tableau, powerbi, etc.)

**Step 8b.2: Deep analysis of the source BI file**

If the original source file is available (TWB, PBIX, etc.), re-read it directly to extract
what the parser missed:

For **Tableau (.twb)**:
- Parse `<worksheet>` elements: extract the `<mark class="...">` for each (Bar, Line, Text, Automatic)
- Parse `<column>` elements within each datasource: extract `caption`, `datatype`, `role`, and `<calculation formula="...">` for calculated fields
- Parse `<relation>` elements: extract the actual custom SQL queries
- Parse `<dashboard>` zones: match zone worksheet references to worksheet names
- Parse `<filter>` elements: extract which fields are used as filters and their allowed values
- Identify the dashboard layout pattern (storyboard? standard? tabbed?)

For **Power BI (.pbix / .pbit)**:
- Parse report page visuals for chart types and field bindings
- Extract DAX measures and their expressions

**Step 8b.3: Build the Streamlit app**

Generate a **single-file `app.py`** (more robust than multi-file for compatibility):

1. **Data layer**: If the user's Snowflake account has the source tables, generate `session.sql()` queries based on the actual custom SQL from the source file. If not (demo mode), generate a `@st.cache_data` function with synthetic data that matches the schema — realistic column names, data types, value distributions, and calculated fields.

2. **Navigation**: Use `st.radio()` in the sidebar (works on Streamlit 1.24+). Do NOT use `st.navigation()` or `st.Page()`.

3. **Layout**: Match the original dashboard structure:
   - If the source is a **storyboard** (Tableau) → sidebar radio navigation between story points
   - If it has **role-based views** (same layout filtered differently) → single page template function called per role with different filter values
   - If it has **distinct dashboards** → separate page functions

4. **Tables**: For crosstab/text mark worksheets, render using **HTML tables via `st.markdown(unsafe_allow_html=True)`** with:
   - Sticky dark-colored header row
   - Right-aligned currency columns with `$X,XXX` formatting
   - Alternating row colors
   - Compact 5-6px padding (Tableau-dense)
   - Cell-level color badges for categorical fields (e.g., risk categories)
   - Color-tinted row backgrounds keyed to a category column

5. **Charts**: For chart worksheets, use `plotly.express` or `plotly.graph_objects` with colors extracted from the source BI file.

6. **KPIs**: Use styled HTML `<div>` cards, not plain `st.metric()`.

7. **Filters**: Extract parameters and filter fields from the inventory. Render as `st.multiselect`, `st.radio`, or `st.selectbox` in the sidebar.

8. **CSS**: Inject a `<style>` block via `st.markdown()` with:
   - Corporate header bar (gradient blue: `#11567F` → `#29B5E8`)
   - Dense table styling (`.dtable` class)
   - Badge/pill components for categorical values
   - Scrollable table containers with max-height

9. **Plotly / Streamlit Compatibility Rules** (MANDATORY — violations break the app):

   These rules are based on real failures observed across multiple BI modernization builds.
   Violating ANY of them produces runtime errors in common Streamlit+Plotly environments.

   a. **No `cornerradius` in Plotly bar markers.** `marker=dict(cornerradius=N)` was added
      in Plotly ≥ 5.19. Many Streamlit environments ship Plotly 5.9–5.18. NEVER use it.
      Instead, bars render with square corners (the default).

   b. **No duplicate keyword arguments in `update_layout()`.** When spreading a shared
      layout dict (`**PLOTLY_LAYOUT`) that contains `margin`, do NOT also pass `margin=`
      as a separate kwarg. Python raises `TypeError: got multiple values for keyword
      argument 'margin'`. If you need a custom margin for one chart, build a one-off
      layout dict or call `update_layout` twice.

   c. **CSS `background-clip: text` does not work in Streamlit's webview.** Text styled
      with `-webkit-background-clip: text; -webkit-text-fill-color: transparent` renders
      as invisible. For gradient-styled headings, use a plain `color:` on the `<h1>` or
      use an SVG/image instead.

   d. **`st.navigation()` / `st.Page()` requires Streamlit ≥ 1.36.** Use `st.radio()`
      in the sidebar for page navigation — works on Streamlit 1.24+.

   e. **Plotly `go.Figure` shared layout pattern.** Define a `PLOTLY_LAYOUT` dict once and
      spread it, but keep `margin`, `height`, `title` OUT of the shared dict if any chart
      needs to override them. Recommended shared dict:
      ```python
      PLOTLY_LAYOUT = dict(
          paper_bgcolor="rgba(0,0,0,0)",
          plot_bgcolor="rgba(0,0,0,0)",
          font=dict(color=TEXT_WHITE, family="Inter, sans-serif"),
          margin=dict(t=40, b=30, l=40, r=20),
      )
      ```
      For charts needing different margins (e.g., small KPI bars), build a separate dict
      or call `fig.update_layout(margin=dict(...))` as a second call AFTER the first.

   f. **Always test Plotly features against the OLDEST likely version.** Assume Plotly 5.9+.
      Avoid: `cornerradius`, `marker.pattern.fgopacity`, `legendgrouptitle`,
      `minor` axis properties. These are 5.15+ or 5.19+ features.

   g. **Choropleth maps for geographic data.** When the TWB has `Multipolygon` marks,
      use `px.choropleth()` with `locationmode="country names"`. Plotly does not support
      Tableau-style filled polygons natively.

   h. **Heatmaps for Square/Circle marks.** When the TWB uses Square or Circle mark class
      with a color encoding on a measure, render as `go.Heatmap()` with `texttemplate="%{text}"`.
      Map the Tableau color gradient to a Plotly `colorscale` list.

**Step 8b.4: Preview**

Save the app to `/tmp/bim_streamlit/app.py` and launch a local preview:

```bash
cd /tmp/bim_streamlit && streamlit run app.py --server.port 8501 --server.headless true
```

Open the browser and take a screenshot to verify. If issues are found, fix them before
presenting to the user.

**ALWAYS offer a local preview before deployment.**

### Step 8c: Deploy (after preview is approved)

Then invoke the Streamlit skill for deployment:
```python
skill(command="developing-with-streamlit-in-snowflake")
```

Tell it: "I have a Streamlit app at /tmp/bim_streamlit/.
Please help me deploy it to Snowflake. The entry point is app.py (or home.py if multi-file)."

### Step 8d: Fidelity Report

**ALWAYS present a fidelity report before deployment.** This tells the user exactly
what is faithful to the original and what is approximated.

Read `/tmp/bim_visuals.json` and the generated app code, then present:

```
Visual Fidelity Report
══════════════════════

✅ Extracted from source (pixel-accurate):
   - Color scheme: [list each field→color mapping with hex codes]
   - Background color: #f5f5f5
   - Parameters: [list each with values]
   - Column aliases: [count] resolved (list top 5)
   - Number formats: [list if any]
   - Dashboard layout proportions: [zone height ratios]
   - Filter configurations: [count] per worksheet

⚠️ Approximated (close but not exact):
   - Chart type: [note if inferred via heuristic vs. explicit mark]
   - Font family: [note if not specified in source]
   - Header bar styling (Snowflake brand gradient — not in original)
   - Column ordering (may differ from original worksheet row/col encoding)

❌ Cannot reproduce in Streamlit:
   - Tableau storyboard tab animation
   - Tooltip rich formatting on hover
   - Action filter cross-highlighting between worksheets
   - Tableau Server/Cloud auth integration
   - Dynamic LOD expressions (if any were flagged as manual)
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
- Tip: "Run the semantic-extraction skill again after adding new BI dashboards to keep your
  Snowflake layer in sync."

---

## Stopping Points

- Step 2: Parse errors — confirm user wants to proceed with partial results
- Step 4: Domain structure — confirm domain grouping before proceeding
- Step 5: Selection confirmed — all choices locked in before generation
- Step 7: Agent preview — user reviews and approves agent config before deployment
