# Source acquisition

Before you can migrate a dashboard you need enough evidence to recover its **structure**, **semantics**, **appearance**, and **representative results**. Read this at Step 1 of the parent skill, before running the inspector.

The single most common way a migration goes wrong is starting from weaker evidence than was actually available — rebuilding from a screenshot when the workbook was one request away. Use the strongest source for each fact, and combine sources when you can: a formula from the `.twb` plus a filtered value exported from Tableau corroborate each other in a way neither does alone.

## What you can honestly claim, given what you have

State the level explicitly at Step 3 so the user knows what "done" will mean.

| Level | Evidence available | What you may claim |
|---|---|---|
| **A** | Workbook, plus metadata, reference images, and representative result data | Full migration, verifiable end to end |
| **B** | `.twb`/`.twbx` and images, but no trusted result data | Structural and visual parity; data parity partly unverified |
| **C** | Server metadata and images, no workbook download | Only the semantics the metadata actually exposes; enumerate the gaps |
| **D** | Screenshots or PDF plus flat exports | Visible layout and observed numbers; no claim about hidden calculations |
| **E** | Screenshot or PDF only | A clearly labelled visual prototype, and a request for better sources |

Do not quietly operate at level D when a workbook exists. Equally, do not refuse to start at level D or E — build what the evidence supports, label it, and say what would raise the level.

## Deciding what to acquire

1. If a **Tableau MCP server** is connected, use it to resolve the exact published content and to download the workbook when permissions allow.
2. Parse a local `.twbx`/`.twb` whenever one is supplied, **even if MCP is available** — the two corroborate each other.
3. Add reference images for every dashboard, at each viewport that matters.
4. Add representative result data for the default state and for the filter states people actually use.
5. Ask the user only for intent and behavior the artifacts cannot reveal — why a filter exists, which numbers get checked, what is already known to be broken.

If several workbooks or views share a name, show the project, owner, URL, and modified date and let the user pick. Never silently choose one; migrating the wrong "Sales Overview" wastes the entire effort.

## Tableau MCP

Treat MCP as an optional capability, not a dependency. Tool names and schemas vary across installations and versions, so **discover the available tools and read their schemas** rather than assuming a fixed set. A short initial tool list is not proof that no Tableau MCP is configured — search for workbook, view, datasource, metadata, image, download, and query capabilities.

A Tableau MCP toolset commonly exposes semantic equivalents of: listing and searching workbooks, projects, views, and datasources; retrieving workbook details; downloading a workbook; retrieving datasource metadata including fields, roles, types, calculation strings, and parameters; retrieving a view's reference image; retrieving CSV data for a view; and querying a published datasource.

Read-only sequence:

1. Search or list content and resolve the exact project, workbook, and view.
2. Retrieve identifiers plus modification and version information.
3. Download the workbook if permitted.
4. Retrieve datasource metadata for every datasource the target dashboards use.
5. Retrieve a reference image per dashboard and per required device state.
6. Retrieve view data for each worksheet you intend to validate against.
7. Query the datasource for bounded samples or aggregates when a view export is insufficient.

**Stay read-only.** Do not call admin, update, delete, publish, or refresh operations during acquisition. Do not ask for a personal access token in chat when an OAuth or configured MCP connection exists.

A dashboard-level CSV export does not necessarily contain every worksheet's data. When a worksheet you need to validate is missing, request that worksheet's data specifically or query the datasource.

When download is forbidden, continue with the metadata you can read and ask for a user-exported `.twbx` only if the missing information actually blocks parity.

## When there is no MCP: the Tableau REST and Metadata APIs

MCP is the preferred path because it is read-only by construction and already
authenticated. When it is not configured and the user has server credentials, the same
information is reachable over Tableau Server's own APIs. Use this only with the user's
explicit agreement — it means handling a credential.

**Ask for a Personal Access Token, never a password.** A PAT is revocable, scoped, and
does not expose the account. Have the user place it in the environment rather than pasting
it into the conversation, and never echo it back or write it into a file you generate.

The REST flow is three steps: sign in for a token, resolve the workbook, download it.

```bash
# 1. Sign in. Returns a credentials token and a site id.
curl -s -X POST "https://$TABLEAU_SERVER/api/3.21/auth/signin" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -d "{\"credentials\":{\"personalAccessTokenName\":\"$PAT_NAME\",
        \"personalAccessTokenSecret\":\"$PAT_SECRET\",
        \"site\":{\"contentUrl\":\"$SITE\"}}}"

# 2. List workbooks, filtering server-side rather than paging everything.
curl -s -H "X-Tableau-Auth: $TOKEN" \
  "https://$TABLEAU_SERVER/api/3.21/sites/$SITE_ID/workbooks?filter=name:eq:$NAME"

# 3. Download. includeExtract=false keeps a .hyper out of the response.
curl -s -H "X-Tableau-Auth: $TOKEN" -o workbook.twbx \
  "https://$TABLEAU_SERVER/api/3.21/sites/$SITE_ID/workbooks/$WB_ID/content?includeExtract=false"

# 4. Sign out when done. Do not leave the session token live.
curl -s -X POST -H "X-Tableau-Auth: $TOKEN" \
  "https://$TABLEAU_SERVER/api/3.21/auth/signout"
```

`includeExtract=false` matters for three reasons: the extract can be gigabytes, it may
contain production data the migration does not need, and the inventory does not read it.
Ask for it only when the data genuinely is not in Snowflake and the extract is the source
of truth — in which case it is a data-loading task, not a dashboard migration.

