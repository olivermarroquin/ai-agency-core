#!/usr/bin/env python3
"""
wire-images-into-html.py — update a Core 30 page draft to point at real images.

Plain English: opens the most recent HTML draft for a page, finds the image
slot you specify (hero, about, or scene), and rewrites the <img> tag inside
that slot to point at the WordPress URL you give it. If the slot is still a
placeholder (no <img> tag yet, just a div with placeholder text), the script
injects a fresh <img> tag with the right styling. Saves the result as a new
version of the HTML file so the previous draft stays intact.

The script is non-destructive on the input file. It always writes a new
draft-v(N+1)-WP-WRAPPED.html and appends a row to _VERSION-LOG.md describing
what changed. The previous draft is left alone.

USAGE
-----
Wire a hero image:

    python wire-images-into-html.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --image-type hero \\
        --image-url "https://evelectric.pro/wp-content/uploads/2026/05/vienna-panel-upgrade-hero-v1-variant-4.webp" \\
        --alt-text "Ahmad Shaban installing a residential electrical panel in Vienna, VA"

Dry run — show what would change but don't write the new file:

    python wire-images-into-html.py \\
        --page-folder ... --image-type hero --image-url ... --alt-text ... \\
        --dry-run

WHAT THE SCRIPT TOUCHES
-----------------------
- Reads:  the highest-numbered draft-v*-WP-WRAPPED.html in the page folder.
- Writes: a new draft-v(N+1)-WP-WRAPPED.html with the slot updated.
- Appends a row to _VERSION-LOG.md noting which slot was wired.

WHAT THE SCRIPT DOES NOT TOUCH
------------------------------
- The original input HTML is never modified.
- Other image slots in the page (if you wire the hero, the about portrait
  and contextual scene are left exactly as they were).
- The image files on disk — those live in images/ and aren't this script's
  concern.

SLOT DETECTION
--------------
The script finds image slots by their CSS class:

- hero  → div with class "evp-hero-image"
- about → div with class "evp-about-image"
- scene → div with class "evp-scene-image" (added when contextual scene used)

Inside each div, the script either replaces the existing <img>'s src + alt
attributes, or — if the div is still a placeholder with no <img> — injects
one with the standard v17 styling.

PRECONDITIONS
-------------
- Python 3.8+ (stdlib only).
- The page folder must contain at least one draft-v*-WP-WRAPPED.html.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------------
# Slot specifications — class name + default styling for fresh injection
# ----------------------------------------------------------------------------


_SLOT_CLASS: dict[str, str] = {
    "hero":  "evp-hero-image",
    "about": "evp-about-image",
    "scene": "evp-scene-image",
}

# When injecting a fresh <img> into a placeholder div, use these defaults.
# `width`/`height` here are fallback intrinsic dimensions; if a local PNG path is
# provided to `wire_slot`, the actual pixel dimensions of that file override these.
# `style` is also overridden at write-time based on detected orientation — see
# `_style_for_orientation()` below.
#
# Why orientation-aware now (2026-05-26): the design system has two hero patterns —
# Pattern A (landscape: width:100%; height:100%; object-fit:cover) and Pattern B
# (portrait: fixed 360px width). When a hero changes orientation between page
# versions (e.g. portrait v1 → landscape v2 on page 2), the old preserved style
# from the previous HTML applies the wrong pattern. Forcing orientation-detected
# style + dims at write-time prevents the regression entirely.
_SLOT_DEFAULTS: dict[str, dict[str, str]] = {
    "hero": {
        "loading": "eager",
        "width": "1200",
        "height": "896",
    },
    "about": {
        "loading": "lazy",
        "width": "896",
        "height": "1200",
    },
    "scene": {
        "loading": "lazy",
        "width": "1600",
        "height": "900",
    },
}


# Inline style strings per orientation. Hero + about use the orientation-aware
# patterns; scene always uses a neutral width:100% scale (it's a wide content
# image, not a portrait).
_STYLE_LANDSCAPE = (
    "width:100%; height:100%; object-fit:cover; object-position:center 25%; "
    "display:block; border-radius:20px; -webkit-border-radius:20px;"
)
_STYLE_PORTRAIT = (
    "width:360px; max-width:100%; height:auto; "
    "display:block; border-radius:20px; -webkit-border-radius:20px;"
)
_STYLE_SCENE = (
    "width:100%; height:auto; "
    "display:block; border-radius:20px; -webkit-border-radius:20px;"
)


def _read_png_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Return (width, height) of the PNG/WebP/JPEG at `path`, or None on any error.

    Uses Pillow if available; falls back to None so callers can use defaults.
    Pillow is already a hard dep of `optimize-image.py` so it's safe to import.
    """
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None


