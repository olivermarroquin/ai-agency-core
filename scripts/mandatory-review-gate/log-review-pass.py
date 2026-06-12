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

This raises the bar from "type 50 chars of plausible text" to "produce a full,
schema-valid reviewer return contract." The forgery surface shrinks from a
sentence to a complete structured artifact, and the operator's spot-check target
becomes a structured file instead of prose.

HONEST LIMIT: The verdict file is still model-authored until the reviewer runs
as an isolated process (Phase 2/3 option). The operator remains the integrity
backstop — spot-check verdict files in <STATE_DIR>/verdicts/.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR

# --- Verdict file schema ---

REQUIRED_VERDICT_KEYS = {'verdict', 'checks_run'}
VALID_VERDICTS = {'PASS', 'BLOCKING', 'FAIL'}

# checks_run entries must each have at least a 'name' field
REQUIRED_CHECK_FIELDS = {'name'}

# For full-tier reviews, these check names (or prefixes) must appear
FULL_TIER_REQUIRED_CHECKS = {
    'ground-truth-cross-check',
    'value-cross-check',
}

# Known boilerplate patterns that indicate a self-claim without real review
# (kept as a secondary filter on the --evidence field when provided)
BOILERPLATE_PATTERNS = [
    r'^\[summary of checks run and findings\]$',
    r'^\[checks run\]',
    r'^gate-peer-reviewer: \[',
    r'^placeholder evidence',
    r'^review passed?$',
    r'^all checks? pass(ed)?\.?$',
    r'^PASS\.?$',
    r'^no (issues|findings|problems)',
    r'^clean\.?$',
    r'^ok\.?$',
    r'^n/a\.?$',
    r'^reviewed\.?$',
    r'^lgtm\.?$',
]


def validate_verdict_file(path: str, tier: str) -> tuple:
    """Validate a verdict file against the reviewer return contract schema.

    Returns (parsed_data, error_message). error_message is empty on success.
    """
    if not os.path.exists(path):
        return None, f'Verdict file does not exist: {path}'

    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f'Verdict file is not valid JSON: {e}'

    if not isinstance(data, dict):
        return None, 'Verdict file must be a JSON object'

    # Required keys
    missing = REQUIRED_VERDICT_KEYS - set(data.keys())
    if missing:
        return None, f'Verdict file missing required keys: {missing}'

    # Verdict enum
    if data['verdict'] not in VALID_VERDICTS:
        return None, (f'Verdict must be one of {VALID_VERDICTS}, '
                      f'got: {data["verdict"]}')

    # checks_run must be a non-empty list
    checks = data.get('checks_run', [])
    if not isinstance(checks, list) or len(checks) == 0:
        return None, 'checks_run must be a non-empty list'

    # Each check must have required fields
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            return None, f'checks_run[{i}] must be a dict, got {type(check).__name__}'
        check_missing = REQUIRED_CHECK_FIELDS - set(check.keys())
        if check_missing:
            return None, f'checks_run[{i}] missing fields: {check_missing}'

    # Tier enforcement: full-tier reviews must include ground-truth checks
    if tier == 'full':
        check_names = {c.get('name', '') for c in checks}
        # Discard empty names — they match everything via startswith('')
        check_names.discard('')
        # Check if any check name starts with a required prefix.
        # One-directional only: name.startswith(req), NOT req.startswith(name).
        # The reverse direction was gameable — a name of "" or "v" would
        # satisfy req.startswith(name) for "value-cross-check" (D-23).
        has_full = False
        for req in FULL_TIER_REQUIRED_CHECKS:
            for name in check_names:
                if name.startswith(req):
                    has_full = True
                    break
            if has_full:
                break
        if not has_full:
            return None, (f'Full-tier review requires at least one of '
                          f'{FULL_TIER_REQUIRED_CHECKS} in checks_run. '
                          f'Got: {check_names}. A full entry cleared by a '
                          f'fast-path-shaped verdict is rejected.')

    return data, ''


def derive_evidence(verdict_data: dict) -> str:
    """Derive a human-readable evidence string from the verdict file data."""
    checks = verdict_data.get('checks_run', [])
    check_names = [c.get('name', '?') for c in checks]
    catches = verdict_data.get('catches', [])
    verdict = verdict_data.get('verdict', '?')

    parts = [f'verdict: {verdict}']
    parts.append(f'checks: {", ".join(check_names)}')
    if catches:
        parts.append(f'catches: {len(catches)}')
        for c in catches[:3]:
            if isinstance(c, dict):
                parts.append(f'  - {c.get("surface", "?")} {c.get("severity", "?")}: {c.get("description", "?")[:80]}')
            else:
                parts.append(f'  - {str(c)[:80]}')
    else:
        parts.append('catches: 0')

    cost = verdict_data.get('cost_usd')
    if cost is not None:
        parts.append(f'cost: ${cost:.4f}')

    return '; '.join(parts)


def validate_evidence_legacy(evidence: str) -> str:
    """Legacy evidence validation (for --evidence without verdict file).
    Returns error message or empty string."""
    if len(evidence) < 50:
        return (f'Evidence too short ({len(evidence)} chars, need >=50). '
                'Provide specific check names, per-surface counts, and actual findings.')

    evidence_lower = evidence.lower().strip()

    for pattern in BOILERPLATE_PATTERNS:
        if re.match(pattern, evidence_lower):
            return ('Evidence matches boilerplate pattern. '
                    'Run the actual gate-peer-reviewer checks and report real findings.')

    required_keywords = [
        'placeholder', 'leak', 'link', 'value', 'cross-check', 'live',
        'verification', 'sweep', 'audit', 'resolution', 'structural',
        'integrity', 'syntax', 'frontmatter', 'yaml', 'parse',
    ]
    if not any(kw in evidence_lower for kw in required_keywords):
        return ('Evidence does not reference any gate-peer-reviewer check. '
                'Must mention at least one of: placeholder-sweep, leak-audit, '
                'link-resolution, value-cross-check, live-verification, '
                'structural-integrity, frontmatter/YAML parse.')

    return ''


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
    verdict_data, err = validate_verdict_file(args.verdict_file, args.tier)
    if err:
        print(f'[review-gate] REJECTED: verdict file invalid — {err}',
              file=sys.stderr)
        sys.exit(1)

    # Derive evidence from verdict file; allow --evidence to supplement
    derived_evidence = derive_evidence(verdict_data)
    if args.evidence:
        evidence = f'{derived_evidence} | operator-note: {args.evidence}'
    else:
        evidence = derived_evidence

    os.makedirs(STATE_DIR, exist_ok=True)

    reviewed_path = os.path.join(STATE_DIR, f'{args.session}-reviewed.jsonl')

    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    entries = []
    for fp in args.files:
        if not fp.startswith('BASH:'):
            fp = os.path.realpath(os.path.abspath(fp))
        entry = {
            'timestamp': now,
            'iso_time': iso,
            'file_path': fp,
            'verdict': args.verdict,
            'tier': args.tier,
            'gate_id': args.gate_id,
            'evidence': evidence,
            'verdict_file': os.path.realpath(args.verdict_file),
            'verdict_data': verdict_data,
            'findings': args.findings if args.findings else None,
        }
        entries.append(entry)

    with open(reviewed_path, 'a') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')

    print(f'[review-gate] Logged {args.verdict} for {len(entries)} file(s) '
          f'(tier: {args.tier}, gate: {args.gate_id})')
    if args.findings:
        print(f'[review-gate] Findings: {args.findings}')


if __name__ == '__main__':
    main()
