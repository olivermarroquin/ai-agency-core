#!/usr/bin/env python3
"""
refresh-cached-image.py — force-refresh entries in the per-client image cache
so a regenerated image propagates everywhere it's used.

Plain English: the imagery pipeline (wire-page-images.py) memorizes uploaded
images in a per-client cache file. Once a slot is cached, the pipeline skips
re-uploads — which is exactly what you want during normal operation. This
script is the override for the moments when the cache is the wrong answer:
the owner shipped a new portrait, Higgsfield re-generated a better hero, or
the operator dropped fresh PNGs into 30 page folders at once and wants them
to propagate without 30 individual command runs.

There are two modes:

MODE 1 — Single-image refresh (one cache key, surgical)
--------------------------------------------------------
Use when you regenerated one image and want it everywhere that image was used.

    python refresh-cached-image.py \\
        --config /path/to/ev-electric.config.json \\
        --cache-key "ev-electric-services/vienna-va/about" \\
        --new-image /path/to/new-vienna-portrait.png \\
        --also-rewire-pages

The script:
  1. Looks up the cache entry by key. Records the old WordPress URL.
  2. (Optional, --delete-old-wp-image) DELETEs the old image from the
     WordPress Media Library via REST API.
  3. Removes the cache entry so the next upload-image-to-wp call won't
     short-circuit.
  4. (Optional, --new-image) Optimizes the PNG to WebP, uploads it via
     upload-image-to-wp.py, records a new cache entry.
  5. (Optional, --also-rewire-pages) Greps every Core 30 page draft for
     the old WordPress URL, runs wire-page-images.py against each
     affected page with --only-types <slot>. This pushes the new URL
     into the live HTML and re-publishes.
  6. Prints a summary of what changed.

MODE 2 — Refresh-all-for-client (bulk)
--------------------------------------
Use when the owner sent a new uniform photo (or any change that affects many
pages at once) and you dropped fresh PNGs into the relevant page folders.

    python refresh-cached-image.py \\
        --config /path/to/ev-electric.config.json \\
        --refresh-all-for-client \\
        --dry-run

The script:
  1. Walks every page folder under the client's Core 30 corpus.
  2. For each page, inspects the canonical PNGs at `images/` root (the
     "winning" variant that organize-image-downloads.py promoted).
  3. Compares each local PNG filename to what's recorded in the cache for
     that slot's cache key. Mismatches mark the cache entry stale.
  4. Plans the work: invalidations, uploads, propagations (for about
     portraits, every other page in the same city needs its HTML
     re-wired to the new URL).
  5. In --dry-run, prints the plan and exits.
  6. Otherwise, applies invalidations, then iterates pages and calls
     wire-page-images.run() to optimize → upload → wire HTML → republish.
  7. Reports a summary: uploads performed, pages republished, slots
     skipped.

About portraits are city-shared. If three pages in Vienna share
ev-electric-services/vienna-va/about and only page 1 has the new local
PNG, this script does the right thing: invalidates the cache entry,
uploads from page 1, then re-wires pages 2 and 3 to the new URL.

NON-DESTRUCTIVE DEFAULTS
------------------------
- The old WordPress Media Library file STAYS unless --delete-old-wp-image
  is passed. WordPress doesn't auto-clean orphaned media; treat that as a
  separate operator decision.
- --dry-run prints the plan for either mode without uploading or
  republishing anything.

OUT OF SCOPE
------------
- An interactive "list cached entries and let me pick one to refresh" UI.
  Use `python upload-image-to-wp.py --config ... --list` to inspect.
- Imagify cache invalidation. Imagify reprocesses on-upload server-side;
  if its CDN ever needs purging, do it via wp-admin.
- LiteSpeed cache purge. The republish step in wire-page-images already
  hits the page once after writing; if you want a full purge, do it
  via wp-admin or call cache-purge separately.

PRECONDITIONS
-------------
- Python 3.8+, requests, Pillow installed.
- WP_APP_PASSWORD env var set (or the per-config equivalent).
- The Core 30 corpus follows the convention
  ~/workspace/second-brain/04_projects/clients/_active/<client_slug>/
      website-archive/new/core-30/<NN>-<page-slug>/
  Override with --corpus-root if your layout differs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    sys.stderr.write("ERROR: `requests` is not installed. Run: pip install requests\n")
    sys.exit(2)


SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_ROOT_TEMPLATE = (
    "~/workspace/second-brain/04_projects/clients/_active/"
    "{client_slug}/website-archive/new/core-30"
)
SLOT_TYPES = ("hero", "about", "scene")


# ----------------------------------------------------------------------------
# Sibling-script loading (matches wire-page-images.py's pattern)
# ----------------------------------------------------------------------------


def _load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load sibling script: {filename}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_upload_mod = None
_wire_mod = None
_publish_mod = None
_optimize_mod = None


def upload_mod():
    global _upload_mod
    if _upload_mod is None:
        _upload_mod = _load_sibling("upload_image_to_wp", "upload-image-to-wp.py")
    return _upload_mod


def wire_mod():
    global _wire_mod
    if _wire_mod is None:
        _wire_mod = _load_sibling("wire_page_images", "wire-page-images.py")
    return _wire_mod


def publish_mod():
    global _publish_mod
    if _publish_mod is None:
        _publish_mod = _load_sibling("publish_core_30_page", "publish-core-30-page.py")
    return _publish_mod


def optimize_mod():
    global _optimize_mod
    if _optimize_mod is None:
        _optimize_mod = _load_sibling("optimize_image", "optimize-image.py")
    return _optimize_mod


# ----------------------------------------------------------------------------
# Cache key helpers
# ----------------------------------------------------------------------------


def slot_type_from_cache_key(cache_key: str) -> str:
    """Cache keys come in two shapes:
        <client>/<city>/about                              → "about"
        <client>/<city>/<page-slug>/<hero|about|scene>     → last segment
    """
    parts = cache_key.strip("/").split("/")
    if len(parts) == 3:
        return parts[-1]  # "about"
    if len(parts) == 4:
        return parts[-1]
    raise ValueError(f"Unrecognized cache-key shape: {cache_key!r}")


def city_from_cache_key(cache_key: str) -> str:
    parts = cache_key.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Cache key missing city segment: {cache_key!r}")
    return parts[1]


def page_slug_from_cache_key(cache_key: str) -> Optional[str]:
    """Returns the page slug for per-page keys, or None for per-city keys."""
    parts = cache_key.strip("/").split("/")
    if len(parts) == 4:
        return parts[2]
    return None


# ----------------------------------------------------------------------------
# Corpus walking
# ----------------------------------------------------------------------------


def resolve_corpus_root(client_slug: str, override: Optional[Path]) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    p = Path(DEFAULT_CORPUS_ROOT_TEMPLATE.format(client_slug=client_slug)).expanduser()
    return p.resolve()


def find_page_folders(corpus_root: Path) -> list[Path]:
    """Page folders are direct children of the corpus root that contain a
    draft-v*-WP-WRAPPED.html file. Ignore _README.md, _build-order.md, etc.
    """
    if not corpus_root.is_dir():
        return []
    folders = []
    for child in sorted(corpus_root.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
            continue
        # Has at least one WP-WRAPPED draft → looks like a page folder
        if any(child.glob("draft-v*-WP-WRAPPED.html")):
            folders.append(child)
    return folders


def latest_draft_path(page_folder: Path) -> Optional[Path]:
    """Returns the highest-versioned draft-vN-WP-WRAPPED.html in the folder."""
    candidates = list(page_folder.glob("draft-v*-WP-WRAPPED.html"))
    if not candidates:
        return None

    def version(p: Path) -> int:
        m = re.search(r"draft-v(\d+)-WP-WRAPPED", p.name)
        return int(m.group(1)) if m else 0

    candidates.sort(key=version)
    return candidates[-1]


def pages_referencing_url(page_folders: list[Path], url: str) -> list[Path]:
    """Returns the subset of page folders whose latest draft contains `url`."""
    hits: list[Path] = []
    for folder in page_folders:
        draft = latest_draft_path(folder)
        if draft is None:
            continue
        try:
            content = draft.read_text(encoding="utf-8")
        except OSError:
            continue
        if url in content:
            hits.append(folder)
    return hits


# ----------------------------------------------------------------------------
# WordPress Media Library delete
# ----------------------------------------------------------------------------


def delete_wp_media(
    media_id: int,
    base_url: str,
    username: str,
    app_password: str,
    timeout: int = 30,
) -> tuple[bool, str]:
    """DELETE /wp-json/wp/v2/media/<id>?force=true. Returns (ok, message)."""
    base = base_url.rstrip("/")
    url = f"{base}/wp-json/wp/v2/media/{media_id}?force=true"
    token = b64encode(f"{username}:{app_password}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "refresh-cached-image.py/1.0",
    }
    try:
        resp = requests.delete(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code} — {resp.text[:300]}"
    return True, f"deleted media {media_id}"


# ----------------------------------------------------------------------------
# Mode 1 — single-image refresh
# ----------------------------------------------------------------------------


def mode1_refresh_single(
    config_path: Path,
    cache_key: str,
    new_image: Optional[Path],
    delete_old_wp_image: bool,
    also_rewire_pages: bool,
    corpus_root_override: Optional[Path],
    dry_run: bool,
) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config.get("client_slug")
    if not client_slug:
        sys.stderr.write("ERROR: config missing client_slug.\n")
        return 2

    umod = upload_mod()
    cache = umod.load_image_cache(client_slug)
    entries = cache.get("entries", {})

    if cache_key not in entries:
        sys.stderr.write(f"ERROR: cache key not found: {cache_key}\n")
        sys.stderr.write("       Tip: list with `upload-image-to-wp.py --config ... --list`\n")
        return 2

    try:
        slot_type = slot_type_from_cache_key(cache_key)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    old_entry = entries[cache_key]
    old_url = old_entry.get("wp_url", "")
    old_media_id = old_entry.get("wp_media_id")

    sys.stderr.write(f"→ Cache key:   {cache_key}\n")
    sys.stderr.write(f"→ Slot type:   {slot_type}\n")
    sys.stderr.write(f"→ Old URL:     {old_url}\n")
    sys.stderr.write(f"→ Old media:   {old_media_id if old_media_id else '(unknown — was seeded entry)'}\n")
    if dry_run:
        sys.stderr.write("→ MODE:        DRY RUN — no destructive actions, no uploads.\n")

    # Find pages that reference the OLD URL (so we can rewire them after upload).
    corpus_root = resolve_corpus_root(client_slug, corpus_root_override)
    page_folders = find_page_folders(corpus_root)
    if not page_folders:
        sys.stderr.write(
            f"WARN: no page folders found under {corpus_root}.\n"
            f"      --also-rewire-pages will be a no-op.\n"
        )
    referencing = pages_referencing_url(page_folders, old_url) if old_url else []
    sys.stderr.write(
        f"→ Pages using old URL: {len(referencing)}"
        f"{' (will rewire)' if also_rewire_pages else ' (rewire not requested)'}\n"
    )
    for folder in referencing:
        sys.stderr.write(f"     - {folder.name}\n")

    # Step 1: optionally delete the old WP media
    if delete_old_wp_image:
        if old_media_id is None:
            sys.stderr.write("WARN: cannot delete WP media — old entry has no wp_media_id.\n")
        elif dry_run:
            sys.stderr.write(f"→ [dry-run] would DELETE WP media {old_media_id}\n")
        else:
            try:
                from _load_secrets import load_wp_app_password
                app_password = load_wp_app_password(config)
            except RuntimeError as e:
                sys.stderr.write(f"ERROR: {e}\n")
                return 3
            ok, msg = delete_wp_media(
                media_id=old_media_id,
                base_url=config["wp_base_url"],
                username=config["wp_username"],
                app_password=app_password,
            )
            sys.stderr.write(f"→ WP media delete: {'OK' if ok else 'FAILED'} — {msg}\n")
            if not ok:
                sys.stderr.write("       (continuing — cache invalidation still proceeds)\n")

    # Step 2: invalidate (remove cache entry)
    if dry_run:
        sys.stderr.write(f"→ [dry-run] would remove cache entry: {cache_key}\n")
    else:
        del entries[cache_key]
        umod.save_image_cache(client_slug, cache)
        sys.stderr.write(f"→ Cache entry removed: {cache_key}\n")

    # Step 3: upload new image if provided
    new_wp_url: Optional[str] = None
    if new_image is not None:
        if not new_image.is_file():
            sys.stderr.write(f"ERROR: --new-image not found: {new_image}\n")
            return 2
        new_wp_url = _upload_replacement(
            new_image=new_image,
            cache_key=cache_key,
            slot_type=slot_type,
            config=config,
            old_entry=old_entry,
            dry_run=dry_run,
        )
        if new_wp_url is None and not dry_run:
            sys.stderr.write("ERROR: upload failed; aborting before rewire.\n")
            return 1

    # Step 4: rewire affected pages
    if also_rewire_pages and referencing:
        if new_wp_url is None and not dry_run:
            sys.stderr.write(
                "WARN: --also-rewire-pages requested but no --new-image provided.\n"
                "      Affected pages will be re-wired against the cache; on a\n"
                "      cache miss with no local PNG, the wire-page-images step\n"
                "      will leave the HTML unchanged. Add --new-image to make\n"
                "      this end-to-end.\n"
            )
        _rewire_pages(
            page_folders=referencing,
            config_path=config_path,
            slot_type=slot_type,
            dry_run=dry_run,
        )

    # Summary
    print()
    print("=== summary ===")
    print(f"Cache key invalidated: {cache_key}")
    if delete_old_wp_image:
        print(f"Old WP media:          {'deletion attempted' if old_media_id else 'skipped (no id)'}")
    else:
        print(f"Old WP media:          left in place (pass --delete-old-wp-image to remove)")
    if new_image:
        print(f"New image:             {new_image.name}")
        print(f"New WP URL:            {new_wp_url or '(dry-run)'}")
    if also_rewire_pages:
        print(f"Pages re-wired:        {len(referencing)}")
    return 0


def _upload_replacement(
    new_image: Path,
    cache_key: str,
    slot_type: str,
    config: dict[str, Any],
    old_entry: dict[str, Any],
    dry_run: bool,
) -> Optional[str]:
    """Optimize + upload a replacement image, recording it under the same
    cache key the old entry occupied. Returns the new WP URL (or a dry-run
    placeholder).
    """
    umod = upload_mod()
    client_slug = config["client_slug"]

    # Compose alt text — reuse the old entry's alt text if present, else
    # synthesize a generic one. The wire-page-images orchestrator will
    # override alt text on its next run using its proper composer.
    alt_text = old_entry.get("alt_text") or f"{client_slug} {slot_type} image"

    # Optimize to WebP
    webp_path = new_image.with_suffix(".webp")
    if dry_run:
        sys.stderr.write(f"→ [dry-run] would optimize {new_image.name} → {webp_path.name}\n")
        sys.stderr.write(f"→ [dry-run] would upload {webp_path.name} with alt: {alt_text!r}\n")
        return f"https://example.com/wp-content/uploads/DRY-RUN/{webp_path.name}"

    omod = optimize_mod()
    max_kb = omod._SIZE_BUDGETS_KB.get(slot_type, 300)
    try:
        bytes_in, bytes_out, quality = omod.optimize_to_webp(
            input_path=new_image,
            output_path=webp_path,
            max_size_kb=max_kb,
        )
        sys.stderr.write(
            f"→ Optimized:   {new_image.name} ({bytes_in/1024:.1f}KB) → "
            f"{webp_path.name} ({bytes_out/1024:.1f}KB @ q={quality})\n"
        )
    except Exception as e:
        sys.stderr.write(f"ERROR optimizing: {e}\n")
        return None

    # Upload
    try:
        from _load_secrets import load_wp_app_password
        app_password = load_wp_app_password(config)
    except RuntimeError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return None

    try:
        media = umod.upload_to_wp_media_library(
            image_path=webp_path,
            base_url=config["wp_base_url"],
            username=config["wp_username"],
            app_password=app_password,
            alt_text=alt_text,
        )
    except Exception as e:
        sys.stderr.write(f"ERROR uploading: {e}\n")
        return None

    new_url = media.get("source_url") or media.get("guid", {}).get("rendered")
    new_media_id = media.get("id")
    if not new_url or not new_media_id:
        sys.stderr.write(f"ERROR: WP returned bad media object: {media}\n")
        return None

    # Record new cache entry
    cache = umod.load_image_cache(client_slug)
    cache["entries"][cache_key] = {
        "wp_url": new_url,
        "wp_media_id": new_media_id,
        "filename": webp_path.name,
        "alt_text": alt_text,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_size_bytes": webp_path.stat().st_size,
        "scope": umod.cache_scope(cache_key),
    }
    umod.save_image_cache(client_slug, cache)
    sys.stderr.write(f"→ Uploaded:    {new_url} (id {new_media_id})\n")
    sys.stderr.write(f"→ Cache entry recorded: {cache_key}\n")
    return new_url


def _rewire_pages(
    page_folders: list[Path],
    config_path: Path,
    slot_type: str,
    dry_run: bool,
) -> None:
    wpm = wire_mod()
    for folder in page_folders:
        sys.stderr.write(f"\n--- rewire: {folder.name} (--only-types {slot_type}) ---\n")
        if dry_run:
            sys.stderr.write("  [dry-run] would call wire-page-images.run() against this folder\n")
            continue
        rc = wpm.run(
            page_folder=folder,
            config_path=config_path,
            only_types=[slot_type],
            skip_publish=False,
            dry_run=False,
        )
        if rc != 0:
            sys.stderr.write(f"  WARN: wire-page-images returned non-zero ({rc}) for {folder.name}\n")


# ----------------------------------------------------------------------------
# Mode 2 — refresh-all-for-client
# ----------------------------------------------------------------------------


def _read_page_metadata(page_folder: Path) -> dict[str, str]:
    """Wraps wire-page-images.read_metadata() with a safe fallback."""
    try:
        return wire_mod().read_metadata(page_folder)
    except Exception:
        # Fall back to folder-name heuristic
        stem = re.sub(r"^\d+-", "", page_folder.name)
        return {"page_slug": stem, "city": "", "service": ""}


def _slot_status_for_page(
    page_folder: Path,
    client_slug: str,
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    """Returns one dict per slot type with:
        slot, local_png, cache_key, cached_filename, action
    where action ∈ {"upload-new", "upload-fresh", "up-to-date", "no-work"}.
    """
    wpm = wire_mod()
    metadata = _read_page_metadata(page_folder)
    city = metadata.get("city", "")
    page_slug = metadata.get("page_slug", "")
    entries = cache.get("entries", {})

    results: list[dict[str, Any]] = []
    for slot in SLOT_TYPES:
        local_png = wpm.find_canonical_image(page_folder, slot)
        cache_key = wpm.cache_key_for(slot, client_slug, city, page_slug)
        cached = entries.get(cache_key)
        cached_filename = (cached or {}).get("filename")

        if local_png is None and cached is None:
            action = "no-work"
        elif local_png is None and cached is not None:
            action = "up-to-date"  # cache supplies the URL, no local replacement queued
        elif local_png is not None and cached is None:
            action = "upload-fresh"
        else:
            # both present — compare filenames (stem-only so .png vs .webp doesn't trip us)
            local_stem = local_png.stem
            cached_stem = Path(cached_filename or "").stem
            if local_stem == cached_stem:
                action = "up-to-date"
            else:
                action = "upload-new"

        results.append({
            "slot": slot,
            "local_png": local_png,
            "cache_key": cache_key,
            "cached_filename": cached_filename,
            "cached_url": (cached or {}).get("wp_url"),
            "cached_media_id": (cached or {}).get("wp_media_id"),
            "action": action,
            "city": city,
            "page_slug": page_slug,
        })
    return results


def mode2_refresh_all(
    config_path: Path,
    corpus_root_override: Optional[Path],
    delete_old_wp_image: bool,
    dry_run: bool,
) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config.get("client_slug")
    if not client_slug:
        sys.stderr.write("ERROR: config missing client_slug.\n")
        return 2

    corpus_root = resolve_corpus_root(client_slug, corpus_root_override)
    if not corpus_root.is_dir():
        sys.stderr.write(f"ERROR: corpus root not found: {corpus_root}\n")
        sys.stderr.write("       Pass --corpus-root if your layout differs.\n")
        return 2

    umod = upload_mod()
    cache = umod.load_image_cache(client_slug)

    page_folders = find_page_folders(corpus_root)
    if not page_folders:
        sys.stderr.write(f"WARN: no page folders found under {corpus_root}\n")
        return 0

    sys.stderr.write(f"→ Corpus root: {corpus_root}\n")
    sys.stderr.write(f"→ Pages found: {len(page_folders)}\n")
    if dry_run:
        sys.stderr.write("→ MODE:        DRY RUN — plan only, no uploads, no republishes.\n")

    # ---- Discovery pass ---------------------------------------------------
    per_page_status: dict[Path, list[dict[str, Any]]] = {}
    for folder in page_folders:
        per_page_status[folder] = _slot_status_for_page(folder, client_slug, cache)

    # Cache invalidations: every slot whose action is "upload-new". For per-city
    # about slots, multiple pages may flag the same cache key — dedupe.
    invalidations: dict[str, dict[str, Any]] = {}  # cache_key → entry
    for folder, slots in per_page_status.items():
        for s in slots:
            if s["action"] == "upload-new" and s["cache_key"] not in invalidations:
                invalidations[s["cache_key"]] = {
                    "cache_key": s["cache_key"],
                    "old_url": s["cached_url"],
                    "old_media_id": s["cached_media_id"],
                    "slot": s["slot"],
                    "triggered_by_page": folder.name,
                }

    # Cities whose about portrait is being refreshed. Every page in such a city
    # needs an about-rewire pass (HTML still points at the OLD url).
    about_cities_changing: set[str] = set()
    for key in invalidations:
        if slot_type_from_cache_key(key) == "about":
            about_cities_changing.add(city_from_cache_key(key))

    # Build the per-page work plan:
    # A page needs processing if:
    #   - any of its slots is upload-new or upload-fresh
    #   - OR it's in an about-changing city (even if it has no local about PNG,
    #     its HTML needs the new URL pushed in via cache hit)
    plan: list[tuple[Path, list[str], str]] = []  # (page, slot_types_to_process, reason)
    for folder, slots in per_page_status.items():
        slots_to_process: list[str] = []
        reasons: list[str] = []
        for s in slots:
            if s["action"] in ("upload-new", "upload-fresh"):
                slots_to_process.append(s["slot"])
                reasons.append(f"{s['slot']}={s['action']}")
        # About propagation: page is in a changing city and we haven't already
        # queued about for it (because it has no local about PNG).
        page_city = next(
            (s["city"] for s in slots if s["city"]),
            ""
        )
        if page_city in about_cities_changing and "about" not in slots_to_process:
            slots_to_process.append("about")
            reasons.append("about=rewire-to-new-city-url")
        if slots_to_process:
            plan.append((folder, slots_to_process, ", ".join(reasons)))

    # Ordering: pages that perform the about UPLOAD must run before pages that
    # merely REWIRE to the new about URL. The uploading page is the one whose
    # about slot is in `invalidations`. Sort accordingly.
    about_uploader_pages = {
        v["triggered_by_page"]
        for v in invalidations.values()
        if v["slot"] == "about"
    }
    plan.sort(key=lambda t: (0 if t[0].name in about_uploader_pages else 1, t[0].name))

    # ---- Print the plan ---------------------------------------------------
    print()
    print("=== plan ===")
    print(f"Cache invalidations: {len(invalidations)}")
    for inv in invalidations.values():
        print(f"  - {inv['cache_key']}")
        print(f"      old URL:    {inv['old_url']}")
        print(f"      triggered:  {inv['triggered_by_page']}")
    if about_cities_changing:
        print(f"About-portrait cities changing: {sorted(about_cities_changing)}")
    print(f"Pages to process: {len(plan)}")
    for folder, slot_list, reason in plan:
        print(f"  - {folder.name}: {','.join(slot_list)}  ({reason})")
    if not plan:
        print("  (no work — every cached image matches the local PNGs on disk)")

    if dry_run:
        print()
        print("Dry-run complete. Re-run without --dry-run to apply.")
        return 0

    if not plan:
        return 0

    # ---- Execution pass ---------------------------------------------------

    # Optional: delete old WP media before invalidating
    if delete_old_wp_image and invalidations:
        try:
            from _load_secrets import load_wp_app_password
            app_password = load_wp_app_password(config)
        except RuntimeError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 3
        for inv in invalidations.values():
            mid = inv["old_media_id"]
            if mid is None:
                sys.stderr.write(
                    f"WARN: cannot delete old WP media for {inv['cache_key']} — "
                    f"no wp_media_id (was a seeded entry).\n"
                )
                continue
            ok, msg = delete_wp_media(
                media_id=mid,
                base_url=config["wp_base_url"],
                username=config["wp_username"],
                app_password=app_password,
            )
            sys.stderr.write(f"→ WP media delete {mid}: {'OK' if ok else 'FAILED'} — {msg}\n")

    # Apply cache invalidations
    cache = umod.load_image_cache(client_slug)  # reload in case anything changed
    for key in invalidations:
        cache["entries"].pop(key, None)
    umod.save_image_cache(client_slug, cache)
    sys.stderr.write(f"→ Invalidated {len(invalidations)} cache entries.\n")

    # Iterate pages, invoke wire-page-images.run()
    wpm = wire_mod()
    uploads_performed = 0
    pages_republished = 0
    pages_failed = 0

    for folder, slot_list, reason in plan:
        sys.stderr.write(f"\n--- page: {folder.name} ---\n")
        sys.stderr.write(f"  slots: {slot_list}  ({reason})\n")
        try:
            rc = wpm.run(
                page_folder=folder,
                config_path=config_path,
                only_types=slot_list,
                skip_publish=False,
                dry_run=False,
            )
        except Exception as e:
            sys.stderr.write(f"  ERROR: wire-page-images crashed for {folder.name}: {e}\n")
            pages_failed += 1
            continue

        if rc != 0:
            sys.stderr.write(f"  WARN: wire-page-images returned {rc} for {folder.name}\n")
            pages_failed += 1
            continue

        # Heuristic accounting (wire-page-images doesn't return structured counts)
        if any(r in reason for r in ("upload-new", "upload-fresh")):
            uploads_performed += 1
        pages_republished += 1

    # ---- Summary ----------------------------------------------------------
    print()
    print("=== summary ===")
    print(f"Cache invalidations applied: {len(invalidations)}")
    print(f"Pages processed:             {len(plan)}")
    print(f"Pages with uploads:          {uploads_performed}")
    print(f"Pages republished:           {pages_republished}")
    if pages_failed:
        print(f"Pages with errors:           {pages_failed}  (see log above)")
    return 0 if pages_failed == 0 else 1


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Force-refresh image cache entries so a regenerated image "
            "propagates everywhere it's used."
        ),
    )
    p.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Client config JSON (same one used by publish-core-30-page.py).",
    )

    # Mode selection
    p.add_argument(
        "--cache-key",
        type=str,
        default=None,
        help=(
            "Mode 1: the cache key to refresh, e.g. "
            "'ev-electric-services/vienna-va/about'."
        ),
    )
    p.add_argument(
        "--refresh-all-for-client",
        action="store_true",
        help=(
            "Mode 2: walk the client's Core 30 corpus, detect new/changed "
            "local PNGs, and propagate uploads + HTML rewrites + republishes."
        ),
    )

    # Mode 1 options
    p.add_argument(
        "--new-image",
        type=Path,
        default=None,
        help="Mode 1: path to a replacement PNG. Optimized to WebP before upload.",
    )
    p.add_argument(
        "--also-rewire-pages",
        action="store_true",
        help=(
            "Mode 1: after the new image is uploaded, find every Core 30 "
            "draft that referenced the old WordPress URL and re-wire them "
            "(runs wire-page-images.py --only-types <slot> per affected page)."
        ),
    )

    # Mode 2 options
    p.add_argument(
        "--from-images-folders",
        action="store_true",
        help=(
            "Mode 2: scan each page's images/ root for new PNGs. "
            "Default-on (this flag is accepted for forward compatibility)."
        ),
    )

    # Cross-mode options
    p.add_argument(
        "--delete-old-wp-image",
        action="store_true",
        help=(
            "Also DELETE the old image from the WordPress Media Library. "
            "Default off — safer to leave the orphaned upload and clean it "
            "manually via wp-admin if you want to."
        ),
    )
    p.add_argument(
        "--corpus-root",
        type=Path,
        default=None,
        help=(
            "Override the Core 30 corpus root. Default: "
            "~/workspace/second-brain/04_projects/clients/_active/"
            "<client_slug>/website-archive/new/core-30"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without uploading, invalidating, or republishing.",
    )

    args = p.parse_args()

    if not args.config.is_file():
        sys.stderr.write(f"ERROR: config not found: {args.config}\n")
        return 2

    # Mode dispatch
    if args.refresh_all_for_client and args.cache_key:
        sys.stderr.write(
            "ERROR: --refresh-all-for-client and --cache-key are mutually exclusive.\n"
            "       Pick one mode.\n"
        )
        return 2

    if args.refresh_all_for_client:
        return mode2_refresh_all(
            config_path=args.config,
            corpus_root_override=args.corpus_root,
            delete_old_wp_image=args.delete_old_wp_image,
            dry_run=args.dry_run,
        )

    if args.cache_key:
        return mode1_refresh_single(
            config_path=args.config,
            cache_key=args.cache_key,
            new_image=args.new_image,
            delete_old_wp_image=args.delete_old_wp_image,
            also_rewire_pages=args.also_rewire_pages,
            corpus_root_override=args.corpus_root,
            dry_run=args.dry_run,
        )

    sys.stderr.write(
        "ERROR: pick a mode.\n"
        "       Mode 1: --cache-key <key> [--new-image <path>] [--also-rewire-pages]\n"
        "       Mode 2: --refresh-all-for-client\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
