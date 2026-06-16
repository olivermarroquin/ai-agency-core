#!/usr/bin/env python3
"""OC-12: Per-deliverable existence + non-stub check.

Deterministic Layer-A check ($0, no LLM, exhaustive).
For each deliverable row in a DoD manifest, verify:
  - The path/glob resolves to at least one file on disk
  - Each resolved file is non-empty (size > 0)
  - Each resolved file contains no placeholder/stub body

Catches: C-11 (fabricated SingleFile), C-14 (empty form fields),
         C-18/C-19 (unshipped criteria).

Usage:
  python3 oc-12-per-deliverable-existence.py --deliverables '<json array>'

  Each deliverable is a JSON object:
    {"name": "...", "path": "...", "assertion": "exists|non-empty|non-stub"}

  Output: JSON with per-deliverable PASS/FAIL + overall verdict.
"""

import argparse
import glob
import json
import os
import re
import sys


# Placeholder patterns that indicate stub content (case-insensitive).
# These target actual placeholder VALUES in deliverable content, not code
# references. The check_non_stub function filters out code lines separately.
PLACEHOLDER_PATTERNS = [
    r'\[FILL[_\s]',       # [FILL_ME], [FILL ...]
    r'\{FILL\}',          # {FILL}
    r'\[INSERT\b',        # [INSERT ...]
    r'\[TODO[:\]\s]',     # [TODO], [TODO: ...] (bracketed form = content placeholder)
    r'\{\{[^}]+\}\}',     # {{handlebars}} templates (non-empty)
    r'Lorem ipsum',       # Lorem ipsum filler text
]

PLACEHOLDER_RE = re.compile('|'.join(PLACEHOLDER_PATTERNS), re.IGNORECASE)

# Lines matching these patterns are code/comments, not content — skip them
CODE_LINE_SKIP_PATTERNS = re.compile(
    r'^\s*#|'           # Python comments
    r"^\s*r'|"          # Raw string (regex definition)
    r'^\s*r"|'          # Raw string (regex definition)
    r'PLACEHOLDER_|'    # Variable names referencing placeholders
    r'placeholder.?pattern|'  # Code discussing patterns
    r'placeholder.?re'        # Regex variable names
    , re.IGNORECASE
)


def resolve_paths(path_spec: str, base_dir: str = None) -> list:
    """Resolve a path or glob to actual files on disk."""
    if base_dir:
        path_spec = os.path.join(base_dir, path_spec)

    path_spec = os.path.expanduser(path_spec)

    # Try glob first (handles wildcards)
    matches = glob.glob(path_spec, recursive=True)
    if matches:
        return sorted(matches)

    # If no glob match, check if it's a literal path
    if os.path.exists(path_spec):
        return [path_spec]

    return []


def check_non_empty(filepath: str) -> tuple:
    """Check if file is non-empty. Returns (passed, detail)."""
    if os.path.isdir(filepath):
        contents = os.listdir(filepath)
        if not contents:
            return False, f'directory is empty: {filepath}'
        return True, f'directory has {len(contents)} entries'

    size = os.path.getsize(filepath)
    if size == 0:
        return False, f'file is empty (0 bytes): {filepath}'
    return True, f'file is {size} bytes'


def check_non_stub(filepath: str) -> tuple:
    """Check if file has no placeholder/stub content. Returns (passed, detail)."""
    if os.path.isdir(filepath):
        return True, 'directory (stub check skipped)'

    try:
        with open(filepath, 'r', errors='replace') as f:
            content = f.read(65536)  # First 64KB is enough
    except (OSError, IOError) as e:
        return False, f'cannot read file: {e}'

    # Check line by line, skipping code/comment lines
    lines = content.splitlines()
    real_matches = []
    for line_num, line in enumerate(lines, 1):
        # Skip lines that are clearly code definitions, not content
        if CODE_LINE_SKIP_PATTERNS.search(line):
            continue
        for m in PLACEHOLDER_RE.finditer(line):
            real_matches.append((line_num, m.group(), line.strip()[:80]))

    if real_matches:
        details = []
        for line_num, match_text, line_text in real_matches[:5]:
            details.append(f'  L{line_num}: "{match_text}" in: {line_text}')
        return False, f'{len(real_matches)} placeholder(s) found:\n' + '\n'.join(details)

    return True, 'no placeholders found'


def check_deliverable(deliverable: dict, base_dir: str = None) -> dict:
    """Check a single deliverable against its assertion.

    Returns a result dict with: name, path, assertion, verdict, details.
    """
    name = deliverable.get('name', '<unnamed>')
    path_spec = deliverable.get('path', '')
    assertion = deliverable.get('assertion', 'exists').lower().strip()

    result = {
        'name': name,
        'path': path_spec,
        'assertion': assertion,
        'verdict': 'FAIL',
        'details': '',
    }

    # Step 1: Resolve paths
    resolved = resolve_paths(path_spec, base_dir)
    if not resolved:
        result['details'] = f'path does not exist: {path_spec}'
        return result

    result['resolved_count'] = len(resolved)

    # Step 2: Check based on assertion type
    if assertion == 'exists':
        result['verdict'] = 'PASS'
        result['details'] = f'{len(resolved)} file(s) found'
        return result

    # non-empty: exists + every file non-empty
    if assertion in ('non-empty', 'non-stub'):
        failures = []
        for fp in resolved:
            passed, detail = check_non_empty(fp)
            if not passed:
                failures.append(detail)

        if failures:
            result['details'] = f'{len(failures)} empty file(s):\n' + '\n'.join(failures)
            return result

        # non-stub: exists + non-empty + no placeholders
        if assertion == 'non-stub':
            stub_failures = []
            for fp in resolved:
                passed, detail = check_non_stub(fp)
                if not passed:
                    stub_failures.append(f'{fp}: {detail}')

            if stub_failures:
                result['details'] = f'{len(stub_failures)} file(s) with stubs:\n' + '\n'.join(stub_failures)
                return result

        result['verdict'] = 'PASS'
        result['details'] = f'{len(resolved)} file(s), all non-{"stub" if assertion == "non-stub" else "empty"}'
        return result

    # Fallback for unrecognized assertions — check exists only
    result['verdict'] = 'PASS'
    result['details'] = f'{len(resolved)} file(s) found (assertion "{assertion}" deferred to specialized check)'
    return result


def main():
    parser = argparse.ArgumentParser(description='OC-12: Per-deliverable existence + non-stub check')
    parser.add_argument('--deliverables', required=True,
                        help='JSON array of deliverable objects [{name, path, assertion}, ...]')
    parser.add_argument('--base-dir', default=None,
                        help='Base directory for resolving relative paths')
    parser.add_argument('--json', action='store_true', default=True,
                        help='Output JSON (default)')
    args = parser.parse_args()

    try:
        deliverables = json.loads(args.deliverables)
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON: {e}', 'verdict': 'FAIL'}))
        sys.exit(1)

    if not isinstance(deliverables, list):
        print(json.dumps({'error': 'deliverables must be a JSON array', 'verdict': 'FAIL'}))
        sys.exit(1)

    results = []
    for d in deliverables:
        results.append(check_deliverable(d, args.base_dir))

    passed = sum(1 for r in results if r['verdict'] == 'PASS')
    failed = sum(1 for r in results if r['verdict'] == 'FAIL')

    output = {
        'check': 'OC-12',
        'check_name': 'per-deliverable-existence',
        'verdict': 'PASS' if failed == 0 else 'FAIL',
        'summary': f'{passed}/{len(results)} deliverables passed',
        'results': results,
    }

    print(json.dumps(output, indent=2))
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
