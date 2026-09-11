"""Multi-source parsing with resumable state.

A portfolio run is the normal case, not the exception: people arrive with a
folder, a Tableau project, or "our dashboards" rather than one file. Parsing them
one at a time by hand loses two things -- the ability to see shared tables across
workbooks (which is what decides how many semantic views you need), and any
record of progress when a run is interrupted partway through 24 files.

State is written to disk after every source rather than kept in the agent's head,
so an interrupted run resumes instead of restarting. Per-source failures are
collected, never fatal: one unreadable workbook must not sink the other 23.
"""

from __future__ import annotations

import glob as globlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .common.logger import get_logger

log = get_logger("portfolio")

# Extensions that identify a parseable source, per source type. Mirrors the
# crawler's mapping; kept here so a glob or explicit file list can be filtered
# without going through directory discovery.
_EXTENSIONS: dict[str, set[str]] = {
    "tableau": {".twb", ".twbx", ".tds", ".tdsx"},
    "powerbi": {".pbix", ".pbit", ".pbip"},
    "looker": {".lkml", ".lookml"},
    "denodo": {".vql"},
    "businessobjects": {".json", ".unv", ".unx"},
}


def resolve_sources(
    path: str,
    source_type: str,
    *,
    recurse: bool = True,
    max_files: int = 500,
) -> tuple[list[str], list[str]]:
    """Expand a file, directory, or glob into a concrete list of source paths.

    Returns (paths, notes). Notes carry anything the caller should tell the user
    -- a truncated crawl, a glob that matched nothing useful -- rather than
    failing silently on it.
    """
    notes: list[str] = []
    exts = _EXTENSIONS.get(source_type, set())

    # Glob first: a pattern is not a path, so os.path checks would reject it.
    if any(ch in path for ch in "*?[") :
        matched = sorted(globlib.glob(path, recursive=True))
        files = [f for f in matched if os.path.isfile(f)
                 and (not exts or Path(f).suffix.lower() in exts)]
        skipped = len(matched) - len(files)
        if skipped:
            notes.append(f"glob matched {skipped} path(s) that are not {source_type} sources")
        if not files:
            notes.append(f"glob matched no {source_type} sources: {path}")
        return files[:max_files], notes

    p = Path(path)
    if p.is_file():
        return [str(p)], notes

    if not p.is_dir():
        raise FileNotFoundError(f"Source not found: {path}")

    # Directory: reuse the bounded crawler so depth, file caps and symlink
    # behaviour match `crawl` exactly. Divergence there would mean `parse` on a
    # folder silently covered a different set of files than `crawl` reported.
    from .common.file_crawler import discover_files

    discovered = discover_files(
        root_path=str(p),
        source_type=source_type,
        # The crawler treats the root as depth 0 and guards with `depth >
        # max_depth`, so 0 means "root only" and 1 would already descend one
        # level -- which is not what --no-recurse promises.
        max_depth=12 if recurse else 0,
        max_files=max_files,
        follow_symlinks=False,
    )
    files = [d.path for d in discovered]
    if len(files) >= max_files:
        notes.append(
            f"stopped at the {max_files}-file cap. A portfolio this large needs a "
            "consolidation conversation before app code is written -- see "
            "references/portfolio-analysis.md"
        )
    if not files:
        notes.append(f"no {source_type} sources found under {path}")
    return files, notes


# ---------------------------------------------------------------------------
# Resumable state
# ---------------------------------------------------------------------------

def _load_state(state_path: str | None) -> dict[str, Any]:
    if not state_path or not os.path.isfile(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        # A corrupt checkpoint must not block the run; start over rather than
        # crash on someone else's half-written file.
        log.warning("ignoring unreadable state file %s (%s)", state_path, exc)
        return {}


def _save_state(state_path: str | None, state: dict[str, Any]) -> None:
    if not state_path:
        return
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = f"{state_path}.tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        # Atomic replace, so an interrupt cannot leave a truncated checkpoint
        # that the next run would then refuse to read.
        os.replace(tmp, state_path)
    except OSError as exc:
        log.warning("could not write state to %s (%s)", state_path, exc)


def _slug(path: str) -> str:
    """Filesystem-safe stem for a per-source inventory filename."""
    stem = Path(path).stem
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in stem)
    return safe.strip("_") or "source"


