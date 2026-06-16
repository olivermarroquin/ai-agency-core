#!/usr/bin/env python3
"""Log a review-pass marker for the mandatory pre-land review gate.

Called by the reviewer dispatch (gate-peer-reviewer + output-quality-loop) after
completing the review. Writes a review-pass entry to <STATE_DIR>/<session>-reviewed.jsonl.

INTEGRITY — VERDICT-FILE REQUIRED (v2, RGH-1):
The marker is backed by a --verdict-file: a JSON file containing the structured
return contract from gate-peer-reviewer (verdict, checks_run, catches, cost_usd).
log-review-pass validates the file parses correctly with required keys present,
verdict in enum, checks_run non-empty. A marker with no valid verdict file is
rejected. The --evidence flag is derived from the verdict file automatically.

HONEST LIMIT: The verdict file is still model-authored until the reviewer runs
as an isolated process (Phase 2/3 option). The operator remains the integrity
backstop — spot-check verdict files in <STATE_DIR>/verdicts/.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR
import engine


def main():
    parser = argparse.ArgumentParser(description='Log review-pass markers')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--files', nargs='+', required=True,
                        help='Reviewed file paths (exact keys from the block message)')
    parser.add_argument('--verdict', required=True, choices=['PASS', 'BLOCKING'],
                        help='Review verdict')
    parser.add_argument('--tier', required=True, choices=['fast-path', 'full'],
                        help='Review tier that was executed')
    parser.add_argument('--gate-id', required=True,
                        help='Gate type used (e.g. G-default, G-scaffold, G-publish)')
    parser.add_argument('--verdict-file', default=None,
                        help='Path to the verdict JSON file from gate-peer-reviewer '
                             '(required; --evidence alone no longer accepted)')
    parser.add_argument('--evidence', default=None,
                        help='Additional evidence context (optional with --verdict-file; '
                             'derived from verdict file if omitted)')
    parser.add_argument('--findings', default='',
                        help='Blocking findings description (required when verdict=BLOCKING)')
    parser.add_argument('--reviewer-type', default='producer',
                        choices=['producer', 'independent'],
                        help='Who authored this review: producer (self-review) or '
                             'independent (separate adversarial reviewer). '
                             'Full-tier items require reviewer-type=independent '
                             'to clear the gate. (RGH-5)')
    args = parser.parse_args()

    if args.verdict == 'BLOCKING' and not args.findings:
        parser.error('--findings is required when verdict is BLOCKING')

    # Verdict file is now required — reject bare --evidence
    if not args.verdict_file:
        print('[review-gate] REJECTED: --verdict-file is required. '
              'Bare --evidence is no longer accepted (RGH-1: kill the self-claim). '
              'gate-peer-reviewer must emit a verdict file and pass its path here.',
              file=sys.stderr)
        sys.exit(1)

    # Validate the verdict file
    verdict_data, err = engine.validate_verdict_file(args.verdict_file, args.tier)
    if err:
        print(f'[review-gate] REJECTED: verdict file invalid — {err}',
              file=sys.stderr)
        sys.exit(1)

    # RGH-5: independent reviewer-type requires the verdict file to carry
    # the independent reviewer schema (fields only the mandate/dispatch produce).
    # A producer calling --reviewer-type independent with a self-authored verdict
    # is rejected here — the verdict must prove it came from the mandate flow.
    if args.reviewer_type == 'independent':
        indie_err = engine.validate_independent_verdict(verdict_data)
        if indie_err:
            print(f'[review-gate] REJECTED: --reviewer-type independent but '
                  f'verdict file lacks independent reviewer schema — {indie_err}. '
                  f'Only the independent reviewer dispatch/mandate produces '
                  f'verdicts with these fields. A producer self-clearing with '
                  f'--reviewer-type independent is a D-09 class defect.',
                  file=sys.stderr)
            sys.exit(1)

    # Derive evidence from verdict file; allow --evidence to supplement
    derived_evidence = engine.derive_evidence(verdict_data)
    if args.evidence:
        evidence = f'{derived_evidence} | operator-note: {args.evidence}'
    else:
        evidence = derived_evidence

    # Build and persist review markers
    markers = engine.build_review_markers(
        file_paths=args.files,
        verdict=args.verdict,
        tier=args.tier,
        gate_id=args.gate_id,
        evidence=evidence,
        verdict_file=args.verdict_file,
        verdict_data=verdict_data,
        findings=args.findings if args.findings else None,
    )

    # Stamp reviewer_type on each marker (RGH-5: producer-isolation)
    for m in markers:
        m['reviewer_type'] = args.reviewer_type

    engine.append_reviewed_entries(STATE_DIR, args.session, markers)

    print(f'[review-gate] Logged {args.verdict} for {len(markers)} file(s) '
          f'(tier: {args.tier}, gate: {args.gate_id}, '
          f'reviewer: {args.reviewer_type})')
    if args.findings:
        print(f'[review-gate] Findings: {args.findings}')


if __name__ == '__main__':
    main()
