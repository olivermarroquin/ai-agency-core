#!/usr/bin/env python3
"""gate-status: show what's blocking the review gate and why.

Usage:
    python3 gate-status.py --session <session_id>
    python3 gate-status.py --session <session_id> --json

Reads the dirty/reviewed ledgers for the given session (with the same
scoping policy as the Stop hook) and reports unreviewed entries.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR

# Import scoping logic from the gate script
from importlib.util import spec_from_file_location, module_from_spec
_gate_path = os.path.join(os.path.dirname(__file__), 'mandatory-review-gate.py')
_spec = spec_from_file_location('gate', _gate_path)
_gate = module_from_spec(_spec)
_spec.loader.exec_module(_gate)

load_scoped_dirty = _gate.load_scoped_dirty
load_scoped_reviewed = _gate.load_scoped_reviewed
extract_session_ids_from_dirty = _gate.extract_session_ids_from_dirty
get_unreviewed = _gate.get_unreviewed
determine_review_tier = _gate.determine_review_tier


def main():
    parser = argparse.ArgumentParser(description='Show review gate status')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--json', action='store_true', dest='as_json',
                        help='Output as JSON')
    args = parser.parse_args()

    dirty_entries = load_scoped_dirty(STATE_DIR, args.session)

    if dirty_entries:
        session_start = min(e.get('timestamp', float('inf'))
                            for e in dirty_entries)
        dirty_sids = extract_session_ids_from_dirty(
            STATE_DIR, args.session, session_start)
    else:
        dirty_sids = {args.session}
    reviewed_entries = load_scoped_reviewed(STATE_DIR, args.session, dirty_sids)

    if not dirty_entries:
        if args.as_json:
            print(json.dumps({'status': 'clean', 'unreviewed': []}))
        else:
            print('Gate status: CLEAN (no dirty entries)')
        return

    unreviewed = get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        if args.as_json:
            print(json.dumps({'status': 'clear', 'unreviewed': []}))
        else:
            print('Gate status: CLEAR (all entries reviewed)')
        return

    tier = determine_review_tier(unreviewed)

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
