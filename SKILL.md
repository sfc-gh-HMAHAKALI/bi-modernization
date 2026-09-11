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

You are orchestrating a complete BI modernization workflow: parse BI sources, generate
Snowflake Semantic Views, then let the user choose from Cortex Agents, a
Streamlit-in-Snowflake app, a React/SPCS app, or any combination — with optional Cortex
Agent chat embedded in the app layer.

Source extraction is part of this skill; there is no separate skill to install. It
delegates only for *deployment*, via the skill tool at the steps that say so.

## Setup

```bash
BIM_DIR="$HOME/.snowflake/cortex/skills/bi-modernization"
cd "$BIM_DIR"
```

Install dependencies if a parse fails on a missing module:
```bash
python3 -m pip install -r requirements.txt
```

Everything runs from one CLI; `python3 -m modules.cli --help` lists all of it. Extraction:
`crawl`, `parse`, `merge`, `propose-views`, `classify`, `generate-yaml`, `report`,
`compare`, `seed-data`, `si-agent`, `test-connection`. Generation: `migrate`,
`enrich-charts`, `extract-visuals`, `generate-streamlit`, `generate-react`,
`build-agent`, `preview`.

---

## Routing

| The user wants | Load |
|---|---|
| To start from BI source files, or asks "what's in here?" | `extract/SKILL.md` |
| Semantic Views / Cortex Analyst YAML | `semantic-views/SKILL.md` |
| A Cortex Agent | `agent/SKILL.md` |
| A Streamlit-in-Snowflake app | `streamlit-app/SKILL.md` |
| A React / SPCS enterprise app | `react-app/SKILL.md` |
| Anything at all, and has not run extraction yet | `extract/SKILL.md` first |

Extraction always comes first: every build path consumes an inventory. If the user
already has an `inventory.json`, skip to the build path they asked for.

### Fast path

For a whole folder, or when the user just wants everything staged, run the chain in one
command instead of stepping through extraction by hand:

```bash
cd "$BIM_DIR"
python3 -m modules.cli migrate "<file, directory, or glob>" --type tableau \
  -o /tmp/bim_migrate [--database DB --schema SCHEMA]
```

That runs crawl → parse → merge → enrich → extract-visuals → propose-views →
generate-yaml, and stops before app generation. It is resumable: re-running skips
completed steps, and `--force` redoes everything. Then load the build path the user
picked.

Use the step-by-step path in `extract/SKILL.md` instead when the user wants to inspect
findings between steps, or when a source needs per-file handling.

---

## Step 5: Ask What to Build

Once extraction is done, present what was found and ask. Do NOT assume; a user who
wanted one semantic view does not want four apps.

```
ask_user_question:
  - "What would you like to build?"
    type: options
    multiSelect: true
    options:
      - label: "Semantic Views"
        description: "Cortex Analyst YAML, deployable as CREATE SEMANTIC VIEW."
      - label: "Cortex Agent"
        description: "Natural-language Q&A over the semantic views."
      - label: "Streamlit-in-Snowflake app"
        description: "Multi-page dashboard app replicating the source dashboards."
      - label: "React / SPCS app"
        description: "Next.js + ECharts enterprise app on Snowflake App Runtime."

  - "Embed Cortex Agent chat in the app?"
    type: options (only if an app was chosen AND an agent was chosen)
    options:
      - label: "Yes"
      - label: "No"
```

Then load the sub-skill for each selection, in this order: semantic views, agent, app.
Semantic views come first because the agent and the app both reference them.

---

## Order that matters

Two sequencing rules override convenience, because getting them wrong means rework:

1. **Translate calculations and reconcile numbers before building UI.** A dashboard that
   looks right and totals wrong is worse than no dashboard. `streamlit-app/SKILL.md`
   owns this gate.
2. **Surface extraction findings before asking what to build.** Orphan worksheets,
   unused fields and non-Snowflake connections change scope. `extract/SKILL.md` owns this.

---

## Stopping Points

Stop and ask the user at each of these:

- After extraction findings, before choosing what to build
- After the semantic view proposal, before generating YAML
- After a local preview, before deploying
- After the fidelity report, before deploying
- Whenever a source fails to parse and partial results are the only option
