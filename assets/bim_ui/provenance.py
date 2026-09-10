"""
bim_ui.provenance — data freshness, row counts, and source lineage.

An enterprise viewer will not act on a number they cannot trace. Tableau shows
the extract refresh time; a dashboard that shows nothing is asking to be
distrusted, and "is this live?" is the first question in every review.

This module renders where the number came from, when, how many rows, and which
warehouse ran it. In demo mode it says so loudly rather than implying the
synthetic figures are real.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

import streamlit as st


@dataclass
class Provenance:
    """Where a panel's data came from."""

    sources: Sequence[str] = ()          # fully-qualified table or view names
    semantic_view: str | None = None
    warehouse: str | None = None
    row_count: int | None = None
    queried_at: datetime | None = None
    elapsed_ms: float | None = None
    demo_mode: bool = False
    freshness_note: str | None = None    # e.g. "source refreshed nightly at 02:00 UTC"

    def with_timing(self, started: float, rows: int | None = None) -> "Provenance":
        """Stamp query duration and row count from a perf_counter start."""
        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.queried_at = datetime.now(timezone.utc)
        if rows is not None:
            self.row_count = rows
        return self


def _age_phrase(ts: datetime) -> str:
    """'just now' / '4 min ago' / absolute timestamp beyond a day."""
    now = datetime.now(timezone.utc)
    ts_utc = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    secs = max(0.0, (now - ts_utc).total_seconds())
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        hours = int(secs // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return ts_utc.strftime("%b %d, %Y %H:%M UTC")


def banner(prov: Provenance, ctx=None, *, container=None) -> None:
    """Render the provenance strip."""
    target = container or st
    parts: list[str] = []

    if prov.demo_mode:
        parts.append(
            '<span class="bim-synthetic">SYNTHETIC DATA</span> '
            "<span>not connected to a live source</span>"
        )
    elif prov.queried_at:
        elapsed = f" in {prov.elapsed_ms:,.0f} ms" if prov.elapsed_ms else ""
        parts.append(f"Queried <b>{_age_phrase(prov.queried_at)}</b>{elapsed}")

    if prov.row_count is not None:
        parts.append(f"<b>{prov.row_count:,}</b> rows")

    if prov.sources:
        shown = ", ".join(prov.sources[:2])
        if len(prov.sources) > 2:
            shown += f" +{len(prov.sources) - 2} more"
        parts.append(f"Source <b>{shown}</b>")

    if prov.semantic_view:
        parts.append(f"Semantic view <b>{prov.semantic_view}</b>")
    if prov.warehouse:
        parts.append(f"Warehouse <b>{prov.warehouse}</b>")
    if prov.freshness_note:
        parts.append(prov.freshness_note)

    if not parts:
        return
    target.markdown(
        '<div class="bim-prov">' + "".join(f"<span>{p}</span>" for p in parts) + "</div>",
        unsafe_allow_html=True,
    )


def header(title: str, subtitle: str = "", *, container=None) -> None:
    """Page header band."""
    target = container or st
    sub = f"<p>{subtitle}</p>" if subtitle else ""
    target.markdown(
        f'<div class="bim-header"><h1>{title}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )


def detail_expander(prov: Provenance, *, container=None,
                    extra: dict[str, Any] | None = None) -> None:
    """Full lineage detail, collapsed by default.

    The banner answers "can I trust this at a glance"; this answers "prove it"
    for the analyst who has to reconcile against the source system.
    """
    target = container or st
    with target.expander("Data lineage and query detail"):
        rows: list[tuple[str, str]] = []
        if prov.demo_mode:
            rows.append(("Mode", "Synthetic demo data — no live query executed"))
        if prov.queried_at:
            ts = prov.queried_at
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            rows.append(("Queried at", ts.strftime("%Y-%m-%d %H:%M:%S UTC")))
        if prov.elapsed_ms is not None:
            rows.append(("Query duration", f"{prov.elapsed_ms:,.1f} ms"))
        if prov.row_count is not None:
            rows.append(("Rows returned", f"{prov.row_count:,}"))
        if prov.warehouse:
            rows.append(("Warehouse", prov.warehouse))
        if prov.semantic_view:
            rows.append(("Semantic view", prov.semantic_view))
        for i, src in enumerate(prov.sources, 1):
            rows.append((f"Source {i}", src))
        for k, v in (extra or {}).items():
            rows.append((k, str(v)))

        if not rows:
            st.caption("No lineage metadata recorded for this panel.")
            return
        body = "".join(
            f"<tr><td style='padding:3px 14px 3px 0;opacity:.7'>{k}</td>"
            f"<td style='padding:3px 0'><code>{v}</code></td></tr>"
            for k, v in rows
        )
        st.markdown(f"<table style='font-size:12px'>{body}</table>",
                    unsafe_allow_html=True)
