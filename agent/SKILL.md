---
name: agent
description: >
  Build a Cortex Agent spec and deployment SQL over the generated semantic views, with
  optional Cortex Search tools. Load from the bi-modernization router.
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

## Reference reading

Load these when the condition applies. Do not read them all up front.

- Load `references/concept_mapping.md` when deciding which semantic views a tool should cover.

---

## When this path is done

Return to the bi-modernization router (`SKILL.md`) and load the next sub-skill the
user selected, or present the summary if this was the last one.
