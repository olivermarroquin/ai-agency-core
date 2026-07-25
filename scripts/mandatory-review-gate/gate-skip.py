#!/usr/bin/env python3
"""gate-skip: operator-only emergency skip of the review gate.

Usage:
    python3 gate-skip.py --session <session_id> --reason "description"
    python3 gate-skip.py --session <session_id> --reason "description" --force-deliverables

OPERATOR-ONLY. A model invoking gate-skip is itself a defect (D-09 class).
This is documented in CLAUDE.md as a hard rule.

DELIVERABLE GUARD (skip-provenance, CR-224):
Before skipping, classifies unreviewed entries as deliverable vs plumbing using
the same logic the Stop hook uses. If ANY deliverable is present, REFUSES the
skip and exits nonzero — the operator must route deliverables through the
reviewer via log-review-pass.py. The --force-deliverables flag overrides this
for true operator emergencies, writing a LOUD event-log row.

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


def classify_unreviewed(unreviewed, workspace_root=None):
    """Classify unreviewed entries as deliverables vs plumbing.

    Returns (deliverable_entries, plumbing_entries).
    Uses the same is_plumbing_exempt logic the Stop hook uses.
    """
    deliverables = []
    plumbing = []
    for e in unreviewed:
        fp = e.get('file_path', '')
        tool = e.get('tool', '')
        bash_cmd = e.get('bash_cmd', e.get('display', ''))
        annotation = engine.is_plumbing_exempt(fp, tool, bash_cmd,
                                                workspace_root)
        if annotation:
            plumbing.append(e)
        else:
            deliverables.append(e)
    return deliverables, plumbing


def main():
    parser = argparse.ArgumentParser(
        description='OPERATOR-ONLY: emergency skip of the review gate')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--reason', required=True,
                        help='Mandatory reason for skipping (audit trail)')
    parser.add_argument('--force-deliverables', action='store_true',
                        help='EMERGENCY ONLY: force-skip even when deliverables '
                             'are present. Writes a LOUD audit trail.')
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

    # --- Deliverable guard (CR-224 / skip-provenance) ---
    deliverables, plumbing = classify_unreviewed(unreviewed, WORKSPACE_ROOT)
    n_deliverables = len(deliverables)
    n_plumbing = len(plumbing)

    if n_deliverables > 0 and not args.force_deliverables:
        # REFUSE: deliverables present, no --force-deliverables
        deliverable_names = [
            e.get('display', '') or os.path.basename(e.get('file_path', ''))
            for e in deliverables
        ]
        print(
            f'[gate-skip] REFUSED: {n_deliverables} deliverable(s) present '
            f'— skip is plumbing-only.\n'
            f'[gate-skip] Deliverables:\n'
            + '\n'.join(f'  - {name}' for name in deliverable_names) +
            f'\n[gate-skip] Route to your reviewer to clear via '
            f'log-review-pass.py.\n'
            f'[gate-skip] If this is a true operator emergency, re-run with '
            f'--force-deliverables.',
            file=sys.stderr)
        sys.exit(1)

    tier = engine.determine_review_tier(unreviewed)
    count = len(unreviewed)

    # If --force-deliverables was used, write an extra-LOUD event-log row
    forced = args.force_deliverables and n_deliverables > 0

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
    metrics_extra = {'skip_reason': args.reason}
    if forced:
        metrics_extra['forced_deliverables'] = True
        metrics_extra['n_deliverables'] = n_deliverables
        metrics_extra['n_plumbing'] = n_plumbing
    engine.log_metrics(STATE_DIR, args.session,
                       'SKIP-FORCED' if forced else 'SKIP',
                       count, tier, 0, extra=metrics_extra)

    # Write LOUD event-log row
    # CR-236: validated event-log path (rejects /dev/null + out-of-workspace)
    event_log = engine.get_event_log_path()
    skipped_files = ', '.join(e.get('file_path', '?')[:60] for e in unreviewed[:5])
    if count > 5:
        skipped_files += f' (+{count - 5} more)'

    if forced:
        event_row = (
            f'\n| {iso} | gate-skip FORCED over {n_deliverables} deliverables '
            f'(tier: {tier}) | gate-skip-forced | {args.session} | '
            f'**gate-skip FORCED over {n_deliverables} deliverable(s)** — '
            f'operator used --force-deliverables to override the deliverable '
            f'guard. This is an EMERGENCY override. '
            f'Reason: {args.reason}. '
            f'Files: {skipped_files}. '
            f'AUDIT: every forced skip must be justified and investigated. |'
        )
    else:
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
    if forced:
        print(f'[gate-skip] ⚠️  FORCED over {n_deliverables} deliverable(s). '
              f'This is an emergency override — investigate.')
    print(f'[gate-skip] Event-log row written. Metrics entry written.')
    print(f'[gate-skip] WARNING: This skip is auditable. '
          f'Every skip weakens the gate\'s guarantee.')


if __name__ == '__main__':
    main()
