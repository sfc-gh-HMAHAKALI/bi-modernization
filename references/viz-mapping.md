# Visual and layout mapping

Translating a Tableau worksheet into a Streamlit chart, and a Tableau dashboard into a Streamlit layout.

## Chart library: Altair or Plotly

**Start with Altair.** Altair is a hard dependency of Streamlit, so it is already in the SiS container runtime. Every SiS app ships a dependency file regardless — a `pyproject.toml` (recommended) or a `requirements.txt`, and an empty `requirements.txt` is acceptable — so the file itself is not the cost. A `pyproject.toml` whose only dependency is `streamlit[snowflake]` needs **no** External Access Integration. The cost is the *extra package*: Plotly is not a Streamlit dependency, so adding it makes an EAI required, and the EAI must be **chosen by the user**. Getting one approved is frequently the thing that stalls a migration for weeks.

So the question is not "which library is better" but "does this Tableau viz need something Altair cannot express."

| Tableau viz | Altair | Verdict |
|---|---|---|
| Bar, stacked bar, line, area, scatter, pie, donut | `mark_bar` / `mark_line` / `mark_area` / `mark_point` / `mark_arc` | Altair |
| Text table, highlight table | `mark_text` / `mark_rect`, or `st.dataframe` with column config | Altair or native |
| Heatmap, density | `mark_rect` | Altair |
| Histogram | `mark_bar` with `bin=True` | Altair |
| Box plot | `mark_boxplot` | Altair |
| Dual axis (two measures, two scales) | `alt.layer(a, b).resolve_scale(y='independent')` | Altair |
| Combo bar + line | layered `mark_bar` + `mark_line` | Altair |
| Reference line, band, average line | layered `mark_rule` / `mark_area` | Altair |
| Gantt / timeline | `mark_bar` with `x` and `x2` | Altair |
| Bullet graph | layered bar + rule | Altair |
| Funnel | `mark_bar` sorted descending with `y` as ordered stage | Altair — Tableau has no native funnel either, so the original was already a shaped bar |
| KPI donut / gauge | `mark_arc` with a two-slice value/remainder frame | Altair for a donut. A true radial gauge needs **Plotly** (`go.Indicator`) — usually not worth an EAI |
| Waterfall | `mark_bar` with `y` and `y2` from a running total | Altair — compute the cumulative bounds in SQL, not in the chart |
| 100% stacked bar | `stack="normalize"` | Altair |
| Symbol map (points on lat/lon) | `mark_circle` with `longitude`/`latitude`, or `st.map` | Altair or native |
| **Filled / choropleth map** | needs TopoJSON wiring, awkward | **Plotly** (`px.choropleth`) |
| **Treemap, sunburst** | no mark type | **Plotly** (`px.treemap`) |
| **Sankey / flow** | no mark type | **Plotly** (`go.Sankey`) |
| **Scatter above ~5,000 marks** | client-side rendering degrades | **Plotly** (`go.Scattergl`, WebGL) |

When a dashboard needs one Plotly chart, you pay the EAI cost for the whole app — so check whether that one view is in scope (Step 3 of the parent skill) before committing. Sometimes the honest answer is to migrate the other eight worksheets on Altair and keep the choropleth in Tableau, or replace it with a ranked bar chart.

If you do need Plotly, add it to `pyproject.toml` alongside `streamlit[snowflake]`, then **ask the user which External Access Integration to use** — list them with `SHOW EXTERNAL ACCESS INTEGRATIONS` and never invent a name — and put the chosen one in `external_access_integrations` in `snowflake.yml`. The scaffolding skill's `snowflake-deployment.md` has the discovery and creation flow for when none exists.

## Inferring the chart type when the workbook does not say

A Tableau worksheet does not always carry an explicit mark type — `<mark class='Automatic'/>` is common, and Tableau's own "Show Me" resolves it from the shelf contents at render time. You have to do the same inference. Work down this ladder and stop at the first rule that fits:

| # | Signal in the worksheet | Chart |
|---|---|---|
| 1 | Explicit `<mark class='...'>` other than `Automatic` | Use it directly — the table in the next section maps each class |
| 2 | One measure, no dimension on rows or columns | `metric_card` / `st.metric` — a single-number tile, not a one-bar chart |
| 3 | A date or datetime dimension on columns, measure on rows | Line chart |
| 4 | A discrete dimension on one axis, one measure on the other, ≤ ~25 members | Bar chart, oriented to match which shelf the dimension is on |
| 5 | Same as 4 but with a second dimension on color | Grouped bars if the second dimension is small, stacked if the workbook stacked them — check `<encoding>` for a stack setting rather than assuming |
| 6 | Two measures, no dimension driving position | Scatter |
| 7 | A geographic role on a field (`semantic-role` contains `Geographic`) | Map — and check the Altair-versus-Plotly decision above, because this is a common reason to need Plotly |
| 8 | Dimension on color with one measure and no positional dimension | Pie, but propose a bar instead and say why |
| 9 | Many dimensions on rows and columns with text marks | A table — `st.dataframe`, not a chart |
| 10 | Nothing above fits | **Do not guess silently.** Render a table of the underlying data and record the object `partial`, noting that the chart type needs confirmation |

