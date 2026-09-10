#!/usr/bin/env python3
"""
wp_rule_sweep.py — sweep every live page for forbidden copy and stale facts.

The SOP requires a sitewide sweep whenever a wording or factual rule changes
(sop-service-page-build.md, Phase 4). This is that sweep.

    python3 wp_rule_sweep.py --config configs/<client>.rules.json
    python3 wp_rule_sweep.py --config configs/<client>.rules.json --quiet

Read-only. Never writes. Pair it with wp_surgical_replace.py, which fixes what
this finds.

WHY THIS EXISTS — 2026-08-25, ev-electric-services. A per-page gate had been
run on every build for six waves and had never once looked at the pages already
live. One sweep of 54 pages took under two minutes and found five live defects:

  * a federal tax credit described as ACTIVE two months after it terminated,
    with a percentage and a dollar figure, sitting inside FAQPage JSON-LD
  * client-dictated offer wording unpaired on 31 of 54 pages, three days after
    the client dictated it - every occurrence predating the decision
  * a free-first-visit framing the client had explicitly ruled out
  * a review count of 91 where every other page said 105
  * the review-count shortcodes on zero pages and the number hand-typed on 55

Every one is the same failure: a decision was made, applied to that week's
builds, and never propagated to what already existed.

⚠️ A per-page gate CANNOT find corpus-wide debt. It is scoped to one artifact by
   construction. Run this after any rule change, and on a schedule regardless.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

# Hostinger's WAF returns a BODYLESS 403 to python-requests' and urllib's default
# User-Agent. Verified 2026-08-24: no headers -> 403/0 bytes; browser UA -> 200.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def fetch(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def all_objects(domain: str) -> list[dict]:
    """Every page and post via the public REST API, rendered content."""
    objs: list[dict] = []
    for kind in ("pages", "posts"):
        page = 1
        while True:
            url = (f"{domain}/wp-json/wp/v2/{kind}?per_page=100&page={page}"
                   f"&_fields=id,slug,link,content")
            try:
                batch = json.loads(fetch(url))
            except Exception:
                break
            if not batch:
                break
            objs += batch
            if len(batch) < 100:
                break
            page += 1
    return objs


def visible_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--quiet", action="store_true",
                    help="counts only, no per-match context")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).expanduser().read_text())
    domain = cfg["domain"].rstrip("/")
    rules = cfg["rules"]

    objs = all_objects(domain)
    print(f"{len(objs)} objects fetched from {domain}\n")
    if not objs:
        print("FATAL: nothing fetched.")
        return 1

    hits: dict[str, list[tuple[str, str]]] = {}
    schema_broken: list[tuple[str, str]] = []
    schema_ok = schema_total = 0

    for o in objs:
        html = (o.get("content") or {}).get("rendered", "")

        # Structured data is the highest-consequence surface and the easiest to
        # break silently, so it is checked on every sweep, not only on demand.
        for block in re.findall(
                r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.S):
            schema_total += 1
            try:
                json.loads(block)
                schema_ok += 1
            except Exception as e:
                schema_broken.append((o["slug"], str(e)[:70]))

        for rule in rules:
            # `visible` rules ignore markup and schema; the default searches raw
            # HTML so a violation hiding inside JSON-LD is still caught.
            hay = visible_text(html) if rule.get("visible_only") else html
            for m in re.finditer(rule["pattern"], hay, re.I if rule.get("ignore_case", True) else 0):
                ctx = re.sub(r"<[^>]+>", " ",
                             hay[max(0, m.start() - 80): m.end() + 100])
                hits.setdefault(rule["name"], []).append(
                    (o["slug"], re.sub(r"\s+", " ", ctx).strip()))

    print(f"JSON-LD: {schema_ok}/{schema_total} blocks parse")
    for slug, err in schema_broken:
        print(f"  !! {slug}: {err}")

    print("\n" + "=" * 68)
    clean = True
    for rule in rules:
        found = hits.get(rule["name"], [])
        sev = rule.get("severity", "BLOCK")
        if not found:
            print(f"  ok    {rule['name']}")
            continue
        clean = False
        pages = sorted({s for s, _ in found})
        print(f"  {sev:<5} {rule['name']}  —  "
              f"{len(found)} occurrence(s) on {len(pages)} page(s)")
        if not args.quiet:
            for slug, ctx in found[:rule.get("show", 4)]:
                print(f"          {slug}: …{ctx[:150]}…")
            if len(found) > rule.get("show", 4):
                print(f"          … and {len(found) - rule.get('show', 4)} more")
    print("=" * 68)
    print("\nCLEAN — no rule violations." if clean and not schema_broken
          else "\nViolations above. Fix with wp_surgical_replace.py, then re-run this.")
    return 0 if (clean and not schema_broken) else 2


if __name__ == "__main__":
    raise SystemExit(main())
