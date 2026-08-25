#!/usr/bin/env python3
"""
Fix the AIOSEO meta keys that the keelworks page-restore route double-wrapped.

The restore route's add_post_meta → maybe_serialize wraps serialized-looking strings
in an extra layer each time. This script uses the new page-meta-raw endpoint (v1.1.0)
to write the correct DB-level value directly via $wpdb->update(), bypassing
maybe_serialize entirely.

Target DB value: s:6:"a:0:{}";
  → get_post_meta returns the string 'a:0:{}' (a serialized empty PHP array)
  → this is the value AIOSEO originally wrote

Usage:
    cd ~/workspace/repos/ai-agency-core/scripts

    # dry-run: show current state of all four pages
    python3 ev_b1_fix_aioseo_double_wrap.py

    # fix all four pages
    python3 ev_b1_fix_aioseo_double_wrap.py --confirm
"""

from __future__ import annotations

import json
import sys

from wp_page_snapshot import (
    SNAPSHOT_ROUTE,
    _auth,
    _client_config,
    _curl,
    _explain,
)

CLIENT_SLUG = "ev-electric-services"
META_RAW_ROUTE = "{base}/wp-json/keelworks/v1/page-meta-raw/{id}"

# The two keys that get double-wrapped
KEYS = ["_aioseo_keywords", "_aioseo_og_article_tags"]

# The correct DB-level value. WordPress's maybe_unserialize('s:6:"a:0:{}";')
# returns the string 'a:0:{}', which is what get_post_meta should show.
CORRECT_DB_VALUE = 's:6:"a:0:{}";'

# Pages to fix: 95, 97, 8 (B1 writes), 103 (selftest)
PAGE_IDS = [95, 97, 8, 103]


def check_current_state(base: str, auth: str) -> dict[int, dict[str, str]]:
    """Snapshot all four pages and return their current AIOSEO key values."""
    state = {}
    for pid in PAGE_IDS:
        r = _curl(SNAPSHOT_ROUTE.format(base=base, id=pid), auth)
        if r.status_code != 200:
            print(f"  Page {pid}: snapshot failed — {_explain(r, 'snapshot')}")
            continue
        snap = r.json()
        meta = snap.get("meta", {})
        state[pid] = {k: meta.get(k, "NOT PRESENT") for k in KEYS}
    return state


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fix AIOSEO double-wrapped meta keys")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually write the fixes. Without this, dry-run only.")
    args = parser.parse_args()

    cfg = _client_config(CLIENT_SLUG)
    base, auth = _auth(cfg)

    print("Checking current state of AIOSEO keys ...\n")
    state = check_current_state(base, auth)

    needs_fix = []
    for pid in PAGE_IDS:
        if pid not in state:
            continue
        for key in KEYS:
            val = state[pid][key]
            ok = val == "a:0:{}"
            status = "OK" if ok else f"NEEDS FIX (current: {repr(val)})"
            print(f"  Page {pid:>3} {key}: {status}")
            if not ok:
                needs_fix.append((pid, key, val))
        print()

    if not needs_fix:
        print("All keys are already at the correct value. Nothing to do.")
        return

    print(f"{len(needs_fix)} key(s) need fixing.\n")

    if not args.confirm:
        print("Dry run — pass --confirm to write the fixes.")
        return

    # Fix each page
    fixed_pages = set()
    for pid, key, old_val in needs_fix:
        fixed_pages.add(pid)

    for pid in sorted(fixed_pages):
        keys_to_fix = {k: CORRECT_DB_VALUE for _, k, _ in needs_fix if _ == pid}
        # Rebuild from needs_fix for this page
        keys_to_fix = {}
        for p, k, _ in needs_fix:
            if p == pid:
                keys_to_fix[k] = CORRECT_DB_VALUE

        print(f"Fixing page {pid} — {len(keys_to_fix)} key(s) ...")
        body = {
            "meta": keys_to_fix,
            "confirm": str(pid),
        }
        r = _curl(META_RAW_ROUTE.format(base=base, id=pid), auth, "POST", json.dumps(body))
        if r.status_code != 200:
            print(f"  FAILED: {_explain(r, 'meta-raw')}")
            print("  Is the keelworks-page-snapshot plugin v1.1.0 deployed?")
            sys.exit(1)

        result = r.json()
        print(f"  Response: {json.dumps(result, indent=2)}")

        # Verify each key
        for key, detail in result.get("results", {}).items():
            if not detail.get("match"):
                print(f"  WARNING: {key} did not land correctly!")
                print(f"    expected DB: {repr(CORRECT_DB_VALUE)}")
                print(f"    actual DB:   {repr(detail.get('after_db'))}")

    # Re-check via snapshot
    print("\nVerifying via snapshot ...\n")
    post_state = check_current_state(base, auth)
    all_ok = True
    for pid in PAGE_IDS:
        if pid not in post_state:
            continue
        for key in KEYS:
            val = post_state[pid][key]
            ok = val == "a:0:{}"
            status = "OK" if ok else f"STILL WRONG: {repr(val)}"
            print(f"  Page {pid:>3} {key}: {status}")
            if not ok:
                all_ok = False
        print()

    if all_ok:
        print("All AIOSEO keys restored to correct values.")
    else:
        print("Some keys are still wrong. Investigate before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
