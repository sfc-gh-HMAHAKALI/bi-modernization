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
  -o /tmp/bim_streamlit/
```

Show the user which pages were generated:
```
Generated Streamlit app: /tmp/bim_streamlit/
  home.py              — navigation entry point
  dashboard_revenue.py — Revenue Dashboard (3 charts: 2 bar, 1 line)
  dashboard_pipeline.py — Pipeline Overview (4 charts: 2 bar, 1 line, 1 metric)
  ...
```

**ALWAYS offer a local preview before deployment** — this is the default behaviour.
Ask the user:

```
ask_user_question: "Would you like to see a local preview of the Streamlit app
  before deploying it to Snowflake? I'll launch it on http://localhost:8501 using
  synthetic data — no Snowflake connection needed."
type: options
options:
  - label: "Yes, show me a preview first" (recommended)
  - label: "Skip preview and deploy directly"
```

If the user wants a preview:
```bash
cd "$BIM_DIR"
python3 -m modules.cli preview /tmp/bim_enriched.json \
  --app-dir /tmp/bim_streamlit/ \
  --type streamlit \
  --rows 30
```

This will:
1. Generate `preview_dashboard_*.py` files with a synthetic data mock session
2. Launch `streamlit run preview_home.py` on http://localhost:8501
3. Display charts using plausible synthetic values for every field type

Tell the user: "Your preview is running at http://localhost:8501. Review the
dashboard layout and chart types. Press Ctrl+C in the terminal to stop the
preview, then let me know if you want any changes before deploying."

Wait for the user to confirm the preview looks acceptable before continuing.

Then invoke the Streamlit skill for deployment:
```python
skill(command="developing-with-streamlit-in-snowflake")
```

Tell it: "I have a multi-page Streamlit app generated at /tmp/bim_streamlit/. 
Please help me deploy it to Snowflake. The entry point is home.py."

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