Rung 10 is the important one. A wrong chart type is more damaging than a table, because it looks finished — a reviewer reads a chart as a decision you made, and a table as an obvious placeholder.

**Report chart-type coverage at the end of Step 5.** For every in-scope worksheet, state which rung resolved it and whether the mark type was explicit or inferred:

```
Chart coverage: 11 worksheets
  explicit mark type   7
  inferred (rungs 2-9) 3   <- state the rung per sheet
  unresolved (rung 10) 1   <- "Regional Detail" rendered as a table, needs confirmation
```

Aggregate counts are what make an inference reviewable. A migration that inferred ten of eleven chart types is a different artifact from one that read them all out of the XML, and the user cannot tell the two apart from the app.

## Worksheet to render function

One worksheet becomes one function that takes an already-filtered frame and renders. Keeping them separate is what makes fragments (Step 6) and layout rearrangement possible later.

```python
def render_monthly_trend(df: pd.DataFrame) -> None:
    chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("month:T", title="Month"),
            y=alt.Y("sales:Q", title="Sales", axis=alt.Axis(format="$,.0f")),
            color=alt.Color("category:N", title="Category"),
            tooltip=["month:T", "category:N", alt.Tooltip("sales:Q", format="$,.0f")],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")
```

`width="stretch"` is the current API. `use_container_width` is deprecated — if you are porting snippets from older Tableau-migration material, strip it.

## Shelves and marks card

Tableau's shelves and marks card map onto Altair's encoding channels almost one to one. The mental model transfers: a pill on a shelf is a field bound to a channel.

| Tableau | Altair encoding | Notes |
|---|---|---|
| Columns shelf | `x` | Continuous date → `:T`, dimension → `:N`, measure → `:Q` |
| Rows shelf | `y` | |
| Marks card → Color (dimension) | `color=alt.Color("cat:N")` | Set an explicit `scale=alt.Scale(domain=…, range=…)` so colors do not reshuffle when filters change the category set |
| Marks card → Color (measure) | `color=alt.Color("profit:Q", scale=alt.Scale(scheme="redyellowgreen"))` | Diverging measure — center the scale with `domainMid=0` |
| Marks card → Size | `size=alt.Size("sales:Q")` | |
| Marks card → Label | layered `mark_text` | Altair has no `text_auto`; layer a text mark sharing the encoding |
| Marks card → Detail | `detail=alt.Detail("id:N")` | Adds grain without adding a visual channel |
| Marks card → Tooltip | `tooltip=[…]` | Use `alt.Tooltip(field, format=…)` to match Tableau number formatting |
| Marks card → Shape | `shape=alt.Shape("cat:N")` | |
| Mark type `Bar` / `Line` / `Area` / `Circle` / `Square` | `mark_bar` / `mark_line` / `mark_area` / `mark_circle` / `mark_square` | |
| Mark type `Automatic` | Infer from shelves: date on Columns → line; two measures → point; else bar | |
| Sort by measure descending | `alt.X("cat:N", sort="-y")` | |
| Axis number format `$#,##0` | `axis=alt.Axis(format="$,.0f")` | Altair formats are D3, not Tableau |
| Stacked vs side-by-side bars | `stack=True` (default) vs `xOffset="cat:N"` | |

**Take the palette from the workbook, not from the library.** The inspector's `visual_styles` section reports the colours the workbook actually used — explicit member-to-hex maps from `color-one-way-map`, the colour-encoded field and its named palette, worksheet and dashboard backgrounds, and any legend the author hand-wrote in an annotation. Feed those hex values straight into an explicit scale:

```python
# From the inventory's visual_styles.member_colors
STATUS_COLORS = {"Profitable": "#59a14f", "Loss": "#e15759"}

color = alt.Color(
    "profit_status:N",
    scale=alt.Scale(domain=list(STATUS_COLORS), range=list(STATUS_COLORS.values())),
    title="Profit Status",
)
```

This matters more than it looks. Dashboard colour is usually **semantic** — red is at-risk, green is on-track — and often documented in a legend somebody agreed to. Letting Altair assign from its default scheme produces a chart that is numerically identical and says something different, which readers spot faster than a wrong total. If the inspector reports a member-colour map, treat reusing it as required rather than as polish.

**Set explicit color domains.** Tableau assigns a category to a color and keeps it. Altair assigns from the data present in the current frame, so filtering out a category shifts every color after it — a visual regression users notice immediately. Build one color map from the full category list and reuse it across charts.

## Dual axis and reference lines

Tableau's dual axis becomes a layer with independent y scales:

```python
bars = alt.Chart(df).mark_bar(opacity=0.7).encode(x="month:T", y="sales:Q")
line = alt.Chart(df).mark_line(color="#29B5E8", point=True).encode(x="month:T", y="margin_pct:Q")
st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent"), width="stretch")
```

