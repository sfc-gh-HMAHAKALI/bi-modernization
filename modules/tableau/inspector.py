#!/usr/bin/env python3
"""Inventory a Tableau workbook or datasource for migration to Streamlit in Snowflake.

Vendored from the `tableau-to-streamlit-in-snowflake` skill (scripts/inspect_workbook.py).
Kept as a near-verbatim copy so it can be re-synced from upstream; the only local
change is binding an em dash outside an f-string expression for Python 3.11
compatibility (backslash escapes inside f-string expressions are 3.12+).

Do not reshape this module's output. `inspector_adapter.py` maps it into the
bi-modernization inventory contract, and that adapter is the seam where local
expectations belong.

Accepts .twb, .twbx, .tds, and .tdsx. Standard library only, by design: this runs in
environments where PyPI access is gated (the same constraint that governs Streamlit in
Snowflake deployments), so it must not need `uv sync` or a dependency file.

Treats the input as untrusted. Tableau files arrive from other people's servers and may
carry embedded connection metadata, so the reader rejects DTD/entity declarations
(ElementTree is vulnerable to entity-expansion attacks), refuses encrypted and oversized
archive members, caps the compression ratio, and never writes archive contents to disk.

Usage:
    python3 inspect_workbook.py SOURCE [--format {markdown,json}] [--section NAME ...]

Examples:
    python3 inspect_workbook.py sales.twbx
    python3 inspect_workbook.py sales.twb --format json
    python3 inspect_workbook.py sales.twbx --section calculated_fields --section filters
    python3 inspect_workbook.py orders.tdsx --section datasources

Sections: datasources, columns, calculated_fields, parameters, filters,
          worksheets, dashboards, actions, warnings

Exit codes:
    0  inventory produced
    1  source could not be read or parsed, or failed a safety check
    2  invalid arguments
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from functools import reduce
from math import gcd
from pathlib import Path
from typing import Any

SECTIONS = [
    "datasources",
    "columns",
    "calculated_fields",
    "groups_and_bins",
    "visual_styles",
    "parameters",
    "filters",
    "worksheets",
    "dashboards",
    "actions",
    "field_usage",
    "warnings",
]

# Tableau field references appear as [none:Region:nk], [sum:Sales:qk], [yr:Order Date:ok].
_FIELD_REF = re.compile(r"^(?:[a-z]{2,4}|none|usr|attr|qk|nk):(.+):(?:[a-z]{2})$", re.IGNORECASE)

# Functions that do not survive a mechanical translation. Each maps to the reason,
# because "unsupported" alone gives the migrator nothing to act on. These drive the
# `blockers` list on a calculated field and, through it, its prescribed action.
_UNTRANSLATABLE = {
    "SCRIPT_REAL": "calls an external R/Python service; no SQL equivalent",
    "SCRIPT_STR": "calls an external R/Python service; no SQL equivalent",
    "SCRIPT_INT": "calls an external R/Python service; no SQL equivalent",
    "SCRIPT_BOOL": "calls an external R/Python service; no SQL equivalent",
    "MODEL_QUANTILE": "Tableau built-in predictive model; needs an explicit model in Snowflake",
    "MODEL_PERCENTILE": "Tableau built-in predictive model; needs an explicit model in Snowflake",
}
# Translatable, but not by pattern-matching the name -- each needs a decision.
_NEEDS_REVIEW = {
    "RAWSQL": "raw passthrough SQL; re-target the dialect and re-check the grain",
    "RAWSQLAGG": "raw passthrough aggregate; re-target the dialect and re-check the grain",
    "MAKEPOINT": "geospatial; maps to Snowflake ST_* but changes the chart library decision",
    "MAKELINE": "geospatial; maps to Snowflake ST_* but changes the chart library decision",
    "DISTANCE": "geospatial; maps to ST_DISTANCE with an explicit unit",
    "BUFFER": "geospatial; maps to ST_BUFFER with an explicit unit",
    "AREA": "geospatial; maps to ST_AREA with an explicit unit",
    "TOTAL": "table calc over the whole partition; confirm the intended scope",
    "INDEX": "positional table calc; depends on the rendered sort order, not the data",
    "FIRST": "positional table calc; depends on the rendered sort order, not the data",
    "LAST": "positional table calc; depends on the rendered sort order, not the data",
    "PREVIOUS_VALUE": "recursive table calc; needs a recursive CTE or a reframing",
}
# Measure names that are usually semi-additive -- they sum across dimensions but must
# be snapshotted over time. A naive SUM(...) GROUP BY month silently multiplies these.
_SEMI_ADDITIVE_HINT = re.compile(
    r"\b(balance|headcount|inventory|on[-_ ]?hand|stock|level|aum|open[-_ ]?items|"
    r"outstanding|snapshot|position|backlog)\b",
    re.IGNORECASE,
)
_LOD_KINDS = ("FIXED", "INCLUDE", "EXCLUDE")
_TABLE_CALC_FUNCS = (
    "WINDOW_SUM", "WINDOW_AVG", "WINDOW_MIN", "WINDOW_MAX", "WINDOW_STDEV",
    "WINDOW_VAR", "WINDOW_MEDIAN", "WINDOW_COUNT", "WINDOW_CORR",
    "RUNNING_SUM", "RUNNING_AVG", "RUNNING_MIN", "RUNNING_MAX", "RUNNING_COUNT",
    "LOOKUP", "PREVIOUS_VALUE", "TOTAL", "INDEX", "SIZE", "FIRST", "LAST",
    "RANK", "RANK_DENSE", "RANK_MODIFIED", "RANK_PERCENTILE", "RANK_UNIQUE",
)
# Constructs the parser deliberately does not model; see references/twb-xml-parsing.md.
# Groups and bins used to live here. They are now extracted into `groups_and_bins`,
# because "not modeled" for a construct that translates to one line of SQL was pushing
# avoidable work onto the migrator.
_UNMODELED = {
    "drill-path": "hierarchies (input for drilldown design)",
    "table-extension": "table extensions calling external scripts (no Streamlit equivalent)",
}

# --- safety limits for untrusted input -------------------------------------
# A .twb is XML text. Real workbooks are single-digit MB; 64 MB is far above any
# genuine file and keeps the full-buffer doctype scan below cheaply bounded memory.
_MAX_XML_BYTES = 64 * 1024 * 1024
# A member that expands more than this many times is treated as a decompression bomb.
_MAX_COMPRESSION_RATIO = 200
# Zone nesting is a layout tree a few levels deep. A deeper one is malformed or hostile,
# and recursing on it would blow the interpreter stack.
_MAX_ZONE_DEPTH = 64
# Custom SQL can be megabytes and can embed credentials; keep only a readable head.
_MAX_SQL_CHARS = 4096
_ARCHIVE_SUFFIXES = {".twbx", ".tdsx"}
_DATASOURCE_SUFFIXES = {".tds", ".tdsx"}
# Attribute names that may carry a secret. Tableau can persist credentials into a
# saved workbook, and a .twbx is just a zip someone may commit to git.
_SECRET_ATTR = re.compile(
    r"password|passwd|pwd|secret|token|credential|apikey|api-key|access[-_]?key|private[-_]?key",
    re.IGNORECASE,
)
# Byte-order marks, longest first so UTF-32 is not shadowed by the UTF-16 prefix.
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Crawl guards for --recurse. The input is a directory someone else organised, possibly
# on a network mount, so bound both breadth and depth rather than trusting the tree.
_MAX_CRAWL_FILES = 500
_MAX_CRAWL_DEPTH = 12
# Everything a malformed or hostile source can raise, collected once so the single-file
# and portfolio paths cannot drift apart in what they survive.
_READ_FAILURES = (
    ET.ParseError,
    zipfile.BadZipFile,
    ValueError,           # our own safety refusals; also UnicodeDecodeError
    OSError,
    NotImplementedError,  # unsupported ZIP compression method
    RecursionError,       # backstop for pathological nesting
    LookupError,          # unknown encoding declared in the XML prolog
)


# ---------------------------------------------------------------- loading


def _decode_for_scan(data: bytes) -> str:
    """Decode XML bytes to text so a doctype scan cannot be evaded by the encoding.

    A byte-level search for b"<!DOCTYPE" misses a UTF-16 document entirely, because the
    needle is interleaved with null bytes there — while expat honors the BOM and parses it
    happily. Decoding first makes the scan encoding-independent.
    """
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    # BOM-less UTF-16: the first character of an XML document is '<'.
    if data[:2] == b"<\x00":
        return data.decode("utf-16-le", errors="replace")
    if data[:2] == b"\x00<":
        return data.decode("utf-16-be", errors="replace")
    return data.decode("utf-8", errors="replace")


def sanitize_for_output(value: str) -> str:
    """Strip control characters from attacker-controlled text before it is printed.

    Archive member names and captions reach the terminal and the markdown report. A name
    containing ANSI escapes or newlines could rewrite the screen or forge report structure.
    """
    return _CONTROL_CHARS.sub("?", value)


def parse_xml_bytes(data: bytes, origin: str) -> ET.Element:
    """Parse XML from untrusted bytes, refusing doctype and entity declarations.

    ElementTree does not fetch external entities, but it does expand internal ones —
    verified: a nested-entity document expands as expected — so a crafted file can blow up
    memory ("billion laughs"). No legitimate Tableau file needs a DTD, so refusing one
    costs nothing and removes the class of attack.

    The scan covers the whole document, not a prefix: a multi-kilobyte leading comment is
    valid XML and would otherwise push the declaration out of a windowed check.
    """
    if len(data) > _MAX_XML_BYTES:
        raise ValueError(
            f"{origin} is {len(data)} bytes, above the {_MAX_XML_BYTES}-byte cap"
        )
    text = _decode_for_scan(data).upper()
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError(
            f"{origin} declares a DTD or XML entity, which this reader refuses "
            "(entity expansion is a denial-of-service vector)"
        )
    return ET.fromstring(data)


def _read_archive_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Read one archive member into memory after checking it is safe to expand."""
    if info.flag_bits & 0x1:
        raise ValueError(f"encrypted archive member not read: {info.filename}")
    if info.file_size > _MAX_XML_BYTES:
        raise ValueError(
            f"archive member {sanitize_for_output(info.filename)} expands to "
            f"{info.file_size} bytes, above the {_MAX_XML_BYTES}-byte cap"
        )
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > _MAX_COMPRESSION_RATIO:
            raise ValueError(
                f"archive member {sanitize_for_output(info.filename)} has a "
                f"{ratio:.0f}x compression ratio, above the "
                f"{_MAX_COMPRESSION_RATIO}x cap (possible decompression bomb)"
            )
    with archive.open(info) as handle:
        # Bounded read. zipfile does stop at the declared size and then fails the CRC, so a
        # member lying about file_size is already caught -- but do not depend on that
        # behavior to be the only thing standing between us and a memory blowup.
        data = handle.read(_MAX_XML_BYTES + 1)
    if len(data) > _MAX_XML_BYTES:
        raise ValueError(
            f"archive member {sanitize_for_output(info.filename)} exceeded the "
            f"{_MAX_XML_BYTES}-byte cap while reading"
        )
    return data


