#!/usr/bin/env python3
"""
publish-differentiation-wave.py — Client-agnostic publisher for differentiation-wave pages.

Updates post_content ONLY via WP REST API. Does NOT touch slug, title, status,
or SEO meta. Auth via _load_secrets.py (key-file-first, tier-3 hierarchy).

Usage:
    python3 publish-differentiation-wave.py --config <path-to-config.json>
    python3 publish-differentiation-wave.py --config <path-to-config.json> --dry-run

Config schema:
    {
      "client_slug": "...",
      "domain": "https://example.com",
      "wp_user": "oliver",
      "pages": [
        {
          "slug": "page-slug",
          "draft_path": "/absolute/path/to/draft.html",
          "wp_id": 1234,          // optional — resolved by slug if absent
          "verify_string": "unique-string-in-new-content"
        }
      ]
    }
"""

import argparse
import json
import re
import sys
from base64 import b64encode
from pathlib import Path

import requests

# Add the scripts dir to path for _load_secrets
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_secrets import load_wp_app_password


def load_config(config_path: str) -> dict:
    """Load and validate the publish config."""
    path = Path(config_path).expanduser()
    if not path.exists():
        print(f"FATAL: config file not found: {path}")
        sys.exit(1)

    config = json.loads(path.read_text(encoding="utf-8"))

    required = ["client_slug", "domain", "pages"]
    for key in required:
        if key not in config:
            print(f"FATAL: config missing required key: {key}")
            sys.exit(1)

    if not config["pages"]:
        print("FATAL: config 'pages' array is empty")
        sys.exit(1)

    for i, page in enumerate(config["pages"]):
        if "slug" not in page:
            print(f"FATAL: page[{i}] missing 'slug'")
            sys.exit(1)
        if "draft_path" not in page:
            print(f"FATAL: page[{i}] missing 'draft_path'")
            sys.exit(1)

    return config