A reference line is another layer. Compute the constant in SQL or from the frame, then rule it:

```python
avg_rule = (
    alt.Chart(df).mark_rule(strokeDash=[5, 5], color="gray")
    .encode(y="mean(sales):Q")
)
st.altair_chart(alt.layer(bars, avg_rule), width="stretch")
```

Tableau's synchronized dual axis (both measures on one shared scale) is `resolve_scale(y='shared')` — the default — so simply do not add `resolve_scale` for that case.

## Dashboard layout

The inventory gives you each zone's `x`, `y`, `w`, `h` **and a derived `layout` model per dashboard** that has already done the arithmetic: for every container it reports the direction, the children in order, and the `st.columns([...])` call with weights reduced to their smallest whole-number ratio. Use those weights. Deriving them by eye from raw pixel widths is how a workbook's 60/40 split silently ships as 50/50.

```
| Container                  | Direction  | Children                   | Streamlit             |
| `layout-flow/layout-flow`  | horizontal | Monthly Trend, Category Mix| `st.columns([3, 2])`  |
```

That row came from `w='720'` and `w='480'`. Nothing about it needs judgement.

**Proportion transfers; absolute position does not.** This is a property of Streamlit's layout model, not a shortcut: `st.columns` takes relative weights, so a tiled dashboard's horizontal proportions port exactly, while a floating zone pinned at `x=400 y=100` has no equivalent. The inspector flags overlapping siblings with ⚠️ and raises a warning; when you see one, reflow it into the normal flow or use `st.popover` for a control panel, and **tell the user the arrangement changed** rather than approximating it with CSS. Absolute positioning via injected CSS breaks on resize and across Streamlit versions, and a dashboard that looks right at one viewport and broken at another is worse than one that was honestly reflowed.

Heights are the weaker axis. A vertical container needs no Streamlit container at all — sequential calls stack — so the reported `child_heights_px` are useful only as the `height=` argument on the charts inside, and only as a starting proportion. Do not chase vertical pixel parity.

| Tableau | Streamlit |
|---|---|
| Tiled zones side by side | `st.columns([w1, w2])` — take the weights from the derived `layout` model |
| Tiled zones stacked | Sequential calls, no container needed |
| Horizontal container of equal items | `st.container(horizontal=True)` — responsive; prefer over `st.columns` unless you need fixed ratios |
| A zone with a border / background | `st.container(border=True)` |
| Floating zone overlapping a sheet | Flagged ⚠️ in the derived layout. Reflow into the normal flow, or `st.popover` if it is a control panel — never injected CSS |
| Dashboard with sheet-selector tabs | `st.tabs` |
| Multiple dashboards in one workbook | `st.navigation` + `st.Page`, one page per dashboard, in an `app_pages/` folder |
| KPI text zones along the top | `st.metric` inside `st.columns` |
| Filter cards docked in a side container | `st.popover("Filters")` in a top bar (see the note below on why not the sidebar) |
| Legend zone | Chart-native legend; drop the separate zone |
| Title / caption zone | `st.title`, `st.subheader`, `st.caption` |

A typical translation of a tiled dashboard with a KPI strip and a 60/40 split:

```python
st.set_page_config(page_title="Executive Overview", page_icon=":material/monitoring:", layout="wide")
st.title("Executive overview")

with st.popover("Filters", type="tertiary"):
    regions = st.multiselect("Region", all_regions, default=all_regions)
    time_range = st.segmented_control("Time range", TIME_RANGES, default="All")

df = load_data(regions, time_range)

k1, k2, k3 = st.columns(3)
k1.metric("Sales", f"${df.sales.sum():,.0f}")
k2.metric("Profit", f"${df.profit.sum():,.0f}")
k3.metric("Margin", f"{df.profit.sum() / df.sales.sum():.1%}")

left, right = st.columns([3, 2])   # from the derived layout model, not guessed
with left:
    with st.container(border=True):
        render_monthly_trend(df)
with right:
    with st.container(border=True):
        render_category_mix(df)
```

**Prefer a filter popover or top bar over the sidebar** when the Tableau dashboard had docked filter cards — it keeps the filters visually adjacent to the charts they affect, as they were in the workbook. Use the sidebar for app-level navigation instead.

## Formatting parity

Small format mismatches read as bugs to users migrating off a dashboard they know well:

- **Number formats** are D3 format strings in Altair, not Tableau's. `$#,##0` → `$,.0f`; `0.0%` → `.1%`; `#,##0.0K` → `,.1s`.
- **Date axis granularity** — Tableau's `MONTH([Order Date])` truncates. Truncate in SQL with `DATE_TRUNC` and encode as `:T`, rather than letting the chart bin an untruncated timestamp.
- **Null handling** — Tableau hides null marks by default. Altair plots gaps in lines; filter nulls explicitly if the workbook showed a continuous line.
- **Sort order** — Tableau's default alphabetical sort differs from data order. Set `sort=` explicitly on categorical axes.
