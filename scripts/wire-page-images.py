#!/usr/bin/env python3
"""
wire-page-images.py — the imagery pipeline orchestrator.

Plain English: this is the one command that takes a Core 30 page from
"placeholder images" to "real images live on WordPress." Given a page
folder that has canonical PNG files at `images/` root (put there by
`organize-image-downloads.py`), this script:

1. Looks at each image slot in the page (hero, about portrait, optional
   contextual scene).
2. Checks the per-client image cache — has this image already been uploaded
   to WordPress? If yes, reuse the URL without re-uploading.
3. For images that need uploading: shrinks the PNG to a smaller WebP file
   (using optimize-image.py), uploads it to the WordPress Media Library
   (using upload-image-to-wp.py), and records the URL in the cache.
4. Rewrites the page HTML to point at the real WordPress URLs (using
   wire-images-into-html.py logic).
5. Saves the result as a new draft-v(N+1)-WP-WRAPPED.html.
6. Optionally re-publishes the updated HTML to the live WordPress page
   (using publish-core-30-page.py).

About portraits are reused across pages in the same city. The cache key
for the about slot is `<client>/<city>/about` (city-scoped). Once page 1
uploaded the Vienna portrait, pages 2–30 for Vienna look it up from the
cache and never re-upload.

Heroes and contextual scenes are unique per page. Their cache key is
`<client>/<city>/<page-slug>/<type>`.

USAGE
-----
Process all slots on one page and republish:

    export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
    python wire-page-images.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --config       /path/to/ev-electric.config.json

Process slots but skip republish (you want to review the HTML first):

    python wire-page-images.py \\
        --page-folder ... --config ... --skip-publish

Only process specific slots:

    python wire-page-images.py \\
        --page-folder ... --config ... --only-types hero

Dry run — show what would happen without changing anything:

    python wire-page-images.py \\
        --page-folder ... --config ... --dry-run

INPUTS
------
- A Core 30 page folder containing draft-v*-WP-WRAPPED.html and
  draft-v1.md (markdown source used for metadata + alt-text composition).
- A client config JSON (the same one publish-core-30-page.py uses).
- For new images: a PNG file at `<page-folder>/images/` root, named to
  identify its slot type (must contain "hero", "about", or "scene" in the
  filename). Place these via `organize-image-downloads.py`.

OUTPUTS
-------
- A new draft-v(N+1)-WP-WRAPPED.html with all wired image URLs.
- New WebP files alongside the source PNGs (one per uploaded image).
- Updated per-client image cache.
- Updated _VERSION-LOG.md.
- (Unless --skip-publish) The page re-published to the live WordPress site
  with the new HTML.

PRECONDITIONS
-------------
- Python 3.8+
- Pillow installed: pip install Pillow --break-system-packages
- requests installed: pip install requests
- The page folder must have been produced by scaffold-core-30-page.py
  (or follow its conventions: draft-v*-WP-WRAPPED.html + draft-v1.md +
  images/ subfolder).

RELATED SCRIPTS
---------------
- organize-image-downloads.py — runs upstream, places PNGs at images/ root.
- optimize-image.py — called by this orchestrator for PNG → WebP.
- upload-image-to-wp.py — called by this orchestrator for Media Library upload.
- wire-images-into-html.py — provides the HTML mutation logic this orchestrator imports.
- publish-core-30-page.py — called downstream to re-publish unless --skip-publish.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent


# ----------------------------------------------------------------------------
# Module loading — pull helpers from sibling scripts
# ----------------------------------------------------------------------------


def _load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load sibling script: {filename}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass introspection works
    # (dataclasses.py looks up cls.__module__ via sys.modules).
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Lazy-load so dry-run / --list paths don't pay the import cost
_optimize_mod = None
_upload_mod = None
_wire_html_mod = None
_publish_mod = None


def optimize_mod():
    global _optimize_mod
    if _optimize_mod is None:
        _optimize_mod = _load_sibling("optimize_image", "optimize-image.py")
    return _optimize_mod


def upload_mod():
    global _upload_mod
    if _upload_mod is None:
        _upload_mod = _load_sibling("upload_image_to_wp", "upload-image-to-wp.py")
    return _upload_mod


def wire_html_mod():
    global _wire_html_mod
    if _wire_html_mod is None:
        _wire_html_mod = _load_sibling("wire_images_into_html", "wire-images-into-html.py")
    return _wire_html_mod


def publish_mod():
    global _publish_mod
    if _publish_mod is None:
        _publish_mod = _load_sibling("publish_core_30_page", "publish-core-30-page.py")
    return _publish_mod


# ----------------------------------------------------------------------------
# Page metadata + alt text composition
# ----------------------------------------------------------------------------


def read_metadata(page_folder: Path) -> dict[str, str]:
    """Pull page slug, city slug, service name, owner name, etc. from the
    draft-v1.md frontmatter (via the publish script's existing parser).

    Returns a dict with at least: page_slug, city (with state suffix when
    derivable), service. Falls back to deriving from folder name where needed.
    """
    pmod = publish_mod()
    md_path = pmod.find_v1_markdown(page_folder)
    fm = pmod.parse_frontmatter(md_path)

    page_slug = fm.get("page-slug") or re.sub(r"^\d+-", "", page_folder.name)
    city = fm.get("city", "")  # e.g., "vienna" or "vienna-va"
    service = fm.get("service", "")

    # If frontmatter city lacks state, try to derive city-with-state from the
    # folder name (e.g., 02-panel-upgrade-vienna-va → vienna-va).
    folder_stem = re.sub(r"^\d+-", "", page_folder.name)
    folder_tokens = folder_stem.split("-")
    if len(folder_tokens) >= 2 and len(folder_tokens[-1]) == 2:
        # Last token is a 2-char state abbrev. Walk back to find the city
        # start — heuristic: city is whatever follows the service slug.
        state = folder_tokens[-1]
        if city and not city.endswith(f"-{state}"):
            city = f"{city}-{state}"

    return {
        "page_slug": page_slug,
        "city": city,
        "service": service,
    }


def compose_alt_text(
    slot_type: str,
    client_name: str,
    owner_name: str,
    owner_title: str,
    service_name: str,
    city_display: str,
) -> str:
    """Generate SEO alt text per the imagery SOP Step 8 conventions."""
    if slot_type == "hero":
        return (
            f"{owner_name}, {owner_title}, performing {service_name} in {city_display}"
        )
    if slot_type == "about":
        return (
            f"{owner_name}, {owner_title} at {client_name} serving {city_display}"
        )
    if slot_type == "scene":
        return (
            f"Residential {service_name} work performed in {city_display}"
        )
    return f"{client_name} — {service_name} — {city_display}"


def city_display(city_slug: str) -> str:
    """vienna-va → Vienna, VA"""
    if not city_slug:
        return ""
    parts = city_slug.split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2:
        state = parts[-1].upper()
        city = " ".join(w.capitalize() for w in parts[:-1])
        return f"{city}, {state}"
    return " ".join(w.capitalize() for w in parts)


# ----------------------------------------------------------------------------
# Slot discovery
# ----------------------------------------------------------------------------


def find_canonical_image(page_folder: Path, slot_type: str) -> Optional[Path]:
    """Find a PNG at images/ root whose name contains the slot type token.

    e.g., slot_type="hero" matches `vienna-panel-upgrade-hero-v1-variant-4.png`.
    Returns the highest-version-suffix file if multiple match.
    """
    images_dir = page_folder / "images"
    if not images_dir.is_dir():
        return None
    candidates = [
        p for p in images_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".png", ".jpg", ".jpeg")
        and re.search(rf"\b{re.escape(slot_type)}\b", p.stem, re.IGNORECASE)
    ]
    if not candidates:
        return None
    # Sort by version suffix (v1, v2, ...) and variant if present; pick highest
    def sort_key(p: Path) -> tuple[int, int, str]:
        v_m = re.search(r"-v(\d+)", p.stem)
        var_m = re.search(r"-variant-(\d+)", p.stem)
        return (
            int(v_m.group(1)) if v_m else 0,
            int(var_m.group(1)) if var_m else 0,
            p.name,
        )
    candidates.sort(key=sort_key)
    return candidates[-1]


def cache_key_for(slot_type: str, client_slug: str, city: str, page_slug: str) -> str:
    """Per-city for about; per-page for hero/scene. Matches the C1 design."""
    if slot_type == "about":
        return f"{client_slug}/{city}/about"
    return f"{client_slug}/{city}/{page_slug}/{slot_type}"


def seed_about_from_html(
    html: str,
    page_folder: Path,
    metadata: dict[str, str],
    config: dict[str, Any],
) -> Optional[str]:
    """One-time migration helper: if the about slot in the HTML already points
    at a real WordPress URL (because page 1 manually uploaded the portrait
    before this pipeline existed), seed the per-city cache entry so
    subsequent pages for the same city skip re-uploading.

    Only seeds for the about slot (city-shared by design). Hero and scene are
    per-page and don't benefit from this.

    Returns the seeded URL if seeding occurred, else None.
    """
    wmod = wire_html_mod()
    slot_class = wmod._SLOT_CLASS["about"]
    bounds = wmod.find_slot_bounds(html, slot_class)
    if bounds is None:
        return None
    start, end = bounds
    block = html[start:end]
    img_match = wmod.find_img_in_block(block)
    if img_match is None:
        return None

    src_m = re.search(r'src\s*=\s*["\']([^"\']+)["\']', img_match.group(0))
    if not src_m:
        return None
    src = src_m.group(1)

    # Only seed if the URL is on the client's WP site (not a placeholder).
    wp_base = config.get("wp_base_url", "").rstrip("/")
    if not wp_base or not src.startswith(wp_base):
        return None

    # Don't seed if there's a local PNG for the about slot — that means the
    # operator wants to upload a fresh portrait, not reuse.
    local_about = find_canonical_image(page_folder, "about")
    if local_about is not None:
        return None

    client_slug = config["client_slug"]
    city = metadata.get("city", "")
    page_slug = metadata.get("page_slug", "")
    cache_key = cache_key_for("about", client_slug, city, page_slug)

    umod = upload_mod()
    cache = umod.load_image_cache(client_slug)
    if cache_key in cache["entries"]:
        return None  # already seeded

    # Pull alt text from the existing tag too
    alt_m = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', img_match.group(0))
    alt_text = alt_m.group(1) if alt_m else ""

    cache["entries"][cache_key] = {
        "wp_url": src,
        "wp_media_id": None,  # unknown — wasn't tracked at the time
        "filename": src.rsplit("/", 1)[-1],
        "alt_text": alt_text,
        "uploaded_at": None,
        "file_size_bytes": None,
        "scope": "per-city-shared",
        "seeded": True,
        "seeded_from": str(page_folder.name),
    }
    umod.save_image_cache(client_slug, cache)
    return src


# ----------------------------------------------------------------------------
# JSON-LD mutation (PF-02)
# ----------------------------------------------------------------------------


def _update_jsonld_image(html: str, hero_url: str) -> tuple[str, bool]:
    """Replace @graph[0].image in the page's JSON-LD block with *hero_url*.

    Returns (possibly-modified html, True if a change was made).
    Handles the block as a regex-bounded JSON round-trip so we don't disturb
    surrounding HTML or whitespace outside the <script> tag.
    """
    pattern = re.compile(
        r'(<script\s+type\s*=\s*["\']application/ld\+json["\'][^>]*>)'
        r'(.*?)'
        r'(</script>)',
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(html)
    if not match:
        return html, False

    try:
        ld = json.loads(match.group(2))
    except json.JSONDecodeError:
        sys.stderr.write("  WARN: could not parse JSON-LD block; skipping image update.\n")
        return html, False

    # Walk @graph for the LocalBusiness entity (always first, but be safe).
    graph = ld.get("@graph", [ld] if "@type" in ld else [])
    updated = False
    for entity in graph:
        if entity.get("@type") in ("LocalBusiness", "ElectricalContractor"):
            old_img = entity.get("image", "")
            if old_img != hero_url:
                entity["image"] = hero_url
                updated = True
            break

    if not updated:
        return html, False

    new_json = json.dumps(ld, ensure_ascii=False, indent=2)
    new_block = match.group(1) + "\n" + new_json + "\n" + match.group(3)
    return html[:match.start()] + new_block + html[match.end():], True


# ----------------------------------------------------------------------------
# Per-slot processing
# ----------------------------------------------------------------------------


def process_slot(
    slot_type: str,
    page_folder: Path,
    metadata: dict[str, str],
    config: dict[str, Any],
    dry_run: bool,
) -> Optional[tuple[str, str, str]]:
    """Resolve the WP URL + alt text for one slot.

    Returns (wp_url, alt_text, action_summary) on success, or None if the
    slot should be skipped (no image found, no cache entry).

    Cache hit:  no upload performed; existing URL returned.
    Cache miss + local image present: optimize → upload → record → return.
    Cache miss + no local image: returns None (skip).
    """
    client_slug = config["client_slug"]
    client_name = config.get("_client_name", client_slug)
    owner_name = config.get("_owner_name", "the owner")
    owner_title = config.get("_owner_title", "")
    service_name = config.get("_service_name") or metadata.get("service", "service work")
    city_disp = city_display(metadata.get("city", ""))

    cache_key = cache_key_for(
        slot_type=slot_type,
        client_slug=client_slug,
        city=metadata.get("city", ""),
        page_slug=metadata.get("page_slug", ""),
    )
    alt_text = compose_alt_text(
        slot_type=slot_type,
        client_name=client_name,
        owner_name=owner_name,
        owner_title=owner_title,
        service_name=service_name,
        city_display=city_disp,
    )

    umod = upload_mod()
    cache = umod.load_image_cache(client_slug)
    if cache_key in cache["entries"]:
        entry = cache["entries"][cache_key]
        return entry["wp_url"], alt_text, f"cache hit ({entry['wp_url']})"

    local_png = find_canonical_image(page_folder, slot_type)
    if not local_png:
        return None  # skip — no image to process

    # Optimize PNG → WebP
    webp_path = local_png.with_suffix(".webp")
    if not dry_run:
        omod = optimize_mod()
        bytes_in, bytes_out, quality = omod.optimize_to_webp(
            input_path=local_png,
            output_path=webp_path,
            max_size_kb=omod._SIZE_BUDGETS_KB[slot_type],
        )
        size_summary = (
            f"{bytes_in/1024:.1f}KB → {bytes_out/1024:.1f}KB @ q={quality}"
        )
    else:
        size_summary = "(dry-run: skip optimize)"

    # Upload via the upload script's helper
    if dry_run:
        wp_url = (
            f"https://example.com/wp-content/uploads/DRY-RUN/{webp_path.name}"
        )
        upload_summary = "(dry-run: skip upload)"
    else:
        from _load_secrets import load_wp_app_password
        app_password = load_wp_app_password(config)
        media = umod.upload_to_wp_media_library(
            image_path=webp_path,
            base_url=config["wp_base_url"],
            username=config["wp_username"],
            app_password=app_password,
            alt_text=alt_text,
        )
        wp_url = media.get("source_url") or media.get("guid", {}).get("rendered")
        wp_media_id = media.get("id")
        if not wp_url or not wp_media_id:
            raise RuntimeError(f"WP returned bad media object: {media}")

        cache["entries"][cache_key] = {
            "wp_url": wp_url,
            "wp_media_id": wp_media_id,
            "filename": webp_path.name,
            "alt_text": alt_text,
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "file_size_bytes": webp_path.stat().st_size,
            "scope": umod.cache_scope(cache_key),
        }
        umod.save_image_cache(client_slug, cache)
        upload_summary = f"uploaded (media id {wp_media_id})"

    summary = f"{local_png.name} → {size_summary}; {upload_summary}"
    return wp_url, alt_text, summary


# ----------------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------------


def run(
    page_folder: Path,
    config_path: Path,
    only_types: Optional[list[str]],
    skip_publish: bool,
    dry_run: bool,
    bypass_quality_gate: bool = False,
) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))

    # Enrich config with client-data fields (used for alt text). The client
    # data file lives at scripts/data/client-<slug>.json and is the source of
    # truth for owner_name, owner_title, client display name.
    client_slug = config.get("client_slug")
    if not client_slug:
        sys.stderr.write("ERROR: config missing client_slug.\n")
        return 2

    client_data_path = SCRIPTS_DIR / "data" / f"client-{client_slug}.json"
    if client_data_path.is_file():
        client_data = json.loads(client_data_path.read_text(encoding="utf-8"))
        config["_client_name"] = client_data.get("name", client_slug)
        config["_owner_name"] = client_data.get("owner_name", "")
        config["_owner_title"] = client_data.get("owner_title", "")

    metadata = read_metadata(page_folder)

    # Try to enrich with service-name from the service data file too;
    # otherwise fall back to titlecasing the slug (panel-upgrade → "panel upgrade").
    service_slug = metadata.get("service")
    if service_slug:
        service_data_path = SCRIPTS_DIR / "data" / "services" / f"{service_slug}.json"
        if service_data_path.is_file():
            svc = json.loads(service_data_path.read_text(encoding="utf-8"))
            config["_service_name"] = svc.get("lowercase_phrase") or svc.get("name", service_slug)
        else:
            config["_service_name"] = service_slug.replace("-", " ")

    sys.stderr.write(f"→ Page folder: {page_folder}\n")
    sys.stderr.write(f"→ Page slug:   {metadata.get('page_slug', '?')}\n")
    sys.stderr.write(f"→ City:        {metadata.get('city', '?')}\n")
    sys.stderr.write(f"→ Service:     {metadata.get('service', '?')}\n")
    if dry_run:
        sys.stderr.write("→ MODE:        DRY RUN — no files written, no uploads.\n")

    wmod = wire_html_mod()
    latest_path, latest_version = wmod.find_latest_draft(page_folder)
    html = latest_path.read_text(encoding="utf-8")
    sys.stderr.write(f"→ Input HTML:  {latest_path.name}\n")

    # Guard 2 — Template-parity assertion (T2-7, Issue #27).
    # Before wiring any <img> tags, verify the target page carries the
    # CURRENT template's image-integration CSS. The older batch (pages
    # 01-05) was generated by a template that lacked these styles, so
    # wiring images into those pages dropped <img> into stale placeholder
    # divs with dashed borders and padding — images rendered inside the
    # placeholder chrome instead of filling edge-to-edge.
    #
    # Required CSS markers (present in the 06-12 template, absent in 01-05):
    #   .evp-corepage .evp-hero-image img   — hero fill + border-radius
    #   .evp-corepage .evp-about-image img  — about portrait fill
    _TEMPLATE_PARITY_MARKERS = [
        ".evp-corepage .evp-hero-image img",
        ".evp-corepage .evp-about-image img",
    ]
    missing_markers = [m for m in _TEMPLATE_PARITY_MARKERS if m not in html]
    if missing_markers:
        sys.stderr.write(
            f"\n→ Template parity: ✗ REFUSED — page is on a stale template.\n"
            f"   Missing image-integration CSS:\n"
        )
        for m in missing_markers:
            sys.stderr.write(f"     - {m}\n")
        sys.stderr.write(
            f"   Re-scaffold this page to the current template before wiring "
            f"images. Never drop an <img> into a stale-template placeholder div.\n"
            f"   See: lesson-core-30-publishing-verification-template-parity-2026-06-04 Issue #27\n"
        )
        return 1

    # One-time migration: seed about-slot cache from existing HTML URLs so
    # this page (and future pages in the same city) reuse an already-uploaded
    # Vienna/Fairfax/etc. portrait without re-uploading. No-op for pages
    # whose about slot is still a placeholder or already cached. Safe in
    # dry-run too — only adds facts already visible in the HTML.
    seeded = seed_about_from_html(html, page_folder, metadata, config)
    if seeded:
        sys.stderr.write(f"→ Cache seed:  about → {seeded}\n")

    slot_types = only_types or ["hero", "about", "scene"]
    changes: list[str] = []
    hero_wp_url: Optional[str] = None  # track for JSON-LD update (PF-02)
    for slot in slot_types:
        sys.stderr.write(f"\n--- slot: {slot} ---\n")
        try:
            result = process_slot(slot, page_folder, metadata, config, dry_run)
        except Exception as e:
            sys.stderr.write(f"ERROR processing {slot}: {e}\n")
            return 1

        if result is None:
            sys.stderr.write(f"  (no image found and no cache entry — skipped)\n")
            continue

        wp_url, alt_text, action_summary = result
        sys.stderr.write(f"  Action: {action_summary}\n")
        sys.stderr.write(f"  WP URL: {wp_url}\n")
        sys.stderr.write(f"  Alt:    {alt_text}\n")

        # Look up the local PNG path so wire_slot can detect orientation and
        # write the correct Pattern A/B style + intrinsic dims. Cheap re-lookup
        # — find_canonical_image() just globs the page folder. Returns None for
        # cache-hit slots where no local file exists; in that case wire_slot
        # falls back to its preserve-existing-attrs behavior.
        local_png_for_wire = find_canonical_image(page_folder, slot)

        try:
            new_html, change_summary = wmod.wire_slot(
                html, slot, wp_url, alt_text,
                local_path=local_png_for_wire,
            )
        except ValueError as e:
            sys.stderr.write(f"  WARN: HTML wire failed for {slot}: {e}\n")
            continue

        if new_html != html:
            html = new_html
            changes.append(change_summary)
            if slot == "hero":
                hero_wp_url = wp_url
        else:
            sys.stderr.write(f"  (HTML already up to date for this slot)\n")

    # PF-02: Update JSON-LD @graph[0].image to match the real hero URL.
    # The scaffold writes brand_image_url into the LocalBusiness schema; after
    # wiring the hero image we patch it to the actual hero WebP URL so Google's
    # Rich Results Test shows the real page image.
    if hero_wp_url:
        html, jsonld_changed = _update_jsonld_image(html, hero_wp_url)
        if jsonld_changed:
            changes.append(f"JSON-LD image → {hero_wp_url}")
            sys.stderr.write(f"\n→ JSON-LD:     image updated to {hero_wp_url}\n")

    if not changes:
        sys.stderr.write("\n→ No HTML changes needed. Done.\n")
        return 0

    if dry_run:
        sys.stderr.write("\n→ DRY RUN — would write new draft with these changes:\n")
        for c in changes:
            sys.stderr.write(f"    - {c}\n")
        return 0

    new_version = latest_version + 1
    new_path = page_folder / f"draft-v{new_version}-WP-WRAPPED.html"
    new_path.write_text(html, encoding="utf-8")
    summary_joined = "; ".join(changes)
    wmod.append_version_log_row(page_folder, new_version, summary_joined)
    sys.stderr.write(f"\n→ Wrote new draft: {new_path.name}\n")

    if skip_publish:
        sys.stderr.write("→ Skipping publish (--skip-publish). HTML draft is ready.\n")
        print(new_path)
        return 0

    # Republish
    sys.stderr.write("\n→ Re-publishing to live WordPress...\n")
    pmod = publish_mod()
    result = pmod.publish(
        page_folder=page_folder,
        config_path=config_path,
        version=f"v{new_version}",
        dry_run=False,
        request_indexing=False,
        bypass_quality_gate=bypass_quality_gate,
    )
    if not result.success:
        sys.stderr.write(f"ERROR: publish failed: {result.error_message}\n")
        return 1
    sys.stderr.write(f"→ Live URL:    {result.live_url}\n")
    print(result.live_url or new_path)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Orchestrate the imagery pipeline: optimize, upload, wire HTML, "
            "and (optionally) re-publish a Core 30 page."
        ),
    )
    p.add_argument(
        "--page-folder",
        type=Path,
        required=True,
        help="Path to a Core 30 page folder.",
    )
    p.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Client config JSON (the same one publish-core-30-page.py uses).",
    )
    p.add_argument(
        "--only-types",
        type=str,
        default=None,
        help="Comma-separated subset of slot types to process (default: all). e.g. 'hero' or 'hero,about'.",
    )
    p.add_argument(
        "--skip-publish",
        action="store_true",
        help="Wire HTML but don't re-publish to WordPress.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without writing files or hitting the network.",
    )
    p.add_argument(
        "--bypass-quality-gate",
        action="store_true",
        help=(
            "Pass through to publish-core-30-page.py: override the "
            "output-quality-loop pre-publish gate. Use when the draft "
            "content has already been operator-approved but lacks a "
            "PASS verdict in frontmatter (common during image wiring)."
        ),
    )

    args = p.parse_args()

    if not args.page_folder.is_dir():
        sys.stderr.write(f"ERROR: page-folder not found: {args.page_folder}\n")
        return 2
    if not args.config.is_file():
        sys.stderr.write(f"ERROR: config not found: {args.config}\n")
        return 2

    only_types: Optional[list[str]] = None
    if args.only_types:
        only_types = [s.strip() for s in args.only_types.split(",") if s.strip()]
        valid = {"hero", "about", "scene"}
        bad = [t for t in only_types if t not in valid]
        if bad:
            sys.stderr.write(f"ERROR: invalid --only-types: {bad}. Valid: {sorted(valid)}\n")
            return 2

    return run(
        page_folder=args.page_folder,
        config_path=args.config,
        only_types=only_types,
        skip_publish=args.skip_publish,
        dry_run=args.dry_run,
        bypass_quality_gate=args.bypass_quality_gate,
    )


if __name__ == "__main__":
    sys.exit(main())
