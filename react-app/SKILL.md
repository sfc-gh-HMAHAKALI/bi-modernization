---
name: react-app
description: >
  Generate a Next.js + ECharts app for Snowflake App Runtime (SPCS) and deploy. Load from the
  bi-modernization router.
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

## Reference reading

Load these when the condition applies. Do not read them all up front.

- Load `references/app-structure.md` when laying out pages and containers.
- Load `references/viz-mapping.md` when choosing an ECharts type for a source mark.

---

## When this path is done

Return to the bi-modernization router (`SKILL.md`) and load the next sub-skill the
user selected, or present the summary if this was the last one.
