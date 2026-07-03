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
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
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
    parser.add_argument('--run-id', default=None,
                        help='Run ID (chat-slug-based) for firing-tracker verification. '
                             'Required when --reviewer-type=independent. The script '
                             'greps the firing tracker for this run ID and refuses to '
                             'log PASS if no matching row exists. (OC-17 enforcement)')
    parser.add_argument('--reviewer-session', default=None,
                        help='Session ID of the reviewer. Required when '
                             '--reviewer-type=independent. REJECTED if equal to '
                             '--session (the producer session), because sub-agents '
                             'inherit the parent session_id and cannot provide '
                             'independent review. (CR-045 / RGH-8)')
    args = parser.parse_args()

    if args.verdict == 'BLOCKING' and not args.findings:
        parser.error('--findings is required when verdict is BLOCKING')

    # CR-165 fix: --reviewer-session is REQUIRED on ALL paths.
    # Previously, --reviewer-session was only validated when
    # --reviewer-type=independent. Omitting --reviewer-type entirely
    # (defaulting to 'producer') allowed a PASS with no reviewer session,
    # bypassing RGH-15's fabricated-verdict guard entirely.
    # Now: every PASS must have a registered reviewer session, regardless
    # of reviewer-type. No code path logs a PASS without it.
    if not args.reviewer_session:
        print('[review-gate] REJECTED: --reviewer-session is required. '
              'Every review-pass marker must identify the reviewer session '
              '(a valid UUID v4, registered via register-reviewer-session.py, '
              'distinct from the producer session). A verdict with no '
              'reviewer-session is rejected. (CR-165 / RGH-18/19-CAL-2)',
              file=sys.stderr)
        sys.exit(1)

    # Validate reviewer-session format: must be a valid UUID v4
    import re as _re
    _UUID4_RE = _re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        _re.IGNORECASE)
    if not _UUID4_RE.match(args.reviewer_session):
        print(f'[review-gate] REJECTED: --reviewer-session '
              f'("{args.reviewer_session}") is not a valid UUID v4. '
              f'(CR-165 / RGH-18/19-CAL-2)',
              file=sys.stderr)
        sys.exit(1)

    # Reviewer session must differ from producer session
    if args.reviewer_session == args.session:
        print(f'[review-gate] REJECTED: --reviewer-session equals --session '
              f'("{args.session}"). The reviewer must be a separate session. '
              f'(CR-165 / RGH-18/19-CAL-2)',
              file=sys.stderr)
        sys.exit(1)

    # Reviewer session must be registered (role marker exists)
    _reviewer_marker = os.path.join(STATE_DIR, f'{args.reviewer_session}-role.json')
    if not os.path.isfile(_reviewer_marker):
        print(f'[review-gate] REJECTED: --reviewer-session '
              f'("{args.reviewer_session}") has no registered role marker '
              f'at {_reviewer_marker}. Register via '
              f'register-reviewer-session.py first. '
              f'(CR-165 / RGH-18/19-CAL-2)',
              file=sys.stderr)
        sys.exit(1)

    # Validate the marker contents
    try:
        with open(_reviewer_marker, 'r') as _mf:
            _marker_data = json.load(_mf)
        if _marker_data.get('role') != 'reviewer':
            print(f'[review-gate] REJECTED: role marker for '
                  f'"{args.reviewer_session}" has role='
                  f'{_marker_data.get("role")!r}, expected "reviewer". '
                  f'(CR-165 / RGH-18/19-CAL-2)',
                  file=sys.stderr)
            sys.exit(1)
    except (json.JSONDecodeError, OSError) as _e:
        print(f'[review-gate] REJECTED: could not read role marker '
              f'{_reviewer_marker}: {_e}. (CR-165 / RGH-18/19-CAL-2)',
              file=sys.stderr)
        sys.exit(1)

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

    # CR-045 / RGH-8: additional independent-reviewer-specific checks.
    # The universal reviewer-session checks (UUID v4, registered, != producer)
    # are now enforced above for ALL reviewer types (CR-165 fix).
    # The independent-type-specific checks below validate the verdict schema
    # and the firing-tracker rows.
    if args.reviewer_type == 'independent':
        # RGH-15A check 3: reviewer session should have dirty-ledger activity
        # (proof it actually ran verification). WARNING only — not blocking.
        reviewer_ledger = os.path.join(
            STATE_DIR, f'{args.reviewer_session}-dirty.jsonl')
        if not os.path.isfile(reviewer_ledger):
            print(f'[review-gate] WARNING: reviewer session '
                  f'"{args.reviewer_session}" has no dirty-ledger activity. '
                  f'A registered-but-inert session may indicate a fabricated '
                  f'identity. The verdict is accepted but flagged for operator '
                  f'spot-check. (RGH-15)',
                  file=sys.stderr)

    # OC-17 enforcement: when independent reviewer closes the gate,
    # verify firing-tracker rows exist for this run ID before allowing PASS.
    if args.reviewer_type == 'independent':
        if not args.run_id:
            print('[review-gate] REJECTED: --run-id is required when '
                  '--reviewer-type=independent. The independent reviewer must '
                  'supply its run ID so we can verify firing-tracker rows exist.',
                  file=sys.stderr)
            sys.exit(1)

        # CR-049 / RGH-8: workspace-root-anchored path, independent of cwd.
        # Uses WORKSPACE_ROOT from _paths.py (derived from this script's
        # location on disk, not cwd). Fail-CLOSED: if the tracker can't be
        # found or has no matching rows, REJECT — never proceed.
        firing_tracker_path = os.path.join(
            WORKSPACE_ROOT, 'second-brain', '_meta', 'handoffs',
            '_review-skill-firing-tracker.md'
        )
        if not os.path.isfile(firing_tracker_path):
            print(f'[review-gate] REJECTED: firing tracker not found at '
                  f'{firing_tracker_path}. Cannot verify reviewer-authored '
                  f'rows exist. A verification step that cannot run must '
                  f'block, not proceed. (CR-049 / RGH-8, fail-closed)',
                  file=sys.stderr)
            sys.exit(1)

        with open(firing_tracker_path, 'r') as ft:
            tracker_content = ft.read()
        if args.run_id not in tracker_content:
            print(
                '[review-gate] REJECTED: No firing-tracker rows found '
                f'for run ID "{args.run_id}".\n'
                'ERROR: The independent reviewer must author firing-tracker '
                'rows before clearing the gate.\n'
                'See Closing Protocol Step 3b in '
                '_review-skill-firing-tracker.md.',
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

    # Stamp reviewer_type + reviewer_session on each marker (RGH-5 + RGH-8)
    for m in markers:
        m['reviewer_type'] = args.reviewer_type
        if args.reviewer_session:
            m['reviewer_session'] = args.reviewer_session

    engine.append_reviewed_entries(STATE_DIR, args.session, markers)

    print(f'[review-gate] Logged {args.verdict} for {len(markers)} file(s) '
          f'(tier: {args.tier}, gate: {args.gate_id}, '
          f'reviewer: {args.reviewer_type})')
    if args.findings:
        print(f'[review-gate] Findings: {args.findings}')


if __name__ == '__main__':
    main()