**API version.** `3.21` corresponds to a specific Tableau Server release. If the server
rejects it, query the server for what it supports rather than guessing downward — the
sign-in response and the server's own version endpoint both report it. Do not hardcode a
version into a script you leave behind.

### The Metadata API, for portfolio work

The Metadata API answers questions the workbook files cannot: which workbooks use a given
table, which fields are actually referenced, and how content depends on upstream data. It
is a GraphQL endpoint and it is worth reaching for when the scope is a portfolio rather
than one dashboard.

```graphql
query {
  workbooks {
    name
    projectName
    upstreamTables { name schema database { name } }
    embeddedDatasources { name fields { name } }
  }
}
```

Two cautions:

1. **Confirm the endpoint path against the server's own version** rather than assuming it.
   The path has differed across Tableau releases, and a wrong guess returns a 404 that
   looks like "the Metadata API is not enabled". Check the server's documentation for the
   deployed version, or probe both candidates and report which answered.
2. **The Metadata API can be disabled.** On many installations it is off by default. If it
   returns nothing, that is a configuration fact to report, not evidence that the content
   has no upstream tables.

Cross-check whatever it reports against the inspector's inventory. Where they disagree,
the workbook XML is authoritative for *what the workbook contains* and the Metadata API is
authoritative for *what the server thinks it depends on* — and the disagreement itself is
usually the interesting finding.

## File formats

| Extension | Contents | Inspector support |
|---|---|---|
| `.twb` | Workbook XML — datasources, fields, formulas, worksheets, dashboards, zones, filters, actions | Yes |
| `.twbx` | ZIP: a `.twb` plus local extracts and image assets | Yes |
| `.tds` | Datasource definition, no data | Yes — fields and calculations only |
| `.tdsx` | ZIP: a `.tds` plus local supporting files | Yes — fields and calculations only |
| `.hyper` | Tableau extract database | No — binary; needs the Tableau Hyper API |

```bash
python3 -m modules.tableau.inspector <source> --format markdown
```

A `.tds`/`.tdsx` gives you the semantic layer but no dashboards. If the user supplies only a datasource, say what is still missing rather than migrating a datasource as though it were a dashboard.

Do not treat the workbook XML as a stable public object model. Parse defensively, keep the source locator for every fact, record tags you did not recognize, and validate important conclusions against a rendered view or official metadata. The published workbook schema validates syntax, not meaning.

## Handling the file safely

A `.twbx` is a zip that arrived from someone else's server, and it may carry embedded connection metadata or credentials. The inspector reads archive members into memory and never writes them to disk, refuses DTD and entity declarations (ElementTree expands internal entities, so a crafted file is a denial-of-service vector), and rejects encrypted, oversized, and suspiciously compressible members. Keep those properties if you extend it.

- Treat `.twbx`, `.tdsx`, `.hyper`, CSV exports, and screenshots as potentially confidential.
- Leave the original at its existing protected location. Copy it into the project only if the user wants a self-contained working bundle, and then keep that directory out of version control.
- Keep credentials and tokens out of generated files, logs, screenshots, and reports. If the inspector warns about credential-like attributes, do not paste the raw XML anywhere.
- Keep production row samples out of committed fixtures unless the user confirms they are safe to share.

## Images and PDFs

Use original-resolution images; render PDF pages losslessly before inspecting detail. For each image record the dashboard and state, capture time, viewport and device pixel ratio, the selected filters and parameters, the theme, and whether any tooltip or dialog is open. Without the state, an image cannot be compared against anything later.

Inspect title hierarchy, container boundaries, whitespace, legends, axes, color encodings, typography, borders, annotations, controls, and the empty, loading, and error states.

Do not infer formulas, filter scope, or action clearing behavior from an image. An image shows what a number *was*, not how it was computed.

## Exports and extracts

CSV and crosstab exports are how you validate displayed results. Record the view, filter state, export options, row count, and whether the export is summary or underlying data.

Watch for these, each of which can make a correct migration look wrong:

- **Aliases versus raw values** — the export may show a display alias, not what the database stores.
- **Formatted versus stored values** — a rounded or currency-formatted export will not equal a raw aggregate.
- **Totals mixed with detail rows** — subtotal rows inflate any comparison that treats the file as flat detail.
- **Tableau-generated fields** — `Number of Records`, `Measure Names`/`Measure Values`, and latitude/longitude have no column in your source table.
- **Hidden fields used in calculations** — a field can be absent from the export and still drive a formula.
- **Extract and datasource filters** — already applied to the export, so your unfiltered query will legitimately disagree.
- **Sampling or aggregation in the export** — a summary export is not row-level truth.
- **Timezone and locale** — date boundaries and decimal separators shift between the export and the warehouse.

For `.hyper`, inspect schema, row counts, types, nullability, and a bounded sample through the Hyper API rather than exporting an entire sensitive extract for convenience. Remember the practical point: if the data lives in a `.hyper`, it is **not in Snowflake yet**, and loading it is a prerequisite to the migration rather than part of it.

## Primary documentation

Re-check these when a capability affects the design; prefer them over blog posts.

- Tableau file types: https://help.tableau.com/current/pro/desktop/en-us/environ_filesandfolders.htm
- Tableau Metadata API: https://help.tableau.com/current/api/metadata_api/en-us/index.html
- Tableau MCP: https://tableau.github.io/tableau-mcp/
- Tableau Hyper API: https://help.tableau.com/current/api/hyper_api/en-us/index.html
- Workbook schemas: https://github.com/tableau/tableau-document-schemas
