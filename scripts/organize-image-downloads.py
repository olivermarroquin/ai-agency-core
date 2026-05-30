#!/usr/bin/env python3
"""
organize-image-downloads.py — sort fresh Higgsfield downloads into a page folder.

Plain English: you generate 4 variants of an image in Higgsfield, download
them, and they land on your laptop in `~/workspace/_inbox/higgsfield-downloads/`
as `1.png`, `2.png`, `3.png`, `4.png`. You eyeball the four, pick the best
one. This script takes your choice and the rest, renames them according to
the imagery SOP convention, moves them into the page folder's `images/`
directory (your pick at the root, the others under `_drafts-and-alternates/`),
and clears the staging folder so you're ready for the next batch.

The script is the only stage in the imagery pipeline that asks the operator
which variant is best — that judgment can't be automated. Everything
downstream (optimize, upload, wire HTML, publish) is mechanical and is
handled by `wire-page-images.py`.

USAGE
-----
Organize a hero image, picking variant 4 as the keeper:

    python organize-image-downloads.py \\
        --client ev-electric-services \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --image-type hero \\
        --selected-variant 4

Provide your own filename stem (overrides the default folder-name derivation):

    python organize-image-downloads.py \\
        --client ev-electric-services \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --image-type hero \\
        --selected-variant 4 \\
        --filename-stem vienna-panel-upgrade

Use a non-default staging folder:

    python organize-image-downloads.py \\
        --client ev-electric-services \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --image-type hero \\
        --selected-variant 4 \\
        --staging-folder /Users/me/Downloads/higgsfield-batch-2026-05-26

Dry run — show what would happen but don't move any files:

    python organize-image-downloads.py ... --dry-run

INPUTS
------
- Staging folder containing files named `1.png`, `2.png`, ... (or just any PNGs).
- A page folder under the client's `website-archive/new/core-30/`.
- The slot type (hero, about, scene) — sets the type token in the filename.
- The variant number you've picked as the keeper.

OUTPUTS
-------
- Selected variant moved to `<page-folder>/images/<stem>-<type>-v1-variant-<N>.png`.
- Other variants moved to `<page-folder>/images/_drafts-and-alternates/`.
- Staging folder cleared (unless --keep-staging is passed).

PRECONDITIONS
-------------
- Python 3.8+ (stdlib only).
- The staging folder exists. The script creates the page-folder/images/
  subtree if it doesn't already.

NAMING
------
The default filename stem is the page folder name with any leading `NN-`
position prefix stripped. So a folder named `02-panel-upgrade-vienna-va`
produces files like `panel-upgrade-vienna-va-hero-v1-variant-4.png`. If you
prefer a different convention (e.g., city-first like
`vienna-panel-upgrade-hero-v1-variant-4.png` which is what pages 1 and 2
used), pass `--filename-stem` to override.

VERSION
-------
Variant filenames always start at v1. If you re-run Higgsfield because the
first batch didn't land, pass `--starting-version 2` and the new batch's
filenames will start at v2. The script never overwrites existing files.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Optional


DEFAULT_STAGING = Path.home() / "workspace" / "_inbox" / "higgsfield-downloads"


def default_filename_stem_from_folder(folder: Path) -> str:
    """02-panel-upgrade-vienna-va → panel-upgrade-vienna-va"""
    return re.sub(r"^\d+-", "", folder.name)


def collect_staged_pngs(staging: Path) -> list[Path]:
    """Return all PNG files in the staging folder, sorted by numeric prefix
    when present, else by name.
    """
    if not staging.is_dir():
        return []
    pngs = sorted(p for p in staging.iterdir() if p.suffix.lower() == ".png")
    # Sort by leading number if files are named `1.png`, `2.png`, ...
    def sort_key(p: Path) -> tuple[int, str]:
        m = re.match(r"^(\d+)", p.stem)
        return (int(m.group(1)) if m else 9999, p.name)
    return sorted(pngs, key=sort_key)


def detect_variant_number(path: Path) -> Optional[int]:
    """Return the variant index encoded in a staged filename, or None.

    Accepts either `1.png` → 1 or `variant-3.png` → 3 or anything starting
    with a number. Used to identify which staged file corresponds to which
    variant the operator picked.
    """
    m = re.match(r"^(\d+)", path.stem)
    if m:
        return int(m.group(1))
    m = re.search(r"variant[-_]?(\d+)", path.stem, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def organize(
    staging: Path,
    page_folder: Path,
    image_type: str,
    selected_variant: int,
    filename_stem: str,
    starting_version: int,
    keep_staging: bool,
    dry_run: bool,
) -> int:
    images_dir = page_folder / "images"
    drafts_dir = images_dir / "_drafts-and-alternates"

    staged = collect_staged_pngs(staging)
    if not staged:
        sys.stderr.write(f"ERROR: no PNG files found in staging folder: {staging}\n")
        return 2

    # Map each staged file to its variant index
    variant_map: dict[int, Path] = {}
    unnumbered: list[Path] = []
    for p in staged:
        v = detect_variant_number(p)
        if v is None:
            unnumbered.append(p)
        else:
            variant_map[v] = p

    if selected_variant not in variant_map:
        sys.stderr.write(
            f"ERROR: --selected-variant {selected_variant} not found in staging.\n"
            f"  Staged files (variant → file):\n"
        )
        for v, p in sorted(variant_map.items()):
            sys.stderr.write(f"    {v}: {p.name}\n")
        for p in unnumbered:
            sys.stderr.write(f"    (unnumbered): {p.name}\n")
        return 2

    sys.stderr.write(f"→ Staging folder: {staging}\n")
    sys.stderr.write(f"→ Page folder:    {page_folder}\n")
    sys.stderr.write(f"→ Slot:           {image_type}\n")
    sys.stderr.write(f"→ Filename stem:  {filename_stem}\n")
    sys.stderr.write(f"→ Version start:  v{starting_version}\n")
    sys.stderr.write(f"→ Selected:       variant {selected_variant} ({variant_map[selected_variant].name})\n")

    # Plan moves
    moves: list[tuple[Path, Path, str]] = []  # (src, dst, label)
    for v, src in sorted(variant_map.items()):
        new_name = (
            f"{filename_stem}-{image_type}-v{starting_version}-variant-{v}.png"
        )
        if v == selected_variant:
            dst = images_dir / new_name
            label = "KEEPER"
        else:
            dst = drafts_dir / new_name
            label = "alternate"
        # If a file with this name already exists, refuse to overwrite (non-destructive)
        if dst.exists():
            sys.stderr.write(
                f"ERROR: destination already exists: {dst}\n"
                f"  Pass --starting-version {starting_version + 1} to skip past, "
                f"or move the existing file first.\n"
            )
            return 3
        moves.append((src, dst, label))

    for src, dst, label in moves:
        sys.stderr.write(f"  [{label}]  {src.name} → {dst.relative_to(page_folder)}\n")

    if dry_run:
        sys.stderr.write("→ DRY RUN — no files moved.\n")
        # Print the would-be keeper path on stdout so the orchestrator can read it.
        keeper_dst = next(d for _, d, l in moves if l == "KEEPER")
        print(keeper_dst)
        return 0

    # Make sure target directories exist
    images_dir.mkdir(parents=True, exist_ok=True)
    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Perform moves
    keeper_path: Optional[Path] = None
    for src, dst, label in moves:
        shutil.move(str(src), str(dst))
        if label == "KEEPER":
            keeper_path = dst

    # Optionally clear staging
    if not keep_staging:
        for leftover in staging.iterdir():
            if leftover.suffix.lower() == ".png":
                # Should already be gone via shutil.move but defensive
                leftover.unlink()
    sys.stderr.write(f"→ Done. Keeper at: {keeper_path}\n")

    if keeper_path:
        print(keeper_path)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sort Higgsfield download variants into a Core 30 page folder.",
    )
    p.add_argument(
        "--client",
        required=True,
        help="Client slug (used for cross-checks; doesn't affect file moves directly).",
    )
    p.add_argument(
        "--page-folder",
        type=Path,
        required=True,
        help="Path to the Core 30 page folder.",
    )
    p.add_argument(
        "--image-type",
        choices=["hero", "about", "scene"],
        required=True,
        help="Which slot the chosen variant fills.",
    )
    p.add_argument(
        "--selected-variant",
        type=int,
        required=True,
        help="Variant number you've chosen as the keeper (e.g., 4).",
    )
    p.add_argument(
        "--filename-stem",
        type=str,
        default=None,
        help="Override the default filename stem (default: page folder name minus NN- prefix).",
    )
    p.add_argument(
        "--staging-folder",
        type=Path,
        default=DEFAULT_STAGING,
        help=f"Where the downloads currently live (default: {DEFAULT_STAGING}).",
    )
    p.add_argument(
        "--starting-version",
        type=int,
        default=1,
        help="Starting v-number for filenames (default: 1). Bump if a previous batch already used v1.",
    )
    p.add_argument(
        "--keep-staging",
        action="store_true",
        help="Don't clear the staging folder after move.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned moves without performing them.",
    )

    args = p.parse_args()

    if not args.page_folder.is_dir():
        sys.stderr.write(f"ERROR: page-folder not found: {args.page_folder}\n")
        return 2

    filename_stem = args.filename_stem or default_filename_stem_from_folder(args.page_folder)

    return organize(
        staging=args.staging_folder,
        page_folder=args.page_folder,
        image_type=args.image_type,
        selected_variant=args.selected_variant,
        filename_stem=filename_stem,
        starting_version=args.starting_version,
        keep_staging=args.keep_staging,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
