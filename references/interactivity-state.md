# Interactivity and state

Tableau's interactivity is declarative: quick filters filter, actions propagate, hierarchies drill. In Streamlit you own the wiring. This reference covers the four things a Tableau dashboard does that need explicit implementation.

## Quick filters and parameters as widgets

Filters and parameters both become widgets, but they behave differently and translate differently.

| Tableau | Widget | Where the value goes |
|---|---|---|
| Categorical quick filter (multi-select) | `st.multiselect` | `WHERE col IN (…)` bind params |
| Categorical quick filter (single-select) | `st.selectbox`, or `st.segmented_control` for ≤ 5 options | `WHERE col = ?` |
| Quantitative range filter | `st.slider` with a tuple value | `WHERE col BETWEEN ? AND ?` |
| Date range filter | `st.date_input` with a tuple | `WHERE d BETWEEN ? AND ?` |
| Relative date filter (last N months) | `st.segmented_control` over named ranges | `WHERE d >= DATEADD(…)` |
| Top-N filter | `st.slider` for N | `QUALIFY DENSE_RANK() OVER (ORDER BY m DESC) <= ?` |
| Integer / float parameter | `st.slider`, `st.number_input` | Substituted into the calculation |
| String-list parameter (measure switcher) | `st.selectbox` | Selects which column or expression to aggregate |
| Boolean parameter | `st.toggle` | Branches the calculation |
| Context filter | Same widget as its type | Applied earlier in the pipeline — see `calculation-translation.md` |

A parameter that switches which measure is displayed needs a whitelist, not string interpolation, because it becomes part of the SQL rather than a bind value:

```python
MEASURES = {"Sales": "sales", "Profit": "profit", "Quantity": "quantity"}
choice = st.selectbox("Measure", list(MEASURES))
column = MEASURES[choice]          # validated against a fixed mapping
df = conn.query(f"SELECT region, SUM({column}) AS value FROM orders GROUP BY 1")
```

Identifiers cannot be bound as parameters, so the mapping is what keeps user input out of the SQL text. Never format a raw widget value into a query.

## Filter actions

A Tableau filter action — click a bar in one sheet, the other sheets filter — has no Streamlit equivalent. It becomes a chart selection event, a `st.session_state` key, and explicit filtering of the downstream frames.

This is the most expensive part of most migrations. Confirm it is actually required before building it; a shared filter bar is often what users wanted anyway, and it is far simpler.

### With Altair

The chart must declare a selection parameter before `on_select` does anything:

```python
click = alt.selection_point(fields=["category"], name="pick", empty=False)

chart = (
    alt.Chart(df_cat)
    .mark_bar()
    .encode(
        x=alt.X("category:N", sort="-y"),
        y="sales:Q",
        opacity=alt.condition(click, alt.value(1.0), alt.value(0.35)),
    )
    .add_params(click)
    .properties(height=300)
)

event = st.altair_chart(chart, on_select="rerun", key="cat_chart", width="stretch")

selected = None
if event and event.selection.get("pick"):
    selected = event.selection["pick"][0]["category"]
```

The `opacity=alt.condition(...)` line gives you Tableau's highlight behavior for free — selected marks stay solid, others dim — without a rerun.

### With Plotly

```python
event = st.plotly_chart(fig, on_select="rerun", key="cat_chart", width="stretch")

selected = None
if event and event.selection and event.selection["points"]:
    selected = event.selection["points"][0]["x"]
```

### Applying the selection

Derive downstream frames from the selection instead of mutating shared state where you can — it avoids a rerun round trip and keeps the data flow readable:

```python
df_detail = df if selected is None else df[df.category == selected]

if selected:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"Filtered to **{selected}**")
        if st.button("Clear", type="tertiary"):
            st.session_state.pop("cat_chart", None)
            st.rerun()

render_detail(df_detail)
```

Always give the user a way out. Tableau shows an active filter badge with a clear affordance; without one, a click that filters the dashboard looks like a bug.

