# Tableau workbook XML — structure and parsing

Read this when `modules/tableau/inspector.py` reports something as `unrecognized`, when the inventory looks thinner than the workbook actually is, or when you need to interpret raw XML the script did not model.

For the normal case, run the script and work from its output. This reference explains what it reads, so you can go back to the raw XML when it misses something.

## File formats

| Extension | Structure |
|---|---|
| `.twb` | A single XML document. The whole workbook definition. |
| `.twbx` | A ZIP archive: one `.twb` at the root plus a `Data/` directory holding extracts (`.hyper`), CSV, or Excel sources, and sometimes `Image/` and `Shape/` assets. |
| `.tds` | A datasource definition — fields, calculations, and connection, with no worksheets or dashboards. Root element is `<datasource>`, not `<workbook>`. |
| `.tdsx` | A ZIP archive containing a `.tds` plus local supporting files. |

`modules/tableau/inspector.py` handles all four. For a `.tds`/`.tdsx` it reports `source_kind: datasource` and empty worksheet, dashboard, and action lists, because those genuinely do not exist in a datasource file — do not read that as a workbook with nothing in it.

Extract files inside an archive are **data, not definitions**: the script reports that they exist but cannot read `.hyper` (a proprietary binary format with no stdlib reader). A workbook whose data lives in a `.hyper` extract needs that data in Snowflake before the migration can proceed; see Step 2 of the parent skill.

The reader treats every input as untrusted — see the safety section of `source-acquisition.md` for what it refuses and why.

## Document shape

```xml
<workbook>
  <datasources>
    <datasource caption='Superstore' name='federated.abc123'>
      <connection class='snowflake' dbname='ANALYTICS' schema='PUBLIC' .../>
      <column datatype='string'  name='[Region]' role='dimension' type='nominal'/>
      <column datatype='real'    name='[Sales]'  role='measure'   type='quantitative'/>
      <column caption='Profit Ratio' datatype='real' name='[Calculation_1]' role='measure'>
        <calculation class='tableau' formula='SUM([Profit]) / SUM([Sales])'/>
      </column>
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Monthly Trend'>
      <table>
        <view>
          <datasource-dependencies datasource='federated.abc123'>...</datasource-dependencies>
          <filter class='categorical' column='[Region]'>...</filter>
        </view>
        <panes><pane><mark class='Line'/></pane></panes>
        <rows>[Sales]</rows>
        <cols>[Order Date]</cols>
      </table>
    </worksheet>
  </worksheets>
  <dashboards>
    <dashboard name='Executive Overview'>
      <zones><zone name='Monthly Trend' w='600' h='400' x='0' y='0'>...</zone></zones>
    </dashboard>
  </dashboards>
  <actions>
    <action caption='Filter Trend to Category'>
      <source worksheet='Category Mix'/>
      <target worksheet='Monthly Trend'/>
    </action>
  </actions>
</workbook>
```

## Tag reference

| XML | Meaning | Migration target |
|---|---|---|
| `<datasource>` / `<connection>` | Connection class, database, schema, table, or custom SQL | The Snowflake table or view the app queries |
| `<column datatype role type>` | Field definition; `role` is `dimension` or `measure` | Column identity, type coercion, and whether it groups or aggregates |
| `<column><calculation formula>` | Calculated field or LOD expression | SQL expression, or a pandas column when the result set is already small |
| `<column value><range>` | Workbook parameter with bounds | A Streamlit input widget (`st.slider`, `st.selectbox`, `st.date_input`) |
| `<filter class column>` | Quick filter; `class` is `categorical`, `quantitative`, or `relative-date` | A widget plus a `WHERE` predicate, or a bind parameter |
| `<filter context='true'>` | **Context** filter | Applied *before* FIXED LODs — see the order-of-operations section in `calculation-translation.md` |
| `<worksheet>` | One chart | One render function |
| `<rows>` / `<cols>` | Shelf assignments | Chart axis encodings |
| `<mark class>` | Marks card type (`Bar`, `Line`, `Area`, `Circle`, `Square`, `Automatic`) | Chart mark type — see `viz-mapping.md` |
| `<encodings>` with `<color>`, `<size>`, `<text>`, `<tooltip>` | Marks card shelves | Chart encoding channels |
| `<dashboard><zones><zone>` | Layout tree with `x`/`y`/`w`/`h` geometry | Container and column structure |
| `<action>` with `<source>`/`<target>` | Filter or highlight action between sheets | Chart selection event plus `st.session_state` |

## Reading the XML directly

When you need to go past the script, the parsing itself is unremarkable — `xml.etree.ElementTree` is enough. What trips people up is the data, not the API:

**Formulas are XML-escaped.** `IF [Profit] >= 0 THEN "Profitable" ELSE "Loss" END` appears as `formula='IF [Profit] &gt;= 0 THEN &quot;Profitable&quot; ELSE &quot;Loss&quot; END'`. `ElementTree` unescapes attribute values for you, so read `elem.get("formula")` and do not unescape again.

**Names and captions are different things.** `name` is the internal identifier (`[Calculation_1084098234]`); `caption` is what the user sees (`Profit Ratio`). Formulas reference the `name`. Build a `name → caption` map first, then rewrite formulas into readable column names — otherwise the migrated code is full of `Calculation_1084098234`.

