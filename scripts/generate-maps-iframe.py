#!/usr/bin/env python3
"""
generate-maps-iframe.py — produce the v17-styled Google Maps iframe HTML for a
Core 30 page.

Replaces the manual google.com/maps → Share → Embed → Copy walkthrough.
Inputs: city + state. Output: a paste-ready `<div class="evp-map">…</div>`
block matching the page-1 v17 design-system pattern.

Pages serving the same city share the same iframe — the cache file keeps one
canonical entry per (city, state) and per-zoom so subsequent runs return the
same bytes instantly without an API call.

The Google Maps Embed API's place mode is free with no quota cap. If billing
ever moves to a paid tier, the engagement terms put it on the client's card
(GCP project owned by the client). See the README for the move-to-client steps.

USAGE
-----
Single city:

    export GOOGLE_MAPS_EMBED_API_KEY="AIzaSyD…"
    python generate-maps-iframe.py --city "Vienna" --state "VA"

Batch (one city per line in the file, format `City, ST`):

    python generate-maps-iframe.py --batch ./cities.txt

Bypass cache for one fetch (regenerate from scratch):

    python generate-maps-iframe.py --city "Vienna" --state "VA" --force-refresh

List everything in the cache:

    python generate-maps-iframe.py --list

CONFIG
------
Pass --config to set client-specific values:

    {
      "api_key_env":  "GOOGLE_MAPS_EMBED_API_KEY",
      "client_name":  "S&H Contracting Unlimited",
      "default_zoom": 12,
      "cache_path":   "cache/maps-iframes.json"
    }

cache_path is resolved relative to the script's own directory unless an
absolute path is given.

API KEY STORAGE
---------------
The key lives in the tier-3 vault at
`~/workspace/second-brain-tier3/personal/business-keelworks.md` under a
Keelworks-tools section (the key is cross-client — same key serves every
Keelworks engagement). The pointer file in the main vault is
`second-brain/04_projects/clients/_active/ev-electric-services/credentials.reference.md`.

NEVER commit the key or paste it into the config file.

PATTERN
-------
The output wrapper matches `pattern-core-30-page-design-system.md` Section 8.
If that pattern changes, update _WRAPPER_TEMPLATE below to match.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus


# ----------------------------------------------------------------------------
# Defaults — client_name MUST be set by caller (no hardcoded brand)
# ----------------------------------------------------------------------------

_DEFAULTS = {
    "api_key_env": "GOOGLE_MAPS_EMBED_API_KEY",
    "client_name": None,  # MUST be set by caller or config — no hardcoded default
    "default_zoom": 12,
    "cache_path": "cache/maps-iframes.json",
}

_EMBED_API_BASE = "https://www.google.com/maps/embed/v1/place"

# Matches v17 page-1 wrapper. Keep formatting verbatim — pastes into the
# Custom HTML block exactly as-is.
_WRAPPER_TEMPLATE = (
    '<div class="evp-map" style="background:none; padding:0; overflow:hidden; '
    'border-radius:20px; border:none; display:block;">\n'
    '  <iframe src="{src}"\n'
    '          width="600"\n'
    '          height="450"\n'
    '          style="width:100%; height:100%; min-height:380px; border:0; '
    'display:block;"\n'
    '          allowfullscreen=""\n'
    '          loading="lazy"\n'
    '          referrerpolicy="no-referrer-when-downgrade"\n'
    '          title="Map of {city}, {state} — {client} service area"></iframe>\n'
    '</div>'
)


# ----------------------------------------------------------------------------
# Config + cache
# ----------------------------------------------------------------------------


def load_config(config_path: Optional[Path]) -> dict:
    """Load config from JSON file, falling back to baked-in defaults."""
    if config_path is None:
        return dict(_DEFAULTS)
    if not config_path.is_file():
        sys.stderr.write(f"ERROR: config not found: {config_path}\n")
        sys.exit(2)
    cfg = dict(_DEFAULTS)
    cfg.update(json.loads(config_path.read_text(encoding="utf-8")))
    return cfg


def resolve_cache_path(cache_path_str: str) -> Path:
    """Resolve cache_path relative to this script's directory (unless absolute)."""
    p = Path(cache_path_str)
    if p.is_absolute():
        return p
    return Path(__file__).resolve().parent / p


def load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(
            f"WARN: cache file corrupted ({e}); starting from empty cache.\n"
        )
        return {}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ----------------------------------------------------------------------------
# Iframe construction
# ----------------------------------------------------------------------------


def build_embed_url(city: str, state: str, api_key: str, zoom: int) -> str:
    """Construct a Google Maps Embed API place-mode URL.

    Reference: https://developers.google.com/maps/documentation/embed/embedding-map
    """
    q = quote_plus(f"{city}, {state}")
    return f"{_EMBED_API_BASE}?key={api_key}&q={q}&zoom={zoom}"


