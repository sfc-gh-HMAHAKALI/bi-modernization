"""
bim_ui.theme — semantic color system, palette resolution, and theme emission.

Design rule: color means exactly one thing across the whole app. A chart series,
a KPI delta, and a grid cell that all represent "actual vs target" must use the
same two colors. This module is the single source of truth for that.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ─────────────────────────────────────────────────────────────
#  Semantic roles
# ─────────────────────────────────────────────────────────────
# These are ROLES, not colors. Charts, KPIs, and grids all reference roles so a
# palette swap propagates everywhere. Never hardcode a hex in a chart builder.

ROLE_ACTUAL = "actual"
ROLE_TARGET = "target"
ROLE_FORECAST = "forecast"
ROLE_GOOD = "good"
ROLE_WARN = "warn"
ROLE_BAD = "bad"
ROLE_NEUTRAL = "neutral"
ROLE_TEXT = "text"
ROLE_TEXT_MUTED = "text_muted"
ROLE_SURFACE = "surface"
ROLE_SURFACE_ALT = "surface_alt"
ROLE_BACKGROUND = "background"
ROLE_GRID = "grid"
ROLE_BORDER = "border"

# Status colors are deliberately NOT drawn from the customer brand palette.
# Brand palettes rarely contain an accessible red/green pair, and "is this
# number good or bad" must never be ambiguous. Two tuned sets: the light-
# background pair fails AA on dark surfaces and vice versa, so pick by background.
_STATUS_ON_LIGHT = {
    ROLE_GOOD: "#1B7F3B",
    ROLE_WARN: "#B45309",
    ROLE_BAD: "#B4232C",
}
_STATUS_ON_DARK = {
    ROLE_GOOD: "#4ADE80",
    ROLE_WARN: "#FBBF24",
    ROLE_BAD: "#F87171",
}


@dataclass
class Palette:
    """Resolved color palette for one app.

    `categorical` is the ordered sequence used for series/category encoding.
    `sequential` is a 2-stop ramp for continuous encoding (heatmaps, treemaps).
    """

    roles: dict[str, str] = field(default_factory=dict)
    categorical: list[str] = field(default_factory=list)
    sequential: tuple[str, str] = ("#e8eef0", "#004952")

    def __getitem__(self, role: str) -> str:
        if role not in self.roles:
            raise KeyError(
                f"Unknown semantic role {role!r}. "
                f"Known roles: {sorted(self.roles)}"
            )
        return self.roles[role]

    def get(self, role: str, default: str | None = None) -> str | None:
        return self.roles.get(role, default)

    def series(self, n: int) -> list[str]:
        """n categorical colors, cycling if the palette is short."""
        if n <= 0:
            return []
        if not self.categorical:
            return [self.roles.get(ROLE_ACTUAL, "#004952")] * n
        out = []
        for i in range(n):
            out.append(self.categorical[i % len(self.categorical)])
        return out

    def for_category(self, values: Sequence[str]) -> dict[str, str]:
        """Stable value -> color map. Same input order always yields same colors."""
        cols = self.series(len(values))
        return {v: c for v, c in zip(values, cols)}

    def status(self, ratio: float, good_at: float = 0.95, warn_at: float = 0.80) -> str:
        """Map an attainment ratio to a status color.

        Thresholds are explicit arguments so a dashboard can carry its own
        definition of "on track" rather than inheriting a hidden default.
        """
        if ratio is None:
            return self.roles[ROLE_NEUTRAL]
        if ratio >= good_at:
            return self.roles[ROLE_GOOD]
        if ratio >= warn_at:
            return self.roles[ROLE_WARN]
        return self.roles[ROLE_BAD]


# ─────────────────────────────────────────────────────────────
#  Color math (used for contrast checking and ramp derivation)
# ─────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Not a hex color: {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: Iterable[float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _relative_luminance(h: str) -> float:
    """WCAG 2.1 relative luminance."""
    def chan(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(h)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(bg: str, light: str = "#ffffff", dark: str = "#1c1c1c") -> str:
    """Pick whichever of light/dark reads better on bg. Used for text in colored fills."""
    return light if contrast_ratio(light, bg) >= contrast_ratio(dark, bg) else dark


def adjust_lightness(h: str, factor: float) -> str:
    """Scale lightness in HLS space. factor > 1 lightens, < 1 darkens."""
    r, g, b = (c / 255.0 for c in _hex_to_rgb(h))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    light = max(0.0, min(1.0, light * factor))
    return _rgb_to_hex(c * 255 for c in colorsys.hls_to_rgb(hue, light, sat))


# Professional extension palette, used to top up when the brand palette cannot
# supply enough usable series colors. Preferred over algorithmic hue rotation,
# which produces neon (#f71579, #40f715) that looks wrong in a finance dashboard.
_EXTENSION_CATEGORICAL = [
    "#11567F", "#29B5E8", "#7D44CF", "#C25E00", "#0E7C7B", "#8A5A44",
    "#4A5C8A", "#7A6900", "#5B4B8A", "#2E6E4F", "#8C4A6E", "#3D6B8C",
]


def _derive_target_color(primary: str, background: str, *, is_dark: bool) -> str:
    """Find a tint of primary that reads as 'benchmark' and still meets contrast.

    A fixed lightness multiplier is too blunt: on some brand primaries it lands
    a hair under 3:1 and fails the audit. This walks lightness away from the
    background until the result both clears AA_LARGE with margin and is
    perceptually distinct from primary, so 'actual' and 'target' never blur.
    """
    r, g, b = (v / 255.0 for v in _hex_to_rgb(primary))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)

    # On light backgrounds a lighter tint reads as secondary; on dark, darker.
    steps = [x / 100.0 for x in range(6, 46, 2)]
    best = None
    for delta in steps:
        cand_light = light - delta if is_dark else light + delta
        cand_light = max(0.06, min(0.94, cand_light))
        cand = _rgb_to_hex(
            v * 255 for v in colorsys.hls_to_rgb(hue, cand_light, sat * 0.92)
        )
        if contrast_ratio(cand, background) < AA_LARGE + 0.25:
            continue
        if _perceptual_distance(cand, primary) < 0.10:
            continue
        best = cand
        break

    if best is None:
        # Neutral fallback that always clears contrast on either background.
        best = "#6B7A82" if not is_dark else "#9AAAB2"
    return best


def _perceptual_distance(a: str, b: str) -> float:
    """Approximate perceptual distance between two colors, 0.0 to ~1.0.

    Luminance alone is the wrong metric for categorical palettes: it rejects a
    teal and a blue of similar brightness (which are easy to tell apart) while
    accepting black next to a dark navy (which are not). This weights hue and
    saturation alongside luminance so the filter matches what the eye does.

    Not CIEDE2000 -- deliberately dependency-free, and good enough to separate
    palette entries. Hue distance is circular and de-weighted at low saturation,
    where hue carries little information.
    """
    ra, ga, ba = (v / 255.0 for v in _hex_to_rgb(a))
    rb, gb, bb = (v / 255.0 for v in _hex_to_rgb(b))
    ha, la, sa = colorsys.rgb_to_hls(ra, ga, ba)
    hb, lb, sb = colorsys.rgb_to_hls(rb, gb, bb)

    d_hue = min(abs(ha - hb), 1.0 - abs(ha - hb)) * 2.0  # 0..1
    d_light = abs(la - lb)
    d_sat = abs(sa - sb)

    # Hue only counts when both colors are saturated enough to show it.
    hue_weight = min(sa, sb)
    return (0.62 * d_hue * hue_weight) + (0.72 * d_light) + (0.20 * d_sat)


def _usable_series_colors(
    candidates: Iterable[str],
    background: str,
    *,
    want: int = 8,
    min_distance: float = 0.13,
    min_saturation: float = 0.12,
    reserved: Iterable[str] = (),
) -> list[str]:
    """Pick categorical colors that are distinguishable, visible, and unreserved.

    Four independent filters, all necessary:

      - contrast vs background >= 3:1, because a bar is a large UI component.
        Brand palettes are full of pale tints (Tebra ships #cbdde0, #ffe9e3,
        #f6f3eb) that render as near-invisible fills on white.
      - saturation floor, because black and grey read as axis, text, or "no
        data" rather than as a category. Tebra ships #000000, #666666, #2b2b2b;
        using them as series colors makes a bar look like a UI element.
      - perceptual separation between accepted colors, so adjacent categories in
        a stacked bar or legend do not read as the same color.
      - distance from `reserved` (the status colors), so a category is never
        painted the same red the app uses to mean "missing target".

    If the brand palette cannot supply `want` usable colors, top up by rotating
    hue off the strongest brand color rather than falling back to a generic
    palette. That keeps the result on-brand instead of half-brand-half-default.
    """
    reserved = list(reserved)
    kept: list[str] = []
    seen: set[str] = set()

    def acceptable(c: str) -> bool:
        if contrast_ratio(c, background) < AA_LARGE:
            return False
        r, g, b = (v / 255.0 for v in _hex_to_rgb(c))
        _, _, sat = colorsys.rgb_to_hls(r, g, b)
        if sat < min_saturation:
            return False
        if any(_perceptual_distance(c, k) < min_distance for k in kept):
            return False
        if any(_perceptual_distance(c, rc) < min_distance for rc in reserved):
            return False
        return True

    for c in candidates:
        try:
            _hex_to_rgb(c)
        except ValueError:
            continue
        key = c.lower()
        if key in seen or not acceptable(c):
            continue
        seen.add(key)
        kept.append(c)

    # NOTE: append incrementally, never via list comprehension. `acceptable`
    # closes over `kept`, so a comprehension would evaluate every candidate
    # against an empty list and admit colors that fail separation against each
    # other. Each candidate must be tested against already-accepted colors.
    if not kept:
        for c in _FALLBACK_CATEGORICAL:
            if len(kept) >= want:
                break
            if acceptable(c):
                kept.append(c)

    # Top up from the curated extension palette first -- professional and
    # predictable. Only fall through to hue rotation if that is still short.
    if len(kept) < want:
        for c in _EXTENSION_CATEGORICAL:
            if len(kept) >= want:
                break
            if c.lower() in {k.lower() for k in kept} or not acceptable(c):
                continue
            kept.append(c)

    if kept and len(kept) < want:
        base = kept[0]
        r, g, b = (v / 255.0 for v in _hex_to_rgb(base))
        h0, l0, s0 = colorsys.rgb_to_hls(r, g, b)
        step = 1.0 / max(want, 1)
        offset = 1
        while len(kept) < want and offset < want * 5:
            hue = (h0 + step * offset) % 1.0
            light = min(0.62, max(0.26, l0 * (1.0 + 0.10 * ((offset % 3) - 1))))
            # Cap saturation: keeps generated colors muted rather than neon.
            cand = _rgb_to_hex(
                v * 255 for v in colorsys.hls_to_rgb(hue, light, min(max(s0, 0.32), 0.62))
            )
            offset += 1
            if cand.lower() in {k.lower() for k in kept} or not acceptable(cand):
                continue
            kept.append(cand)

    return kept[:max(want, 1)]


# ─────────────────────────────────────────────────────────────
#  Palette resolution from extracted visual metadata
# ─────────────────────────────────────────────────────────────

# Fallback palette (Snowflake-neutral) used when the source file yields nothing.
_FALLBACK_CATEGORICAL = [
    "#11567F", "#29B5E8", "#7D44CF", "#FF9F36", "#71D3DC", "#8A999E", "#255E7E",
]


def _panel_tint(primary: str, background: str, *, strength: float = 0.055) -> str:
    """A barely-there tint of primary over background, for sidebars and panels.

    Kept very subtle: enough that the sidebar reads as a separate surface,
    not enough to compete with the data or to fail text contrast against it.
    """
    pr, pg, pb = _hex_to_rgb(primary)
    br, bg_, bb = _hex_to_rgb(background)
    mix = (
        br + (pr - br) * strength,
        bg_ + (pg - bg_) * strength,
        bb + (pb - bb) * strength,
    )
    return _rgb_to_hex(mix)


def resolve_palette(
    visuals: dict | None = None,
    *,
    brand_colors: Sequence[str] | None = None,
    background: str | None = None,
) -> Palette:
    """Build a Palette from extract-visuals output.

    Precedence: explicit brand_colors argument, then visuals.json global_styles /
    color_mappings, then the neutral fallback. Status colors are always fixed.
    """
    visuals = visuals or {}
    styles = visuals.get("global_styles") or {}

    candidates: list[str] = []
    if brand_colors:
        candidates.extend(brand_colors)
    else:
        for key in ("categorical_palette", "palette", "colors"):
            val = styles.get(key)
            if isinstance(val, list):
                candidates.extend(c for c in val if isinstance(c, str))
        # color_mappings carries field-value -> color assignments from the source.
        for cm in visuals.get("color_mappings") or []:
            vals = cm.get("values") if isinstance(cm, dict) else None
            if isinstance(vals, dict):
                candidates.extend(c for c in vals.values() if isinstance(c, str))

    bg = background or styles.get("background") or "#FFFFFF"
    # A dark source background needs a different text/surface treatment.
    is_dark = _relative_luminance(bg) < 0.3
    status = _STATUS_ON_DARK if is_dark else _STATUS_ON_LIGHT

    # Series colors are filtered for visibility against this specific background,
    # and kept clear of the status colors so category never reads as good/bad.
    categorical = _usable_series_colors(
        candidates, bg, want=8, reserved=status.values()
    )

    primary = categorical[0]
    # Target/reference color: a tint of primary that reads as "benchmark" rather
    # than competing with the actual series, contrast-verified rather than guessed.
    target = _derive_target_color(primary, bg, is_dark=is_dark)

    roles = {
        ROLE_ACTUAL: primary,
        ROLE_TARGET: target,
        ROLE_FORECAST: categorical[1] if len(categorical) > 1 else adjust_lightness(primary, 1.4),
        ROLE_NEUTRAL: "#8A999E",
        ROLE_TEXT: "#F1F5F9" if is_dark else "#1C1C1C",
        ROLE_TEXT_MUTED: "#94A3B8" if is_dark else "#5A6872",
        ROLE_SURFACE: adjust_lightness(bg, 1.08) if is_dark else "#FFFFFF",
        # A distinct panel tint for the sidebar and grouped regions. On a white
        # page, surface is also white (cards are separated by a border), so
        # without this the sidebar would be invisible against the content area.
        ROLE_SURFACE_ALT: (adjust_lightness(bg, 1.22) if is_dark
                           else _panel_tint(primary, bg)),
        ROLE_BACKGROUND: bg,
        ROLE_GRID: "#33414B" if is_dark else "#E8E5DF",
        ROLE_BORDER: "#3A4750" if is_dark else "#DFDCD5",
        **status,
    }

    return Palette(
        roles=roles,
        categorical=categorical,
        sequential=(adjust_lightness(primary, 2.4), primary),
    )


# ─────────────────────────────────────────────────────────────
#  Contrast auditing
# ─────────────────────────────────────────────────────────────

# WCAG 2.1 AA: 4.5:1 for normal text, 3.0:1 for large text and UI components.
AA_TEXT = 4.5
AA_LARGE = 3.0


@dataclass
class ContrastFinding:
    label: str
    fg: str
    bg: str
    ratio: float
    required: float

    @property
    def passes(self) -> bool:
        return self.ratio >= self.required

    def __str__(self) -> str:
        verdict = "PASS" if self.passes else "FAIL"
        return (f"{verdict} {self.label}: {self.fg} on {self.bg} "
                f"= {self.ratio:.2f}:1 (need {self.required}:1)")


def audit_contrast(palette: Palette) -> list[ContrastFinding]:
    """Check every foreground/background pair the kit actually renders.

    Returns findings rather than raising, so a generator can surface warnings
    without blocking on a customer's brand palette it cannot change.
    """
    bg = palette[ROLE_BACKGROUND]
    surface = palette[ROLE_SURFACE]
    alt = palette.get(ROLE_SURFACE_ALT, surface)
    pairs = [
        ("body text on background", palette[ROLE_TEXT], bg, AA_TEXT),
        ("body text on surface", palette[ROLE_TEXT], surface, AA_TEXT),
        ("body text on sidebar", palette[ROLE_TEXT], alt, AA_TEXT),
        ("muted text on surface", palette[ROLE_TEXT_MUTED], surface, AA_TEXT),
        ("muted text on sidebar", palette[ROLE_TEXT_MUTED], alt, AA_TEXT),
        ("KPI value on surface", palette[ROLE_ACTUAL], surface, AA_LARGE),
        ("good status on surface", palette[ROLE_GOOD], surface, AA_TEXT),
        ("warn status on surface", palette[ROLE_WARN], surface, AA_TEXT),
        ("bad status on surface", palette[ROLE_BAD], surface, AA_TEXT),
        ("actual series on background", palette[ROLE_ACTUAL], bg, AA_LARGE),
        ("target series on background", palette[ROLE_TARGET], bg, AA_LARGE),
    ]
    return [ContrastFinding(lbl, fg, b, contrast_ratio(fg, b), req)
            for lbl, fg, b, req in pairs]


def contrast_failures(palette: Palette) -> list[ContrastFinding]:
    return [f for f in audit_contrast(palette) if not f.passes]


# ─────────────────────────────────────────────────────────────
#  Theme emission
# ─────────────────────────────────────────────────────────────

def config_toml(palette: Palette, *, font: str = "sans serif") -> str:
    """Emit .streamlit/config.toml.

    Prefer this over CSS injection: it themes widgets, the sidebar, and native
    dataframes, which CSS cannot reach reliably across Streamlit versions.
    """
    return f"""# Generated by bi-modernization. Theme derived from the source BI file.
[theme]
base = "{'dark' if _relative_luminance(palette[ROLE_BACKGROUND]) < 0.3 else 'light'}"
primaryColor = "{palette[ROLE_ACTUAL]}"
backgroundColor = "{palette[ROLE_BACKGROUND]}"
secondaryBackgroundColor = "{palette[ROLE_SURFACE_ALT]}"
textColor = "{palette[ROLE_TEXT]}"
font = "{font}"

[server]
# Container runtime shares one server across viewers; keep messages modest.
maxMessageSize = 200
"""


def base_css(palette: Palette) -> str:
    """Minimal CSS for what config.toml cannot express.

    Deliberately small. Every rule here is something the theme config has no
    equivalent for. Notably absent: background-clip: text (renders invisible in
    Streamlit's webview) and any fixed heights that would clip content.
    """
    return f"""
<style>
  /* Page header band */
  .bim-header {{
      background: linear-gradient(135deg, {palette[ROLE_ACTUAL]} 0%,
                  {adjust_lightness(palette[ROLE_ACTUAL], 1.35)} 100%);
      padding: 14px 22px; border-radius: 10px; margin-bottom: 6px;
  }}
  .bim-header h1 {{
      color: {readable_on(palette[ROLE_ACTUAL])};
      font-size: 21px; margin: 0; font-weight: 700; line-height: 1.25;
  }}
  .bim-header p {{
      color: {readable_on(palette[ROLE_ACTUAL])};
      opacity: .82; font-size: 12px; margin: 3px 0 0 0;
  }}

  /* KPI card. min-height not height: labels wrap rather than truncate. */
  .bim-kpi {{
      background: {palette[ROLE_SURFACE]};
      border: 1px solid {palette[ROLE_BORDER]};
      border-radius: 10px; padding: 12px 14px; min-height: 118px;
      display: flex; flex-direction: column; gap: 2px;
  }}
  .bim-kpi-label {{
      font-size: 10.5px; font-weight: 600; letter-spacing: .02em;
      text-transform: uppercase; color: {palette[ROLE_TEXT_MUTED]};
      line-height: 1.25; overflow-wrap: break-word;
  }}
  .bim-kpi-value {{
      font-size: clamp(17px, 2.1vw, 25px); font-weight: 700;
      color: {palette[ROLE_TEXT]}; line-height: 1.15; white-space: nowrap;
  }}
  .bim-kpi-delta {{ font-size: 11px; line-height: 1.3; overflow-wrap: break-word; }}
  .bim-kpi-spark {{ margin-top: auto; line-height: 0; }}

  /* Provenance strip */
  .bim-prov {{
      display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
      background: {palette[ROLE_SURFACE]};
      border: 1px solid {palette[ROLE_BORDER]};
      border-left: 3px solid {palette[ROLE_ACTUAL]};
      border-radius: 8px; padding: 7px 13px; margin-bottom: 12px;
      font-size: 11.5px; color: {palette[ROLE_TEXT_MUTED]};
  }}
  .bim-prov b {{ color: {palette[ROLE_TEXT]}; font-weight: 600; }}
  .bim-prov .bim-synthetic {{
      color: {palette[ROLE_WARN]}; font-weight: 600;
  }}

  /* Active-filter chips */
  .bim-chip {{
      display: inline-flex; align-items: center; gap: 6px;
      background: {adjust_lightness(palette[ROLE_ACTUAL], 1.9)};
      color: {palette[ROLE_ACTUAL]};
      border: 1px solid {palette[ROLE_ACTUAL]};
      border-radius: 13px; padding: 2px 10px; font-size: 11.5px;
      font-weight: 600; margin: 0 5px 5px 0;
  }}
  .bim-chip-dim {{ opacity: .72; font-weight: 500; }}

  /* Drill breadcrumb */
  .bim-crumb {{
      font-size: 12px; color: {palette[ROLE_TEXT_MUTED]}; margin-bottom: 8px;
  }}
  .bim-crumb b {{ color: {palette[ROLE_TEXT]}; }}

  .bim-section {{
      font-size: 14px; font-weight: 600; color: {palette[ROLE_TEXT]};
      margin: 4px 0 6px 0;
  }}

  /* Sidebar gets the panel tint so it reads as a distinct surface. Also set via
     config.toml secondaryBackgroundColor; this covers Streamlit versions where
     the theme key does not reach the sidebar container. */
  section[data-testid="stSidebar"] {{
      background-color: {palette.get(ROLE_SURFACE_ALT, palette[ROLE_SURFACE])};
      border-right: 1px solid {palette[ROLE_BORDER]};
  }}
  section[data-testid="stSidebar"] .stRadio label p {{ font-size: 13px; }}
</style>
"""
