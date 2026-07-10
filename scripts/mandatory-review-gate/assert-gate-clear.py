#!/usr/bin/env python3
"""assert-gate-clear: commit guard that refuses to proceed if the gate is blocked.

Usage (in .commit.sh):
    python3 .../assert-gate-clear.py --session <session_id> || exit 1
    git add ... && git commit ... && git push

Exits 0 if the session's dirty ledger has zero unreviewed entries.
Exits 1 and prints the unreviewed list if any entries remain.

This prevents premature commits — a .commit.sh that starts with this guard
will fail safe with a clear message instead of committing unreviewed work.

Created by skip-provenance fix (CR-225).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR
import engine

# CC-scoped by default (same as Stop hook and gate-skip)
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})


def main():
    parser = argparse.ArgumentParser(
        description='Assert the review gate is clear before committing')
    parser.add_argument('--session', required=True, help='Session ID')
    args = parser.parse_args()

    dirty_entries = engine.load_scoped_dirty(STATE_DIR, args.session,
                                             included_sources=CC_INCLUDED_SOURCES)
    reviewed_entries = engine.read_reviewed_ledger(STATE_DIR, args.session)
    unreviewed = engine.get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        print('[assert-gate-clear] Gate is clear — commit may proceed.')
        return

    count = len(unreviewed)
    print(f'[assert-gate-clear] BLOCKED: {count} unreviewed entry/entries '
          f'remain. Commit refused.', file=sys.stderr)
    for e in unreviewed:
        fp = e.get('file_path', '')
        if fp.startswith('BASH:'):
            label = e.get('display', fp)[:60]
        else:
            label = os.path.basename(fp)
        tier = e.get('tier', '?')
        print(f'  - {label} ({tier})', file=sys.stderr)
    print(f'\n[assert-gate-clear] Clear the gate via log-review-pass.py '
          f'or operator gate-skip before committing.', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
