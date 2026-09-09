"""
Local preview helpers.

Generates synthetic data from an enriched inventory and runs either:
  - The Streamlit app locally via `streamlit run preview_home.py`
  - The React/Next.js app locally via `npm run dev`

No Snowflake connection required — all data is synthetic.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Synthetic data generator
# ---------------------------------------------------------------------------

_DATE_POOL = [
    f"202{y}-{m:02d}-{d:02d}"
    for y in (3, 4, 5, 6)
    for m in range(1, 13)
    for d in (1, 8, 15, 22)
]

_REGION_POOL  = ["North America", "EMEA", "APAC", "LATAM", "ANZ"]
_STATUS_POOL  = ["Active", "Pending", "Closed", "Won", "Lost", "In Progress"]
_STAGE_POOL   = ["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
_TYPE_POOL    = ["Enterprise", "Commercial", "SMB", "Strategic", "Named"]
_COUNTRY_POOL = ["USA", "UK", "Germany", "France", "Canada", "Australia", "Japan"]
_DEPT_POOL    = ["Sales", "Marketing", "Engineering", "Finance", "Operations", "HR"]
_PRODUCT_POOL = ["Core Platform", "Analytics Add-on", "AI Suite", "Data Share", "Connector"]


def _synthetic_value(name: str, data_type: str, row_index: int) -> Any:
    """Generate a plausible synthetic value for a field."""
    n = name.lower()
    dt = (data_type or "").lower()

    # Date / timestamp types
    if "date" in dt or "time" in dt or "date" in n or "month" in n or "year" in n:
        return _DATE_POOL[row_index % len(_DATE_POOL)]

    # Boolean
    if "bool" in dt or n in ("is_active", "is_deleted", "flag"):
        return row_index % 3 != 0

    # Numeric
    if any(t in dt for t in ("int", "number", "float", "decimal", "numeric", "double")):
        # Revenue / amount / value → large numbers
        if any(k in n for k in ("revenue", "amount", "value", "total", "sales", "arr", "mrr", "deal")):
            base = (row_index + 1) * 12500.0
            return round(base * (1 + (row_index % 7) * 0.15), 2)
        # Count / qty → small integers
        if any(k in n for k in ("count", "qty", "quantity", "num", "number_of")):
            return (row_index % 50) + 1
        # Percentage
        if any(k in n for k in ("pct", "percent", "rate", "ratio")):
            return round(((row_index * 7) % 100) / 100, 4)
        # Default numeric
        return round((row_index + 1) * 1234.56, 2)

    # VARCHAR — infer from name keywords
    if any(k in n for k in ("region", "territory", "area", "zone", "geo")):
        return _REGION_POOL[row_index % len(_REGION_POOL)]
    if any(k in n for k in ("status", "state")):
        return _STATUS_POOL[row_index % len(_STATUS_POOL)]
    if any(k in n for k in ("stage",)):
        return _STAGE_POOL[row_index % len(_STAGE_POOL)]
    if any(k in n for k in ("type", "segment", "tier", "category", "class")):
        return _TYPE_POOL[row_index % len(_TYPE_POOL)]
    if any(k in n for k in ("country", "nation")):
        return _COUNTRY_POOL[row_index % len(_COUNTRY_POOL)]
    if any(k in n for k in ("dept", "department", "team", "division")):
        return _DEPT_POOL[row_index % len(_DEPT_POOL)]
    if any(k in n for k in ("product", "sku", "item", "service")):
        return _PRODUCT_POOL[row_index % len(_PRODUCT_POOL)]
    if any(k in n for k in ("owner", "rep", "manager", "user", "name", "account", "customer")):
        first = ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry"][row_index % 8]
        last  = ["Smith", "Jones", "Chen", "Patel", "Kim", "Brown", "Davis", "Wilson"][row_index % 8]
        return f"{first} {last}" if "name" in n else f"{first[0]}{last.lower()}"

    # Fallback: category label
    prefix = re.sub(r"[^a-z]", "", n)[:6].title() or "Item"
    return f"{prefix}_{(row_index % 20) + 1:02d}"


def generate_synthetic_table(
    table_name: str,
    dims: list[dict],
    facts: list[dict],
    rows: int = 25,
) -> list[dict[str, Any]]:
    """Generate `rows` synthetic rows for a table."""
    columns = dims + facts
    result = []
    for i in range(rows):
        row: dict[str, Any] = {}
        for col in columns:
            col_name = col.get("name", "COL").upper()
            data_type = col.get("data_type") or col.get("original", {}).get("datatype") or "VARCHAR"
            row[col_name] = _synthetic_value(col.get("name", ""), data_type, i)
        result.append(row)
    return result


def generate_all_tables(inventory: dict, rows: int = 25) -> dict[str, list[dict]]:
    """
    Return {table_name: [row, ...]} for every table in the inventory.
    Maps dimensions/facts to their source table.
    """
    dims  = inventory.get("dimensions", [])
    facts = inventory.get("facts", []) + inventory.get("metrics", [])
    tables_meta = inventory.get("tables", [])

    # Build per-table field lists
    table_fields: dict[str, dict] = {t["name"]: {"dims": [], "facts": []} for t in tables_meta}

    for d in dims:
        tbl = d.get("table") or d.get("source_view") or (tables_meta[0]["name"] if tables_meta else "DEFAULT")
        if tbl not in table_fields:
            table_fields[tbl] = {"dims": [], "facts": []}
        table_fields[tbl]["dims"].append(d)

    for f in facts:
        tbl = f.get("table") or f.get("source_view") or (tables_meta[0]["name"] if tables_meta else "DEFAULT")
        if tbl not in table_fields:
            table_fields[tbl] = {"dims": [], "facts": []}
        table_fields[tbl]["facts"].append(f)

    result: dict[str, list[dict]] = {}
    for tname, fields in table_fields.items():
        if fields["dims"] or fields["facts"]:
            result[tname] = generate_synthetic_table(tname, fields["dims"], fields["facts"], rows)

    # Fallback: if nothing mapped, make one table from all fields
    if not result:
        all_dims  = dims[:10]
        all_facts = facts[:5]
        tname = tables_meta[0]["name"] if tables_meta else "SYNTHETIC_TABLE"
        result[tname] = generate_synthetic_table(tname, all_dims, all_facts, rows)

    return result


# ---------------------------------------------------------------------------
# Streamlit preview: generate preview_*.py files with mock session
# ---------------------------------------------------------------------------

_MOCK_SESSION_HEADER = '''\
"""
LOCAL PREVIEW — generated by bi-modernization skill.
Uses synthetic data — no Snowflake connection required.
Run with:  streamlit run {home_file}
"""
import re
import json
import pandas as pd

# ── Synthetic data (auto-generated) ──────────────────────────────────────────
_MOCK_DATA = {mock_data_json}


class _MockResult:
    """Mimics snowpark DataFrame result."""
    def __init__(self, df):
        self._df = df

    def to_pandas(self):
        return self._df.copy()


class _MockSession:
    """Drop-in replacement for get_active_session() result."""

    def sql(self, query: str) -> _MockResult:
        # Extract table name from SQL (best-effort)
        m = re.search(r'FROM\\s+([\\w\\.]+)', query, re.IGNORECASE)
        table_key = m.group(1).split(".")[-1].upper() if m else ""

        # Find the best matching mock table
        for key, rows in _MOCK_DATA.items():
            if key.upper() == table_key or key.upper() in table_key or table_key in key.upper():
                return _MockResult(pd.DataFrame(rows))

        # Fallback: return first table
        first_rows = next(iter(_MOCK_DATA.values()), [])
        return _MockResult(pd.DataFrame(first_rows))


def get_active_session():
    return _MockSession()


'''


def generate_streamlit_preview(
    app_dir: str,
    inventory: dict,
    rows: int = 25,
) -> dict[str, Any]:
    """
    Generate preview_*.py versions of every dashboard_*.py in app_dir.
    The preview files use synthetic data instead of a Snowflake session.

    Returns {preview_home: path, preview_files: [...]}
    """
    app_path = Path(app_dir)
    dashboard_files = list(app_path.glob("dashboard_*.py"))

    if not dashboard_files:
        return {"status": "error", "error": f"No dashboard_*.py files found in {app_dir}"}

    # Generate synthetic data
    mock_tables = generate_all_tables(inventory, rows=rows)
    mock_json = json.dumps(mock_tables, indent=2, default=str)

    generated: list[str] = []

    for src_path in dashboard_files:
        original = src_path.read_text(encoding="utf-8")

        # Patch: replace the real session import with our mock
        patched = original
        patched = re.sub(
            r"from snowflake\.snowpark\.context import get_active_session\n",
            "",
            patched,
        )
        patched = re.sub(
            r"_session\s*=\s*get_active_session\(\)\n",
            "",
            patched,
        )
        # Remove the agent REST API block to avoid import errors in preview
        patched = re.sub(
            r"import requests.*?st\.sidebar\.markdown\(_answer\)\n",
            "    st.sidebar.info('Agent chat not available in preview mode.')\n",
            patched,
            flags=re.DOTALL,
        )

        # Prepend mock header
        header = _MOCK_SESSION_HEADER.format(
            home_file="preview_home.py",
            mock_data_json=mock_json,
        )
        preview_content = header + patched

        preview_path = app_path / f"preview_{src_path.name}"
        preview_path.write_text(preview_content, encoding="utf-8")
        generated.append(str(preview_path))

    # Generate preview_home.py
    home_path = app_path / "home.py"
    if home_path.exists():
        home_src = home_path.read_text(encoding="utf-8")
        preview_home_src = home_src.replace("dashboard_", "preview_dashboard_")
        preview_home_path = app_path / "preview_home.py"
        preview_home_path.write_text(preview_home_src, encoding="utf-8")
        generated.append(str(preview_home_path))
    else:
        preview_home_path = app_path / "preview_home.py"

    return {
        "status": "ok",
        "preview_home": str(preview_home_path),
        "preview_files": generated,
        "mock_table_count": len(mock_tables),
        "mock_rows_per_table": rows,
    }


# ---------------------------------------------------------------------------
# React preview: inject mock data + .env.local
# ---------------------------------------------------------------------------

def _gen_mock_data_ts(mock_tables: dict[str, list[dict]]) -> str:
    """Generate src/lib/mock-data.ts with static synthetic JSON."""
    entries: list[str] = []
    for tname, rows in mock_tables.items():
        safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", tname.upper())
        entries.append(
            f"  {safe_key}: {json.dumps(rows, indent=4, default=str)},"
        )

    return (
        "// AUTO-GENERATED synthetic data for local preview.\n"
        "// Do not edit — regenerate with bi-modernization enrich-charts.\n\n"
        "export const MOCK_DATA: Record<string, Record<string, unknown>[]> = {\n"
        + "\n".join(entries)
        + "\n};\n"
    )


def _gen_mock_snowflake_ts() -> str:
    """Generate a mock-friendly src/lib/snowflake.ts for local preview."""
    return '''\
/**
 * LOCAL PREVIEW snowflake.ts — uses MOCK_DATA instead of Snowflake API.
 * The real snowflake.ts is preserved as snowflake.prod.ts.
 * Run `npm run dev` to see the preview.
 */
import { MOCK_DATA } from "./mock-data";

function findMockTable(sql: string): Record<string, unknown>[] {
  const m = sql.match(/FROM\\s+([\\w.]+)/i);
  const tableName = m ? m[1].split(".").pop()?.toUpperCase() ?? "" : "";

  for (const [key, rows] of Object.entries(MOCK_DATA)) {
    if (key === tableName || key.includes(tableName) || tableName.includes(key)) {
      return rows;
    }
  }
  // Fallback: return first available table
  return Object.values(MOCK_DATA)[0] ?? [];
}

export async function query<T = Record<string, unknown>>(sql: string): Promise<T[]> {
  // Simulate network latency
  await new Promise(r => setTimeout(r, 80 + Math.random() * 120));
  return findMockTable(sql) as T[];
}

export async function callAgent(_agentFqn: string, message: string): Promise<string> {
  await new Promise(r => setTimeout(r, 500));
  return `[Preview mode] You asked: "${message}"\\n\\nAgent responses are not available in local preview. Deploy to Snowflake to enable live agent chat.`;
}
''';


def generate_react_preview(
    app_dir: str,
    inventory: dict,
    rows: int = 25,
) -> dict[str, Any]:
    """
    Inject synthetic mock data and a mock snowflake.ts into the React app at app_dir.
    Backs up the real snowflake.ts as snowflake.prod.ts first.

    Returns info for running `npm run dev`.
    """
    app_path = Path(app_dir)
    lib_dir = app_path / "src" / "lib"

    if not lib_dir.exists():
        return {"status": "error", "error": f"src/lib not found in {app_dir}"}

    mock_tables = generate_all_tables(inventory, rows=rows)

    # Write mock-data.ts
    mock_data_path = lib_dir / "mock-data.ts"
    mock_data_path.write_text(_gen_mock_data_ts(mock_tables), encoding="utf-8")

    # Backup real snowflake.ts and replace with mock version
    sf_path = lib_dir / "snowflake.ts"
    sf_prod_path = lib_dir / "snowflake.prod.ts"

    if sf_path.exists() and not sf_prod_path.exists():
        sf_prod_path.write_text(sf_path.read_text(encoding="utf-8"), encoding="utf-8")

    sf_path.write_text(_gen_mock_snowflake_ts(), encoding="utf-8")

    # Write .env.local so Next.js knows it's preview
    env_path = app_path / ".env.local"
    env_path.write_text("NEXT_PUBLIC_PREVIEW=true\n", encoding="utf-8")

    return {
        "status": "ok",
        "app_dir": str(app_dir),
        "mock_data_path": str(mock_data_path),
        "mock_table_count": len(mock_tables),
        "mock_rows_per_table": rows,
        "snowflake_backup": str(sf_prod_path),
    }


# ---------------------------------------------------------------------------
# Local server launchers
# ---------------------------------------------------------------------------

def run_streamlit_preview(preview_home: str) -> subprocess.Popen:
    """
    Launch `streamlit run <preview_home>` in the background.
    Returns the Popen handle so the caller can kill it.
    """
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", preview_home,
         "--server.headless", "true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def run_react_preview(app_dir: str) -> subprocess.Popen:
    """
    Run `npm run dev` inside app_dir in the background.
    Returns the Popen handle.
    """
    node_modules = Path(app_dir) / "node_modules"
    if not node_modules.exists():
        # Install dependencies first (blocking)
        subprocess.run(["npm", "install"], cwd=app_dir, check=True)

    return subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=app_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def restore_react_production(app_dir: str) -> None:
    """
    Restore the real snowflake.ts from the prod backup.
    Called after the developer is done previewing.
    """
    lib_dir = Path(app_dir) / "src" / "lib"
    sf_path = lib_dir / "snowflake.ts"
    sf_prod_path = lib_dir / "snowflake.prod.ts"

    if sf_prod_path.exists():
        sf_path.write_text(sf_prod_path.read_text(encoding="utf-8"), encoding="utf-8")
        sf_prod_path.unlink()