# ---------------------------------------------------------------------------
# Portfolio parse
# ---------------------------------------------------------------------------

def parse_portfolio(
    paths: list[str],
    source_type: str,
    *,
    parse_one: Callable[[str, str], dict],
    build_inventory: Callable[[dict, str, dict | None], dict],
    sf_target: dict | None = None,
    inventory_dir: str | None = None,
    state_path: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Parse every source, checkpointing after each one.

    `parse_one` and `build_inventory` are injected rather than imported so this
    module stays testable without the CLI, and so the caller keeps ownership of
    parser routing (including the Tableau inspector/legacy switch).

    Returns per-source results plus the list of inventories, for the caller to
    merge. Merging is deliberately not done here: the caller may want the
    per-source inventories on their own.
    """
    state = _load_state(state_path) if resume else {}
    processed: dict[str, Any] = state.get("processed", {}) if state.get("source_type") == source_type else {}
    failed: dict[str, Any] = {}

    state = {
        "source_type": source_type,
        "manifest": paths,
        "processed": processed,
        "failed": failed,
        "current": None,
    }

    inventories: list[dict] = []
    sources: list[dict] = []

    for path in paths:
        prior = processed.get(path)
        if prior and prior.get("inventory_path") and os.path.isfile(prior["inventory_path"]):
            # Already done in an earlier run and the artifact is still there.
            try:
                with open(prior["inventory_path"], encoding="utf-8") as f:
                    inventories.append(json.load(f))
                sources.append({**prior, "path": path, "resumed": True})
                log.info("resumed %s from %s", Path(path).name, prior["inventory_path"])
                continue
            except (OSError, json.JSONDecodeError):
                log.warning("checkpointed inventory for %s is unreadable; reparsing", path)

        state["current"] = path
        _save_state(state_path, state)

        try:
            parsed = parse_one(path, source_type)
            inventory = build_inventory(parsed, source_type, sf_target or None)
            if parsed.get("inspection"):
                inventory["tableau_inspection"] = parsed["inspection"]
            # Record which file each inventory came from, so a merged inventory
            # can still attribute a field to its workbook.
            inventory["source_file"] = path
        except Exception as exc:
            # One bad source must not end the run. Record the cause and move on.
            log.warning("parse failed for %s (%s: %s)", path, type(exc).__name__, exc)
            entry = {"path": path, "status": "error",
                     "error": f"{type(exc).__name__}: {exc}"}
            failed[path] = entry
            sources.append(entry)
            state["failed"] = failed
            _save_state(state_path, state)
            continue

        inv_path = None
        if inventory_dir:
            os.makedirs(inventory_dir, exist_ok=True)
            inv_path = os.path.join(inventory_dir, f"{_slug(path)}.json")
            with open(inv_path, "w", encoding="utf-8") as f:
                json.dump(inventory, f, indent=2, default=str)

        entry = {
            "path": path,
            "name": Path(path).name,
            "status": "ok",
            "inventory_path": inv_path,
            "counts": {
                "tables": len(inventory.get("tables", [])),
                "dimensions": len(inventory.get("dimensions", [])),
                "facts": len(inventory.get("facts", [])),
                "metrics": len(inventory.get("metrics", [])),
                "dashboards": len(inventory.get("dashboards", [])),
            },
        }
        processed[path] = entry
        sources.append(entry)
        inventories.append(inventory)
        state["processed"] = processed
        _save_state(state_path, state)

    state["current"] = None
    _save_state(state_path, state)

    return {
        "sources": sources,
        "inventories": inventories,
        "ok_count": sum(1 for s in sources if s.get("status") == "ok"),
        "failed_count": len(failed),
        "failed": list(failed.values()),
        "state_path": state_path,
    }
