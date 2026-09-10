# BI Modernization Suite

**Version:** 2.0.0
**Requires:** nothing else — source extraction is built in.

End-to-end BI modernization. Takes BI source files (Tableau, Power BI, Looker, Denodo, SAP BO) and generates any combination of:

- Snowflake Semantic Views
- Cortex Agents (Cortex Analyst + optional Cortex Search tools)
- Streamlit-in-Snowflake enterprise dashboard app
- React/Next.js SPCS app with Apache ECharts
- Cortex Agent chat embedded in either app

## Install

```bash
cd ~/.snowflake/cortex/skills/bi-modernization
pip install -r requirements.txt
```

## Layout

```
modules/
  tableau/  powerbi/  looker/  denodo/  businessobjects/   source parsers
  output/                        inventory, semantic YAML, reports, si_agent
  adapters/  common/             shared plumbing
  cli.py                         single entry point for all 17 commands
  cli_semex.py                   extraction command implementations
  chart_extractor.py  streamlit_generator.py  react_generator.py
  agent_builder.py    visual_extractor.py     preview.py
assets/bim_ui/                   component kit copied into generated apps
references/                      extraction + migration guidance
scripts/visual_compare.py        visual QA diffing
tests/run_all.py                 render gate and component suites
```

## CLI

`python3 -m modules.cli --help` lists everything. Two halves:

**Extraction** — `crawl`, `parse`, `classify`, `generate-yaml`, `report`, `compare`, `generate-from-workbook`, `seed-data`, `si-agent`, `test-connection`

**Generation** — `enrich-charts`, `extract-visuals`, `generate-streamlit`, `generate-react`, `build-agent`, `preview`, `restore-react`

### Typical run

```bash
# 1. Find the sources
python3 -m modules.cli crawl /path/to/dashboards --type tableau

# 2. Parse to an inventory
python3 -m modules.cli parse "/path/to/wb.twb" --type tableau -o /tmp/inv.json

# 3. Semantic view YAML
python3 -m modules.cli generate-yaml /tmp/inv.json -o /tmp/yaml/

# 4. Chart types and visual metadata
python3 -m modules.cli enrich-charts /tmp/inv.json --source-files "/path/to/wb.twb" -o /tmp/enr.json
python3 -m modules.cli extract-visuals "/path/to/wb.twb" -o /tmp/visuals.json

# 5. Build
python3 -m modules.cli generate-streamlit /tmp/enr.json --visuals /tmp/visuals.json -o /tmp/app/
python3 -m modules.cli build-agent /tmp/enr.json --database MY_DB --schema PUBLIC -o /tmp/agent/
```

### Tableau inspector

Tableau parsing runs through an inspector that also produces a standalone migration report.
Read it before building — it leads with the warnings that change scope.

```bash
python3 -m modules.tableau.inspector "/path/to/wb.twb" --format markdown
```

It reports data blending, non-Snowflake connections, orphan worksheets, high-complexity
calculations, member aliases, hand-written colour legends, dashboard background colours, and
how many fields never reach a dashboard. Its structured detail is attached to the inventory
under `tableau_inspection` (LOD and table-calc translation prescriptions, filter
order-of-operations staging, layout ratios, visual styles, field reach).

`BIM_TABLEAU_PARSER=legacy` forces the older parser if a workbook regresses.

## Chart engines

Generated apps default to **Altair**, because it is a hard Streamlit dependency and therefore
already present in Streamlit in Snowflake. Plotly requires an External Access Integration the
customer has to request and approve.

Plotly is still used automatically for choropleth, treemap, sunburst, sankey, gauge, and
scatter above ~5k marks. Override per app with `charts.set_engine("plotly")` or
`BIM_CHART_ENGINE=plotly`, or per chart with `ChartSpec(engine="plotly")`.

## Tests

```bash
python3 tests/run_all.py                               # component + contract suites
BIM_APP_DIR=/path/to/generated/app python3 tests/run_all.py   # adds the page render gate
```

The page gate executes every page against a mocked Streamlit and asserts no page raises, no
KPI is NaN or Inf, empty-filter and cross-filtered and drilled states render, grids carry a
derived `column_config`, exports are non-empty, month axes are in calendar order, and value
labels do not collide.

## Workflow (via SKILL.md)

1. Detect and crawl sources; route by source tier
2. Parse to an inventory
3. Enrich with chart types and visual metadata
4. Preview dashboards, domains, proposed agent structure
5. User selects what to build
6. Translate calculations and reconcile numbers **before** building UI
7. `build-agent` → invoke `agent-studio`
8. `generate-streamlit` → invoke `developing-with-streamlit-in-snowflake`
9. `generate-react` → invoke `snowflake-apps` + `sar-actions-desktop`

## Changelog

### 2.0.0 (September 2026)
- Merged the `semantic-extraction` skill in flat at the same module depth: all five source
  parsers, semantic YAML generation, reports, and si_agent are now in-tree. No second install,
  and `agent_builder` no longer shells out to another skill.
- Tableau extraction now backed by an inspector behind a contract-preserving adapter. On the
  FBR reference workbook this moves extraction from 68 dimensions / 32 facts / 0 metrics to
  68 / 110 / 44, and surfaces 122 calculation translation prescriptions including 23 LODs.
- Altair chart engine, now the default; Plotly retained for the marks Vega-Lite does not cover.
- Calculation translation, Tableau's nine-stage order of operations, and the
  `a / NULLIF(b, 0)` division-parity rule documented in SKILL.md.
- Numeric reconciliation promoted to an explicit pre-deploy gate, with SPCS prerequisites.
- Eight migration references and `scripts/visual_compare.py` for visual QA.
- Fixed: Tableau columns were keyed to the datasource name while tables were keyed to the
  relation name, so the split-by-table path silently wrote zero semantic views once a
  workbook exceeded the column threshold.
- Fixed: multi-line Tableau formulas were emitted as bare YAML scalars, which made the
  generated semantic view unparseable.

### 1.0.0 (September 2026)
- Initial release
- Tableau mark-type extraction, Power BI visual passthrough, Looker tile types
- Streamlit and React/ECharts generators
- Agent builder extending si_agent with Cortex Search tools
- Orchestration SKILL.md with sub-skill delegation
