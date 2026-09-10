# App structure — laying out a migrated dashboard across files

A single-file `streamlit_app.py` is fine for one worksheet and becomes unmanageable at a
real dashboard's size. This is the layout to grow into, and the reason each part exists is
specific to migration rather than to taste.

The shape below is taken from a production Snowflake-internal dashboard of comparable
scope — seven pages, twenty-plus queries — rather than invented here.

```
<app>/
├── streamlit_app.py          # page_config, navigation, shared filters. No queries.
├── helpers.py                # connection, SQL loader, cache, shared constants
├── app_pages/                # one file per Tableau dashboard
│   ├── executive_overview.py
│   └── regional_detail.py
├── sql/                      # one file per query — the migration's real output
│   ├── monthly_trend.sql
│   ├── category_mix.sql
│   └── kpi_summary.sql
├── pyproject.toml
├── snowflake.yml
└── .streamlit/
    └── config.toml
```

## The mapping from Tableau

| Tableau | Goes to |
|---|---|
| Workbook | The app |
| Dashboard | One file in `app_pages/`, one `st.Page` |
| Dashboard-level or context filter | The shared sidebar in `streamlit_app.py` |
| Worksheet | A render block in its dashboard's page file |
| Worksheet's query | One file in `sql/` |
| Calculated field | A CTE or expression inside the relevant `sql/` file |
| Color palette | A constants dict in `helpers.py` |
| Sheet-local quick filter | A widget inside that page file, not the shared sidebar |

Dashboards that Tableau grouped into a navigator or a story become sections, which
`st.navigation` takes as a dict:

```python
pages = {
    "Overview": [
        st.Page("app_pages/executive_overview.py", title="Executive Overview", default=True),
    ],
    "Detail": [
        st.Page("app_pages/regional_detail.py", title="Regional Detail"),
    ],
}
current_page = st.navigation(pages, position="sidebar")
st.title(current_page.title)
current_page.run()
```

## Why `sql/` as separate files is the important part

This is the decision that pays off most in a migration, for a reason that has nothing to do
with tidiness:

**A `.sql` file is runnable on its own.** Step 8 asks you to reconcile aggregates against the
workbook at each grain. If the query lives in a `.sql` file, the reconciliation is *execute
this file and compare* — no Python, no app, no reading around render code. A reviewer who
knows the data but not Streamlit can check your translation. That is also what makes the
audit-workbook offer in `migration-qa.md` cheap to honour: the audit workbook and the app run
the same text.

Two further consequences worth having:

- **A formula change produces a readable diff.** When a Tableau calculation is corrected, the
  diff is confined to one `.sql` file instead of buried in a Python string.
- **The order-of-operations pipeline stays visible.** The staged CTEs from
  `calculation-translation.md` read as SQL, in order, rather than as concatenated fragments.

Bind parameters with a leading `params` CTE so filter values are typed once and threaded
through every stage:

```sql
-- monthly_trend.sql
-- Parameters: start_dt (DATE), end_dt (DATE), regions (ARRAY)
WITH params AS (
    SELECT ?::DATE AS start_dt, ?::DATE AS end_dt, ?::ARRAY AS regions
),
-- stage 2-3: data source filters and CONTEXT filters, before any FIXED LOD
context_filtered AS (
    SELECT o.*
    FROM orders o
    CROSS JOIN params p
    WHERE o.order_date BETWEEN p.start_dt AND p.end_dt
      AND (ARRAY_SIZE(p.regions) = 0 OR ARRAY_CONTAINS(o.region::VARIANT, p.regions))
),
-- stage 5: FIXED LOD, computed on the context-filtered set
with_lod AS (
    SELECT c.*, MIN(order_date) OVER (PARTITION BY customer_id) AS customer_acquisition
    FROM context_filtered c
)
-- stage 6+: dimension filters and the display aggregate
SELECT DATE_TRUNC('month', order_date) AS month, SUM(sales) AS sales
FROM with_lod
GROUP BY 1
ORDER BY 1
```

