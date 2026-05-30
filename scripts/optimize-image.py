#!/usr/bin/env python3
"""
optimize-image.py — convert a PNG into a smaller WebP, ready for upload.

Plain English: takes a big PNG (typically 2-3 megabytes straight out of
Higgsfield), produces a much smaller WebP version of the same image
(typically 200-300 kilobytes) with no visible loss of quality. The smaller
file uploads to WordPress faster and uses less storage on the server. Imagify
on the WordPress side does further optimization after upload — this script
just gets us most of the way there before the file ever leaves the laptop.

The script picks a WebP quality level that lands the output under a target
file size. Different page slots tolerate different sizes — hero images get a
bit more headroom, portraits less, contextual scenes the most. Defaults match
the sizes recommended in the imagery SOP.

USAGE
-----
Optimize a hero image (defaults: target <300KB, quality starts at 90):

    python optimize-image.py /path/to/some-hero.png

Specify the slot type so the script picks the right size budget:

    python optimize-image.py /path/to/some-about.png --type about
    python optimize-image.py /path/to/some-scene.png --type scene

Override the output path (default: alongside the input, same name, .webp ext):

    python optimize-image.py /path/to/in.png --output /path/to/out.webp

Override the max size in kilobytes:

    python optimize-image.py /path/to/in.png --max-size-kb 250

INPUTS
------
- A PNG file produced by Higgsfield, Midjourney, Flux, or any other tool.
- Optional: image type (hero / about / scene) — sets the size budget.
- Optional: explicit output path.

OUTPUTS
-------
- A .webp file at the chosen output path.
- Prints the input size, output size, compression ratio, and quality used.

PRECONDITIONS
-------------
- Python 3.8+
- Pillow installed: pip install Pillow --break-system-packages
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:
    sys.stderr.write(
        "ERROR: Pillow is not installed. Run: pip install Pillow --break-system-packages\n"
    )
    sys.exit(2)


# Per-type size budgets from the imagery SOP. The number is kilobytes — we
# iterate WebP quality down until the file size lands under the budget.
_SIZE_BUDGETS_KB: dict[str, int] = {
    "hero": 300,
    "about": 200,
    "scene": 500,
}

# Quality search range. Start high, walk down. WebP quality 75 is still
# visually very good for photographic content; below 60 starts to show
# artifacts on faces. We refuse to go below 60.
_QUALITY_START = 90
_QUALITY_MIN = 60
_QUALITY_STEP = 5


def detect_image_type_from_filename(path: Path) -> Optional[str]:
    """Best-effort guess of slot type from filename. Returns hero/about/scene
    or None. Used as a fallback when --type isn't passed.
    """
    name_lower = path.name.lower()
    if "hero" in name_lower:
        return "hero"
    if "about" in name_lower or "portrait" in name_lower:
        return "about"
    if "scene" in name_lower:
        return "scene"
    return None


def optimize_to_webp(
    input_path: Path,
    output_path: Path,
    max_size_kb: int,
    quality_start: int = _QUALITY_START,
    quality_min: int = _QUALITY_MIN,
    quality_step: int = _QUALITY_STEP,
) -> tuple[int, int, int]:
    """Convert input PNG to WebP under a target file size.

    Iterates WebP quality from `quality_start` down to `quality_min` in
    `quality_step` decrements until the output file lands at or below
    `max_size_kb` kilobytes. If even the lowest quality is too big, writes
    the lowest-quality version anyway and warns.

    Returns (bytes_in, bytes_out, quality_used).
    """
    if not input_path.is_file():
        raise FileNotFoundError(f"Input image not found: {input_path}")

    bytes_in = input_path.stat().st_size
    max_bytes = max_size_kb * 1024

    output_path.parent.mkdir(parents=True, exist_ok=True)

    quality = quality_start
    last_bytes_out: int = 0
    while quality >= quality_min:
        with Image.open(input_path) as im:
            # Pillow refuses to save palette + transparency to WebP without
            # a conversion. Normalize everything to RGBA first.
            if im.mode in ("P", "LA"):
                im = im.convert("RGBA")
            elif im.mode == "CMYK":
                im = im.convert("RGB")
            im.save(
                output_path,
                format="WEBP",
                quality=quality,
                method=6,  # max-effort encoder; ~2x slower but ~5% smaller
            )
        last_bytes_out = output_path.stat().st_size
        if last_bytes_out <= max_bytes:
            return bytes_in, last_bytes_out, quality
        quality -= quality_step

    # Walked all the way down. Warn but accept.
    sys.stderr.write(
        f"WARN: even at quality={quality_min} the output is "
        f"{last_bytes_out/1024:.1f}KB (budget {max_size_kb}KB). Keeping it anyway.\n"
    )
    return bytes_in, last_bytes_out, quality_min


def main() -> int:
    p = argparse.ArgumentParser(
        description="Convert a PNG into a smaller WebP for WordPress upload.",
    )
    p.add_argument(
        "input",
        type=Path,
        help="Path to the input PNG (or any Pillow-readable image).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path. Defaults to alongside input with .webp extension.",
    )
    p.add_argument(
        "--type",
        choices=["hero", "about", "scene"],
        default=None,
        help=(
            "Image slot type. Sets the size budget per the imagery SOP "
            "(hero<300KB, about<200KB, scene<500KB). Defaults to guessing "
            "from the filename, else 'hero'."
        ),
    )
    p.add_argument(
        "--max-size-kb",
        type=int,
        default=None,
        help="Override max output size in kilobytes (ignores --type budget).",
    )

    args = p.parse_args()

    if not args.input.is_file():
        sys.stderr.write(f"ERROR: input not found: {args.input}\n")
        return 2

    # Resolve image type
    image_type = args.type or detect_image_type_from_filename(args.input) or "hero"
    max_kb = args.max_size_kb if args.max_size_kb is not None else _SIZE_BUDGETS_KB[image_type]

    # Resolve output path
    output_path = args.output or args.input.with_suffix(".webp")

    sys.stderr.write(f"→ Input:      {args.input}\n")
    sys.stderr.write(f"→ Output:     {output_path}\n")
    sys.stderr.write(f"→ Slot type:  {image_type}\n")
    sys.stderr.write(f"→ Budget:     ≤{max_kb} KB\n")

    try:
        bytes_in, bytes_out, quality = optimize_to_webp(
            args.input, output_path, max_kb
        )
    except Exception as e:
        sys.stderr.write(f"FATAL: {e}\n")
        return 1

    ratio = (1 - bytes_out / bytes_in) * 100 if bytes_in else 0
    sys.stderr.write(
        f"→ Done.       {bytes_in/1024:.1f}KB → {bytes_out/1024:.1f}KB "
        f"({ratio:+.1f}% size change, quality={quality})\n"
    )
    # Print the output path on stdout so the orchestrator can capture it.
    print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
