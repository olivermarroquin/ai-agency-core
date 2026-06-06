#!/usr/bin/env python3
"""
ingest-real-photos.py — frictionless real-photo ingest for the imagery pipeline.

Operator drops real job photos into position-named subfolders of a per-client
drop folder. This script picks them up, resolves positions to page folders via
the build-order, files keepers + alternates per SOP naming, and optionally
light-enhances via Higgsfield CLI (flux_kontext img2img).

NO SLUG-TYPING. The drop-folder convention is:

    ~/workspace/_inbox/real-photos/<client-slug>/
    ├── 01-hero/
    │   └── IMG_1234.jpg          ← single file = keeper
    ├── 01-about/
    │   ├── keeper.jpg             ← named "keeper" = keeper
    │   └── other.jpg             ← alternate
    ├── 02-hero/
    │   └── panel-photo.png
    └── 05-scene/
        └── finished-panel.jpg

Position numbers match the build-order (01 = first page, etc.).
Slot types: hero, about, scene.

KEEPER SELECTION:
  - If ONE file in the subfolder → it's the keeper.
  - If multiple files and one is named keeper.* → that's the keeper.
  - If multiple files with no keeper.* → error; operator must rename one.

USAGE
-----
Ingest all real photos for S&H (dry run first):

    python ingest-real-photos.py \\
        --client s-and-h-contracting \\
        --dry-run

Ingest + light-enhance keepers via Higgsfield:

    python ingest-real-photos.py \\
        --client s-and-h-contracting \\
        --enhance

Process a single position (for testing):

    python ingest-real-photos.py \\
        --client s-and-h-contracting \\
        --only-position 01

OUTPUTS
-------
- Keepers filed at <page>/images/<stem>-<type>-v1-variant-1.png
- Enhanced versions (if --enhance) at <page>/images/<stem>-<type>-v1-variant-1-enhanced.png
- Alternates at <page>/images/_drafts-and-alternates/
- Processed subfolders cleared
- Stdout: one page folder path per line (for piping to wire-page-images.py)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_DROP_ROOT = Path.home() / "workspace" / "_inbox" / "real-photos"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic"}


# ---------------------------------------------------------------------------
# Build-order parsing
# ---------------------------------------------------------------------------


def parse_build_order(build_order_path: Path) -> dict[int, str]:
    """Parse _build-order.md → {position: page-slug}.

    Expects a markdown table with | # | Slug | rows, or lines like:
      | 01 | panel-upgrade-woodbridge-va |
    """
    mapping: dict[int, str] = {}
    text = build_order_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*([\w-]+)\s*\|", line)
        if m:
            pos = int(m.group(1))
            slug = m.group(2).strip()
            if slug and slug != "(reserved)":
                mapping[pos] = slug
    return mapping


def find_page_folder(core_30_dir: Path, position: int, slug: str) -> Optional[Path]:
    """Find the page folder for a given position + slug.

    Convention: folder named `<NN>-<slug>` under core_30_dir.
    """
    prefix = f"{position:02d}-"
    for d in core_30_dir.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            return d
    # Fallback: try exact match
    expected = core_30_dir / f"{prefix}{slug}"
    if expected.is_dir():
        return expected
    return None


# ---------------------------------------------------------------------------
# Drop-folder scanning
# ---------------------------------------------------------------------------


def scan_drop_folder(drop_dir: Path) -> list[dict]:
    """Scan the drop folder for position-named subfolders.

    Returns list of {position: int, slot_type: str, path: Path, files: [Path]}
    """
    entries = []
    if not drop_dir.is_dir():
        return entries

    for subdir in sorted(drop_dir.iterdir()):
        if not subdir.is_dir():
            continue
        m = re.match(r"^(\d+)-(hero|about|scene)$", subdir.name, re.IGNORECASE)
        if not m:
            continue
        position = int(m.group(1))
        slot_type = m.group(2).lower()
        files = [
            f for f in sorted(subdir.iterdir())
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS
        ]
        if files:
            entries.append({
                "position": position,
                "slot_type": slot_type,
                "path": subdir,
                "files": files,
            })
    return entries


def select_keeper(files: list[Path]) -> tuple[Path, list[Path]]:
    """Select the keeper from a list of files.

    Returns (keeper, alternates).
    Raises ValueError if ambiguous (multiple files, no keeper.* named).
    """
    if len(files) == 1:
        return files[0], []

    # Look for a file named "keeper.*"
    keepers = [f for f in files if f.stem.lower() == "keeper"]
    if len(keepers) == 1:
        alternates = [f for f in files if f != keepers[0]]
        return keepers[0], alternates

    if len(keepers) > 1:
        raise ValueError(
            f"Multiple files named 'keeper.*': {[k.name for k in keepers]}. "
            f"Keep only one."
        )

    raise ValueError(
        f"Multiple files but none named 'keeper.*': {[f.name for f in files]}. "
        f"Rename the best one to keeper{files[0].suffix} to select it."
    )


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


def file_stem_from_page(page_folder: Path) -> str:
    """02-panel-upgrade-vienna-va → panel-upgrade-vienna-va"""
    return re.sub(r"^\d+-", "", page_folder.name)


def file_image(
    src: Path,
    page_folder: Path,
    slot_type: str,
    is_keeper: bool,
    stem: str,
    version: int = 1,
    variant: int = 1,
    dry_run: bool = False,
) -> Path:
    """Move/copy an image into the page folder per SOP naming."""
    images_dir = page_folder / "images"
    drafts_dir = images_dir / "_drafts-and-alternates"

    # Normalize extension to .png for consistency (downstream expects it)
    ext = src.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        ext = ".jpg"  # keep jpg as-is, pipeline handles both

    new_name = f"{stem}-{slot_type}-v{version}-variant-{variant}{ext}"
    if is_keeper:
        dst = images_dir / new_name
    else:
        dst = drafts_dir / new_name

    if dst.exists():
        raise FileExistsError(f"Destination already exists: {dst}")

    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))

    return dst


# ---------------------------------------------------------------------------
# Light-enhance via Higgsfield CLI
# ---------------------------------------------------------------------------


def enhance_image(
    input_path: Path,
    output_path: Path,
    slot_type: str,
    dry_run: bool = False,
) -> Optional[Path]:
    """Light-enhance an image via Higgsfield flux_kontext.

    Returns the enhanced image path, or None on failure/dry-run.
    """
    aspect = "4:3" if slot_type == "hero" else "3:4" if slot_type == "about" else "4:3"
    prompt = (
        "Enhance this photo: improve lighting quality to warm natural light, "
        "sharpen subject details, subtle background cleanup. Keep all "
        "electrical components, wiring, breakers, panel details EXACTLY as "
        "they appear — do not modify, add, or remove any technical details. "
        "Photo-realistic editorial quality."
    )

    if dry_run:
        sys.stderr.write(f"    (dry-run: would enhance via flux_kontext → {output_path.name})\n")
        return None

    cmd = [
        "higgsfield", "generate", "create", "flux_kontext",
        "--prompt", prompt,
        "--image", str(input_path),
        "--aspect_ratio", aspect,
        "--wait", "--json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"    WARN: enhance timed out for {input_path.name}\n")
        return None

    if result.returncode != 0:
        sys.stderr.write(f"    WARN: enhance failed: {result.stderr[:200]}\n")
        return None

    try:
        data = json.loads(result.stdout)
        url = data[0]["result_url"]
    except (json.JSONDecodeError, KeyError, IndexError):
        sys.stderr.write(f"    WARN: could not parse enhance result\n")
        return None

    # Download the enhanced image
    import urllib.request
    try:
        urllib.request.urlretrieve(url, str(output_path))
    except Exception as e:
        sys.stderr.write(f"    WARN: download failed: {e}\n")
        return None

    sys.stderr.write(f"    Enhanced → {output_path.name}\n")
    return output_path


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def run(
    client_slug: str,
    drop_dir: Path,
    build_order_path: Path,
    core_30_dir: Path,
    enhance: bool,
    only_position: Optional[int],
    dry_run: bool,
) -> int:
    # Parse build order
    if not build_order_path.is_file():
        sys.stderr.write(f"ERROR: build-order not found: {build_order_path}\n")
        return 2
    build_order = parse_build_order(build_order_path)
    if not build_order:
        sys.stderr.write(f"ERROR: no positions found in build-order: {build_order_path}\n")
        return 2

    # Scan drop folder
    entries = scan_drop_folder(drop_dir)
    if not entries:
        sys.stderr.write(f"→ No images found in drop folder: {drop_dir}\n")
        sys.stderr.write(f"  Expected subfolders like 01-hero/, 02-about/, etc.\n")
        return 0

    if only_position is not None:
        entries = [e for e in entries if e["position"] == only_position]
        if not entries:
            sys.stderr.write(f"→ No images for position {only_position:02d} in drop folder.\n")
            return 0

    sys.stderr.write(f"→ Client:     {client_slug}\n")
    sys.stderr.write(f"→ Drop folder: {drop_dir}\n")
    sys.stderr.write(f"→ Found {len(entries)} slot(s) to ingest\n")
    if enhance:
        sys.stderr.write(f"→ Light-enhance: ON (flux_kontext)\n")
    if dry_run:
        sys.stderr.write(f"→ MODE: DRY RUN\n")

    pages_touched: list[Path] = []
    errors: list[str] = []

    for entry in entries:
        pos = entry["position"]
        slot = entry["slot_type"]
        files = entry["files"]
        subdir = entry["path"]

        sys.stderr.write(f"\n--- {pos:02d}-{slot} ({len(files)} file(s)) ---\n")

        # Resolve position → page folder
        slug = build_order.get(pos)
        if not slug:
            errors.append(f"{pos:02d}-{slot}: position not in build-order")
            sys.stderr.write(f"  ERROR: position {pos} not in build-order — skipped\n")
            continue

        page_folder = find_page_folder(core_30_dir, pos, slug)
        if not page_folder:
            errors.append(f"{pos:02d}-{slot}: page folder not found for {slug}")
            sys.stderr.write(f"  ERROR: page folder not found for {pos:02d}-{slug} — skipped\n")
            continue

        # Select keeper
        try:
            keeper, alternates = select_keeper(files)
        except ValueError as e:
            errors.append(f"{pos:02d}-{slot}: {e}")
            sys.stderr.write(f"  ERROR: {e}\n")
            continue

        stem = file_stem_from_page(page_folder)
        sys.stderr.write(f"  Page: {page_folder.name}\n")
        sys.stderr.write(f"  Keeper: {keeper.name}\n")

        # File the keeper
        try:
            keeper_dst = file_image(
                src=keeper, page_folder=page_folder, slot_type=slot,
                is_keeper=True, stem=stem, dry_run=dry_run,
            )
            sys.stderr.write(f"  Filed → {keeper_dst.relative_to(page_folder)}\n")
        except FileExistsError as e:
            errors.append(f"{pos:02d}-{slot}: {e}")
            sys.stderr.write(f"  ERROR: {e}\n")
            continue

        # File alternates
        for i, alt in enumerate(alternates, start=2):
            try:
                alt_dst = file_image(
                    src=alt, page_folder=page_folder, slot_type=slot,
                    is_keeper=False, stem=stem, variant=i, dry_run=dry_run,
                )
                sys.stderr.write(f"  Alt → {alt_dst.relative_to(page_folder)}\n")
            except FileExistsError as e:
                sys.stderr.write(f"  WARN: {e} — skipped alternate\n")

        # Optional enhance
        if enhance and not dry_run:
            enhanced_name = f"{stem}-{slot}-v1-variant-1-enhanced.png"
            enhanced_dst = page_folder / "images" / enhanced_name
            enhance_image(keeper_dst, enhanced_dst, slot, dry_run=dry_run)

        # Clear the processed subfolder
        if not dry_run:
            for f in subdir.iterdir():
                if f.is_file():
                    f.unlink()
            # Remove empty dir
            try:
                subdir.rmdir()
            except OSError:
                pass  # not empty (hidden files, etc.)

        if page_folder not in pages_touched:
            pages_touched.append(page_folder)

    # Summary
    sys.stderr.write(f"\n→ Pages touched: {len(pages_touched)}\n")
    if errors:
        sys.stderr.write(f"→ Errors: {len(errors)}\n")
        for e in errors:
            sys.stderr.write(f"    - {e}\n")

    # Print page paths to stdout for downstream
    for pf in pages_touched:
        print(pf)

    return 1 if errors else 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Frictionless real-photo ingest for the Core 30 imagery pipeline. "
            "Operator drops photos into position-named subfolders; this script "
            "files them into page folders per the build-order."
        ),
    )
    p.add_argument(
        "--client",
        required=True,
        help="Client slug (e.g., s-and-h-contracting).",
    )
    p.add_argument(
        "--drop-folder",
        type=Path,
        default=None,
        help=f"Drop folder root. Default: {DEFAULT_DROP_ROOT}/<client>/",
    )
    p.add_argument(
        "--build-order",
        type=Path,
        default=None,
        help="Path to _build-order.md. Default: auto-detected from client slug.",
    )
    p.add_argument(
        "--core-30-dir",
        type=Path,
        default=None,
        help="Path to the core-30/ directory. Default: auto-detected from client slug.",
    )
    p.add_argument(
        "--enhance",
        action="store_true",
        help="Light-enhance keepers via Higgsfield flux_kontext (1.5 credits each).",
    )
    p.add_argument(
        "--only-position",
        type=int,
        default=None,
        help="Only process a specific position number (for testing).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the ingest plan without moving files.",
    )

    args = p.parse_args()

    # Resolve paths
    client_slug = args.client
    projects_base = (
        Path.home() / "workspace" / "second-brain" / "04_projects"
        / "clients" / "_active" / client_slug
    )
    core_30_dir = args.core_30_dir or (
        projects_base / "website-archive" / "new" / "core-30"
    )
    build_order_path = args.build_order or (core_30_dir / "_build-order.md")
    drop_dir = args.drop_folder or (DEFAULT_DROP_ROOT / client_slug)

    if not core_30_dir.is_dir():
        sys.stderr.write(f"ERROR: core-30 directory not found: {core_30_dir}\n")
        return 2

    return run(
        client_slug=client_slug,
        drop_dir=drop_dir,
        build_order_path=build_order_path,
        core_30_dir=core_30_dir,
        enhance=args.enhance,
        only_position=args.only_position,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
