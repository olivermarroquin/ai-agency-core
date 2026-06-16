#!/usr/bin/env python3
"""gate-skip: operator-only emergency skip of the review gate.

Usage:
    python3 gate-skip.py --session <session_id> --reason "description"

OPERATOR-ONLY. A model invoking gate-skip is itself a defect (D-09 class).
This is documented in CLAUDE.md as a hard rule.

Clears all unreviewed entries for the session by writing skip markers.
Writes a LOUD event-log row + metrics entry for audit trail.
Uses engine.py for substrate-agnostic ledger operations.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
import engine

# CC-scoped by default (same as Stop hook)
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})


def main():
    parser = argparse.ArgumentParser(
        description='OPERATOR-ONLY: emergency skip of the review gate')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--reason', required=True,
                        help='Mandatory reason for skipping (audit trail)')
    args = parser.parse_args()

    if len(args.reason.strip()) < 10:
        print('[gate-skip] REJECTED: --reason must be at least 10 characters.',
              file=sys.stderr)
        sys.exit(1)

    # Load unreviewed entries (own-ledger-only scoping, RGH-1.6)
    dirty_entries = engine.load_scoped_dirty(STATE_DIR, args.session,
                                             included_sources=CC_INCLUDED_SOURCES)
    reviewed_entries = engine.read_reviewed_ledger(STATE_DIR, args.session)
    unreviewed = engine.get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        print('[gate-skip] Nothing to skip — gate is already clear.')
        return

    tier = engine.determine_review_tier(unreviewed)
    count = len(unreviewed)

    # Write skip markers to the reviewed ledger
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    skip_markers = []
    for entry in unreviewed:
        fp = entry.get('file_path', '')
        skip_markers.append({
            'timestamp': now,
            'iso_time': iso,
            'file_path': fp,
            'verdict': 'SKIP',
            'tier': entry.get('tier', 'unknown'),
            'gate_id': 'G-skip',
            'evidence': f'OPERATOR SKIP: {args.reason}',
            'verdict_file': None,
            'verdict_data': None,
            'findings': None,
            'skip': True,
            'skip_reason': args.reason,
        })

    engine.append_reviewed_entries(STATE_DIR, args.session, skip_markers)

    # Write LOUD metrics entry
    engine.log_metrics(STATE_DIR, args.session, 'SKIP', count, tier, 0,
                       extra={'skip_reason': args.reason})

    # Write LOUD event-log row
    # REVIEW_GATE_EVENT_LOG env override for test isolation (default unchanged)
    event_log = os.environ.get(
        'REVIEW_GATE_EVENT_LOG',
        os.path.join(WORKSPACE_ROOT, 'second-brain', '_meta', '_event-log.md')
    )
    skipped_files = ', '.join(e.get('file_path', '?')[:60] for e in unreviewed[:5])
    if count > 5:
        skipped_files += f' (+{count - 5} more)'
    event_row = (
        f'\n| {iso} | GATE SKIP ({count} files, tier: {tier}) | '
        f'gate-skip | {args.session} | '
        f'**OPERATOR GATE SKIP** — {count} unreviewed artifact(s) skipped '
        f'without review. Reason: {args.reason}. '
        f'Files: {skipped_files}. '
        f'This is an audit event — every skip must be justified. |'
    )
    try:
        with open(event_log, 'a') as f:
            f.write(event_row)
    except OSError as e:
        print(f'[gate-skip] WARNING: could not write event-log row: {e}',
              file=sys.stderr)

    print(f'[gate-skip] SKIPPED {count} unreviewed artifact(s) '
          f'(tier: {tier})')
    print(f'[gate-skip] Reason: {args.reason}')
    print(f'[gate-skip] Event-log row written. Metrics entry written.')
    print(f'[gate-skip] WARNING: This skip is auditable. '
          f'Every skip weakens the gate\'s guarantee.')


if __name__ == '__main__':
    main()
