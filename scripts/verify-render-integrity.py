#!/usr/bin/env python3
"""Verify (and optionally fix) render-path CSS corruption on WordPress pages.

Catches the defect class discovered in the EV Wave-1 incident (2026-07-09):
stored content was byte-identical to canonical drafts, but WordPress's wpautop
filter injected </p><p> tokens into <style> blocks during rendering because
<!-- wp:html --> block markers were missing. Stored-content checks alone miss
this defect class — this tool checks RENDERED output.

USAGE
-----
Verify all pages for a client:

    python verify-render-integrity.py --client ev-electric-services

Verify specific pages:

    python verify-render-integrity.py --client ev-electric-services \\
        --page-ids 6098 6167 6177

Fix pages failing the marker check (wrap in <!-- wp:html -->):

    python verify-render-integrity.py --client ev-electric-services --fix

Dry-run fix (show what would change):

    python verify-render-integrity.py --client ev-electric-services --fix --dry-run

Requires: WP app password via tier-3 vending machine, env var, or tier-3 markdown.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from _load_secrets import load_wp_app_password

TIMEOUT = 30
RATE_LIMIT = 0.5  # seconds between requests


def load_client_config(slug: str) -> dict:
    config_path = SCRIPTS_DIR / "data" / f"client-{slug}.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Client config not found: {config_path}\n"
            f"Available: {', '.join(p.stem for p in (SCRIPTS_DIR / 'data').glob('client-*.json'))}"
        )
    return json.loads(config_path.read_text(encoding="utf-8"))


def build_headers(config: dict) -> dict:
    wp_user = config.get("wp_user", "oliver")
    pw = load_wp_app_password(config)
    token = base64.b64encode(f"{wp_user}:{pw}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "verify-render-integrity/1.0",
    }


def get_all_page_ids(base_url: str, headers: dict) -> list[int]:
    """Fetch all published page IDs via WP REST API pagination."""
    page_ids = []
    page_num = 1
    while True:
        url = f"{base_url}/wp-json/wp/v2/pages?status=publish&per_page=100&page={page_num}&_fields=id"
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                if not data:
                    break
                page_ids.extend(item["id"] for item in data)
                # Check for more pages
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page_num >= total_pages:
                    break
                page_num += 1
        except Exception as e:
            print(f"  Warning: pagination stopped at page {page_num}: {e}")
            break
        time.sleep(RATE_LIMIT)
    return sorted(page_ids)


def fetch_page(base_url: str, headers: dict, page_id: int, context: str = "") -> dict:
    """Fetch a page. context='edit' for raw, '' for rendered."""
    url = f"{base_url}/wp-json/wp/v2/pages/{page_id}"
    if context:
        url += f"?context={context}"
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def update_page(base_url: str, headers: dict, page_id: int, content: str) -> dict:
    """POST updated content to a page."""
    url = f"{base_url}/wp-json/wp/v2/pages/{page_id}"
    payload = json.dumps({"content": content}).encode()
    import urllib.request
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def extract_style_blocks(html: str) -> list[str]:
    """Extract all <style> block contents."""
    return re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)


def verify_page(base_url: str, headers: dict, page_id: int) -> dict:
    """Run all integrity checks on a single page. Returns a result dict."""
    result = {
        "page_id": page_id,
        "checks": {},
        "verdict": "PASS",
        "fixable": False,
    }

    try:
        # Fetch raw content
        raw_data = fetch_page(base_url, headers, page_id, context="edit")
        raw_content = raw_data.get("content", {}).get("raw", "")
        slug = raw_data.get("slug", "")
        result["slug"] = slug
        time.sleep(RATE_LIMIT)

        # Fetch rendered content
        rendered_data = fetch_page(base_url, headers, page_id)
        rendered_content = rendered_data.get("content", {}).get("rendered", "")
        time.sleep(RATE_LIMIT)

        # Check (a): wp:html markers in raw
        has_markers = raw_content.lstrip().startswith("<!-- wp:html -->")
        result["checks"]["wp_html_markers"] = has_markers

        # Extract style blocks from both
        raw_styles = extract_style_blocks(raw_content)
        rendered_styles = extract_style_blocks(rendered_content)

        # Only check pages that HAVE style blocks (Core-30 pages)
        if not raw_styles and not rendered_styles:
            result["checks"]["has_style_block"] = False
            result["verdict"] = "SKIP"  # no style block = not a Core-30 page
            return result

        result["checks"]["has_style_block"] = True

        # Check (b): 0 <p> tags inside any rendered <style>
        total_p = sum(s.count("<p>") for s in rendered_styles)
        result["checks"]["rendered_p_in_css"] = total_p
        result["checks"]["p_clean"] = total_p == 0

        # Check (c): rendered style-block length == raw style-block length
        raw_total = sum(len(s) for s in raw_styles)
        rendered_total = sum(len(s) for s in rendered_styles)
        result["checks"]["raw_css_len"] = raw_total
        result["checks"]["rendered_css_len"] = rendered_total
        result["checks"]["length_match"] = raw_total == rendered_total

        # Check (d): braces balanced in rendered style blocks
        total_open = sum(s.count("{") for s in rendered_styles)
        total_close = sum(s.count("}") for s in rendered_styles)
        result["checks"]["braces_open"] = total_open
        result["checks"]["braces_close"] = total_close
        result["checks"]["braces_balanced"] = total_open == total_close

        # Determine verdict
        failures = []
        if not has_markers:
            failures.append("no-wp-html-markers")
        if total_p > 0:
            failures.append(f"p-in-css({total_p})")
        if raw_total != rendered_total:
            failures.append(f"css-inflation({rendered_total - raw_total:+d})")
        if total_open != total_close:
            failures.append(f"unbalanced-braces({total_open}/{total_close})")

        if failures:
            result["verdict"] = "FAIL"
            result["failures"] = failures
            # Fixable only if the sole issue is missing markers
            result["fixable"] = (
                failures == ["no-wp-html-markers"]
                or set(failures) <= {"no-wp-html-markers", f"p-in-css({total_p})", f"css-inflation({rendered_total - raw_total:+d})"}
                and not has_markers
            )

    except Exception as e:
        result["verdict"] = "ERROR"
        result["error"] = str(e)

    return result


def fix_page(base_url: str, headers: dict, page_id: int, dry_run: bool = False) -> dict:
    """Wrap a page's content in <!-- wp:html --> markers."""
    raw_data = fetch_page(base_url, headers, page_id, context="edit")
    raw_content = raw_data.get("content", {}).get("raw", "")

    if raw_content.lstrip().startswith("<!-- wp:html -->"):
        return {"status": "already-wrapped", "page_id": page_id}

    wrapped = f"<!-- wp:html -->\n{raw_content}\n<!-- /wp:html -->"

    if dry_run:
        return {"status": "would-fix", "page_id": page_id}

    update_page(base_url, headers, page_id, wrapped)
    time.sleep(RATE_LIMIT)

    # Re-verify
    result = verify_page(base_url, headers, page_id)
    return {
        "status": "fixed" if result["verdict"] == "PASS" else "fix-failed",
        "page_id": page_id,
        "post_fix_verdict": result["verdict"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Verify render-path CSS integrity on WordPress pages"
    )
    parser.add_argument(
        "--client", required=True,
        help="Client slug (e.g. ev-electric-services). Resolves config from data/client-<slug>.json"
    )
    parser.add_argument(
        "--page-ids", type=int, nargs="+",
        help="Specific page IDs to check (default: all published pages)"
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Fix pages failing only the marker check (wrap in <!-- wp:html -->)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --fix, show what would change without modifying"
    )
    args = parser.parse_args()

    config = load_client_config(args.client)
    base_url = config.get("website_url_no_slash", config.get("website_url", "").rstrip("/"))
    headers = build_headers(config)

    # Resolve page IDs
    if args.page_ids:
        page_ids = args.page_ids
    else:
        print(f"Fetching all published pages for {args.client}...")
        page_ids = get_all_page_ids(base_url, headers)
        print(f"Found {len(page_ids)} pages\n")

    # Verify
    results = []
    for pid in page_ids:
        result = verify_page(base_url, headers, pid)
        results.append(result)
        slug = result.get("slug", "")
        verdict = result["verdict"]
        checks = result.get("checks", {})

        if verdict == "SKIP":
            print(f"  {pid:>6} {slug[:45]:45} SKIP (no style block)")
        elif verdict == "PASS":
            css_len = checks.get("raw_css_len", 0)
            braces = f"{checks.get('braces_open', 0)}/{checks.get('braces_close', 0)}"
            print(f"  {pid:>6} {slug[:45]:45} PASS  css={css_len:>6}  braces={braces}")
        elif verdict == "FAIL":
            failures = ", ".join(result.get("failures", []))
            fixable = " [FIXABLE]" if result.get("fixable") else " [TRIAGE]"
            print(f"  {pid:>6} {slug[:45]:45} FAIL  {failures}{fixable}")
        else:
            print(f"  {pid:>6} {slug[:45]:45} ERROR {result.get('error', '')[:60]}")

    # Summary
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    n_skip = sum(1 for r in results if r["verdict"] == "SKIP")
    n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
    n_fixable = sum(1 for r in results if r.get("fixable"))
    n_triage = n_fail - n_fixable
    n_error = sum(1 for r in results if r["verdict"] == "ERROR")

    print(f"\n{'=' * 70}")
    print(f"  {n_pass} pass / {n_skip} skip / {n_fail} fail ({n_fixable} fixable, {n_triage} triage) / {n_error} error")
    print(f"{'=' * 70}")

    # Fix mode
    if args.fix and n_fixable > 0:
        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Fixing {n_fixable} pages...")
        n_fixed = 0
        for result in results:
            if result.get("fixable"):
                pid = result["page_id"]
                fix_result = fix_page(base_url, headers, pid, dry_run=args.dry_run)
                status = fix_result["status"]
                print(f"  {pid}: {status}")
                if status in ("fixed", "would-fix"):
                    n_fixed += 1

        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}{n_fixed} fixed / {n_triage} need triage")

    if n_triage > 0:
        print(f"\n⚠ {n_triage} page(s) need human triage (non-marker failures)")

    sys.exit(1 if (n_fail - n_fixable) > 0 or n_error > 0 else 0)


if __name__ == "__main__":
    main()