def load_source_xml(path: Path) -> tuple[ET.Element, list[str], list[str]]:
    """Return (root element, data-file members, warnings) for a .twb/.twbx/.tds/.tdsx.

    Archive members are read into memory, never extracted to disk, so a member name
    containing `..` or an absolute path cannot escape anywhere.
    """
    warnings: list[str] = []
    data_members: list[str] = []
    suffix = path.suffix.lower()
    inner_ext = ".tds" if suffix == ".tdsx" else ".twb"

    if suffix in _ARCHIVE_SUFFIXES:
        with zipfile.ZipFile(path) as archive:
            infos = [i for i in archive.infolist() if not i.filename.endswith("/")]
            primary = [i for i in infos if i.filename.lower().endswith(inner_ext)]
            if not primary:
                raise ValueError(
                    f"{path.name} is a {suffix} but contains no {inner_ext} member"
                )
            if len(primary) > 1:
                warnings.append(
                    f"archive contains {len(primary)} {inner_ext} members; "
                    f"parsed {sanitize_for_output(primary[0].filename)}"
                )
            data_members = [
                sanitize_for_output(i.filename) for i in infos
                if not i.filename.lower().endswith(inner_ext)
            ]
            root = parse_xml_bytes(
                _read_archive_member(archive, primary[0]),
                sanitize_for_output(primary[0].filename),
            )
    else:
        root = parse_xml_bytes(path.read_bytes(), path.name)

    if any(
        _SECRET_ATTR.search(key)
        for element in root.iter()
        for key in element.attrib
    ):
        warnings.append(
            "source contains credential-like attribute name(s) — treat this file as a "
            "secret: keep it out of version control, and do not paste its raw XML into "
            "a report, ticket, or chat"
        )

    extracts = [n for n in data_members if n.lower().endswith(".hyper")]
    if extracts:
        warnings.append(
            "source ships .hyper extract(s) "
            + ", ".join(sorted(extracts))
            + " — this data is not in Snowflake yet; loading it is a prerequisite, "
            "and .hyper is a binary format this script cannot read"
        )
    flat_files = [
        n for n in data_members
        if n.lower().endswith((".csv", ".xls", ".xlsx", ".txt", ".json"))
    ]
    if flat_files:
        warnings.append(
            "source ships flat data file(s) "
            + ", ".join(sorted(flat_files))
            + " — confirm an equivalent Snowflake table exists"
        )
    return root, data_members, warnings


# ---------------------------------------------------------------- helpers


def local_tag(element: ET.Element) -> str:
    """Tag name without any XML namespace prefix."""
    return element.tag.rsplit("}", 1)[-1]


def datasource_elements(root: ET.Element) -> list[ET.Element]:
    """Datasource elements, whether the root is a workbook or a bare .tds datasource."""
    if local_tag(root) == "datasource":
        return [root]
    return list(root.iterfind("./datasources/datasource"))


def clean_field(raw: str | None) -> str:
    """Normalize a Tableau field reference to a readable field name."""
    if not raw:
        return ""
    name = raw.strip()
    # Keep only the trailing bracketed part: [datasource].[none:Region:nk] -> [none:Region:nk]
    parts = re.findall(r"\[([^\[\]]*)\]", name)
    if parts:
        name = parts[-1]
    match = _FIELD_REF.match(name)
    if match:
        name = match.group(1)
    return name.strip()


def to_snowflake_identifier(name: str) -> str:
    """Suggest a Snowflake column name for a Tableau field caption."""
    ident = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return ident or "unnamed"


def classify_formula(formula: str, caption: str = "") -> dict[str, Any]:
    """Describe a calculated-field formula: LOD kind, table-calc use, complexity, action.

    The `action` is the point of this function. A complexity score on its own tells a
    migrator that something is hard without telling them what to do about it, and
    "high" gets skimmed past. Every field comes back with a prescribed next step.
    """
    upper = formula.upper()
    lod_kinds = [kind for kind in _LOD_KINDS if re.search(rf"\{{\s*{kind}\b", upper)]
    brace_depth = 0
    max_depth = 0
    for char in formula:
        if char == "{":
            brace_depth += 1
            max_depth = max(max_depth, brace_depth)
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
    table_calcs = sorted({f for f in _TABLE_CALC_FUNCS if re.search(rf"\b{f}\s*\(", upper)})

    blockers = sorted(
        {
            f"{func}: {why}"
            for func, why in _UNTRANSLATABLE.items()
            if re.search(rf"\b{func}\s*\(", upper)
        }
    )
    review = sorted(
        {
            f"{func}: {why}"
            for func, why in _NEEDS_REVIEW.items()
            if re.search(rf"\b{func}[A-Z_]*\s*\(", upper)
        }
    )
    semi_additive = bool(_SEMI_ADDITIVE_HINT.search(caption)) and bool(
        re.search(r"\bSUM\s*\(", upper)
    )

    score = 0
    score += 2 * len(lod_kinds)
    score += 2 * max(0, max_depth - 1)          # nested LOD
    score += 2 * len(table_calcs)
    score += len(re.findall(r"\bIF\b|\bCASE\b", upper))
    score += len(re.findall(r"\bELSEIF\b|\bWHEN\b", upper))
    score += 3 * len(blockers)
    score += len(review)
    if len(formula) > 400:
        score += 2
    elif len(formula) > 150:
        score += 1

    complexity = "low" if score <= 1 else "medium" if score <= 4 else "high"
    if blockers:
        complexity = "blocked"

    return {
        "lod_kinds": lod_kinds,
        "lod_nested": max_depth > 1,
        "table_calc_functions": table_calcs,
        "complexity": complexity,
        "complexity_score": score,
        "blockers": blockers,
        "needs_review": review,
        "semi_additive_suspect": semi_additive,
        "action": _prescribe_action(
            complexity, lod_kinds, max_depth > 1, table_calcs, blockers, review, semi_additive
        ),
        "translate_to": (
            "SQL window function" if lod_kinds or table_calcs else "SQL expression"
        ),
    }


def _prescribe_action(
    complexity: str,
    lod_kinds: list[str],
    nested: bool,
    table_calcs: list[str],
    blockers: list[str],
    review: list[str],
    semi_additive: bool,
) -> str:
    """Map a classification to the one thing the migrator should do next.

    Ordered most-blocking first: a field that cannot be translated at all should not
    be reported as "translate the LOD" just because it also contains one.
    """
    if blockers:
        return (
            "STOP and ask. Cannot be translated mechanically -- decide with the user "
            "whether to reimplement, replace, or drop it. Disposition: blocked."
        )
    if semi_additive:
        return (
            "Confirm additivity before translating. The name suggests a snapshot measure; "
            "a plain SUM over a time grain will multiply it. See the semi-additive section "
            "of calculation-translation.md."
        )
    if nested:
        return (
            "Translate inner LOD first as a CTE, then the outer over its result. Reconcile "
            "the inner value independently before trusting the outer."
        )
    if review:
        return "Translate, then confirm the decision each flagged function forces: " + "; ".join(review)
    if lod_kinds and table_calcs:
        return (
            "Split it. The LOD belongs in SQL and the table calc belongs at the display "
            "grain; doing both in one expression is where order of operations goes wrong."
        )
    if lod_kinds:
        return f"Translate as a window function partitioned on the {lod_kinds[0]} dimensions."
    if table_calcs:
        return (
            "Translate as a window function at the viz grain -- the PARTITION BY must mirror "
            "the marks card, not the source table."
        )
    if complexity == "high":
        return "Translate as a CASE expression, then reconcile it on its own before wiring it in."
    return "Translate directly as a SQL expression."


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