def _style_for_orientation(
    slot_type: str,
    width: int,
    height: int,
) -> str:
    """Pick the right inline style for this image's orientation in this slot.

    - hero/about: landscape (w>h) → Pattern A; portrait (h>=w) → Pattern B.
    - scene: always Pattern Scene (neutral width:100%; height:auto).
    """
    if slot_type == "scene":
        return _STYLE_SCENE
    if width > height:
        return _STYLE_LANDSCAPE
    return _STYLE_PORTRAIT


def _resolve_dimensions_and_style(
    slot_type: str,
    local_path: Optional[Path],
) -> tuple[str, str, str]:
    """Return (width, height, style) strings for an <img> tag.

    If `local_path` is provided AND the file's pixel dimensions can be read,
    use those for both intrinsic dims and orientation-aware style.

    If `local_path` is None or unreadable, fall back to slot defaults and the
    landscape style (the most common case).
    """
    defaults = _SLOT_DEFAULTS[slot_type]
    if local_path is not None:
        dims = _read_png_dimensions(local_path)
        if dims is not None:
            w, h = dims
            return str(w), str(h), _style_for_orientation(slot_type, w, h)

    # Fall back: use slot defaults. Default-orientation style follows the
    # default dims (so a hero default of 1200×896 → landscape style).
    w = int(defaults["width"])
    h = int(defaults["height"])
    return defaults["width"], defaults["height"], _style_for_orientation(slot_type, w, h)


# ----------------------------------------------------------------------------
# File discovery + version bumping
# ----------------------------------------------------------------------------


_DRAFT_PATTERN = re.compile(r"draft-v(\d+)-WP-WRAPPED\.html$")