def resolve_wp_id(domain: str, slug: str, headers: dict) -> int | None:
    """Resolve a page's WP ID by slug via WP REST API."""
    resp = requests.get(
        f"{domain}/wp-json/wp/v2/pages",
        headers=headers,
        params={"slug": slug, "status": "any"},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return results[0]["id"]


def verify_page(domain: str, slug: str, wp_id: int, verify_string: str | None) -> dict:
    """Verify a published page: HTTP 200, verify string present, images 200."""
    live_url = f"{domain}/{slug}/"
    result = {"slug": slug, "wp_id": wp_id, "url": live_url, "pass": True, "issues": []}

    try:
        resp = requests.get(
            live_url, timeout=30,
            headers={"User-Agent": "wave-verify/1.0", "Cache-Control": "no-cache"},
        )
    except Exception as e:
        result["pass"] = False
        result["issues"].append(f"fetch error: {e}")
        return result

    if resp.status_code != 200:
        result["pass"] = False
        result["issues"].append(f"HTTP {resp.status_code} (expected 200)")
        return result

    body = resp.text

    # Verify string check
    if verify_string and verify_string not in body:
        result["pass"] = False
        result["issues"].append(f"verify_string '{verify_string}' NOT found in page body")

    # Image check — find all <img> src on the domain
    img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', body)
    domain_host = domain.replace("https://", "").replace("http://", "")
    domain_imgs = [s for s in img_srcs if domain_host in s]

    img_fails = []
    for img_url in domain_imgs[:15]:
        try:
            ir = requests.head(img_url, timeout=10, allow_redirects=True)
            if ir.status_code != 200:
                img_fails.append((img_url.split("/")[-1], ir.status_code))
        except Exception:
            img_fails.append((img_url.split("/")[-1], "ERR"))

    if img_fails:
        result["pass"] = False
        result["issues"].append(f"image failures: {img_fails}")

    result["images_checked"] = len(domain_imgs)
    result["images_failed"] = len(img_fails)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Publish differentiation-wave pages (content-only) via WP REST API."
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to the client wave config JSON file.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve IDs and validate drafts without publishing.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    domain = config["domain"].rstrip("/")
    wp_user = config.get("wp_user", "oliver")
    pages = config["pages"]

    # Auth
    client_config = {"client_slug": config["client_slug"]}
    app_password = load_wp_app_password(client_config)
    token = b64encode(f"{wp_user}:{app_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "publish-differentiation-wave/1.0",
    }

    mode_label = "DRY-RUN" if args.dry_run else "PUBLISH"
    print(f"\n{'='*60}")
    print(f"  {mode_label}: {config['client_slug']} — {len(pages)} pages")
    print(f"  Domain: {domain}")
    print(f"{'='*60}\n")

    # ---- Step 1: Validate drafts exist & resolve WP IDs ----
    print("Step 1: Validating drafts and resolving WP IDs...\n")
    resolved = []
    has_errors = False

    for page in pages:
        slug = page["slug"]
        draft_path = Path(page["draft_path"]).expanduser()
        wp_id = page.get("wp_id")
        verify_string = page.get("verify_string")

        if not draft_path.exists():
            print(f"  FATAL: draft not found: {draft_path}")
            has_errors = True
            continue

        if wp_id is None:
            # Resolve by slug
            wp_id = resolve_wp_id(domain, slug, headers)
            if wp_id is None:
                print(f"  FATAL: no page found for slug '{slug}' on {domain}")
                has_errors = True
                continue
            print(f"  Resolved slug '{slug}' → WP ID {wp_id}")
        else:
            print(f"  Confirmed slug '{slug}' → WP ID {wp_id} (from config)")

        resolved.append({
            "slug": slug,
            "draft_path": draft_path,
            "wp_id": wp_id,
            "verify_string": verify_string,
        })

    if has_errors:
        print("\nFATAL: one or more pages could not be resolved. Aborting.")
        return 1

    print(f"\nAll {len(resolved)} IDs confirmed:")
    for r in resolved:
        print(f"  {r['slug']} → WP {r['wp_id']}")

    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"  DRY-RUN complete. {len(resolved)} pages resolved. No changes made.")
        print(f"{'='*60}")
        return 0

    # ---- Step 2: Publish each page (content-only update) ----
    print("\nStep 2: Publishing (content-only update)...\n")
    publish_results = []

    for r in resolved:
        html = r["draft_path"].read_text(encoding="utf-8")
        payload = {
            "content": html,
            "status": "publish",
        }
        resp = requests.post(
            f"{domain}/wp-json/wp/v2/pages/{r['wp_id']}",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            print(f"  FAIL {r['slug']} (WP {r['wp_id']}): {resp.status_code} — {resp.text[:200]}")
            publish_results.append({"slug": r["slug"], "wp_id": r["wp_id"], "published": False})
            continue

        data = resp.json()
        live_url = data.get("link", f"{domain}/{r['slug']}/")
        print(f"  PUBLISHED {r['slug']} (WP {r['wp_id']}) → {live_url}")
        publish_results.append({"slug": r["slug"], "wp_id": r["wp_id"], "published": True})

    # ---- Step 3: Verify each page live ----
    print("\nStep 3: Verifying live pages...\n")
    all_pass = True

    for r, pr in zip(resolved, publish_results):
        if not pr["published"]:
            print(f"  SKIP {r['slug']} — publish failed")
            all_pass = False
            continue

        vr = verify_page(domain, r["slug"], r["wp_id"], r["verify_string"])
        status = "PASS" if vr["pass"] else "FAIL"
        print(f"  {r['slug']} (WP {r['wp_id']}): {status}")
        if vr.get("images_checked"):
            print(f"    Images checked: {vr['images_checked']}, failed: {vr['images_failed']}")
        for issue in vr.get("issues", []):
            print(f"    !! {issue}")

        if not vr["pass"]:
            all_pass = False

    # ---- Summary ----
    published_count = sum(1 for pr in publish_results if pr["published"])
    print(f"\n{'='*60}")
    print(f"  RESULT: {published_count}/{len(resolved)} published, verification {'ALL PASS' if all_pass else 'FAILURES — see above'}")
    print(f"{'='*60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