The comment header naming the parameters and their order is not decoration. Positional binds
have no names at the call site, so the file is the only place the contract is written down.

## `helpers.py` — one cached runner, not one function per query

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

SQL_DIR = Path(__file__).parent / "sql"
_SQL_DIR_RESOLVED = str(SQL_DIR.resolve())

# Tableau's palette, kept in one place so every page renders a dimension identically.
REGION_COLORS = {"West": "#4C78A8", "East": "#F58518", "Central": "#54A24B", "South": "#E45756"}


def _read_sql(filename: str) -> str:
    resolved = (SQL_DIR / filename).resolve()
    if not str(resolved).startswith(_SQL_DIR_RESOLVED):
        raise ValueError(f"SQL file path escapes the sql directory: {filename}")
    return resolved.read_text()


@st.cache_data(ttl=3600)
def run_sql(filename: str, *params) -> pd.DataFrame:
    """Run a query from sql/ with positional binds.

    Cached on (filename, params), which is the right key: the same file with different
    filter values is a different result, and nothing else varies.
    """
    session = st.connection("snowflake").session()
    df = session.sql(_read_sql(filename), params=list(params)).to_pandas()
    df.columns = df.columns.str.lower()   # Snowflake returns uppercase identifiers
    return df
```

Four things in that small file are load-bearing:

1. **The path check.** `filename` reaching `_read_sql` from anything user-influenced would
   otherwise be a traversal. Cheap to add now, awkward to retrofit.
2. **`@st.cache_data` on the runner, not on each query.** One decorator, correctly keyed,
   instead of a per-query wrapper each of which can get its key wrong.
3. **Column lowercasing in one place.** Otherwise every page repeats it and one page forgets.
4. **`st.connection` is not cached.** Caching a connection object is the classic SiS mistake;
   cache the *result*.

## Page files read shared state at the top

Each file in `app_pages/` is a script body, not a function. Read the shared filters first,
then render:

```python
# app_pages/executive_overview.py
from __future__ import annotations

import streamlit as st
from helpers import REGION_COLORS, run_sql

start_date = st.session_state.get("start_date")
end_date = st.session_state.get("end_date")
regions = st.session_state.get("regions", [])

trend = run_sql("monthly_trend.sql", start_date, end_date, regions)
```

Dashboard-level filters belong in `streamlit_app.py`, writing into `st.session_state`, because
in Tableau they applied to every sheet on the dashboard. A quick filter that sat on a single
worksheet belongs in that page file instead. Getting this split wrong is a fidelity bug, not a
layout preference: a filter promoted to the sidebar silently starts affecting charts the
workbook did not apply it to.

## `snowflake.yml` artifacts must list the new folders

A missing path here uploads nothing and the app fails at import — globs are not implicit:

```yaml
artifacts:
  - pyproject.toml
  - snowflake.yml
  - streamlit_app.py
  - helpers.py
  - app_pages/*.py
  - sql/*.sql
  - .streamlit/config.toml
```

`sql/*.sql` is the one most often forgotten, because nothing in local preview depends on the
manifest — the app works on a laptop and dies in Snowflake. Run the pre-flight artifact check
from the scaffolding skill's `snowflake-deployment.md`.

## When to split, and one trap

Split when there is more than one dashboard, or when a page file passes roughly 300 lines.
Below that, `streamlit_app.py` plus `sql/` is enough — `app_pages/` for a single dashboard is
ceremony.

**The trap: a shared CTE copied into every `sql/` file.** The reference app this layout comes
from carries a comment reading *"this CTE is duplicated across 8 SQL files. KEEP IN SYNC"* —
and it is in fact now in nine. That is the whole argument in one detail: the warning was
written honestly, the duplication grew anyway, and the note meant to prevent drift drifted
first. A corrected Tableau formula would have to be fixed in nine places, and one will be
missed.

When two queries need the same derived set, promote it to a Snowflake view and select from it
in both, rather than pasting it. A migration is exactly when this bites, because every
worksheet on a dashboard tends to share the same context-filtered base — so the shared CTE is
not an edge case, it is the normal shape of the work.
