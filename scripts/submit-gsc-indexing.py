#!/usr/bin/env python3
"""
submit-gsc-indexing.py — Submit one or many URLs to the Google Search Console
Indexing API via Application Default Credentials (ADC).

Standalone sibling to publish-core-30-page.py. Uses the same auth + request
logic (via gsc_indexing module) but doesn't touch WordPress at all.

USAGE
-----
Single URL:

    python submit-gsc-indexing.py \
        --url https://evelectric.pro/electrical-troubleshooting-vienna-va/ \
        --client-config ev-electric.config.json

Multiple URLs (repeatable flag):

    python submit-gsc-indexing.py \
        --url https://evelectric.pro/page-a/ \
        --url https://evelectric.pro/page-b/ \
        --client-config ev-electric.config.json

Batch from file (one URL per line):

    python submit-gsc-indexing.py \
        --url-file urls.txt \
        --client-config ev-electric.config.json

Dry run (prints what would be submitted, no API call):

    python submit-gsc-indexing.py \
        --url https://evelectric.pro/page-a/ \
        --client-config ev-electric.config.json \
        --dry-run

PREREQUISITES
-------------
- ADC configured per sop-gsc-indexing-api-setup.md
- Client config JSON with "gsc_indexing": true
- pip install requests google-auth google-auth-httplib2 google-api-python-client
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gsc_indexing import request_gsc_indexing


def collect_urls(args: argparse.Namespace) -> list[str]:
    """Gather URLs from --url flags and --url-file, deduplicated in order."""
    urls: list[str] = []
    seen: set[str] = set()

    for u in (args.url or []):
        u = u.strip()
        if u and u not in seen:
            urls.append(u)
            seen.add(u)

    if args.url_file:
        p = Path(args.url_file)
        if not p.is_file():
            sys.stderr.write(f"ERROR: --url-file not found: {p}\n")
            sys.exit(2)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in seen:
                urls.append(line)
                seen.add(line)

    return urls


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Submit URLs to the Google Search Console Indexing API via ADC. "
            "Reuses the same auth + request logic as publish-core-30-page.py."
        ),
    )
    p.add_argument(
        "--url",
        action="append",
        help="URL to submit (repeatable). At least one --url or --url-file required.",
    )
    p.add_argument(
        "--url-file",
        type=str,
        default=None,
        help="Path to a text file with one URL per line. Lines starting with # are skipped.",
    )
    p.add_argument(
        "--client-config",
        type=Path,
        required=True,
        help="Path to client config JSON (must have gsc_indexing: true).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be submitted without making API calls.",
    )

    args = p.parse_args()

    if not args.client_config.is_file():
        sys.stderr.write(f"ERROR: config not found: {args.client_config}\n")
        return 2

    urls = collect_urls(args)
    if not urls:
        sys.stderr.write("ERROR: no URLs provided. Use --url or --url-file.\n")
        return 2

    config = json.loads(args.client_config.read_text(encoding="utf-8"))

    if not config.get("gsc_indexing"):
        sys.stderr.write(
            'WARNING: config does not have "gsc_indexing": true — '
            "all submissions will be skipped.\n"
        )

    submitted = 0
    skipped = 0
    errored = 0

    for i, url in enumerate(urls, 1):
        if args.dry_run:
            print(f"[{i}/{len(urls)}] [dry-run] would submit: {url}")
            skipped += 1
            continue

        status = request_gsc_indexing(url, config)
        print(f"[{i}/{len(urls)}] {url} — {status}")

        if status.startswith("submitted"):
            submitted += 1
        elif status.startswith("[skipped]"):
            skipped += 1
        else:
            errored += 1

    print(f"\nSummary: {len(urls)} URLs — {submitted} submitted, {skipped} skipped, {errored} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
