#!/usr/bin/env python3
"""Produce visual diagnostics for a Tableau screenshot and its Streamlit counterpart.

Unlike inspect_workbook.py, this needs Pillow. That is a deliberate exception: this
script runs on a developer's laptop during QA, never inside Streamlit in Snowflake, so
a dependency here does not affect what the deployed app needs.

    python3 scripts/visual_compare.py REFERENCE CANDIDATE --output-dir DIR

Writes side-by-side.png, overlay.png, difference.png, and stats.json into the output
directory, and prints the summary.

A pixel score locates differences. It never proves semantic parity: a near-zero
difference on a chart showing wrong numbers is worse than useless. Read the review order
in references/migration-qa.md before drawing conclusions.

Exit codes:
    0  diagnostics written
    1  an image could not be read
    2  invalid arguments
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops
except ImportError:
    raise SystemExit(
        "Pillow is required for visual comparison.\n"
        "Run: python3 -m pip install Pillow\n"
        "(QA-time only — it is not needed by the deployed app.)"
    ) from None


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite transparency onto white so alpha differences do not read as content."""
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def on_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Place an image top-left on a white canvas of the given size.

    Screenshots of the two tools rarely match exactly. Padding rather than scaling keeps
    the comparison honest: a resize would smear real layout differences into a uniform
    blur and flatter the result.
    """
    canvas = Image.new("RGB", size, "white")
    canvas.paste(image, (0, 0))
    return canvas


def compare(reference: Path, candidate: Path, out_dir: Path, threshold: float) -> dict:
    with Image.open(reference) as ref_raw, Image.open(candidate) as cand_raw:
        ref = flatten_to_rgb(ref_raw)
        cand = flatten_to_rgb(cand_raw)
        ref_size, cand_size = ref.size, cand.size

        canvas_size = (max(ref.width, cand.width), max(ref.height, cand.height))
        ref_c = on_canvas(ref, canvas_size)
        cand_c = on_canvas(cand, canvas_size)

        diff = ImageChops.difference(ref_c, cand_c)
        gray = diff.convert("L")

        cutoff = round(threshold * 255)
        histogram = gray.histogram()
        total = canvas_size[0] * canvas_size[1]
        changed = sum(histogram[cutoff + 1:]) if cutoff < 255 else 0
        weighted = sum(level * count for level, count in enumerate(histogram))
        mean_diff = (weighted / total / 255) if total else 0.0

        out_dir.mkdir(parents=True, exist_ok=True)

        side = Image.new("RGB", (canvas_size[0] * 2, canvas_size[1]), "white")
        side.paste(ref_c, (0, 0))
        side.paste(cand_c, (canvas_size[0], 0))
        side.save(out_dir / "side-by-side.png")

        Image.blend(ref_c, cand_c, 0.5).save(out_dir / "overlay.png")
        # Autocontrast would exaggerate trivial antialiasing; keep the raw difference.
        diff.save(out_dir / "difference.png")

        stats = {
            "reference": str(reference),
            "candidate": str(candidate),
            "reference_size": list(ref_size),
            "candidate_size": list(cand_size),
            "dimensions_match": ref_size == cand_size,
            "compared_canvas": list(canvas_size),
            "pixel_threshold": threshold,
            "changed_pixels": changed,
            "total_pixels": total,
            "changed_fraction": round(changed / total, 6) if total else 0.0,
            "mean_normalized_difference": round(mean_diff, 6),
            "artifacts": ["side-by-side.png", "overlay.png", "difference.png"],
            "caveat": (
                "Locates differences only. Not evidence of semantic parity; verify "
                "numbers separately."
            ),
        }
        (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
        return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Visual diagnostics for a Tableau/Streamlit screenshot pair."
    )
    parser.add_argument("reference", type=Path, help="Tableau reference screenshot")
    parser.add_argument("candidate", type=Path, help="Streamlit screenshot")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="directory for diagnostics"
    )
    parser.add_argument(
        "--pixel-threshold",
        type=float,
        default=0.05,
        help="normalized difference above which a pixel counts as changed (default 0.05)",
    )
    args = parser.parse_args(argv)

    if not 0 <= args.pixel_threshold <= 1:
        print("error: --pixel-threshold must be between 0 and 1", file=sys.stderr)
        return 2
    for path in (args.reference, args.candidate):
        if not path.is_file():
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2

    try:
        stats = compare(
            args.reference, args.candidate, args.output_dir, args.pixel_threshold
        )
    except OSError as exc:
        print(f"error: could not read an image: {exc}", file=sys.stderr)
        return 1

    if not stats["dimensions_match"]:
        print(
            f"dimensions differ: reference {stats['reference_size']} vs "
            f"candidate {stats['candidate_size']} — compared on a "
            f"{stats['compared_canvas']} canvas, so padding contributes to the "
            "difference. Recapture at a matching viewport for a meaningful score."
        )
    print(
        f"changed pixels: {stats['changed_pixels']}/{stats['total_pixels']} "
        f"({stats['changed_fraction']:.2%}) above threshold {args.pixel_threshold}"
    )
    print(f"mean normalized difference: {stats['mean_normalized_difference']:.6f}")
    print(f"artifacts written to {args.output_dir}")
    print("This locates differences; it does not prove the numbers agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
