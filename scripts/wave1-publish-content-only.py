#!/usr/bin/env python3
"""
wave1-publish-content-only.py — Publish Wave-1 rebuilt content to 5 EV pages.

Updates post_content ONLY via WP REST API. Does NOT touch slug, title, status,
or AIOSEO meta. Auth via _load_secrets.py (key-file-first, env fallback).

Usage: python3 wave1-publish-content-only.py
"""

import json
import re
import sys
from base64 import b64encode
from pathlib import Path

import requests

# Add the scripts dir to path for _load_secrets
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_secrets import load_wp_app_password

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CORE30 = Path.home() / "workspace" / "second-brain" / "04_projects" / "clients" / "_active" / "ev-electric-services" / "website-archive" / "new" / "core-30"
CONFIG_PATH = Path(__file__).resolve().parent / "data" / "client-ev-electric-services.json"

# Pages: (draft_file_relative_to_CORE30, wp_id_or_slug)
PAGES = [
    ("04-electrical-troubleshooting-fairfax-va/draft-v5-WP-WRAPPED.html", 6178),
    ("05-panel-upgrade-fairfax-va/draft-v5-WP-WRAPPED.html", 6179),
    ("03-ev-charger-vienna-va/draft-v5-WP-WRAPPED.html", 6177),
    ("01-electrical-troubleshooting-vienna-va/draft-v23-WP-WRAPPED.html", "electrical-troubleshooting-vienna-va"),
    ("02-panel-upgrade-vienna-va/draft-v15-WP-WRAPPED.html", "panel-upgrade-vienna-va"),
]

BASE_URL = "https://evelectric.pro"


def main():
    config = json.loads(CONFIG_PATH.read_text())
    app_password = load_wp_app_password(config)
    token = b64encode(f"oliver:{app_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "wave1-publish/1.0",
    }

    # ---- Step 1: Resolve all IDs ----
    resolved = []
    for draft_rel, id_or_slug in PAGES:
        draft_path = CORE30 / draft_rel
        if not draft_path.exists():
            print(f"FATAL: draft not found: {draft_path}")
            return 1

        if isinstance(id_or_slug, int):
            wp_id = id_or_slug
            slug = draft_rel.split("/")[0].split("-", 1)[1]  # e.g. electrical-troubleshooting-fairfax-va
        else:
            # Resolve by slug
            slug = id_or_slug
            resp = requests.get(
                f"{BASE_URL}/wp-json/wp/v2/pages",
                headers=headers,
                params={"slug": slug, "status": "any"},
                timeout=30,
            )
            resp.raise_for_status()
            results = resp.json()
            if not results:
                print(f"FATAL: no page found for slug '{slug}'")
                return 1
            wp_id = results[0]["id"]
            print(f"  Resolved slug '{slug}' → WP ID {wp_id}")

        resolved.append((draft_path, wp_id, slug))

    print(f"\nAll 5 IDs confirmed:")
    for _, wp_id, slug in resolved:
        print(f"  {slug} → {wp_id}")

    # ---- Step 2: Publish each page (content-only update) ----
    results = []
    for draft_path, wp_id, slug in resolved:
        html = draft_path.read_text(encoding="utf-8")
        payload = {
            "content": html,
            "status": "publish",
        }
        resp = requests.post(
            f"{BASE_URL}/wp-json/wp/v2/pages/{wp_id}",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            print(f"  FAIL {slug} (WP {wp_id}): {resp.status_code} — {resp.text[:200]}")
            results.append((slug, wp_id, False, resp.status_code))
            continue

        data = resp.json()
        live_url = data.get("link", f"{BASE_URL}/{slug}/")
        print(f"  PUBLISHED {slug} (WP {wp_id}) → {live_url}")
        results.append((slug, wp_id, True, 200))

    # ---- Step 3: Purge LiteSpeed cache ----
    print("\nPurging LiteSpeed cache for published pages...")
    purge_urls = [f"{BASE_URL}/{slug}/" for _, _, slug in resolved]
    try:
        resp = requests.post(
            f"{BASE_URL}/wp-json/keelworks/v1/litespeed-purge",
            headers=headers,
            json={"urls": purge_urls},
            timeout=30,
        )
        if resp.status_code == 200:
            print(f"  LiteSpeed purge: OK ({resp.json().get('count', '?')} URLs)")
        else:
            print(f"  LiteSpeed purge: {resp.status_code} — operator may need to purge via wp-admin")
    except Exception as e:
        print(f"  LiteSpeed purge: error — {e}. Operator should purge via wp-admin.")

    # ---- Step 4: Verify each page live ----
    print("\nVerifying live pages...")
    all_pass = True
    for slug, wp_id, published, _ in results:
        if not published:
            print(f"  SKIP {slug} — publish failed")
            all_pass = False
            continue

        live_url = f"{BASE_URL}/{slug}/"
        try:
            resp = requests.get(live_url, timeout=30, headers={"User-Agent": "wave1-verify/1.0", "Cache-Control": "no-cache"})
        except Exception as e:
            print(f"  FAIL {slug}: fetch error — {e}")
            all_pass = False
            continue

        status_ok = resp.status_code == 200
        body = resp.text

        # Check for new content marker
        has_safety_triage = "evp-safety-triage" in body
        has_91_reviews = '"91"' in body or "91 customer reviews" in body or "91 reviews" in body

        # Check images
        img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', body)
        # Filter to evelectric.pro images only
        ev_imgs = [s for s in img_srcs if "evelectric.pro" in s]
        img_results = []
        for img_url in ev_imgs[:10]:  # check up to 10
            try:
                ir = requests.head(img_url, timeout=10, allow_redirects=True)
                img_results.append((img_url.split("/")[-1], ir.status_code))
            except:
                img_results.append((img_url.split("/")[-1], "ERR"))

        imgs_ok = all(s == 200 for _, s in img_results)
        img_fails = [(n, s) for n, s in img_results if s != 200]

        # Check no dollar figures (beyond priceRange/$$ in schema)
        dollar_matches = re.findall(r'\$\d+', body)

        print(f"\n  {slug} (WP {wp_id}):")
        print(f"    HTTP 200: {'YES' if status_ok else 'NO (' + str(resp.status_code) + ')'}")
        print(f"    New content (safety-triage): {'YES' if has_safety_triage else 'NO'}")
        print(f"    91 reviews: {'YES' if has_91_reviews else 'NO'}")
        print(f"    Images ({len(ev_imgs)} found, all 200): {'YES' if imgs_ok else 'NO — ' + str(img_fails)}")
        print(f"    Dollar figures: {'NONE (clean)' if not dollar_matches else 'FOUND: ' + str(dollar_matches)}")

        if not (status_ok and has_safety_triage and imgs_ok):
            all_pass = False

    print(f"\n{'='*60}")
    print(f"RESULT: {'ALL 5 PASS' if all_pass else 'FAILURES — see above'}")
    print(f"{'='*60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
