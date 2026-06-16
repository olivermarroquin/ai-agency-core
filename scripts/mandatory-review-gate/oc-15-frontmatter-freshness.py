#!/usr/bin/env python3
"""OC-15: Frontmatter-freshness check.

Deterministic Layer-A check ($0, no LLM, exhaustive).
For every file in the dirty-ledger that carries an `updated:` YAML
frontmatter field, assert updated == today's date.

Catches: C-24 (changelog/coverage-matrix/toolkit-reuse-map stale after edit).

Usage:
  python3 oc-15-frontmatter-freshness.py --ledger <path-to-dirty.jsonl> \
    [--date YYYY-MM-DD]

  If --date is omitted, uses today's date.
  Output: JSON with per-file PASS/FAIL + overall verdict.
"""

import argparse
import json
import os
import re
import sys
import time


def extract_frontmatter_updated(filepath: str) -> tuple:
    """Extract the `updated:` value from YAML frontmatter.

    Returns (date_str | None, error_str).
    None date means no frontmatter or no updated: field (not an error for OC-15).
    """
    if not os.path.isfile(filepath):
        return None, f'file does not exist: {filepath}'

    try:
        with open(filepath, 'r', errors='replace') as f:
            content = f.read(4096)  # Frontmatter is always at the top
    except (OSError, IOError) as e:
        return None, f'cannot read: {e}'

    # Check for YAML frontmatter delimiters
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not fm_match:
        return None, ''  # No frontmatter — not a defect, just skip

    fm_text = fm_match.group(1)

    # Look for updated: field
    updated_match = re.search(r'^updated:\s*(.+)$', fm_text, re.MULTILINE)
    if not updated_match:
        return None, ''  # No updated: field — skip

    raw_value = updated_match.group(1).strip()
    # Strip quotes if present
    raw_value = raw_value.strip("'\"")

    return raw_value, ''


def load_dirty_ledger(ledger_path: str) -> list:
    """Load file paths from a dirty-ledger JSONL file.

    Returns deduplicated list of file paths (excluding BASH: entries).
    """
    paths = set()
    if not os.path.isfile(ledger_path):
        return []

    with open(ledger_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            fp = entry.get('file_path', '')
            # Skip Bash entries (BASH:<hash>) and review-gate state files
            if fp.startswith('BASH:'):
                continue
            if '/.review-gate/' in fp:
                continue
            if fp:
                paths.add(fp)

    return sorted(paths)


def check_freshness(filepath: str, expected_date: str) -> dict:
    """Check a single file's frontmatter freshness.

    Returns {file, verdict, updated_value, details}.
    """
    result = {
        'file': filepath,
        'verdict': 'SKIP',
        'updated_value': None,
        'details': '',
    }

    date_str, err = extract_frontmatter_updated(filepath)

    if err:
        result['verdict'] = 'FAIL'
        result['details'] = err
        return result

    if date_str is None:
        result['verdict'] = 'SKIP'
        result['details'] = 'no updated: field in frontmatter'
        return result

    result['updated_value'] = date_str

    if date_str == expected_date:
        result['verdict'] = 'PASS'
        result['details'] = f'updated: {date_str} matches today'
    else:
        result['verdict'] = 'FAIL'
        result['details'] = f'updated: {date_str} != today ({expected_date})'

    return result


def main():
    parser = argparse.ArgumentParser(description='OC-15: Frontmatter-freshness check')
    parser.add_argument('--ledger', default=None,
                        help='Path to the dirty-ledger JSONL file (required unless --files given)')
    parser.add_argument('--date', default=None,
                        help='Expected date (YYYY-MM-DD). Default: today')
    parser.add_argument('--files', nargs='*', default=None,
                        help='Explicit file list (overrides --ledger)')
    args = parser.parse_args()

    if not args.files and not args.ledger:
        parser.error('--ledger is required when --files is not provided')

    expected_date = args.date or time.strftime('%Y-%m-%d')

    if args.files:
        file_paths = args.files
    else:
        file_paths = load_dirty_ledger(args.ledger)

    if not file_paths:
        output = {
            'check': 'OC-15',
            'check_name': 'frontmatter-freshness',
            'verdict': 'PASS',
            'summary': 'no files in dirty-ledger (nothing to check)',
            'results': [],
        }
        print(json.dumps(output, indent=2))
        sys.exit(0)

    results = []
    for fp in file_paths:
        results.append(check_freshness(fp, expected_date))

    checked = [r for r in results if r['verdict'] != 'SKIP']
    failed = [r for r in results if r['verdict'] == 'FAIL']
    passed = [r for r in results if r['verdict'] == 'PASS']
    skipped = [r for r in results if r['verdict'] == 'SKIP']

    output = {
        'check': 'OC-15',
        'check_name': 'frontmatter-freshness',
        'verdict': 'PASS' if len(failed) == 0 else 'FAIL',
        'summary': (f'{len(passed)} fresh, {len(failed)} stale, '
                    f'{len(skipped)} skipped (no updated: field)'),
        'expected_date': expected_date,
        'results': [r for r in results if r['verdict'] != 'SKIP'],
        'skipped_count': len(skipped),
    }

    if failed:
        output['stale_files'] = [r['file'] for r in failed]

    print(json.dumps(output, indent=2))
    sys.exit(0 if len(failed) == 0 else 1)


if __name__ == '__main__':
    main()
