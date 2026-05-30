#!/usr/bin/env python3
"""
upload-image-to-wp.py — upload an image to the WordPress Media Library.

Plain English: takes an image file from your laptop and puts it into the
client's WordPress Media Library, where pages can reference it by URL. Also
remembers that it did this in a per-client memory file (the image cache) so
that the next time the same image is requested, the script just returns the
existing URL instead of uploading a second copy.

WordPress has two storage areas. The Page Editor holds the HTML for each
page. The Media Library holds image files. Pages reference images by URL —
they don't contain the image bytes themselves. This script handles the
Media Library side. The publish-core-30-page.py script handles the Page
Editor side.

USAGE
-----
Upload an image with an explicit cache key:

    export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
    python upload-image-to-wp.py /path/to/some.webp \\
        --config /path/to/ev-electric.config.json \\
        --cache-key "ev-electric-services/vienna-va/02-panel-upgrade-vienna-va/hero" \\
        --alt-text "Ahmad Shaban, master electrician, installing a residential electrical panel in Vienna, VA"

Dry run — show what would be uploaded but don't hit the network:

    python upload-image-to-wp.py /path/to/some.webp \\
        --config /path/to/ev-electric.config.json \\
        --cache-key "..." \\
        --alt-text "..." \\
        --dry-run

Check the cache without uploading anything (returns cached URL if present):

    python upload-image-to-wp.py --config /path/to/ev-electric.config.json \\
        --cache-key "..." \\
        --check-only

List every entry in the cache for this client:

    python upload-image-to-wp.py --config /path/to/ev-electric.config.json --list

CACHE
-----
Per-client JSON file at `repos/ai-agency-core/scripts/cache/<client-slug>-images.json`.
Structure:

    {
      "client_slug": "ev-electric-services",
      "entries": {
        "ev-electric-services/vienna-va/about": {
          "wp_url": "https://evelectric.pro/wp-content/uploads/2026/05/vienna-about-v2.webp",
          "wp_media_id": 1234,
          "filename": "vienna-about-v2.webp",
          "alt_text": "Ahmad Shaban, master electrician at EV Electric Services serving Vienna, VA",
          "uploaded_at": "2026-05-26T03:00:00+00:00",
          "file_size_bytes": 198765,
          "scope": "per-city-shared"
        }
      }
    }

The cache key encodes the scope. About portraits use a city-scoped key
(`<client>/<city>/about`) so 30 pages for one city share one upload.
Heroes and scenes use a page-scoped key
(`<client>/<city>/<page-slug>/<type>`) because they're unique per page.

OUTPUTS
-------
- The WordPress-hosted URL of the uploaded image, printed on stdout.
- A new (or unchanged) entry in the cache file.
- Console log of what happened (uploaded, cache hit, alt-text updated).

PRECONDITIONS
-------------
- Python 3.8+
- `requests` library installed.
- WP user has Editor or Administrator role.
- WP_APP_PASSWORD env var set (or whatever the config's wp_app_password_env says).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
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
CACHE_DIR = SCRIPTS_DIR / "cache"


# ----------------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------------


def cache_path_for_client(client_slug: str) -> Path:
    return CACHE_DIR / f"{client_slug}-images.json"


def load_image_cache(client_slug: str) -> dict[str, Any]:
    path = cache_path_for_client(client_slug)
    if not path.exists():
        return {"client_slug": client_slug, "entries": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(
            f"WARN: image cache corrupted ({e}); starting fresh.\n"
        )
        return {"client_slug": client_slug, "entries": {}}


def save_image_cache(client_slug: str, cache: dict[str, Any]) -> None:
    path = cache_path_for_client(client_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def cache_scope(cache_key: str) -> str:
    """Infer scope from key shape. Keys with 4 segments are per-page;
    3 segments are per-city (about portrait reuse pattern).
    """
    parts = cache_key.strip("/").split("/")
    if len(parts) == 3:
        return "per-city-shared"
    return "per-page"


# ----------------------------------------------------------------------------
# WordPress upload
# ----------------------------------------------------------------------------


def wp_auth_headers(username: str, app_password: str) -> dict[str, str]:
    token = b64encode(f"{username}:{app_password}".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "User-Agent": "upload-image-to-wp.py/1.0",
    }


def upload_to_wp_media_library(
    image_path: Path,
    base_url: str,
    username: str,
    app_password: str,
    alt_text: str,
    timeout: int = 60,
) -> dict[str, Any]:
    """POST the image file to /wp-json/wp/v2/media. Returns the parsed JSON.

    WordPress media uploads are binary POSTs with two important headers:
    `Content-Type` (the MIME type of the file) and `Content-Disposition`
    (which contains the filename). After upload, we make a second call to
    PATCH the alt_text on the new media object.
    """
    base = base_url.rstrip("/")
    url = f"{base}/wp-json/wp/v2/media"

    mime, _ = mimetypes.guess_type(str(image_path))
    if mime is None:
        # WebP wasn't in the stdlib mimetypes db on older Python versions.
        ext = image_path.suffix.lower()
        mime = {
            ".webp": "image/webp",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(ext, "application/octet-stream")

    headers = wp_auth_headers(username, app_password)
    headers["Content-Type"] = mime
    headers["Content-Disposition"] = f'attachment; filename="{image_path.name}"'

    with image_path.open("rb") as f:
        resp = requests.post(url, headers=headers, data=f.read(), timeout=timeout)

    if resp.status_code >= 400:
        raise RuntimeError(
            f"WP media upload failed: {resp.status_code} — {resp.text[:500]}"
        )

    media = resp.json()
    media_id = media["id"]

    # Second call to set alt_text. Some WP versions accept alt_text in the
    # initial upload payload; many don't. PATCH is the reliable path.
    if alt_text:
        patch_url = f"{base}/wp-json/wp/v2/media/{media_id}"
        patch_headers = dict(headers)
        patch_headers["Content-Type"] = "application/json"
        # Remove the file-upload-only header from the JSON PATCH:
        patch_headers.pop("Content-Disposition", None)
        patch_resp = requests.post(
            patch_url,
            headers=patch_headers,
            json={"alt_text": alt_text, "title": alt_text[:120]},
            timeout=timeout,
        )
        if patch_resp.status_code >= 400:
            sys.stderr.write(
                f"WARN: alt_text PATCH returned {patch_resp.status_code}: "
                f"{patch_resp.text[:200]}\n"
            )
        else:
            media = patch_resp.json()

    return media


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def cmd_upload(
    image_path: Path,
    config_path: Path,
    cache_key: str,
    alt_text: str,
    dry_run: bool,
) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config.get("client_slug")
    if not client_slug:
        sys.stderr.write("ERROR: config missing client_slug.\n")
        return 2

    cache = load_image_cache(client_slug)

    if cache_key in cache["entries"]:
        existing = cache["entries"][cache_key]
        sys.stderr.write(
            f"→ Cache hit:  {cache_key}\n"
            f"  Existing:   {existing['wp_url']}\n"
            f"  (skipping upload — image already on WordPress)\n"
        )
        print(existing["wp_url"])
        return 0

    if not image_path.is_file():
        sys.stderr.write(f"ERROR: image not found: {image_path}\n")
        return 2

    sys.stderr.write(f"→ Cache miss: {cache_key}\n")
    sys.stderr.write(f"→ Uploading:  {image_path} ({image_path.stat().st_size/1024:.1f} KB)\n")
    sys.stderr.write(f"→ Alt text:   {alt_text}\n")

    if dry_run:
        sys.stderr.write("→ DRY RUN — no network call. Cache not updated.\n")
        # Print a placeholder URL so the orchestrator's flow stays the same.
        print(f"https://example.com/wp-content/uploads/DRY-RUN/{image_path.name}")
        return 0

    try:
        from _load_secrets import load_wp_app_password
        app_password = load_wp_app_password(config)
    except RuntimeError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 3

    try:
        media = upload_to_wp_media_library(
            image_path=image_path,
            base_url=config["wp_base_url"],
            username=config["wp_username"],
            app_password=app_password,
            alt_text=alt_text,
        )
    except Exception as e:
        sys.stderr.write(f"FATAL: {e}\n")
        return 1

    wp_url = media.get("source_url") or media.get("guid", {}).get("rendered")
    wp_media_id = media.get("id")
    if not wp_url or not wp_media_id:
        sys.stderr.write(
            f"ERROR: WP returned media object without a URL or ID:\n{media}\n"
        )
        return 1

    entry = {
        "wp_url": wp_url,
        "wp_media_id": wp_media_id,
        "filename": image_path.name,
        "alt_text": alt_text,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_size_bytes": image_path.stat().st_size,
        "scope": cache_scope(cache_key),
    }
    cache["entries"][cache_key] = entry
    save_image_cache(client_slug, cache)

    sys.stderr.write(f"→ Uploaded:   {wp_url}\n")
    sys.stderr.write(f"→ Media ID:   {wp_media_id}\n")
    sys.stderr.write(f"→ Cache:      updated at {cache_path_for_client(client_slug)}\n")

    print(wp_url)
    return 0


def cmd_check(config_path: Path, cache_key: str) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config["client_slug"]
    cache = load_image_cache(client_slug)
    if cache_key in cache["entries"]:
        entry = cache["entries"][cache_key]
        print(entry["wp_url"])
        sys.stderr.write(f"→ Found:      {cache_key} → {entry['wp_url']}\n")
        return 0
    sys.stderr.write(f"→ Not cached: {cache_key}\n")
    return 1


def cmd_list(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config["client_slug"]
    cache = load_image_cache(client_slug)
    entries = cache.get("entries", {})
    if not entries:
        print("(empty cache)")
        return 0
    for key in sorted(entries.keys()):
        e = entries[key]
        print(f"{key}")
        print(f"  → {e['wp_url']}")
        print(f"    id={e['wp_media_id']}  scope={e.get('scope', '?')}  uploaded={e.get('uploaded_at', '?')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload an image to a WordPress site's Media Library, with per-client caching.",
    )
    p.add_argument(
        "image",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the image file. Required for upload; omit for --check-only or --list.",
    )
    p.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Client config JSON (same one used by publish-core-30-page.py).",
    )
    p.add_argument(
        "--cache-key",
        type=str,
        default=None,
        help='Cache key, e.g. "ev-electric-services/vienna-va/about" or "ev-electric-services/vienna-va/02-panel-upgrade-vienna-va/hero".',
    )
    p.add_argument(
        "--alt-text",
        type=str,
        default="",
        help="SEO-friendly alt text for the image (see imagery SOP Step 8).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded but don't hit the network. Cache unchanged.",
    )
    p.add_argument(
        "--check-only",
        action="store_true",
        help="Look up the cache key and print the existing URL if present. No upload.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="Print every entry in the cache for this client.",
    )

    args = p.parse_args()

    if not args.config.is_file():
        sys.stderr.write(f"ERROR: config not found: {args.config}\n")
        return 2

    if args.list:
        return cmd_list(args.config)

    if args.check_only:
        if not args.cache_key:
            sys.stderr.write("ERROR: --check-only requires --cache-key.\n")
            return 2
        return cmd_check(args.config, args.cache_key)

    if not args.image or not args.cache_key:
        sys.stderr.write(
            "ERROR: upload mode requires both an image path and --cache-key.\n"
        )
        return 2

    return cmd_upload(
        image_path=args.image,
        config_path=args.config,
        cache_key=args.cache_key,
        alt_text=args.alt_text,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