**Use `st.session_state` when the selection must outlive the chart** — for example when several charts cross-filter each other, or when the selection also drives a query. In that case, guard the write against a redundant rerun:

```python
if "active_category" not in st.session_state:
    st.session_state.active_category = None

if selected is not None and selected != st.session_state.active_category:
    st.session_state.active_category = selected
    st.rerun()
```

The equality check matters: writing state and calling `st.rerun()` unconditionally on every pass creates an infinite rerun loop, since the selection is still present on the next run.

**Two-way cross-filtering — where two charts each filter the other — will loop** unless one direction wins. Pick a primary chart, or track which chart originated the current selection and skip re-applying it to that chart. Tableau resolves this internally; you have to decide the precedence explicitly.

## Highlight actions

A highlight action dims rather than filters. Do it inside the chart, not by filtering the data — `alt.condition` on `opacity` or `color`, as in the Altair example above. In Plotly, build an explicit color list:

```python
active = st.session_state.active_category
colors = ["#29B5E8" if c == active or active is None else "#cbd5e1" for c in df_cat.category]
fig.update_traces(marker_color=colors)
```

Hard-coding a color per position breaks when filters change the row order. Key the color off the category value, as above.

## Hierarchical drilldown

Tableau hierarchies (`Category → Sub-Category → Product`) drill in place with a `+` control. Model the drill path as a list in session state and derive the current level from its length:

```python
LEVELS = ["category", "sub_category", "product_name"]

if "drill_path" not in st.session_state:
    st.session_state.drill_path = []

depth = len(st.session_state.drill_path)
level = LEVELS[min(depth, len(LEVELS) - 1)]

with st.container(horizontal=True, vertical_alignment="center"):
    if depth and st.button(":material/arrow_back: Back", type="tertiary"):
        st.session_state.drill_path.pop()
        st.rerun()
    st.caption(" > ".join(["All"] + st.session_state.drill_path))

df_level = df
for i, value in enumerate(st.session_state.drill_path):
    df_level = df_level[df_level[LEVELS[i]] == value]

agg = df_level.groupby(level, as_index=False)["sales"].sum()
```

Push the filtering into SQL when the table is large — build the `WHERE` from the drill path with bind parameters rather than filtering a full extract in pandas.

## URL parameters for shareable views

Tableau dashboards can be shared as URLs carrying filter state. Bind the widget to a query parameter and Streamlit keeps the URL in sync automatically:

```python
region = st.selectbox("Region", options, key="region", bind="query-params")
```

Do not hand-roll this by reading and writing `st.query_params` around the widget — that pattern fights the widget lifecycle and produces stale values on the first run after a change. `bind="query-params"` with a `key` handles both directions.

## Rerun cost and fragments

Tableau re-renders only what changed. Streamlit re-runs the whole script, so a widget affecting one chart re-executes every chart unless you isolate it.

Wrap a chart plus its local controls in `@st.fragment` when the control only affects that chart — a per-card grain toggle, a sort order, a local top-N:

```python
@st.fragment
def render_trend_card(df: pd.DataFrame) -> None:
    with st.container(border=True):
        grain = st.segmented_control("Grain", ["Day", "Week", "Month"], default="Month")
        st.altair_chart(build_trend(df, grain), width="stretch")
```

Global filters that affect every chart belong outside fragments — they should trigger a full rerun.

Two more things that keep interactions responsive:

- **Cache the query, not the filter.** Cache the expensive aggregate with `@st.cache_data(ttl=…)`, then apply cheap selections to the result. A cache keyed on every widget value misses on every click.
- **Order the script so fast UI renders first.** Title, filter bar, and layout containers before the slow queries, so the page appears immediately instead of after the warehouse responds.

## Reset

Give the migrated dashboard one control that returns it to its initial state — Tableau's revert button:

```python
if st.button(":material/restart_alt: Reset", type="tertiary"):
    st.session_state.clear()
    st.rerun()
```