def find_latest_draft(page_folder: Path) -> tuple[Path, int]:
    """Return (path, version_number) for the highest-numbered draft."""
    candidates: list[tuple[int, Path]] = []
    for f in page_folder.glob("draft-v*-WP-WRAPPED.html"):
        m = _DRAFT_PATTERN.search(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        raise FileNotFoundError(
            f"No draft-v*-WP-WRAPPED.html found in {page_folder}"
        )
    candidates.sort()
    version, path = candidates[-1]
    return path, version


# ----------------------------------------------------------------------------
# Slot finding + mutation
# ----------------------------------------------------------------------------


def find_slot_bounds(html: str, slot_class: str) -> Optional[tuple[int, int]]:
    """Return (start_idx, end_idx) of the slot div in the HTML, or None.

    The slot div is identified by a `class="<slot_class>"` attribute. The
    div may have other classes alongside — we match by tokenized class list.
    """
    # Find every <div ...> opening tag. For each, check if its class list
    # contains our target slot_class. Then walk forward to find the matching
    # </div>, handling nested divs.
    open_tag_re = re.compile(r"<div\b([^>]*)>", re.IGNORECASE)
    for m in open_tag_re.finditer(html):
        attrs = m.group(1)
        class_match = re.search(r'class\s*=\s*["\']([^"\']+)["\']', attrs)
        if not class_match:
            continue
        classes = class_match.group(1).split()
        if slot_class not in classes:
            continue
        # Found the opening tag of our slot. Walk forward to its matching
        # </div>, accounting for nested <div>s.
        start = m.start()
        depth = 1
        cursor = m.end()
        while depth > 0 and cursor < len(html):
            next_open = html.find("<div", cursor)
            next_close = html.find("</div>", cursor)
            if next_close == -1:
                return None  # malformed HTML
            if next_open != -1 and next_open < next_close:
                depth += 1
                cursor = next_open + 4
            else:
                depth -= 1
                cursor = next_close + 6
        return start, cursor
    return None


def find_img_in_block(block: str) -> Optional[re.Match]:
    """Return a regex match for the first <img ...> in the block, or None."""
    return re.search(r"<img\b[^>]*?>", block, re.IGNORECASE | re.DOTALL)


def replace_img_attributes(
    img_tag: str,
    new_src: str,
    new_alt: str,
    new_width: Optional[str] = None,
    new_height: Optional[str] = None,
    new_style: Optional[str] = None,
) -> str:
    """Rewrite src and alt on an existing <img ...> tag. Optionally also
    overwrite width, height, and style attributes when provided.

    When width/height/style are provided (typically by `wire_slot` after it
    reads the local PNG's actual dimensions), the OLD values for those
    attributes are replaced rather than preserved. This is what prevents the
    Pattern-A-vs-B regression when an image changes orientation between page
    versions.

    Other attributes not named here (loading, class, etc.) are still preserved.
    """
    # Rewrite src
    if re.search(r'\bsrc\s*=\s*["\']', img_tag):
        img_tag = re.sub(
            r'\bsrc\s*=\s*["\'][^"\']*["\']',
            f'src="{new_src}"',
            img_tag,
            count=1,
        )
    else:
        # No src attribute — insert one right after <img
        img_tag = img_tag.replace("<img", f'<img src="{new_src}"', 1)

    # Rewrite alt (only if alt text was supplied)
    if new_alt:
        if re.search(r'\balt\s*=\s*["\']', img_tag):
            img_tag = re.sub(
                r'\balt\s*=\s*["\'][^"\']*["\']',
                f'alt="{new_alt}"',
                img_tag,
                count=1,
            )
        else:
            img_tag = img_tag.replace("<img", f'<img alt="{new_alt}"', 1)

    # Optionally rewrite width/height/style — only when caller passed them.
    # When None, behave as before (preserve the existing value).
    if new_width is not None:
        if re.search(r'\bwidth\s*=\s*["\']', img_tag):
            img_tag = re.sub(
                r'\bwidth\s*=\s*["\'][^"\']*["\']',
                f'width="{new_width}"',
                img_tag,
                count=1,
            )
        else:
            img_tag = img_tag.replace("<img", f'<img width="{new_width}"', 1)

    if new_height is not None:
        if re.search(r'\bheight\s*=\s*["\']', img_tag):
            img_tag = re.sub(
                r'\bheight\s*=\s*["\'][^"\']*["\']',
                f'height="{new_height}"',
                img_tag,
                count=1,
            )
        else:
            img_tag = img_tag.replace("<img", f'<img height="{new_height}"', 1)

    if new_style is not None:
        if re.search(r'\bstyle\s*=\s*["\']', img_tag):
            img_tag = re.sub(
                r'\bstyle\s*=\s*["\'][^"\']*["\']',
                f'style="{new_style}"',
                img_tag,
                count=1,
            )
        else:
            # Append style right before closing >
            img_tag = re.sub(r'\s*/?>$', f' style="{new_style}">', img_tag)

    return img_tag


def build_fresh_img_tag(
    slot_type: str,
    src: str,
    alt: str,
    local_path: Optional[Path] = None,
) -> str:
    """Construct an <img> tag with orientation-aware dims and style.

    When `local_path` is provided and readable, intrinsic width/height come
    from the actual PNG file and the inline style is picked to match the
    detected orientation. Otherwise falls back to slot defaults.
    """
    d = _SLOT_DEFAULTS[slot_type]
    width, height, style = _resolve_dimensions_and_style(slot_type, local_path)
    return (
        f'<img src="{src}"\n'
        f'             alt="{alt}"\n'
        f'             loading="{d["loading"]}"\n'
        f'             width="{width}" height="{height}"\n'
        f'             style="{style}">'
    )


def wire_slot(
    html: str,
    slot_type: str,
    new_src: str,
    new_alt: str,
    local_path: Optional[Path] = None,
) -> tuple[str, str]:
    """Mutate HTML to wire a new image URL into the named slot.

    When `local_path` points at the local PNG that's being uploaded, the
    intrinsic width/height attrs AND the inline style are rewritten based on
    the file's actual pixel dimensions. This prevents the Pattern-A-vs-B
    regression when an image's orientation changes between page versions.

    When `local_path` is None (e.g. CLI invocation with just a URL),
    behaves as before — only src and alt are rewritten on existing img tags;
    placeholders get the slot defaults.

    Returns (new_html, change_summary). Raises ValueError if the slot can't
    be located in the HTML.
    """
    slot_class = _SLOT_CLASS[slot_type]
    bounds = find_slot_bounds(html, slot_class)
    if bounds is None:
        raise ValueError(
            f"Could not find a <div class=\"{slot_class}\"> in the HTML. "
            f"Was the page scaffolded with a {slot_type} slot?"
        )
    start, end = bounds
    block = html[start:end]
    img_match = find_img_in_block(block)

    # Resolve orientation-aware dims+style once (if local file available)
    new_width = new_height = new_style = None
    if local_path is not None:
        w, h, s = _resolve_dimensions_and_style(slot_type, local_path)
        new_width, new_height, new_style = w, h, s

    if img_match:
        old_img = img_match.group(0)
        new_img = replace_img_attributes(
            old_img, new_src, new_alt,
            new_width=new_width,
            new_height=new_height,
            new_style=new_style,
        )
        new_block = block[:img_match.start()] + new_img + block[img_match.end():]
        # Pull old src out of the old img for the summary
        old_src_m = re.search(r'src\s*=\s*["\']([^"\']*)["\']', old_img)
        old_src = old_src_m.group(1) if old_src_m else "(none)"
        orient_note = ""
        if new_style is not None:
            orient = "landscape" if "object-fit:cover" in new_style else (
                "portrait" if "width:360px" in new_style else "scene"
            )
            orient_note = f" [{orient} style applied]"
        summary = (
            f"{slot_type}: src `{old_src}` → `{new_src}` "
            f"(existing <img> updated{orient_note})"
        )
    else:
        # Placeholder div with no img. Inject one.
        fresh = build_fresh_img_tag(slot_type, new_src, new_alt, local_path=local_path)
        # Inject as the first child of the div. Find the position right after
        # the opening div tag.
        open_div_end = block.find(">") + 1
        new_block = (
            block[:open_div_end]
            + "\n        "
            + fresh
            + "\n      "
            + block[open_div_end:]
        )
        summary = f"{slot_type}: placeholder → <img src=`{new_src}`> injected"

    new_html = html[:start] + new_block + html[end:]
    return new_html, summary


# ----------------------------------------------------------------------------
# Version log
# ----------------------------------------------------------------------------


def append_version_log_row(
    page_folder: Path,
    new_version: int,
    summary: str,
) -> None:
    """Insert a new row into the page's _VERSION-LOG.md iteration table.

    Locates the LAST existing `| **vN** | ...` table row and inserts the new
    row immediately after it — keeping the iteration table contiguous instead
    of stranding the new row at end-of-file (the v1 bug). Column format
    matches the existing scaffolder-generated table: Version | File | Notes.

    Falls back to creating a fresh table if no existing version log is found
    or no `| **vN** |` rows are present.
    """
    log_path = page_folder / "_VERSION-LOG.md"
    new_row = (
        f"| **v{new_version}** | `draft-v{new_version}-WP-WRAPPED.html` | "
        f"Image wiring: {summary} |\n"
    )

    if not log_path.exists():
        # No existing log — write a fresh table
        log_path.write_text(
            "| Version | File | Notes |\n|---|---|---|\n" + new_row,
            encoding="utf-8",
        )
        return

    existing = log_path.read_text(encoding="utf-8")
    lines = existing.splitlines(keepends=True)

    # Find the LAST line that looks like a version-table row: `| **vN** | ... |`
    row_pattern = re.compile(r"^\|\s*\*\*v\d+\*\*\s*\|.*\|\s*$")
    last_row_idx = -1
    for i, line in enumerate(lines):
        if row_pattern.match(line.rstrip("\n")):
            last_row_idx = i

    if last_row_idx == -1:
        # No existing version rows found — append at EOF (fallback)
        log_path.write_text(existing.rstrip("\n") + "\n" + new_row, encoding="utf-8")
        return

    # Insert the new row immediately after the last existing one
    lines.insert(last_row_idx + 1, new_row)
    log_path.write_text("".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Update a Core 30 page draft to point at a real image URL.",
    )
    p.add_argument(
        "--page-folder",
        type=Path,
        required=True,
        help="Path to a Core 30 page folder (must contain draft-v*-WP-WRAPPED.html).",
    )
    p.add_argument(
        "--image-type",
        choices=["hero", "about", "scene"],
        required=True,
        help="Which slot to wire.",
    )
    p.add_argument(
        "--image-url",
        type=str,
        required=True,
        help="WordPress URL of the uploaded image.",
    )
    p.add_argument(
        "--alt-text",
        type=str,
        default="",
        help="SEO alt text (see imagery SOP Step 8).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the diff but don't write the new draft.",
    )

    args = p.parse_args()

    if not args.page_folder.is_dir():
        sys.stderr.write(f"ERROR: page-folder not found: {args.page_folder}\n")
        return 2

    try:
        latest_path, latest_version = find_latest_draft(args.page_folder)
    except FileNotFoundError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    html = latest_path.read_text(encoding="utf-8")

    try:
        new_html, summary = wire_slot(
            html, args.image_type, args.image_url, args.alt_text
        )
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1

    sys.stderr.write(f"→ Input:    {latest_path.name}\n")
    sys.stderr.write(f"→ Change:   {summary}\n")

    if args.dry_run:
        sys.stderr.write("→ DRY RUN — no file written.\n")
        return 0

    new_version = latest_version + 1
    new_path = args.page_folder / f"draft-v{new_version}-WP-WRAPPED.html"
    new_path.write_text(new_html, encoding="utf-8")
    append_version_log_row(args.page_folder, new_version, summary)

    sys.stderr.write(f"→ Wrote:    {new_path.name}\n")
    sys.stderr.write(f"→ Logged:   _VERSION-LOG.md\n")
    print(new_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
