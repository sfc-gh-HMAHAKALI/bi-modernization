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
SKILL.md                         router: intent table and the fast path
extract/  semantic-views/         one sub-skill per build path
agent/    streamlit-app/  react-app/
modules/
  tableau/  powerbi/  looker/  denodo/  businessobjects/   source parsers
  output/                        inventory, semantic YAML, reports, si_agent
  adapters/  common/             shared plumbing
  cli.py                         single entry point for all 20 commands
  cli_semex.py                   extraction command implementations
  portfolio.py                   multi-source parsing with resumable state
  views.py                       semantic view grouping from observed usage
  chart_extractor.py  streamlit_generator.py  react_generator.py
  agent_builder.py    visual_extractor.py     preview.py
assets/bim_ui/                   component kit copied into generated apps
references/                      extraction + migration guidance
scripts/visual_compare.py        visual QA diffing
tests/run_all.py                 render gate, component and structure suites
```

## CLI

`python3 -m modules.cli --help` lists everything. Two halves:

**Extraction** — `crawl`, `parse`, `merge`, `propose-views`, `classify`, `generate-yaml`, `report`, `compare`, `generate-from-workbook`, `seed-data`, `si-agent`, `test-connection`

**Generation** — `migrate`, `enrich-charts`, `extract-visuals`, `generate-streamlit`, `generate-react`, `build-agent`, `preview`, `restore-react`

### Fast path

One command for the whole deterministic chain, stopping before app generation:

```bash
python3 -m modules.cli migrate "/path/to/dashboards" --type tableau -o /tmp/out \
  [--database MY_DB --schema PUBLIC]
```

Runs crawl → parse → merge → enrich → extract-visuals → propose-views → generate-yaml.
Accepts a file, a directory, or a glob. Resumable: re-running skips completed steps,
`--force` redoes everything. On a 24-workbook folder this takes about two seconds.

### Step by step

```bash
# 1. Find the sources
python3 -m modules.cli crawl /path/to/dashboards --type tableau

# 2. Parse — a file, a directory, or a glob. A directory also writes one inventory
#    per source and a checkpoint, so an interrupted run resumes.
python3 -m modules.cli parse "/path/to/dashboards" --type tableau -o /tmp/inv.json

# 3. Combine inventories from different source types (de-duplicates columns)
python3 -m modules.cli merge /tmp/inv_a.json /tmp/inv_b.json -o /tmp/inv.json

# 4. Decide the semantic view count from observed dashboard usage, then generate
python3 -m modules.cli propose-views /tmp/inv.json -o /tmp/views.json
python3 -m modules.cli generate-yaml /tmp/inv.json --groups /tmp/views.json -o /tmp/yaml/

# 5. Chart types and visual metadata
python3 -m modules.cli enrich-charts /tmp/inv.json --source-files "/path/to/*.twb" -o /tmp/enr.json
python3 -m modules.cli extract-visuals "/path/to/wb.twb" -o /tmp/visuals.json

# 6. Build
python3 -m modules.cli generate-streamlit /tmp/enr.json --visuals /tmp/visuals.json -o /tmp/app/
python3 -m modules.cli build-agent /tmp/enr.json --database MY_DB --schema PUBLIC -o /tmp/agent/
```

### Semantic view grouping

`propose-views` answers "how many views, and which tables in each" from evidence rather
than a rule: tables that appear together on a dashboard get joined together in real
queries, so they belong in one view. It reports the driving dashboards, contributing
workbooks and column counts behind each group, flags tables no dashboard touches, and falls
back to one view per table when there is no usage evidence — saying so, rather than
presenting a guess as a recommendation.


### Migration report (Tableau)

```bash
python3 -m modules.tableau.inspector "/path/to/wb.twb" --format markdown
```

Reports data blending, non-Snowflake connections, orphan worksheets, high-complexity
calculations, member aliases, hand-written colour legends, dashboard background colours, and
how many fields never reach a dashboard.

`BIM_TABLEAU_PARSER=legacy` forces the older Tableau parser if a workbook regresses.

## Chart engines

Generated apps default to **Altair**, because it is a hard Streamlit dependency and therefore
already present in Streamlit in Snowflake. Plotly requires an External Access Integration the
user has to request and approve.

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

`SKILL.md` is a thin router; each build path is its own sub-skill, loaded only when needed.

1. `extract/` — detect and crawl sources, parse, surface the findings that change scope
2. Router asks what to build
3. `semantic-views/` — propose the grouping, generate YAML, deploy
4. `agent/` — build the Cortex Agent
5. `streamlit-app/` — translate calculations and reconcile numbers **before** building UI
6. `react-app/` — Next.js + ECharts on SPCS
7. `build-agent` → invoke `agent-studio`
8. `generate-streamlit` → invoke `developing-with-streamlit-in-snowflake`
9. `generate-react` → invoke `snowflake-apps` + `sar-actions-desktop`

## Changelog

### 2.0.0 (September 2026)
- Merged the `semantic-extraction` skill in flat at the same module depth: all five source
  parsers, semantic YAML generation, reports, and si_agent are now in-tree. No second install,
  and `agent_builder` no longer shells out to another skill.
- Much deeper Tableau extraction, behind a contract-preserving adapter so the inventory
  contract is unchanged. On the FBR reference workbook this moves extraction from
  68 dimensions / 32 facts / 0 metrics to 68 / 110 / 44, and surfaces 122 calculation
  translation prescriptions including 23 LODs. The other four sources are unaffected and
  are the next candidates for the same treatment.
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
