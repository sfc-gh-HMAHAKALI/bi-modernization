"""
bim_ui.fmt — one formatting code path for the whole app.

The failure this prevents: a KPI card saying "$4.2M", its axis saying "4200",
and the grid cell saying "4,200.00" for the same underlying number. Every
surface formats through this registry, and the registry is seeded from the
number formats extracted from the source BI file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["currency", "percent", "count", "number", "duration", "text"]


@dataclass(frozen=True)
class NumberFormat:
    """How one field should render, everywhere it appears.

    scale divides the raw value before display, so a column stored in whole
    dollars can render as thousands without mutating the dataframe.
    """

    kind: Kind = "number"
    decimals: int = 0
    prefix: str = ""
    suffix: str = ""
    scale: float = 1.0
    compact: bool = False
    thousands: bool = True

    # ── rendering ──

    def __call__(self, value: Any) -> str:
        return self.render(value)

    def render(self, value: Any, *, na: str = "—") -> str:
        if value is None:
            return na
        if isinstance(value, str):
            return value
        try:
            v = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isnan(v) or math.isinf(v):
            return na

        v = v / self.scale if self.scale not in (0, 1.0) else v

        if self.kind == "percent":
            return f"{self.prefix}{v * 100:.{self.decimals}f}%{self.suffix}"
        if self.compact:
            return f"{self.prefix}{_compact(v, self.decimals)}{self.suffix}"

        spec = f",.{self.decimals}f" if self.thousands else f".{self.decimals}f"
        return f"{self.prefix}{v:{spec}}{self.suffix}"

    # ── Plotly axis interop ──

    def plotly_axis(self) -> dict:
        """Axis kwargs so tick labels match card and grid formatting."""
        if self.kind == "percent":
            return {"tickformat": f".{self.decimals}%"}
        out: dict[str, Any] = {
            "tickformat": f",.{self.decimals}f" if self.thousands else f".{self.decimals}f"
        }
        if self.prefix:
            out["tickprefix"] = self.prefix
        if self.suffix:
            out["ticksuffix"] = self.suffix
        return out

    def plotly_hover(self) -> str:
        """d3-format string for hovertemplate."""
        if self.kind == "percent":
            return f"{self.prefix}%{{y:.{self.decimals}%}}{self.suffix}"
        if self.compact:
            return f"{self.prefix}%{{y:.3s}}{self.suffix}"
        return f"{self.prefix}%{{y:,.{self.decimals}f}}{self.suffix}"

    # ── Vega-Lite / Altair interop ──

    def vega_format(self) -> str:
        """d3-format string for Altair axes and tooltips.

        Vega-Lite takes a bare d3 spec rather than Plotly's templated form, and
        has no prefix/suffix properties -- a currency symbol has to be baked into
        the format string itself. Keeping this beside plotly_axis/plotly_hover is
        what stops an axis tick, a tooltip, a KPI card and a grid cell from
        showing the same number four different ways.
        """
        if self.kind == "percent":
            return f"{self.prefix}.{self.decimals}%{self.suffix}"
        if self.compact:
            # "~s" is d3's SI prefix with insignificant zeros trimmed: 0 -> "0",
            # 200 -> "200", 1000 -> "1k", 4.2e6 -> "4.2M". Plain ".3s" pads to
            # three significant digits and produces a visibly inconsistent axis
            # -- "$0.00, $200, $1.00k" on the same scale.
            return f"{self.prefix}~s{self.suffix}"
        spec = f",.{self.decimals}f" if self.thousands else f".{self.decimals}f"
        return f"{self.prefix}{spec}{self.suffix}"

    # ── Streamlit column_config interop ──

    def st_number_format(self) -> str:
        """Format string for st.column_config.NumberColumn.

        Streamlit accepts printf-style specs. Percent columns are passed as
        already-scaled values with a literal %% so we keep full control of
        rounding rather than relying on Streamlit's own percent handling.
        """
        if self.kind == "percent":
            return f"{self.prefix}%.{self.decimals}f%%{self.suffix}"
        return f"{self.prefix}%,.{self.decimals}f{self.suffix}"


def _compact(v: float, decimals: int = 1) -> str:
    """1_234_567 -> 1.2M. Used for KPI values and dense axes."""
    a = abs(v)
    sign = "-" if v < 0 else ""
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= cutoff:
            scaled = a / cutoff
            # 4.2M reads better than 4M; 42M reads better than 42.0M.
            d = decimals if scaled < 10 else 0
            return f"{sign}{scaled:.{d}f}{suffix}"
    # Below 1000: never invent decimals for a whole number ($0, not $0.0).
    d = 0 if float(a).is_integer() else decimals
    return f"{sign}{a:,.{d}f}"


# ─────────────────────────────────────────────────────────────
#  Stock formats
# ─────────────────────────────────────────────────────────────

CURRENCY = NumberFormat("currency", decimals=0, prefix="$")
CURRENCY_CENTS = NumberFormat("currency", decimals=2, prefix="$")
CURRENCY_COMPACT = NumberFormat("currency", decimals=1, prefix="$", compact=True)
CURRENCY_K = NumberFormat("currency", decimals=0, prefix="$", suffix="K")
PERCENT = NumberFormat("percent", decimals=0)
PERCENT_1 = NumberFormat("percent", decimals=1)
COUNT = NumberFormat("count", decimals=0)
NUMBER = NumberFormat("number", decimals=0)
NUMBER_1 = NumberFormat("number", decimals=1)
RATIO = NumberFormat("number", decimals=2)
TEXT = NumberFormat("text")


# ─────────────────────────────────────────────────────────────
#  Registry
# ─────────────────────────────────────────────────────────────

# Name-based inference. Ordered: first match wins, so put specific before generic.
# Percent patterns must precede currency ones ("attainment %" is not dollars).
_DATEISH = re.compile(
    r"(date|_dt$|^dt_|timestamp|_at$|month$|_month|quarter|^year$|_year$|period|week_of)",
    re.I,
)

_INFERENCE: list[tuple[re.Pattern[str], NumberFormat]] = [
    (re.compile(r"(attain|pct|percent|%|_rate$|rate_|ratio|attach|share|margin|growth)", re.I), PERCENT),
    (re.compile(r"(count|cnt|#|num_|_num$|deals?$|wins?$|customers?$|providers?$|opps?$)", re.I), COUNT),
    (re.compile(r"(arr|mrr|nrr|acv|tcv|bookings?|revenue|amount|plan|forecast|quota|spend|cost|price|gap)", re.I), CURRENCY_COMPACT),
    (re.compile(r"(days?|weeks?|term)$", re.I), COUNT),
]


class FormatRegistry:
    """Field name -> NumberFormat, seeded from source metadata.

    Lookup order:
      1. explicit override registered by the generator or app author
      2. format extracted from the source BI file (visuals.json number_formats)
      3. name-based inference
      4. dtype-based default
    """

    def __init__(self) -> None:
        self._explicit: dict[str, NumberFormat] = {}
        self._extracted: dict[str, NumberFormat] = {}

    # ── registration ──

    def register(self, field: str, fmt: NumberFormat) -> "FormatRegistry":
        self._explicit[_norm(field)] = fmt
        return self

    def register_many(self, mapping: dict[str, NumberFormat]) -> "FormatRegistry":
        for k, v in mapping.items():
            self.register(k, v)
        return self

    def load_visuals(self, visuals: dict | None) -> "FormatRegistry":
        """Ingest number_formats from extract-visuals output.

        This is the step the previous generator skipped, which is why formatting
        was hand-rolled. Tableau format strings are parsed best-effort; anything
        unrecognized falls through to inference rather than failing the build.
        """
        for entry in (visuals or {}).get("number_formats") or []:
            if not isinstance(entry, dict):
                continue
            field = entry.get("field") or entry.get("column") or entry.get("name")
            if not field:
                continue
            fmt = _parse_source_format(entry)
            if fmt is not None:
                self._extracted[_norm(str(field))] = fmt
        return self

    # ── lookup ──

    def get(self, field: str, dtype: Any = None) -> NumberFormat:
        key = _norm(field)
        if key in self._explicit:
            return self._explicit[key]
        if key in self._extracted:
            return self._extracted[key]
        # Date-like fields are labels, not measures. Guard before numeric
        # inference so CLOSED_MONTH does not become a formatted count.
        if _DATEISH.search(field):
            return TEXT
        for pattern, fmt in _INFERENCE:
            if pattern.search(field):
                return fmt
        return _from_dtype(dtype)

    def __contains__(self, field: str) -> bool:
        return _norm(field) in self._explicit or _norm(field) in self._extracted

    def render(self, field: str, value: Any, dtype: Any = None) -> str:
        return self.get(field, dtype).render(value)

    def describe(self) -> dict[str, str]:
        """Human-readable dump for the fidelity report."""
        out = {}
        for k, v in {**self._extracted, **self._explicit}.items():
            src = "explicit" if k in self._explicit else "source file"
            out[k] = f"{v.kind}, {v.decimals}dp, prefix={v.prefix!r} ({src})"
        return out


def _norm(field: str) -> str:
    """Match on a loose key so 'Split ARR', 'SPLIT_ARR', and 'split arr' agree."""
    return re.sub(r"[^a-z0-9]+", "", str(field).lower())


def _from_dtype(dtype: Any) -> NumberFormat:
    name = str(dtype).lower() if dtype is not None else ""
    if "float" in name:
        return NUMBER_1
    if "int" in name:
        return COUNT
    if "bool" in name or "object" in name or "string" in name or "category" in name:
        return TEXT
    return NUMBER


def _parse_source_format(entry: dict) -> NumberFormat | None:
    """Best-effort parse of a source BI number format descriptor.

    Handles the shapes extract-visuals emits: an explicit kind, or a raw
    Tableau/Excel-style format string like '$#,##0.00' or '0.0%'.
    """
    kind = (entry.get("kind") or entry.get("type") or "").lower()
    raw = entry.get("format") or entry.get("format_string") or entry.get("pattern") or ""
    raw = str(raw)

    decimals = entry.get("decimals")
    if decimals is None:
        m = re.search(r"\.(0+|#+)", raw)
        decimals = len(m.group(1)) if m else 0
    decimals = int(decimals)

    if kind.startswith("percent") or "%" in raw:
        return NumberFormat("percent", decimals=decimals)
    if kind.startswith("currency") or "$" in raw or "€" in raw or "£" in raw:
        prefix = "$"
        for sym in ("€", "£", "¥"):
            if sym in raw:
                prefix = sym
        return NumberFormat("currency", decimals=decimals, prefix=prefix,
                            compact=bool(entry.get("compact")))
    if kind.startswith(("count", "int")):
        return NumberFormat("count", decimals=0)
    if kind.startswith("number") or raw:
        return NumberFormat("number", decimals=decimals,
                            thousands="," in raw or not raw)
    return None


# ─────────────────────────────────────────────────────────────
#  Convenience wrappers
# ─────────────────────────────────────────────────────────────

def currency(v: Any, *, compact: bool = True, decimals: int | None = None) -> str:
    f = CURRENCY_COMPACT if compact else CURRENCY
    if decimals is not None:
        f = NumberFormat(f.kind, decimals=decimals, prefix=f.prefix,
                         suffix=f.suffix, compact=f.compact)
    return f.render(v)


def percent(v: Any, decimals: int = 0) -> str:
    return NumberFormat("percent", decimals=decimals).render(v)


def count(v: Any) -> str:
    return COUNT.render(v)


def delta(
    value: Any,
    baseline: Any,
    fmt: NumberFormat = CURRENCY_COMPACT,
    *,
    as_percent: bool = False,
    na: str = "—",
) -> tuple[str, str]:
    """Return (rendered_delta, direction) where direction is up/down/flat.

    Callers map direction to a semantic color role. Keeping the arrow glyph and
    the color decision together here stops "green with a down arrow" bugs.
    """
    try:
        v, b = float(value), float(baseline)
    except (TypeError, ValueError):
        return na, "flat"
    if math.isnan(v) or math.isnan(b):
        return na, "flat"

    diff = v - b
    if abs(diff) < 1e-9:
        return "no change", "flat"

    direction = "up" if diff > 0 else "down"
    arrow = "▲" if diff > 0 else "▼"

    if as_percent:
        if abs(b) < 1e-9:
            return na, direction
        return f"{arrow} {abs(diff / b) * 100:.0f}%", direction
    return f"{arrow} {fmt.render(abs(diff))}", direction
