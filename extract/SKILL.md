---
name: extract
description: >
  Parse BI source files into an inventory: detect sources, crawl a folder or portfolio, parse,
  surface the findings that change scope, enrich with chart types, and extract visual
  metadata. Load from the bi-modernization router.
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

`parse` takes a single file, a directory, or a glob. Pass the directory rather than looping
per file — one workbook at a time hides the shared tables across workbooks, and that
sharing is what decides how many semantic views you need.

```bash
cd "$BIM_DIR"
# One workbook
python3 -m modules.cli parse "<file>" --type tableau -o /tmp/bim_inventory.json

# A folder or a portfolio: parses every source, writes one inventory per source
# plus a merged one, and checkpoints so an interrupted run resumes
python3 -m modules.cli parse "<directory-or-glob>" --type tableau -o /tmp/bim_inventory.json
```

On a directory it also writes `<name>_sources/` (per-workbook inventories) and
`<name>_state.json` (the checkpoint). Re-running resumes; `--no-resume` reparses
everything. `--no-recurse` stays at the top level, `--max-files N` caps the run.

Per-source failures are collected, not fatal: read `failed_count` and `failed` in the
result and tell the user which workbooks did not parse and why. Do not silently proceed as
though the portfolio was complete.

For Tableau this uses the inspector automatically and attaches its richer detail to the
inventory under `tableau_inspection` (translation prescriptions, filter staging, layout
ratios, visual styles, field reach). `BIM_TABLEAU_PARSER=legacy` forces the older parser if
a workbook ever regresses.

For multiple source *types*, parse once per type, then combine:
```bash
python3 -m modules.cli merge /tmp/inv_tableau.json /tmp/inv_powerbi.json -o /tmp/bim_inventory.json
```
`merge` de-duplicates columns on (table, name) and reports before/after counts, so the
de-duplication is visible. Check them: on a real portfolio the drop is large (1,489
dimensions to 349 on a 24-workbook set), and a small drop suggests the workbooks are less
related than assumed.

Review the parse output for errors. If there are critical errors, show them to the user and ask
whether to proceed with partial results.

### Step 2b: Surface the findings that change scope (Tableau)

Run this and present the findings BEFORE asking the user what to build. These change scope,
so finding them after the app is built means rework:

```bash
python3 -m modules.tableau.inspector "<source_path>" --format markdown | head -40
```

Report each finding that applies, in plain language, and ask the user to decide:

| Finding | Ask the user |
|---|---|
| Datasource not on a live Snowflake connection | The data must exist in Snowflake first. Is it already there, and under what name? |
| Data blending across datasources | Tableau blends left-join at the viz grain. Confirm the join semantics before trusting any total. |
| Worksheets on no dashboard | Usually out of scope. Confirm before translating them. |
| Fields that reach no dashboard | Translating them creates parity obligations for things nobody sees. Skip unless asked. |
| High-complexity calculations | Name them. These are the ones to translate and reconcile first. |
| Member aliases | The workbook showed labels that differ from stored values. Filters and axis labels must use the aliases. |
| Hand-written colour legend in a text box | The author recorded a colour convention no encoding element captures. Read it before choosing a palette. |
| Credential-like attributes in the file | Treat the source as a secret: keep it out of version control and never paste its raw XML anywhere. |

On the FBR reference workbook this reported that 117 of 222 fields reach no dashboard and 30
worksheets sit on none — roughly half the apparent work was not real work.


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

## Reference reading

Load these when the condition applies. Do not read them all up front.

- Load `references/portfolio-analysis.md` when the scope is a folder, a Tableau project, or "our dashboards" rather than one file.
- Load `references/source-acquisition.md` when the user does not yet have the source files, or only has a published/live source.
- Load `references/tableau_extraction.md` when parsing Tableau and you need field-level detail.
- Load `references/twb-xml-parsing.md` when the inspector reports something as `unrecognized`, or the inventory looks thinner than the workbook actually is.
- Load `references/powerbi_extraction.md` when parsing Power BI.
- Load `references/looker_extraction.md` when parsing Looker / LookML.
- Load `references/denodo_extraction.md` when parsing Denodo VQL.
- Load `references/businessobjects_extraction.md` when parsing SAP Business Objects.

---

## When this path is done

Return to the bi-modernization router (`SKILL.md`) and load the next sub-skill the
user selected, or present the summary if this was the last one.
