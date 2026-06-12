#!/usr/bin/env python3
"""gate-skip: operator-only emergency skip of the review gate.

Usage:
    python3 gate-skip.py --session <session_id> --reason "description"

OPERATOR-ONLY. A model invoking gate-skip is itself a defect (D-09 class).
This is documented in CLAUDE.md as a hard rule.

Clears all unreviewed entries for the session by writing skip markers.
Writes a LOUD event-log row + metrics entry for audit trail.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT

# Import scoping logic
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

    # Load unreviewed entries
    dirty_entries = load_scoped_dirty(STATE_DIR, args.session)
    if dirty_entries:
        session_start = min(e.get('timestamp', float('inf'))
                            for e in dirty_entries)
        dirty_sids = extract_session_ids_from_dirty(
            STATE_DIR, args.session, session_start)
    else:
        dirty_sids = {args.session}
    reviewed_entries = load_scoped_reviewed(STATE_DIR, args.session, dirty_sids)
    unreviewed = get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        print('[gate-skip] Nothing to skip — gate is already clear.')
        return

    tier = determine_review_tier(unreviewed)
    count = len(unreviewed)

    # Write skip markers to the reviewed ledger
    os.makedirs(STATE_DIR, exist_ok=True)
    reviewed_path = os.path.join(STATE_DIR, f'{args.session}-reviewed.jsonl')
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    with open(reviewed_path, 'a') as f:
        for entry in unreviewed:
            fp = entry.get('file_path', '')
            marker = {
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
            }
            f.write(json.dumps(marker) + '\n')

    # Write LOUD metrics entry
    metrics_path = os.path.join(STATE_DIR, 'metrics.jsonl')
    metrics_entry = {
        'timestamp': now,
        'iso_time': iso,
        'session_id': args.session,
        'outcome': 'SKIP',
        'unreviewed_count': count,
        'tier': tier,
        'wall_ms': 0,
        'skip_reason': args.reason,
    }
    try:
        with open(metrics_path, 'a') as f:
            f.write(json.dumps(metrics_entry) + '\n')
    except OSError:
        pass

    # Write LOUD event-log row
    event_log = os.path.join(WORKSPACE_ROOT, 'second-brain', '_meta', '_event-log.md')
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
