# BI Modernization Suite

**Version:** 1.0.0  
**Requires:** `semantic-extraction` skill installed at `~/.snowflake/cortex/skills/semantic-extraction`

End-to-end BI modernization orchestrator. Takes BI source files (Tableau, Power BI, Looker, Denodo, SAP BO) and generates any combination of:

- Snowflake Semantic Views
- Cortex Agents (Cortex Analyst + optional Cortex Search tools)
- Streamlit-in-Snowflake enterprise dashboard app
- React/Next.js SPCS app with Apache ECharts (enterprise-grade visuals)
- Cortex Agent chat embedded in either app

## Prerequisites

```bash
cd ~/.snowflake/cortex/skills/bi-modernization
pip install -r requirements.txt

# semantic-extraction must also be installed
cd ~/.snowflake/cortex/skills/semantic-extraction
pip install -r requirements.txt
```

## CLI Commands

### enrich-charts
Enriches an existing `inventory.json` from semantic-extraction with chart type information.

```bash
python3 -m modules.cli enrich-charts inventory.json \
  --source-files "/path/to/*.twb" \
  -o enriched_inventory.json
```

### generate-streamlit
Generates a multi-page Streamlit-in-Snowflake app from an enriched inventory.

```bash
python3 -m modules.cli generate-streamlit enriched_inventory.json \
  --semantic-view DB.SCHEMA.SEMANTIC_VIEW \
  --embed-agent DB.SCHEMA.AGENT_NAME \
  -o ./streamlit_output/
```

### generate-react
Generates a Next.js + Apache ECharts app for deployment via Snowflake App Runtime (SPCS).

```bash
python3 -m modules.cli generate-react enriched_inventory.json \
  --app-name "My BI Suite" \
  --semantic-view DB.SCHEMA.SEMANTIC_VIEW \
  --embed-agent DB.SCHEMA.AGENT_NAME \
  -o ./react_output/
```

### build-agent
Generates Cortex Agent specification + deployment SQL, extending semantic-extraction's si-agent with Cortex Search support.

```bash
python3 -m modules.cli build-agent enriched_inventory.json \
  --database MY_DB \
  --schema PUBLIC \
  --agent-name MY_AGENT \
  --search-services "DB.SCH.SVC1,DB.SCH.SVC2" \
  --assess-only

# Remove --assess-only to generate artifacts
python3 -m modules.cli build-agent enriched_inventory.json \
  --database MY_DB --schema PUBLIC \
  -o ./agent_output/
```

## Workflow (via SKILL.md)

1. Parse BI sources using semantic-extraction CLI
2. Enrich with chart types via `enrich-charts`
3. Preview dashboards, domains, proposed agent structure
4. User selects what to build (any combination)
5. Build-agent → invoke `agent-studio` skill
6. Generate-streamlit → invoke `developing-with-streamlit-in-snowflake` skill
7. Generate-react → invoke `snowflake-apps` + `sar-actions-desktop` skills

## Changelog

### 1.0.0 (September 2026)
- Initial release
- Tableau mark-type extraction (fills gap in semantic-extraction parser)
- Power BI visual type passthrough from existing report_pages
- Looker tile type extraction from LookML
- Streamlit generator with Plotly Express (bar, line, area, pie, scatter, metric cards)
- React/Next.js generator with Apache ECharts (enterprise-grade)
- Agent builder extending si_agent.py with Cortex Search tools + human-readable preview
- Full orchestration SKILL.md with sub-skill delegation
