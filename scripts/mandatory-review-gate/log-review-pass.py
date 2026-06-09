#!/usr/bin/env python3
"""Log a review-pass marker for the mandatory pre-land review gate.

Called by the reviewer dispatch (gate-peer-reviewer + output-quality-loop) after
completing the review. Writes a review-pass entry to .claude/state/<session>-reviewed.jsonl.

INTEGRITY RULES (anti-gaming — partial, not solved):
- --evidence must be substantive (>=50 chars, not boilerplate)
- Evidence must reference at least one named check from gate-peer-reviewer
  (placeholder-sweep, leak-audit, link-resolution, value-cross-check, live-verification)
- Known boilerplate/template strings are rejected

HONEST LIMIT: The marker is model-authored. The boilerplate filter raises the bar
but cannot prevent a model from fabricating plausible-sounding evidence without
actually running the review. The operator remains the integrity backstop —
spot-check the --evidence field in .claude/state/<session>-reviewed.jsonl.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR

# Known boilerplate patterns that indicate a self-claim without real review
BOILERPLATE_PATTERNS = [
    r'^\[summary of checks run and findings\]$',
    r'^\[checks run\]',
    r'^gate-peer-reviewer: \[',  # template brackets from the block message
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

# Evidence must reference at least one real check name
REQUIRED_CHECK_KEYWORDS = [
    'placeholder',
    'leak',
    'link',
    'value',
    'cross-check',
    'live',
    'verification',
    'sweep',
    'audit',
    'resolution',
    'structural',
    'integrity',
    'syntax',
    'frontmatter',
    'YAML',
    'parse',
]


def validate_evidence(evidence: str) -> str:
    """Validate evidence is substantive, not boilerplate. Returns error message or empty string."""
    if len(evidence) < 50:
        return (f'Evidence too short ({len(evidence)} chars, need >=50). '
                'Provide specific check names, per-surface counts, and actual findings.')

    evidence_lower = evidence.lower().strip()

    for pattern in BOILERPLATE_PATTERNS:
        if re.match(pattern, evidence_lower):
            return (f'Evidence matches boilerplate pattern. '
                    'Run the actual gate-peer-reviewer checks and report real findings.')

    # Must reference at least one real check
    found_check = False
    for keyword in REQUIRED_CHECK_KEYWORDS:
        if keyword.lower() in evidence_lower:
            found_check = True
            break

    if not found_check:
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
    parser.add_argument('--evidence', required=True,
                        help='Reviewer execution evidence: specific checks run, counts, findings')
    parser.add_argument('--findings', default='',
                        help='Blocking findings description (required when verdict=BLOCKING)')
    args = parser.parse_args()

    if args.verdict == 'BLOCKING' and not args.findings:
        parser.error('--findings is required when verdict is BLOCKING')

    # Anti-gaming: validate evidence is substantive
    err = validate_evidence(args.evidence)
    if err:
        print(f'[review-gate] REJECTED: {err}', file=sys.stderr)
        sys.exit(1)

    os.makedirs(STATE_DIR, exist_ok=True)

    reviewed_path = os.path.join(STATE_DIR, f'{args.session}-reviewed.jsonl')

    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    entries = []
    for fp in args.files:
        # Normalize non-BASH paths to match dirty-ledger-track.py
        if not fp.startswith('BASH:'):
            fp = os.path.realpath(os.path.abspath(fp))
        entry = {
            'timestamp': now,
            'iso_time': iso,
            'file_path': fp,
            'verdict': args.verdict,
            'tier': args.tier,
            'gate_id': args.gate_id,
            'evidence': args.evidence,
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