**Brackets are part of the identifier.** `[Sales]` in a formula, `Sales` as a Snowflake column. Strip brackets when rewriting, and handle names containing spaces or hyphens (`[Sub-Category]` → `sub_category`).

**Duplicate captions happen.** Two datasources can each define `[Sales]`. Scope by datasource `name` when a workbook has more than one.

**Zone trees nest.** A dashboard zone containing other zones is a layout container, not a chart. Only leaf zones with a `name` matching a worksheet are charts; the rest carry the `orientation` that tells you whether to use columns or vertical stacking.

**Hidden and unused sheets are common.** A worksheet with no zone reference in any dashboard is not displayed. Report it as out-of-scope rather than migrating it silently.

## Constructs the script now models

These were once hand-inspection items and are now extracted, because each translates to
about one line of SQL and leaving them to the reader was pushing avoidable work onto the
migrator. They appear in the inventory's `groups_and_bins` section with a suggested
translation:

- **Groups** (`<group>`) — a `CASE` expression, or a join to a mapping table past about a
  dozen members. The inspector picks based on member count.
- **Bins** (`<bin>`, or a `<column>` carrying `bin-size`/`bin-base`) — `FLOOR(x / size) * size`.
  Use `WIDTH_BUCKET` only when you want a bucket ordinal rather than a bucket floor.
- **Sets** (`<set>`) — an `IN` predicate when members are enumerated, a `DENSE_RANK` window
  plus a predicate when membership is a rule. Sets resolve at **stage 4**, before dimension
  filters and after context filters, so where you apply them changes FIXED LOD values.

Placement varies by Tableau version: any of the three can be a sibling of `<column>` under
`<datasource>` or nested inside the `<column>` it derives from. The inspector handles both,
plus the bin-attributes-on-a-plain-column form.

Per-field metadata is extracted too, and each item is parity-relevant rather than
decoration: `type` (discrete versus continuous, which decides an Altair `:N`/`:O`/`:Q`
channel and whether a filter is a multiselect or a slider), `semantic-role` (a geographic
role is the usual reason a migration needs a map, and therefore Plotly, and therefore an
EAI), `hidden`, `default-format` (number and date formatting is parity — matching values
with mismatched formatting still reads as a failed migration), member `<aliases>` (the
workbook displayed labels that differ from the stored values), and Tableau `<folder>`
membership (the author's own grouping of fields, which usually beats a similarity score
when splitting a dashboard into pages).

## Constructs the script does not model

These are rare enough that hand-inspection is cheaper than automated extraction. If the inventory flags one, open the XML:

- **Hierarchies** (`<drill-path>`) — the drilldown ordering; useful input for Step 6 interactivity.
- **Blends** (multiple `<datasource-dependencies>` in one worksheet) — a join in SQL, and worth flagging to the user because Tableau blending has left-join semantics at the viz grain that are easy to get wrong.
- **Table extensions and analytics extensions** — external script calls. No Streamlit equivalent without rewriting the logic in Python.

## Features that need an explicit decision

Beyond unmodeled XML tags, these Tableau features have no clean Streamlit or Snowflake equivalent. When the workbook uses one, decide its disposition (`equivalent`, `partial`, `blocked`, or `intentionally-omitted` — see `migration-qa.md`) and tell the user, rather than quietly approximating it.

| Feature | The problem |
|---|---|
| Data densification, domain completion, Show Missing Values | Tableau silently invents rows for missing date or category combinations. A `GROUP BY` will not, so lines break and totals differ. Densify deliberately with a calendar or dimension spine join. |
| `Measure Names` / `Measure Values` | Generated fields with no column in your table. They become an explicit unpivot, or a measure-selector widget. |
| `Number of Records`, generated lat/long | Also generated. `COUNT(*)`, and real geo columns. |
| Sets, set controls, set actions | Stateful membership. Needs session state plus a predicate; a set action is more than a filter. |
| Dynamic parameters | Domain refreshes from a query on load. Reload the domain rather than hard-coding it. |
| Parameter actions | A click writes a parameter. Maps to a selection callback that validates before writing. |
| Custom geocoding, spatial files, map layers, background images | Requires the asset to ship with the app, and SiS blocks remote fetches at runtime. |
| Forecasting, trend lines, clustering | Tableau's models are specific. Reimplementing with a different library gives different numbers; say so rather than implying parity. |
| Analytics extensions (R/Python services) | External compute. No SiS equivalent without rewriting the logic. |
| Dashboard extensions, web objects, embedded content | Third-party or iframe content. Usually `blocked` or `intentionally-omitted`. |
| Viz in Tooltip | A nested view inside a hover. No native equivalent; consider a detail panel and mark it `improved` or `partial`. |
| Dynamic zone visibility | Zones shown or hidden by a field's value. Becomes conditional rendering — straightforward, but easy to miss entirely when reading zone geometry. |
| Device-specific layouts | Separate per-device layouts. Decide which one is authoritative before matching geometry. |
| Stories | Ordered narrative of captured states. Maps to pages plus stored filter presets. |
| Custom views | Per-user saved states. Maps to query parameters or saved presets. |
| Subscriptions, alerts, comments | Platform features, not app features. Out of scope for the app itself — route to Snowflake alerts if the user needs them. |
| `USERNAME()`, `ISMEMBEROF()`, user filters | Security, not formulas. See the row-level security section of `migration-qa.md`. |