# ---------------------------------------------------------------- extraction


def is_parameter_datasource(ds: ET.Element) -> bool:
    return ds.get("name") == "Parameters" or ds.get("hasconnection") == "false"


def extract_datasources(root: ET.Element) -> list[dict[str, Any]]:
    out = []
    for ds in datasource_elements(root):
        if is_parameter_datasource(ds):
            continue
        connections = []
        for conn in ds.iterfind(".//connection"):
            cls = conn.get("class", "")
            if cls in ("", "federated"):
                continue  # wrapper, not a real endpoint
            connections.append(
                {
                    "class": cls,
                    "server": conn.get("server", ""),
                    "database": conn.get("dbname", ""),
                    "schema": conn.get("schema", ""),
                    "warehouse": conn.get("warehouse", ""),
                    # How the workbook authenticated. This is not trivia: a workbook on
                    # OAuth or a viewer-specific credential was relying on the *viewer's*
                    # identity reaching the warehouse, which is a row-level-security
                    # requirement in disguise. Never the credential value -- only the mode.
                    "authentication": sanitize_for_output(conn.get("authentication", "")),
                    "username": sanitize_for_output(conn.get("username", "")),
                    "role": sanitize_for_output(conn.get("role", "")),
                    # One-time SQL runs on every connection and can set session state that
                    # silently changes results (timezone, warehouse, role, session params).
                    "one_time_sql": bool(conn.get("one-time-sql")),
                }
            )
        relations = []
        for rel in ds.iterfind(".//relation"):
            kind = rel.get("type", "")
            if kind == "text":
                # Custom SQL can be large and can embed credentials or tokens; keep a
                # readable head rather than serializing the whole body into output.
                sql = _text(rel)
                truncated = len(sql) > _MAX_SQL_CHARS
                relations.append(
                    {
                        "type": "custom_sql",
                        "sql": sql[:_MAX_SQL_CHARS],
                        "sql_truncated": truncated,
                    }
                )
            elif rel.get("table"):
                relations.append({"type": kind or "table", "table": rel.get("table", "")})
        out.append(
            {
                "name": ds.get("name", ""),
                "caption": ds.get("caption", ds.get("name", "")),
                "connections": connections,
                "relations": relations,
                "in_snowflake": any(c["class"] == "snowflake" for c in connections),
            }
        )
    return out


def _default_format(col: ET.Element) -> str:
    """Tableau's per-field number/date format, if the author set one.

    Formatting is parity. A workbook showing $1.2M and an app showing 1234567.0 are
    "the same number" only to someone who is not the person who asked for the migration.
    """
    fmt = col.find("./default-format")
    if fmt is not None and fmt.get("format"):
        return sanitize_for_output(fmt.get("format", ""))
    # Older workbooks put it on the column itself.
    return sanitize_for_output(col.get("default-format", ""))


def _aliases(col: ET.Element) -> dict[str, str]:
    """Member-value aliases: displayed label -> stored value.

    These are invisible in the data and load-bearing in the UI. A filter offering the
    raw stored values when the workbook showed aliases reads as a broken migration.
    """
    out: dict[str, str] = {}
    for alias in col.iterfind(".//alias"):
        key, value = alias.get("key"), alias.get("value")
        if key and value:
            out[sanitize_for_output(key.strip("\"'"))] = sanitize_for_output(value)
    return out


def _field_folder(ds: ET.Element, internal_name: str) -> str:
    """Which Tableau folder a field sits in -- the author's own grouping of fields."""
    for folder in ds.iterfind(".//folder"):
        for item in folder.iterfind("./folder-item"):
            if clean_field(item.get("name", "")) == internal_name:
                return sanitize_for_output(folder.get("name", ""))
    return ""


def extract_groups_and_bins(root: ET.Element) -> list[dict[str, Any]]:
    """Groups, bins, and sets, which Tableau stores as their own element kinds.

    All three translate to plain SQL, so the work is finding them rather than writing
    them -- which is exactly why they need to appear on the inventory instead of being
    discovered in Step 4 when a filter turns out to have no backing column.

    Placement varies by Tableau version and by how the field was created: a group or bin
    can be a sibling of <column> under <datasource>, or nested inside the <column> it
    derives from, and a bin can instead be a plain <column> carrying bin-size/bin-base.
    All three shapes are handled -- missing one means silently dropping a field.
    """
    out: list[dict[str, Any]] = []

    def add_group(ds_label: str, elem: ET.Element, caption: str) -> None:
        members: list[dict[str, Any]] = []
        for gf in elem.iterfind(".//groupfilter"):
            literal = gf.get("member") or gf.get("value") or ""
            if literal:
                members.append(
                    {
                        "member": sanitize_for_output(literal.strip("\"'")),
                        "function": gf.get("function", ""),
                    }
                )
        out.append(
            {
                "datasource": ds_label,
                "kind": "group",
                "caption": sanitize_for_output(caption),
                "source_field": clean_field(
                    elem.get("from-field", "") or elem.get("column", "")
                ),
                "members": members,
                "translate_to": (
                    "join to a mapping table" if len(members) > 12 else "CASE expression"
                ),
            }
        )

    def add_bin(ds_label: str, elem: ET.Element, caption: str) -> None:
        out.append(
            {
                "datasource": ds_label,
                "kind": "bin",
                "caption": sanitize_for_output(caption),
                "source_field": clean_field(
                    elem.get("from-field", "") or elem.get("bin-field", "") or elem.get("column", "")
                ),
                "bin_size": sanitize_for_output(elem.get("bin-size", elem.get("size", ""))),
                "bin_base": sanitize_for_output(elem.get("bin-base", elem.get("base", ""))),
                "translate_to": "FLOOR(field / size) * size",
            }
        )

    def add_set(ds_label: str, elem: ET.Element, caption: str) -> None:
        members = [
            sanitize_for_output((gf.get("member") or gf.get("value") or "").strip("\"'"))
            for gf in elem.iterfind(".//groupfilter")
            if (gf.get("member") or gf.get("value"))
        ]
        # A set with enumerated members is constant; one without is computed by a rule.
        computed = not members
        out.append(
            {
                "datasource": ds_label,
                "kind": "set",
                "caption": sanitize_for_output(caption),
                "source_field": clean_field(
                    elem.get("from-field", "") or elem.get("column", "")
                ),
                "members": [{"member": m, "function": ""} for m in members],
                "computed": computed,
                "translate_to": (
                    "DENSE_RANK() window + predicate (stage 4, before dimension filters)"
                    if computed
                    else "IN predicate (stage 4, before dimension filters)"
                ),
            }
        )

    handlers = {"group": add_group, "bin": add_bin, "set": add_set}

    for ds in datasource_elements(root):
        if is_parameter_datasource(ds):
            continue
        ds_label = ds.get("caption", ds.get("name", ""))

        # Shape 1: sibling of <column> under <datasource>.
        for tag, handler in handlers.items():
            for elem in ds.iterfind(f"./{tag}"):
                handler(ds_label, elem, elem.get("caption") or clean_field(elem.get("name", "")))

        for col in ds.iterfind(".//column"):
            caption = col.get("caption") or clean_field(col.get("name", ""))
            # Shape 2: nested inside the <column> it derives from.
            for tag, handler in handlers.items():
                for elem in col.iterfind(f"./{tag}"):
                    handler(ds_label, elem, caption)
            # Shape 3: a plain column carrying bin attributes.
            if (col.get("bin-size") or col.get("bin-base")) and col.find("./bin") is None:
                add_bin(ds_label, col, caption)

    return out


_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
# Members of Tableau's own default palettes. Reporting these as "the author's choices"
# is noise, so they are held out of the deliberate-palette list.
_TABLEAU_DEFAULTS = {
    "#4e79a7", "#4e79a6", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc949", "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab", "#ffffff", "#000000",
}


def _serialize(root: ET.Element) -> str:
    """The document as text, for regex sweeps no structured walk would catch."""
    try:
        return ET.tostring(root, encoding="unicode")
    except (TypeError, ValueError):
        return ""


