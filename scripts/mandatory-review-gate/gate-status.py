#!/usr/bin/env python3
"""gate-status: show what's blocking the review gate and why.

Usage:
    python3 gate-status.py --session <session_id>
    python3 gate-status.py --session <session_id> --json

Reads the dirty/reviewed ledgers for the given session and reports
unreviewed entries. Uses engine.check_gate for substrate-agnostic logic.
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
import engine

# CC-scoped by default (same as Stop hook) — shows what CC Stop would see
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})


def _find_latest_session(state_dir: str) -> str:
    """Find the most recently modified dirty ledger's session ID.

    RGH-1b item 3: when --session is omitted, default to the most recent
    dirty ledger so the model doesn't need to know the session ID.
    """
    if not os.path.isdir(state_dir):
        return ''
    candidates = []
    for f in os.listdir(state_dir):
        if f.endswith('-dirty.jsonl'):
            path = os.path.join(state_dir, f)
            mtime = os.path.getmtime(path)
            sid = f[:-len('-dirty.jsonl')]
            candidates.append((mtime, sid))
    if not candidates:
        return ''
    candidates.sort(reverse=True)
    return candidates[0][1]


def main():
    parser = argparse.ArgumentParser(description='Show review gate status')
    parser.add_argument('--session', required=False, default=None,
                        help='Session ID (defaults to most recent dirty ledger)')
    parser.add_argument('--json', action='store_true', dest='as_json',
                        help='Output as JSON')
    parser.add_argument('--all-sources', action='store_true',
                        help='Show entries from all sources (not just claude-code)')
    args = parser.parse_args()

    # RGH-1b item 3: default to latest session if not specified
    if not args.session:
        args.session = _find_latest_session(STATE_DIR)
        if not args.session:
            if args.as_json:
                print(json.dumps({'status': 'clean', 'unreviewed': [],
                                  'note': 'no dirty ledgers found'}))
            else:
                print('Gate status: CLEAN (no dirty ledgers found in state dir)')
            return

    sources = None if args.all_sources else CC_INCLUDED_SOURCES

    # Use engine for ledger loading + unreviewed computation
    dirty_entries = engine.load_scoped_dirty(STATE_DIR, args.session,
                                             included_sources=sources)
    reviewed_entries = engine.read_reviewed_ledger(STATE_DIR, args.session)

    if not dirty_entries:
        if args.as_json:
            print(json.dumps({'status': 'clean', 'unreviewed': []}))
        else:
            print('Gate status: CLEAN (no dirty entries)')
        return

    unreviewed = engine.get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        if args.as_json:
            print(json.dumps({'status': 'clear', 'unreviewed': []}))
        else:
            print('Gate status: CLEAR (all entries reviewed)')
        return

    tier = engine.determine_review_tier(unreviewed)

    if args.as_json:
        print(json.dumps({
            'status': 'blocked',
            'tier': tier,
            'unreviewed_count': len(unreviewed),
            'unreviewed': [
                {
                    'file_path': e.get('file_path', '?'),
                    'tool': e.get('tool', '?'),
                    'tier': e.get('tier', '?'),
                    'source': e.get('source', '?'),
                    'display': e.get('display', ''),
                }
                for e in unreviewed
            ],
        }, indent=2))
    else:
        print(f'Gate status: BLOCKED ({len(unreviewed)} unreviewed, tier: {tier})')
        print()
        for e in unreviewed:
            fp = e.get('file_path', '?')
            display = e.get('display', '')
            tool = e.get('tool', '?')
            t = e.get('tier', '?')
            src = e.get('source', '?')
            if display:
                print(f'  {fp}  [{display}]  (tool: {tool}, tier: {t}, source: {src})')
            else:
                print(f'  {fp}  (tool: {tool}, tier: {t}, source: {src})')


if __name__ == '__main__':
    main()
