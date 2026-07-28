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

    # CR-244: close-coordination fast-path.
    # If all unreviewed entries are close-coordination surfaces and all
    # deliverables are PASSed, auto-clear them.
    if unreviewed:
        from _paths import WORKSPACE_ROOT as _ws
        all_coord = all(
            engine.is_close_coordination_entry(e, _ws) for e in unreviewed)
        if all_coord and engine.all_deliverables_passed(
                dirty_entries, reviewed_entries, _ws):
            cleared, _ = engine.try_close_coordination_auto_clear(
                unreviewed, args.session, STATE_DIR, _ws)
            if cleared:
                unreviewed = []

    if not unreviewed:
        print('[assert-gate-clear] Gate is clear — commit may proceed.')
        return

    count = len(unreviewed)
    tier = engine.determine_review_tier(unreviewed)
    print(f'[assert-gate-clear] BLOCKED: {count} unreviewed entry/entries '
          f'remain. Commit refused.', file=sys.stderr)
    for e in unreviewed:
        fp = e.get('file_path', '')
        if fp.startswith('BASH:'):
            label = e.get('display', fp)[:60]
        else:
            label = os.path.basename(fp)
        t = e.get('tier', '?')
        print(f'  - {label} ({t})', file=sys.stderr)

    # CR-256: print full requirements contract on rejection
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')
    files_argv = engine.format_files_argv(unreviewed)
    operator_clear_script = os.path.join(script_dir, 'operator-clear.py')
    skip_script = os.path.join(script_dir, 'gate-skip.py')
    print(f'\n[assert-gate-clear] To clear the gate:', file=sys.stderr)
    print(f'  Option 1 — reviewer log-review-pass:', file=sys.stderr)
    print(f'    python3 {log_script} --session {args.session} '
          f'--files {files_argv} --verdict PASS --tier {tier} '
          f'--gate-id G-default --verdict-file <path> '
          f'--reviewer-session <UUID> --reviewer-type independent '
          f'--run-id <chat-slug>', file=sys.stderr)
    print(f'  Requirements: --reviewer-session must be a UUIDv4, '
          f'registered via register-reviewer-session.py, '
          f'distinct from --session.', file=sys.stderr)
    print(f'  Option 2 — operator-dispatched (CR-256, one command):', file=sys.stderr)
    print(f'    python3 {operator_clear_script} --session {args.session} '
          f'--files {files_argv} --verdict PASS --tier {tier} '
          f'--gate-id G-default', file=sys.stderr)
    print(f'  Option 3 — operator gate-skip (plumbing-only):', file=sys.stderr)
    print(f'    python3 {skip_script} --session {args.session} '
          f'--reason "description"', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
