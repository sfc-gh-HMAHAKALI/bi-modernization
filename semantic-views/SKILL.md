---
name: semantic-views
description: >
  Propose semantic view groupings from observed dashboard usage, generate Cortex Analyst YAML,
  and deploy via agent-studio. Load from the bi-modernization router.
---

## Step 6: Generate Semantic Views (if selected)

### 6a. Propose the grouping first — do not guess the view count

How many views, and which tables in each, cannot be answered from the data model alone.
"One view per domain" sounds right on paper and is often wrong: it may be one per domain
or one spanning several. The deciding evidence is how the BI layer already queries the
data, because tables that appear together on a dashboard get joined together in real
queries.

```bash
cd "$BIM_DIR"
python3 -m modules.cli propose-views /tmp/bim_inventory.json -o /tmp/bim_views.json
```

This prints a proposal with the evidence behind each group: the driving dashboards, the
contributing workbooks, column counts, and any table no dashboard touches.

**Present it and let the user decide.** It is a proposal, not a decision. Three things in
it are worth reading aloud rather than skipping:

- **A view spanning several workbooks is the consolidation win.** Ten workbooks over the
  same tables want one semantic view, not ten. Say so explicitly.
- **Tables no dashboard uses.** Including them creates parity obligations for things
  nobody looks at. Ask before keeping them.
- **`strategy: one-per-table`** means there was no usage evidence, so it fell back to 1:1
  replication. That is the simplest correct answer, not a recommendation — if the user
  knows the model, consolidate by hand.

The user can edit `/tmp/bim_views.json` (rename a view, move a table between groups) and
you pass the edited file straight to the next step. Force a strategy with
`--strategy co-usage` or `--strategy one-per-table` if they prefer.

### 6b. Generate the YAML

Pass the approved grouping. Note the command is `generate-yaml`, named to stay
unambiguous alongside `generate-streamlit` and `generate-react`:

```bash
python3 -m modules.cli generate-yaml /tmp/bim_inventory.json \
  --groups /tmp/bim_views.json -o /tmp/bim_yaml/
```

Without `--groups` it falls back to splitting on a column-count threshold, which is an
arbitrary boundary rather than a meaningful one. Prefer the proposal.

### 6c. Deploy

Review the YAML, then invoke the agent-studio skill, which handles CREATE SEMANTIC VIEW
deployment and validation:

```python
skill(command="agent-studio")
```

Tell the agent-studio skill: "I have generated semantic view YAML files at /tmp/bim_yaml/
and I need to deploy them to Snowflake. The files are: [list files]."


---

## Reference reading

Load these when the condition applies. Do not read them all up front.

- Load `references/semantic_view_yaml_spec.md` when writing or correcting semantic view YAML by hand.
- Load `references/concept_mapping.md` when mapping a source concept (LookML explore, BO universe object, Tableau datasource) onto Snowflake semantic view constructs.

---

## When this path is done

Return to the bi-modernization router (`SKILL.md`) and load the next sub-skill the
user selected, or present the summary if this was the last one.