def build_wrapped_html(city: str, state: str, embed_url: str, client: str) -> str:
    return _WRAPPER_TEMPLATE.format(
        src=embed_url, city=city, state=state, client=client
    )


def cache_key(city: str, state: str, zoom: int) -> str:
    return f"{city.strip()}, {state.strip().upper()} @z{zoom}"


def generate_for_city(
    city: str,
    state: str,
    config: dict,
    cache: dict,
    force_refresh: bool,
) -> tuple[str, str, bool]:
    """Return (cache_key, wrapped_html, was_cache_hit)."""
    zoom = int(config["default_zoom"])
    key = cache_key(city, state, zoom)

    if not config.get("client_name"):
        sys.stderr.write(
            "ERROR: client_name is required in maps config but was not set.\n"
            "Pass --config with a JSON containing client_name, or set it in the\n"
            "caller (scaffold-page.py passes it from the client data file).\n"
        )
        sys.exit(2)

    if not force_refresh and key in cache:
        # Cache hit — but check client_name matches. A cached entry from a
        # different client would leak the wrong brand into the iframe title
        # (source-client-leak class, PR-35).
        cached_client = cache[key].get("client", "")
        if cached_client == config["client_name"]:
            return key, cache[key]["wrapped_html"], True
        # Client mismatch — regenerate for this client
        sys.stderr.write(
            f"[maps:client-mismatch] cached '{cached_client}' != '{config['client_name']}'; regenerating\n"
        )

    # Resolve API key — env var first, tier-3 markdown fallback (see _load_secrets.py)
    try:
        from _load_secrets import load_google_maps_api_key
        api_key = load_google_maps_api_key(config)
    except RuntimeError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.exit(3)

    embed_url = build_embed_url(city, state, api_key, zoom)
    wrapped = build_wrapped_html(city, state, embed_url, config["client_name"])

    # Cache stores the URL with the literal key string (so the cache file is
    # human-readable for audit). The wrapped HTML pastes into the page draft.
    cache[key] = {
        "iframe_url": embed_url,
        "wrapped_html": wrapped,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "zoom": zoom,
        "client": config["client_name"],
    }
    return key, wrapped, False


# ----------------------------------------------------------------------------
# Batch input
# ----------------------------------------------------------------------------


def parse_batch_file(batch_path: Path) -> list[tuple[str, str]]:
    """One city per line, format `City, ST`. Blank lines and # comments skipped."""
    out: list[tuple[str, str]] = []
    for line in batch_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," not in line:
            sys.stderr.write(f"WARN: skipping malformed line: {line!r}\n")
            continue
        city, _, state = line.partition(",")
        out.append((city.strip(), state.strip()))
    return out


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def cmd_list(cache: dict) -> int:
    if not cache:
        print("(cache empty)")
        return 0
    for key in sorted(cache.keys()):
        entry = cache[key]
        print(f"{key}  —  generated {entry.get('generated_at', '?')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Generate the v17-styled Google Maps iframe HTML for a Core 30 page."
        ),
    )
    p.add_argument("--city", help='City name, e.g. "Vienna"')
    p.add_argument("--state", help='State abbreviation, e.g. "VA"')
    p.add_argument(
        "--batch",
        type=Path,
        help='Path to a text file with one "City, ST" per line',
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Client config JSON with client_name. Required for correct iframe titles.",
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="Bypass cache for the requested city/cities and regenerate.",
    )
    p.add_argument(
        "--no-save",
        action="store_true",
        help="Don't write the cache file (handy for ad-hoc one-off tests).",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="Print every entry in the cache and exit.",
    )

    args = p.parse_args()

    config = load_config(args.config)
    cache_path = resolve_cache_path(config["cache_path"])
    cache = load_cache(cache_path)

    if args.list:
        return cmd_list(cache)

    # Decide what to process
    targets: list[tuple[str, str]] = []
    if args.batch:
        if not args.batch.is_file():
            sys.stderr.write(f"ERROR: batch file not found: {args.batch}\n")
            return 2
        targets = parse_batch_file(args.batch)
    elif args.city and args.state:
        targets = [(args.city, args.state)]
    else:
        sys.stderr.write(
            "ERROR: pass either --city AND --state, or --batch <file>, "
            "or --list to inspect the cache.\n"
        )
        return 2

    if not targets:
        sys.stderr.write("ERROR: no cities to process.\n")
        return 2

    something_changed = False
    for city, state in targets:
        key, wrapped, hit = generate_for_city(
            city, state, config, cache, args.force_refresh
        )
        marker = "cache" if hit else "fresh"
        sys.stderr.write(f"[{marker}] {key}\n")
        if not hit:
            something_changed = True
        print(wrapped)
        # Separator between multiple cities so they're easy to split on paste
        if len(targets) > 1:
            print()

    if something_changed and not args.no_save:
        save_cache(cache_path, cache)
        sys.stderr.write(f"Cache updated: {cache_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
