#!/usr/bin/env python3
"""EV-FU1: Replace hardcoded review count/rating with shortcodes in Wave-1 pages.

Targets body text only — JSON-LD schema is handled by the WPCode content filter
(Snippet 2), so no per-page schema edits are needed here.

Usage:
    python3 ev-fu1-swap-review-shortcodes.py [--dry-run]

Requires: WP app password via tier-3 vending machine or env var.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import requests

# -- Setup --
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from _load_secrets import load_wp_app_password

CONFIG_PATH = SCRIPTS_DIR / "data" / "client-ev-electric-services.json"
BASE_URL = "https://evelectric.pro"
TIMEOUT = 30

# Wave-1 page IDs
WAVE1_PAGES = {
    6098: "electrical-troubleshooting-vienna-va",
    6167: "panel-upgrade-vienna-va",
    6177: "ev-charger-vienna-va",
    6178: "electrical-troubleshooting-fairfax-va",
    6179: "panel-upgrade-fairfax-va",
}

# Patterns to replace in body text (NOT in JSON-LD script blocks).
# Matches "5.0-star average across 91 Google review" variants.
BODY_PATTERN = re.compile(
    r"(?P<rating>5\.0)-star average across (?P<count>91) Google review"
)
BODY_REPLACEMENT = "[ev_review_rating]-star average across [ev_review_count] Google review"


def build_headers(config: dict) -> dict:
    pw = load_wp_app_password(config)
    token = base64.b64encode(f"oliver:{pw}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "ev-fu1-swap-review-shortcodes/1.0",
    }


def fetch_page(headers: dict, page_id: int) -> dict:
    url = f"{BASE_URL}/wp-json/wp/v2/pages/{page_id}"
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def update_page(headers: dict, page_id: int, new_content: str) -> dict:
    url = f"{BASE_URL}/wp-json/wp/v2/pages/{page_id}"
    resp = requests.post(
        url, headers=headers, json={"content": new_content}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def replace_body_only(html: str) -> tuple[str, int]:
    """Replace review count/rating in body text, leaving JSON-LD script blocks untouched.

    Returns (new_html, replacement_count).
    """
    # Split content into script blocks and non-script segments
    # to avoid touching JSON-LD
    parts = re.split(r"(<script[^>]*>.*?</script>)", html, flags=re.DOTALL)
    total_replacements = 0
    new_parts = []
    for part in parts:
        if part.startswith("<script"):
            # Leave script blocks (JSON-LD) untouched
            new_parts.append(part)
        else:
            replaced, count = BODY_PATTERN.subn(BODY_REPLACEMENT, part)
            total_replacements += count
            new_parts.append(replaced)
    return "".join(new_parts), total_replacements


def main():
    parser = argparse.ArgumentParser(description="EV-FU1: Swap hardcoded review counts with shortcodes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without updating WP")
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    headers = build_headers(config)

    results = []
    for page_id, slug in WAVE1_PAGES.items():
        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Processing WP {page_id} ({slug})...")

        page = fetch_page(headers, page_id)
        content = page.get("content", {}).get("rendered", "")

        new_content, replacements = replace_body_only(content)

        if replacements == 0:
            print(f"  No hardcoded review counts found (already converted or pattern mismatch)")
            results.append((page_id, slug, 0, "skipped"))
            continue

        print(f"  Found {replacements} replacement(s) in body text")

        if args.dry_run:
            # Show a diff snippet
            for m in BODY_PATTERN.finditer(content):
                ctx_start = max(0, m.start() - 30)
                ctx_end = min(len(content), m.end() + 30)
                print(f"  BEFORE: ...{content[ctx_start:ctx_end]}...")
                replaced_snippet = BODY_PATTERN.sub(BODY_REPLACEMENT, content[ctx_start:ctx_end])
                print(f"  AFTER:  ...{replaced_snippet}...")
            results.append((page_id, slug, replacements, "dry-run"))
        else:
            update_page(headers, page_id, new_content)
            print(f"  Updated via REST API")
            results.append((page_id, slug, replacements, "updated"))

    # Summary
    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN ' if args.dry_run else ''}SUMMARY")
    print(f"{'=' * 60}")
    for page_id, slug, count, status in results:
        print(f"  WP {page_id} ({slug}): {count} replacement(s) — {status}")

    updated = sum(1 for _, _, _, s in results if s == "updated")
    skipped = sum(1 for _, _, _, s in results if s == "skipped")
    if not args.dry_run:
        print(f"\n{updated} pages updated, {skipped} skipped")
        if updated > 0:
            print("Next: clear LiteSpeed cache, then verify with cache-busted curl")


if __name__ == "__main__":
    main()