def extract_visual_styles(root: ET.Element) -> dict[str, Any]:
    """Extract the colours and backgrounds the workbook actually used.

    Colour in a Tableau dashboard is frequently *semantic* rather than decorative: red
    means at-risk, green means on-track, and a status dimension's palette is a convention
    somebody signed off on. A migration that reassigns those hues ships a dashboard that
    is numerically correct and says something different -- which readers notice faster
    than a wrong total.

    Tableau records colour in several places, so all of them are collected:
      - <color-one-way-map><entry key='L' value='#59a14f'/></color-one-way-map>
            an explicit member-to-hex mapping; the most reliable source
      - <encodings><color column='...' palette='...'/>
            which field drives colour, and the named palette
      - <run fontcolor='#59a14f'>Profitable = Green</run>
            annotation text where the author hand-wrote a legend
      - <style-rule element='table'><format attr='background-color' value='...'/>
            worksheet and dashboard backgrounds
    """
    member_colors: dict[str, dict[str, str]] = {}
    encodings: list[dict[str, str]] = []
    backgrounds: dict[str, list[str]] = {"worksheet": [], "dashboard": []}
    annotations: list[dict[str, str]] = []

    for ws in root.iterfind("./worksheets/worksheet"):
        ws_name = ws.get("name", "")

        # Keyed by worksheet as well as field: the same dimension can legitimately carry
        # different palettes on different sheets.
        for cmap in ws.iterfind(".//color-one-way-map"):
            entries = {}
            for entry in cmap.iterfind("./entry"):
                key, value = entry.get("key"), entry.get("value")
                if key and value:
                    entries[sanitize_for_output(key.strip("\"'"))] = sanitize_for_output(value)
            if entries:
                field = clean_field(cmap.get("column", "")) or "(unnamed)"
                member_colors.setdefault(f"{ws_name}:{field}", {}).update(entries)

        for enc in ws.iterfind(".//encodings/color"):
            field = clean_field(enc.get("column", ""))
            if field:
                encodings.append(
                    {
                        "worksheet": ws_name,
                        "field": field,
                        "palette": sanitize_for_output(enc.get("palette", "")),
                    }
                )

        for fmt in ws.iterfind(".//style-rule/format"):
            if fmt.get("attr") == "background-color" and fmt.get("value"):
                backgrounds["worksheet"].append(sanitize_for_output(fmt.get("value", "")))

    for dash in root.iterfind("./dashboards/dashboard"):
        for fmt in dash.iterfind(".//format"):
            if fmt.get("attr") == "background-color" and fmt.get("value"):
                backgrounds["dashboard"].append(sanitize_for_output(fmt.get("value", "")))

    # An author who typed "Low (L) = Green" into a text box was documenting a convention
    # that appears in no encoding element.
    for run in root.iter("run"):
        fontcolor = run.get("fontcolor", "")
        text = _text(run)
        if fontcolor.startswith("#") and text:
            annotations.append(
                {"text": sanitize_for_output(text)[:80], "color": sanitize_for_output(fontcolor)}
            )

    all_hex = sorted({m.group(0).lower() for m in _HEX_COLOR.finditer(_serialize(root))})
    return {
        "member_colors": member_colors,
        "color_encodings": encodings,
        "backgrounds": {k: sorted(set(v)) for k, v in backgrounds.items()},
        "annotation_colors": annotations,
        "all_hex_colors": all_hex,
        "non_default_hex_colors": [c for c in all_hex if c not in _TABLEAU_DEFAULTS],
    }


def extract_columns_and_calcs(
    root: ET.Element,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    calcs: list[dict[str, Any]] = []
    for ds in datasource_elements(root):
        if is_parameter_datasource(ds):
            continue
        ds_label = ds.get("caption", ds.get("name", ""))
        for col in ds.iterfind(".//column"):
            raw_name = col.get("name", "")
            caption = col.get("caption") or clean_field(raw_name)
            calc = col.find("./calculation")
            internal = clean_field(raw_name)
            aliases = _aliases(col)
            record = {
                "datasource": ds_label,
                "internal_name": internal,
                "caption": caption,
                "suggested_column": to_snowflake_identifier(caption),
                "datatype": col.get("datatype", ""),
                "role": col.get("role", ""),
                # `type` is the discrete/continuous distinction, which decides whether a
                # field becomes an Altair :N/:O or :Q channel and whether a filter is a
                # multiselect or a slider.
                "type": col.get("type", ""),
                # A geographic semantic-role is the usual reason a migration needs a map,
                # which is in turn the usual reason it needs Plotly and therefore an EAI.
                "semantic_role": col.get("semantic-role", ""),
                "hidden": col.get("hidden", "") == "true",
                "default_format": _default_format(col),
                "aliases": aliases,
                "description": sanitize_for_output(
                    " ".join(_text(r) for r in col.iterfind(".//run") if _text(r))
                ),
                "folder": _field_folder(ds, internal),
            }
            if calc is not None and calc.get("formula"):
                formula = calc.get("formula", "")
                calcs.append(
                    {**record, "formula": formula, **classify_formula(formula, caption)}
                )
            else:
                columns.append(record)
    return columns, calcs


def extract_parameters(root: ET.Element) -> list[dict[str, Any]]:
    out = []
    for ds in datasource_elements(root):
        if not is_parameter_datasource(ds):
            continue
        for col in ds.iterfind(".//column"):
            rng = col.find("./range")
            members = [m.get("value", "") for m in col.iterfind(".//member")]
            datatype = col.get("datatype", "")
            record: dict[str, Any] = {
                "caption": col.get("caption") or clean_field(col.get("name", "")),
                "datatype": datatype,
                "current_value": col.get("value", ""),
                "allowable": col.get("param-domain-type", ""),
            }
            if rng is not None:
                record["range"] = {
                    "min": rng.get("min", ""),
                    "max": rng.get("max", ""),
                    "step": rng.get("granularity", rng.get("step", "")),
                }
                record["suggested_widget"] = "st.slider"
            elif members:
                record["members"] = members
                record["suggested_widget"] = (
                    "st.segmented_control" if len(members) <= 5 else "st.selectbox"
                )
            elif datatype in ("date", "datetime"):
                record["suggested_widget"] = "st.date_input"
            elif datatype == "boolean":
                record["suggested_widget"] = "st.toggle"
            else:
                record["suggested_widget"] = "st.text_input"
            out.append(record)
    return out


_FILTER_WIDGETS = {
    "categorical": "st.multiselect",
    "quantitative": "st.slider",
    "relative-date": "st.segmented_control (named ranges)",
}


def extract_filters(root: ET.Element) -> list[dict[str, Any]]:
    out = []

    # Data source filters live under <datasource>, not <worksheet>. They are stage 2
    # of Tableau's order of operations and belong in the base CTE, so a migration that
    # only collects worksheet filters silently drops them.
    for ds in datasource_elements(root):
        if is_parameter_datasource(ds):
            continue
        ds_label = ds.get("caption", ds.get("name", ""))
        for filt in ds.iterfind(".//filter"):
            cls = filt.get("class", "")
            out.append(
                {
                    "scope": "datasource",
                    "worksheet": f"(datasource: {ds_label})",
                    "field": clean_field(filt.get("column", "")),
                    "class": cls,
                    "context": False,
                    "suggested_widget": "none — not user-facing",
                    "pipeline_stage": (
                        "2 — data source filter: bake into the base CTE or a view"
                    ),
                }
            )

    for ws in root.iterfind("./worksheets/worksheet"):
        ws_name = ws.get("name", "")
        for filt in ws.iterfind(".//filter"):
            cls = filt.get("class", "")
            is_context = filt.get("context", "").lower() == "true"
            out.append(
                {
                    "scope": "worksheet",
                    "worksheet": ws_name,
                    "field": clean_field(filt.get("column", "")),
                    "class": cls,
                    "context": is_context,
                    "suggested_widget": _FILTER_WIDGETS.get(cls, "st.selectbox"),
                    "pipeline_stage": (
                        "3 — context filter: apply BEFORE any FIXED LOD window"
                        if is_context
                        else "6 — dimension filter: apply AFTER FIXED LOD windows"
                    ),
                }
            )
    return out


_MARK_TO_ALTAIR = {
    "Bar": "mark_bar",
    "Line": "mark_line",
    "Area": "mark_area",
    "Circle": "mark_circle",
    "Square": "mark_square",
    "Shape": "mark_point",
    "Text": "mark_text",
    "Pie": "mark_arc",
    "Map": "mark_geoshape (consider Plotly for choropleth)",
    "Automatic": "infer from shelves",
}


def extract_worksheets(root: ET.Element) -> list[dict[str, Any]]:
    out = []
    for ws in root.iterfind("./worksheets/worksheet"):
        marks = sorted({m.get("class", "") for m in ws.iterfind(".//mark") if m.get("class")})
        encodings = []
        for enc in ws.iterfind(".//encodings/*"):
            encodings.append(
                {"channel": enc.tag, "field": clean_field(enc.get("column", ""))}
            )
        datasources = sorted(
            {
                dep.get("datasource", "")
                for dep in ws.iterfind(".//datasource-dependencies")
                if dep.get("datasource")
            }
        )
        # Every field this worksheet touches, from any of the places Tableau records
        # one. A field can appear on a shelf, in an encoding, in a filter, in a tooltip,
        # or only in a dependency block -- collecting a single set is what makes
        # field-level orphan detection possible later.
        fields_used = {f for f in (clean_field(_text(ws.find(".//rows"))),
                                   clean_field(_text(ws.find(".//cols")))) if f}
        fields_used.update(e["field"] for e in encodings if e["field"])
        for holder in ws.iterfind(".//datasource-dependencies"):
            for col in holder.iterfind("./column"):
                name = clean_field(col.get("name", ""))
                if name:
                    fields_used.add(name)
            for ref in holder.iterfind("./column-instance"):
                name = clean_field(ref.get("column", ""))
                if name:
                    fields_used.add(name)
        for filt in ws.iterfind(".//filter"):
            name = clean_field(filt.get("column", ""))
            if name:
                fields_used.add(name)

        out.append(
            {
                "name": ws.get("name", ""),
                "marks": marks,
                "suggested_mark": ", ".join(
                    _MARK_TO_ALTAIR.get(m, "mark_point") for m in marks
                ) or "infer from shelves",
                "rows_shelf": clean_field(_text(ws.find(".//rows"))),
                "cols_shelf": clean_field(_text(ws.find(".//cols"))),
                "encodings": encodings,
                "fields_used": sorted(fields_used),
                "datasources": datasources,
                "blended": len(datasources) > 1,
            }
        )
    return out


def _walk_zone(zone: ET.Element, depth: int = 0) -> dict[str, Any]:
    if depth > _MAX_ZONE_DEPTH:
        raise ValueError(
            f"dashboard zone nesting exceeds {_MAX_ZONE_DEPTH} levels; the file is "
            "malformed or hostile (a real layout tree is a few levels deep)"
        )
    children = [_walk_zone(child, depth + 1) for child in zone.findall("./zone")]
    return {
        "name": sanitize_for_output(zone.get("name", "")),
        "param": sanitize_for_output(zone.get("param", "")),
        "type": zone.get("type-v2", zone.get("type", "")),
        "orientation": zone.get("orientation", ""),
        "x": zone.get("x", ""),
        "y": zone.get("y", ""),
        "w": zone.get("w", ""),
        "h": zone.get("h", ""),
        "depth": depth,
        "is_container": bool(children),
        "children": children,
    }


def _as_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _reduce_ratio(values: list[int]) -> list[int]:
    """Smallest whole-number ratio, so 720/480 reads as [3, 2] not [720, 480].

    `st.columns` takes relative weights, so the reduced form is exactly equivalent and
    far easier to eyeball in a review.
    """
    positive = [v for v in values if v > 0]
    if not positive:
        return values
    divisor = reduce(gcd, positive)
    return [v // divisor if v > 0 else v for v in values]


def _rects_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ax, ay = _as_int(a["x"]), _as_int(a["y"])
    aw, ah = _as_int(a["w"]), _as_int(a["h"])
    bx, by = _as_int(b["x"]), _as_int(b["y"])
    bw, bh = _as_int(b["w"]), _as_int(b["h"])
    if None in (ax, ay, aw, ah, bx, by, bw, bh):
        return False
    # Touching edges is a tiled neighbour, not an overlap.
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def build_layout_model(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn zone geometry into the column weights a migration actually needs.

    The zone tree already carries x/y/w/h. What a migrator needs is one step further on:
    for each container, the *ratio* of its children, because `st.columns` takes relative
    weights. Deriving that by eye from raw pixel widths is exactly the kind of arithmetic
    that silently comes out as a 50/50 split when the workbook said 60/40.

    Streamlit's layout model maps proportion faithfully and absolute position not at all,
    so this reports proportion and flags what cannot be reproduced rather than pretending
    a pixel-accurate port is available.
    """
    out: list[dict[str, Any]] = []

    def visit(zone: dict[str, Any], path: str) -> None:
        children = zone["children"]
        if children:
            horizontal = zone["orientation"] == "horz"
            axis = "w" if horizontal else "h"
            raw = [_as_int(c[axis]) or 0 for c in children]
            labels = [
                c["name"] or c["param"] or c["type"] or "(blank)" for c in children
            ]
            overlaps = [
                [labels[i], labels[j]]
                for i in range(len(children))
                for j in range(i + 1, len(children))
                if _rects_overlap(children[i], children[j])
            ]
            entry = {
                "container": path,
                "direction": "horizontal" if horizontal else "vertical",
                "children": labels,
                f"child_{axis}": raw,
                "overlapping_children": overlaps,
            }
            if horizontal and len(raw) == 1:
                # A wrapper around a single sheet needs no columns.
                entry["streamlit"] = "no container needed (single child)"
            elif horizontal and all(v > 0 for v in raw):
                weights = _reduce_ratio(raw)
                entry["column_weights"] = weights
                entry["streamlit"] = (
                    f"st.columns({weights})"
                    if len(set(weights)) > 1
                    else f"st.columns({len(weights)})"
                )
            elif horizontal:
                # Widths absent or zero. Splitting evenly would silently invent a layout
                # the workbook never specified, so say what is missing instead.
                entry["streamlit"] = (
                    f"st.columns({len(raw)}) — widths missing from the XML, so this is an "
                    "even split by assumption; confirm the intended proportions"
                )
            else:
                # Vertical stacking needs no container; heights become chart height=.
                entry["streamlit"] = "sequential calls, no container needed"
                entry["child_heights_px"] = raw
            out.append(entry)
            for index, child in enumerate(children):
                visit(child, f"{path}/{labels[index]}")

    for index, zone in enumerate(zones):
        visit(zone, zone["name"] or zone["type"] or f"zone[{index}]")
    return out


def extract_dashboards(root: ET.Element) -> list[dict[str, Any]]:
    out = []
    for dash in root.iterfind("./dashboards/dashboard"):
        zones = [_walk_zone(z) for z in dash.iterfind("./zones/zone")]
        out.append(
            {
                "name": dash.get("name", ""),
                "size": {
                    "maxheight": dash.get("maxheight", ""),
                    "maxwidth": dash.get("maxwidth", ""),
                },
                "zones": zones,
                "layout": build_layout_model(zones),
            }
        )
    return out


def build_field_usage(
    columns: list[dict[str, Any]],
    calcs: list[dict[str, Any]],
    worksheets: list[dict[str, Any]],
    dashboards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map every field to the worksheets and dashboards that display it.

    Worksheet-level orphan detection ("this sheet is on no dashboard") is the coarse
    version of this. The finer question is the one that saves real work: *which fields
    actually reach a user?* A workbook that accumulated 200 calculated fields over five
    years typically surfaces a few dozen. Translating the rest is effort spent producing
    parity obligations for things nobody looks at.

    A field used only by another calculated field is not an orphan -- it is a dependency,
    and dropping it breaks the field that needs it. Those are reported separately.
    """
    ws_to_dash: dict[str, list[str]] = {}
    for dash in dashboards:
        for ws_name in _referenced_worksheets(dash["zones"]):
            ws_to_dash.setdefault(ws_name, []).append(dash["name"])

    all_fields = {c["internal_name"]: c for c in columns}
    all_fields.update({c["internal_name"]: c for c in calcs})

    # A field referenced inside any calculated field's formula, by caption or internal
    # name. Formula text carries captions in brackets, so match on both.
    referenced_by_calc: dict[str, list[str]] = {}
    for calc in calcs:
        formula = calc.get("formula", "")
        for bracketed in re.findall(r"\[([^\]]+)\]", formula):
            for key, field in all_fields.items():
                if bracketed == field["caption"] or bracketed == key:
                    if key != calc["internal_name"]:
                        referenced_by_calc.setdefault(key, []).append(calc["caption"])

    usage: list[dict[str, Any]] = []
    for key, field in sorted(all_fields.items()):
        sheets = sorted(ws["name"] for ws in worksheets if key in ws.get("fields_used", []))
        dash_names = sorted({d for s in sheets for d in ws_to_dash.get(s, [])})
        via_calc = sorted(set(referenced_by_calc.get(key, [])))
        usage.append(
            {
                "field": key,
                "caption": field["caption"],
                "is_calculated": "formula" in field,
                "worksheets": sheets,
                "dashboards": dash_names,
                "used_by_calculations": via_calc,
                "reaches_a_dashboard": bool(dash_names),
                # Unused means nothing displays it and nothing computes from it.
                "unused": not sheets and not via_calc,
                # On a sheet, but that sheet is on no dashboard.
                "stranded": bool(sheets) and not dash_names,
            }
        )

    return {
        "fields": usage,
        "summary": {
            "total": len(usage),
            "reach_a_dashboard": sum(1 for u in usage if u["reaches_a_dashboard"]),
            "stranded_on_orphan_sheets": sum(1 for u in usage if u["stranded"]),
            "unused": sum(1 for u in usage if u["unused"]),
            "dependencies_only": sum(
                1 for u in usage if not u["worksheets"] and u["used_by_calculations"]
            ),
        },
    }


def _referenced_worksheets(zones: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for zone in zones:
        if zone["name"] and not zone["is_container"]:
            found.add(zone["name"])
        found |= _referenced_worksheets(zone["children"])
    return found


def extract_actions(root: ET.Element) -> list[dict[str, Any]]:
    out = []
    for action in root.iterfind("./actions/action"):
        source = action.find(".//source")
        target = action.find(".//target")
        command = action.find(".//command")
        cmd = (command.get("command", "") if command is not None else "")
        # Some Tableau versions carry the kind on the <action type="..."> attribute
        # instead of a <command> child, so fall back to it rather than reporting
        # every action as unknown.
        declared = action.get("type", "")
        probe = f"{cmd} {declared}".lower()
        kind = "filter" if "filter" in probe else (
            "highlight" if "highlight" in probe else (cmd or declared or "unknown")
        )
        out.append(
            {
                "caption": action.get("caption", ""),
                "kind": kind,
                "command": cmd,
                "source_worksheet": source.get("worksheet", "") if source is not None else "",
                "target_worksheet": target.get("worksheet", "") if target is not None else "",
                "implementation": (
                    "chart selection event + st.session_state"
                    if kind == "filter"
                    else "alt.condition on opacity (no rerun needed)"
                    if kind == "highlight"
                    else "review manually"
                ),
            }
        )
    return out


def collect_warnings(root: ET.Element, inventory: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    for tag, note in _UNMODELED.items():
        count = len(list(root.iter(tag)))
        if count:
            warnings.append(f"{count} <{tag}> element(s) not modeled — {note}")

    if any(len(list(root.iter(t))) for t in ("set",)):
        warnings.append(
            "workbook uses sets — a constant set is an IN predicate and a computed set is a "
            "window function plus a predicate; both sit at stage 4 of the order of operations "
            "(see references/calculation-translation.md)"
        )

    blended = [w["name"] for w in inventory["worksheets"] if w["blended"]]
    if blended:
        warnings.append(
            "data blending on worksheet(s) "
            + ", ".join(blended)
            + " — Tableau blends left-join at the viz grain; confirm join semantics"
        )

    non_snowflake = [
        d["caption"] for d in inventory["datasources"] if not d["in_snowflake"]
    ]
    if non_snowflake:
        warnings.append(
            "datasource(s) not on a live Snowflake connection: "
            + ", ".join(non_snowflake)
            + " — the data must exist in Snowflake before migrating"
        )

    referenced: set[str] = set()
    for dash in inventory["dashboards"]:
        referenced |= _referenced_worksheets(dash["zones"])
    orphans = [w["name"] for w in inventory["worksheets"] if w["name"] not in referenced]
    if orphans and inventory["dashboards"]:
        warnings.append(
            "worksheet(s) not placed on any dashboard: "
            + ", ".join(orphans)
            + " — likely out of scope; confirm with the user"
        )

    high = [c["caption"] for c in inventory["calculated_fields"] if c["complexity"] == "high"]
    if high:
        warnings.append(
            "high-complexity calculated field(s): "
            + ", ".join(high)
            + " — translate and reconcile these first"
        )

    blocked = [
        f"{c['caption']} ({'; '.join(c['blockers'])})"
        for c in inventory["calculated_fields"]
        if c.get("blockers")
    ]
    if blocked:
        warnings.append(
            "calculated field(s) that cannot be translated mechanically: "
            + " | ".join(blocked)
            + " — STOPPING POINT: decide with the user whether to reimplement, replace, or drop"
        )

    semi = [
        c["caption"] for c in inventory["calculated_fields"] if c.get("semi_additive_suspect")
    ]
    if semi:
        warnings.append(
            "possibly semi-additive measure(s): "
            + ", ".join(semi)
            + " — the name suggests a snapshot quantity; a plain SUM over a time grain "
            "will multiply it. Confirm additivity before translating"
        )

    aliased = [c["caption"] for c in inventory["columns"] if c.get("aliases")]
    if aliased:
        warnings.append(
            f"{len(aliased)} field(s) carry member aliases ("
            + ", ".join(aliased[:5])
            + (", …" if len(aliased) > 5 else "")
            + ") — the workbook displayed labels that differ from the stored values; "
            "filters and axis labels must use the aliases or the app will look wrong"
        )

    formatted = [
        c["caption"]
        for c in inventory["columns"] + inventory["calculated_fields"]
        if c.get("default_format")
    ]
    if formatted:
        warnings.append(
            f"{len(formatted)} field(s) have an explicit Tableau number/date format — "
            "carry these into the chart axes and tooltips; matching values with "
            "mismatched formatting still reads as a failed migration"
        )

    one_time = [
        d["caption"]
        for d in inventory["datasources"]
        if any(c.get("one_time_sql") for c in d["connections"])
    ]
    if one_time:
        warnings.append(
            "datasource(s) with one-time SQL on connect: "
            + ", ".join(one_time)
            + " — that SQL sets session state (role, warehouse, timezone, session params) "
            "which can change results; replicate it or confirm it is unnecessary"
        )

    viewer_auth = [
        d["caption"]
        for d in inventory["datasources"]
        if any(
            "oauth" in (c.get("authentication") or "").lower()
            or "prompt" in (c.get("authentication") or "").lower()
            for c in d["connections"]
        )
    ]
    if viewer_auth:
        warnings.append(
            "datasource(s) authenticating as the viewer: "
            + ", ".join(viewer_auth)
            + " — the workbook relied on the viewer's own identity reaching the warehouse. "
            "That is row-level security, not a connection detail; enforce it with a Snowflake "
            "policy or secure view (see references/migration-qa.md)"
        )

    overlapping = [
        f"{dash['name']}: {' / '.join(pair)}"
        for dash in inventory["dashboards"]
        for container in dash.get("layout", [])
        for pair in container.get("overlapping_children", [])
    ]
    if overlapping:
        warnings.append(
            "overlapping (floating) dashboard zone(s): "
            + "; ".join(overlapping[:5])
            + (", …" if len(overlapping) > 5 else "")
            + " — Streamlit has no absolute positioning, so these cannot be reproduced "
            "as laid out. Reflow them into the normal flow, or use st.popover for a "
            "floating control panel, and tell the user the arrangement changed"
        )

    styles = inventory.get("visual_styles", {})
    if styles.get("member_colors"):
        fields = ", ".join(sorted(styles["member_colors"])[:4])
        warnings.append(
            f"{len(styles['member_colors'])} explicit member-to-colour map(s) present ({fields}"
            + (", \u2026" if len(styles["member_colors"]) > 4 else "")
            + ") \u2014 these are usually semantic (red = at risk). Reuse the workbook's own hex "
            "values as an explicit Altair scale domain/range; letting the chart library assign "
            "colours changes what the dashboard says, not just how it looks"
        )
    if styles.get("annotation_colors"):
        warnings.append(
            f"{len(styles['annotation_colors'])} coloured annotation run(s) found \u2014 the author "
            "hand-wrote a colour legend in a text box. Read it before choosing a palette; it "
            "records a convention no encoding element captures"
        )
    if styles.get("backgrounds", {}).get("dashboard"):
        warnings.append(
            "dashboard background colour(s) set: "
            + ", ".join(styles["backgrounds"]["dashboard"])
            + " \u2014 carry these into the Streamlit theme rather than shipping the default white"
        )

    usage = inventory.get("field_usage", {}).get("summary", {})
    if usage.get("total") and usage.get("reach_a_dashboard") is not None:
        unreached = usage["total"] - usage["reach_a_dashboard"]
        if unreached > 0:
            warnings.append(
                f"{unreached} of {usage['total']} field(s) do not reach any dashboard "
                f"({usage.get('unused', 0)} unused entirely, "
                f"{usage.get('dependencies_only', 0)} used only by other calculations, "
                f"{usage.get('stranded_on_orphan_sheets', 0)} stranded on orphan sheets) — "
                "translating the unused ones creates parity obligations for things nobody sees"
            )

    datasource_filters = [f for f in inventory["filters"] if f["scope"] == "datasource"]
    if datasource_filters:
        warnings.append(
            f"{len(datasource_filters)} data source filter(s) present — these are not "
            "user-facing widgets; bake them into the base CTE or a view or the app will "
            "show more data than the workbook did"
        )

    context_filters = [f for f in inventory["filters"] if f["context"]]
    if context_filters:
        warnings.append(
            f"{len(context_filters)} context filter(s) present — the query pipeline must "
            "apply them before FIXED LOD windows, in a separate CTE from dimension filters"
        )
    return warnings


def build_inventory(path: Path) -> dict[str, Any]:
    root, data_members, load_warnings = load_source_xml(path)
    columns, calcs = extract_columns_and_calcs(root)
    # A .tds/.tdsx defines a datasource only — it has no worksheets, dashboards, or
    # actions to find, so report those as empty rather than implying they were absent
    # from a workbook.
    datasource_only = local_tag(root) == "datasource"
    inventory: dict[str, Any] = {
        "source": path.name,
        "source_kind": "datasource" if datasource_only else "workbook",
        "data_files": sorted(data_members),
        "datasources": extract_datasources(root),
        "columns": columns,
        "calculated_fields": calcs,
        "groups_and_bins": extract_groups_and_bins(root),
        "visual_styles": extract_visual_styles(root),
        "parameters": extract_parameters(root),
        "filters": extract_filters(root),
        "worksheets": [] if datasource_only else extract_worksheets(root),
        "dashboards": [] if datasource_only else extract_dashboards(root),
        "actions": [] if datasource_only else extract_actions(root),
    }
    inventory["field_usage"] = build_field_usage(
        columns, calcs, inventory["worksheets"], inventory["dashboards"]
    )
    inventory["warnings"] = load_warnings + collect_warnings(root, inventory)
    if datasource_only:
        inventory["warnings"].append(
            "source is a datasource definition, not a workbook — it carries fields and "
            "calculations but no worksheets, dashboards, or actions; supply the .twb/.twbx "
            "to inventory the dashboards themselves"
        )
    return inventory


# ---------------------------------------------------------------- rendering


def _render_zone_lines(zones: list[dict[str, Any]], lines: list[str]) -> None:
    for zone in zones:
        indent = "  " * (zone["depth"] + 1)
        if zone["is_container"]:
            label = f"container ({zone['orientation'] or zone['type'] or 'layout'})"
        else:
            label = zone["name"] or zone["param"] or zone["type"] or "(blank)"
        geometry = f" w={zone['w']} h={zone['h']}" if zone["w"] or zone["h"] else ""
        lines.append(f"{indent}- {label}{geometry}")
        _render_zone_lines(zone["children"], lines)


def render_markdown(inv: dict[str, Any], sections: list[str]) -> str:
    lines = [f"# {inv['source_kind'].capitalize()} inventory — {inv['source']}", ""]

    if "warnings" in sections and inv["warnings"]:
        lines += ["## Warnings", ""]
        lines += [f"- {w}" for w in inv["warnings"]]
        lines.append("")

    if "datasources" in sections:
        lines += ["## Datasources", ""]
        if not inv["datasources"]:
            lines += ["_none_", ""]
        for ds in inv["datasources"]:
            mark = "live Snowflake" if ds["in_snowflake"] else "NOT Snowflake"
            lines.append(f"- **{ds['caption']}** ({mark})")
            for conn in ds["connections"]:
                detail = ".".join(x for x in (conn["database"], conn["schema"]) if x)
                lines.append(f"  - `{conn['class']}` {detail}".rstrip())
            for rel in ds["relations"]:
                if rel["type"] == "custom_sql":
                    snippet = " ".join(rel["sql"].split())[:120]
                    lines.append(f"  - custom SQL: `{snippet}`")
                else:
                    lines.append(f"  - table: `{rel['table']}`")
        lines.append("")

    if "columns" in sections:
        lines += [
            "## Columns",
            "",
            "| Field | Role | Type | Discrete/continuous | Format | Aliases | Folder | Suggested column |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for col in inv["columns"]:
            flags = []
            if col.get("hidden"):
                flags.append("hidden")
            if col.get("semantic_role"):
                flags.append(col["semantic_role"].strip("[]"))
            name = col["caption"] + (f" _({', '.join(flags)})_" if flags else "")
            aliases = col.get("aliases") or {}
            alias_cell = (
                f"{len(aliases)}: " + ", ".join(list(aliases)[:3]) + ("…" if len(aliases) > 3 else "")
                if aliases
                else ""
            )
            lines.append(
                f"| {name} | {col['role']} | {col['datatype']} | {col.get('type', '')} "
                f"| `{col.get('default_format', '')}` | {alias_cell} | {col.get('folder', '')} "
                f"| `{col['suggested_column']}` |"
            )
        lines.append("")

    if "calculated_fields" in sections:
        lines += ["## Calculated fields", ""]
        if not inv["calculated_fields"]:
            lines += ["_none_", ""]
        for calc in inv["calculated_fields"]:
            tags = []
            if calc["lod_kinds"]:
                tags.append("LOD: " + "/".join(calc["lod_kinds"]))
            if calc["lod_nested"]:
                tags.append("nested")
            if calc["table_calc_functions"]:
                tags.append("table calc: " + ", ".join(calc["table_calc_functions"]))
            if calc.get("semi_additive_suspect"):
                tags.append("possibly semi-additive")
            suffix = f" — {'; '.join(tags)}" if tags else ""
            lines.append(f"### {calc['caption']}  `{calc['complexity']}`{suffix}")
            lines.append("")
            lines.append("```")
            lines.append(calc["formula"])
            lines.append("```")
            if calc.get("blockers"):
                lines.append("**Blocked:**")
                lines += [f"- {b}" for b in calc["blockers"]]
                lines.append("")
            if calc.get("needs_review"):
                lines.append("**Forces a decision:**")
                lines += [f"- {r}" for r in calc["needs_review"]]
                lines.append("")
            lines.append(f"**Action:** {calc['action']}")
            lines.append("")
            lines.append(f"Translate to: {calc['translate_to']}")
            lines.append("")

    if "groups_and_bins" in sections:
        lines += ["## Groups and bins", ""]
        if not inv.get("groups_and_bins"):
            lines += ["_none_", ""]
        else:
            lines += ["| Kind | Field | Source field | Detail | Translate to |", "|---|---|---|---|---|"]
            for item in inv["groups_and_bins"]:
                if item["kind"] == "bin":
                    detail = f"size {item.get('bin_size', '')} base {item.get('bin_base', '')}"
                elif item["kind"] == "set":
                    detail = (
                        "computed (rule-based)"
                        if item.get("computed")
                        else f"constant, {len(item['members'])} member(s)"
                    )
                else:
                    detail = f"{len(item['members'])} member(s)"
                lines.append(
                    f"| {item['kind']} | {item['caption']} | `{item['source_field']}` "
                    f"| {detail} | {item['translate_to']} |"
                )
            lines.append("")

    if "visual_styles" in sections and inv.get("visual_styles"):
        styles = inv["visual_styles"]
        lines += ["## Visual styles", ""]
        if styles.get("member_colors"):
            lines += [
                "Explicit member colours \u2014 reuse these hex values as an Altair "
                "`scale=alt.Scale(domain=\u2026, range=\u2026)` rather than letting the library assign:",
                "",
            ]
            for scope, mapping in sorted(styles["member_colors"].items()):
                pairs = ", ".join(f"`{k}` = `{v}`" for k, v in sorted(mapping.items()))
                lines.append(f"- **{scope}** \u2014 {pairs}")
            lines.append("")
        if styles.get("color_encodings"):
            lines += ["| Worksheet | Colour field | Palette |", "|---|---|---|"]
            # em dash bound outside the f-string: a backslash escape inside an
            # f-string expression is a syntax error before Python 3.12.
            _dash = "\u2014"
            for enc in styles["color_encodings"]:
                lines.append(
                    f"| {enc['worksheet']} | `{enc['field']}` | {enc['palette'] or _dash} |"
                )
            lines.append("")
        if styles.get("annotation_colors"):
            lines += ["Hand-written colour legend (from annotation text):", ""]
            for note in styles["annotation_colors"][:12]:
                lines.append(f"- `{note['color']}` \u2014 {note['text']}")
            lines.append("")
        backgrounds = styles.get("backgrounds", {})
        if backgrounds.get("worksheet") or backgrounds.get("dashboard"):
            lines.append(
                "- backgrounds \u2014 worksheet: "
                + (", ".join(f"`{c}`" for c in backgrounds.get("worksheet", [])) or "none")
                + "; dashboard: "
                + (", ".join(f"`{c}`" for c in backgrounds.get("dashboard", [])) or "none")
            )
        if styles.get("non_default_hex_colors"):
            palette = ", ".join(f"`{c}`" for c in styles["non_default_hex_colors"][:24])
            lines.append(f"- deliberate (non-default) palette: {palette}")
        lines.append("")

    if "field_usage" in sections and inv.get("field_usage"):
        usage = inv["field_usage"]
        summary = usage["summary"]
        lines += [
            "## Field reach",
            "",
            f"- fields total: {summary['total']}",
            f"- reach a dashboard: {summary['reach_a_dashboard']}",
            f"- stranded on orphan sheets: {summary['stranded_on_orphan_sheets']}",
            f"- used only by other calculations: {summary['dependencies_only']}",
            f"- unused entirely: {summary['unused']}",
            "",
        ]
        unreached = [f for f in usage["fields"] if not f["reaches_a_dashboard"]]
        if unreached:
            lines += [
                "Fields that reach no dashboard — confirm before translating:",
                "",
                "| Field | Calculated | On sheets | Used by calcs | Verdict |",
                "|---|---|---|---|---|",
            ]
            for field in unreached:
                verdict = (
                    "dependency — keep"
                    if field["used_by_calculations"]
                    else "stranded on orphan sheet"
                    if field["stranded"]
                    else "unused — candidate to skip"
                )
                lines.append(
                    f"| {field['caption']} | {'yes' if field['is_calculated'] else 'no'} "
                    f"| {', '.join(field['worksheets']) or '—'} "
                    f"| {', '.join(field['used_by_calculations']) or '—'} | {verdict} |"
                )
            lines.append("")

    if "parameters" in sections:
        lines += ["## Parameters", "", "| Parameter | Type | Current | Widget | Domain |", "|---|---|---|---|---|"]
        for param in inv["parameters"]:
            domain = ""
            if "range" in param:
                rng = param["range"]
                domain = f"{rng['min']}..{rng['max']} step {rng['step']}".strip()
            elif "members" in param:
                domain = ", ".join(param["members"][:8])
            lines.append(
                f"| {param['caption']} | {param['datatype']} | {param['current_value']} "
                f"| `{param['suggested_widget']}` | {domain} |"
            )
        lines.append("")

    if "filters" in sections:
        lines += ["## Filters", "", "| Scope | Worksheet | Field | Class | Context | Widget | Pipeline stage |", "|---|---|---|---|---|---|---|"]
        for filt in inv["filters"]:
            lines.append(
                f"| {filt['scope']} | {filt['worksheet']} | {filt['field']} | {filt['class']} "
                f"| {'yes' if filt['context'] else 'no'} | `{filt['suggested_widget']}` "
                f"| {filt['pipeline_stage']} |"
            )
        lines.append("")

    if "worksheets" in sections:
        lines += ["## Worksheets", "", "| Worksheet | Marks | Suggested | Cols shelf | Rows shelf | Encodings |", "|---|---|---|---|---|---|"]
        for ws in inv["worksheets"]:
            channels = ", ".join(
                f"{e['channel']}={e['field']}" for e in ws["encodings"] if e["field"]
            )
            lines.append(
                f"| {ws['name']} | {', '.join(ws['marks']) or '—'} | `{ws['suggested_mark']}` "
                f"| {ws['cols_shelf']} | {ws['rows_shelf']} | {channels} |"
            )
        lines.append("")

    if "dashboards" in sections:
        lines += ["## Dashboards", ""]
        if not inv["dashboards"]:
            lines += ["_none_", ""]
        for dash in inv["dashboards"]:
            lines.append(f"### {dash['name']}")
            lines.append("")
            zone_lines: list[str] = []
            _render_zone_lines(dash["zones"], zone_lines)
            lines += zone_lines or ["  - _no zones_"]
            lines.append("")
            if dash.get("layout"):
                lines += [
                    "Derived layout — use these weights rather than eyeballing the pixel widths:",
                    "",
                    "| Container | Direction | Children | Streamlit |",
                    "|---|---|---|---|",
                ]
                for container in dash["layout"]:
                    flag = " ⚠️ overlapping" if container["overlapping_children"] else ""
                    lines.append(
                        f"| `{container['container']}` | {container['direction']} "
                        f"| {', '.join(container['children'])} "
                        f"| `{container.get('streamlit', '—')}`{flag} |"
                    )
                lines.append("")

    if "actions" in sections:
        lines += ["## Actions", "", "| Caption | Kind | Source | Target | Implementation |", "|---|---|---|---|---|"]
        for action in inv["actions"]:
            lines.append(
                f"| {action['caption']} | {action['kind']} | {action['source_worksheet']} "
                f"| {action['target_worksheet']} | {action['implementation']} |"
            )
        if not inv["actions"]:
            lines.append("| _none_ | | | | |")
        lines.append("")

    lines += [
        "## Counts",
        "",
        f"- datasources: {len(inv['datasources'])}",
        f"- columns: {len(inv['columns'])}",
        f"- calculated fields: {len(inv['calculated_fields'])}",
        f"- parameters: {len(inv['parameters'])}",
        f"- filters: {len(inv['filters'])}",
        f"- worksheets: {len(inv['worksheets'])}",
        f"- dashboards: {len(inv['dashboards'])}",
        f"- actions: {len(inv['actions'])}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- discovery


def discover_sources(root: Path) -> tuple[list[Path], list[str]]:
    """Find Tableau sources under a directory, with guards.

    A migrator pointed at a shared drive is the normal case for portfolio work, and a
    naive `rglob` on one is a good way to hang on a network mount, follow a symlink out
    of the tree, or try to inventory ten thousand files. The guards are not paranoia:
    the input is a directory someone else organised.
    """
    notes: list[str] = []
    found: list[Path] = []
    seen_dirs: set[tuple[int, int]] = set()
    truncated = False

    # Breadth-first with an explicit queue so depth is bounded without recursion.
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth > _MAX_CRAWL_DEPTH:
            notes.append(
                f"stopped descending at {_MAX_CRAWL_DEPTH} levels below the start "
                f"directory (at {current.name}/) — pass a narrower path if the "
                "workbooks live deeper"
            )
            continue
        try:
            entries = sorted(current.iterdir())
        except (OSError, PermissionError) as exc:
            notes.append(f"could not read {current.name}/: {exc}")
            continue
        for entry in entries:
            if len(found) >= _MAX_CRAWL_FILES:
                truncated = True
                break
            if entry.is_symlink():
                # Following symlinks can leave the tree entirely and can form cycles.
                notes.append(f"skipped symlink {entry.name}")
                continue
            if entry.is_dir():
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                # Guard against directory cycles created by hard links or bind mounts.
                key = (stat.st_dev, stat.st_ino)
                if key in seen_dirs:
                    notes.append(f"skipped already-visited directory {entry.name}/")
                    continue
                seen_dirs.add(key)
                queue.append((entry, depth + 1))
            elif entry.suffix.lower() in {".twb", ".twbx", ".tds", ".tdsx"}:
                found.append(entry)
        if truncated:
            break

    if truncated:
        notes.append(
            f"stopped after {_MAX_CRAWL_FILES} file(s) — inventory a subdirectory at a "
            "time rather than raising this limit; a portfolio that large needs the "
            "consolidation conversation first (references/portfolio-analysis.md)"
        )
    return found, notes


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory a Tableau workbook or datasource for migration to Streamlit in Snowflake."
    )
    parser.add_argument(
        "source",
        help="path to a .twb, .twbx, .tds, or .tdsx file, or a directory to search",
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="output format (default: markdown)",
    )
    parser.add_argument(
        "--section", action="append", choices=SECTIONS, dest="sections",
        help="limit output to a section; repeatable (default: all)",
    )
    parser.add_argument(
        "--recurse", action="store_true",
        help="when source is a directory, inventory every workbook found under it",
    )
    args = parser.parse_args(argv)

    path = Path(args.source).expanduser()

    if path.is_dir():
        if not args.recurse:
            print(
                f"error: {path} is a directory; pass --recurse to inventory every "
                "workbook under it",
                file=sys.stderr,
            )
            return 2
        sources, notes = discover_sources(path)
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        if not sources:
            print(f"error: no Tableau files found under {path}", file=sys.stderr)
            return 2
        return _inventory_many(sources, args, notes)

    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2
    if path.suffix.lower() not in {".twb", ".twbx", ".tds", ".tdsx"}:
        print(
            f"error: unsupported extension {path.suffix!r}; expected .twb, .twbx, "
            ".tds, or .tdsx",
            file=sys.stderr,
        )
        return 2

    try:
        inventory = build_inventory(path)
    except _READ_FAILURES as exc:
        print(f"error: could not read {path.name}: {exc}", file=sys.stderr)
        return 1

    sections = args.sections or SECTIONS
    if args.format == "json":
        payload = (
            inventory
            if not args.sections
            else {
                "source": inventory["source"],
                "source_kind": inventory["source_kind"],
                **{s: inventory[s] for s in sections},
            }
        )
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(inventory, sections))
    return 0


def _inventory_many(sources: list[Path], args: argparse.Namespace, notes: list[str]) -> int:
    """Inventory several workbooks. One unreadable file must not lose the rest."""
    sections = args.sections or SECTIONS
    inventories: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for source in sources:
        try:
            inventories.append(build_inventory(source))
        except _READ_FAILURES as exc:
            failures.append({"source": str(source), "error": str(exc)})
            print(f"error: could not read {source.name}: {exc}", file=sys.stderr)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "sources_found": len(sources),
                    "inventoried": len(inventories),
                    "crawl_notes": notes,
                    "failures": failures,
                    "inventories": [
                        inv
                        if not args.sections
                        else {
                            "source": inv["source"],
                            "source_kind": inv["source_kind"],
                            **{s: inv[s] for s in sections},
                        }
                        for inv in inventories
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"# Portfolio inventory — {len(inventories)} of {len(sources)} source(s)")
        print()
        if failures:
            print("## Could not read")
            print()
            for failure in failures:
                print(f"- `{failure['source']}` — {failure['error']}")
            print()
        print(
            "Read `references/portfolio-analysis.md` before migrating any of these: "
            "decide overlap, consolidation, and order first."
        )
        print()
        for inv in inventories:
            print(render_markdown(inv, sections))
            print()

    # A crawl that read nothing is a failure; a crawl that read some is a partial success
    # the caller can act on, and the failures were already reported on stderr.
    return 0 if inventories else 1


if __name__ == "__main__":
    sys.exit(main())
