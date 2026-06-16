#!/usr/bin/env python3
"""OC-14: Rename-propagation completeness check.

Deterministic Layer-A check ($0, no LLM, exhaustive).
Given an old→new identifier pair, grep the old name across all live paths.
Hits in historical-allowlisted paths are exempt; any live hit is a defect.

Catches: C-03 (dead path in program handoff), C-04 (stale registry),
         C-05/C-08/C-09/C-10/C-25 (stale live forward-refs).

Usage:
  python3 oc-14-rename-propagation.py --old-name <old> --new-name <new> \
    --search-root <dir> [--allowlist-patterns <json array>]

  Output: JSON with per-hit PASS(exempted)/FAIL(live straggler) + overall verdict.
"""

import argparse
import json
import os
import re
import sys


# Default historical allowlist: paths where stale references are expected
# (event logs, changelogs, recently-closed, execution logs, consumed handoffs, lineage notes)
DEFAULT_ALLOWLIST_PATTERNS = [
    r'_event-log\.md$',
    r'_active-chats-tracker-changelog\.md$',
    r'_recently-closed\.md$',
    r'execution-log[s]?[/-]',
    r'execution-log-\d{4}-\d{2}-\d{2}',
    r'/lessons?/',
    r'lesson-.*\.md$',
    r'/complete/',           # consumed handoffs archived in complete/ subfolder
    r'_recently-closed-archive',  # archived closed-chat records
    r'\.git/',
    r'__pycache__/',
    r'\.pyc$',
    r'node_modules/',
    r'\.review-gate/',
    r'/99_archive/',
]

# File extensions to search (text files only)
TEXT_EXTENSIONS = {
    '.md', '.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.yaml',
    '.yml', '.toml', '.html', '.css', '.sh', '.bash', '.zsh', '.cfg',
    '.ini', '.conf', '.env', '.sql', '.xml', '.csv',
}

# Binary/large dirs to skip
SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', '.next', '.venv', 'venv',
    'dist', 'build', '.review-gate', '.claude',
}


def is_text_file(filepath: str) -> bool:
    """Check if a file is likely a text file based on extension."""
    _, ext = os.path.splitext(filepath)
    if ext.lower() in TEXT_EXTENSIONS:
        return True
    # Files with no extension might be text (READMEs, Makefiles, etc.)
    if not ext:
        basename = os.path.basename(filepath)
        return basename in {'README', 'Makefile', 'Dockerfile', 'CLAUDE',
                            'LICENSE', 'CODEOWNERS', '.gitignore', '.gitattributes'}
    return False


def is_path_allowlisted(filepath: str, allowlist_patterns: list) -> bool:
    """Check if a file path matches any allowlist pattern."""
    for pattern in allowlist_patterns:
        if re.search(pattern, filepath):
            return True
    return False


def search_for_old_name(search_root: str, old_name: str,
                        allowlist_patterns: list) -> list:
    """Search for the old name across all text files under search_root.

    Returns a list of hit dicts: {file, line_num, line_text, allowlisted, reason}.
    """
    hits = []
    old_name_lower = old_name.lower()

    for dirpath, dirnames, filenames in os.walk(search_root):
        # Prune skippable directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            filepath = os.path.join(dirpath, filename)

            if not is_text_file(filepath):
                continue

            # Check path-level allowlist
            rel_path = os.path.relpath(filepath, search_root)
            path_allowlisted = is_path_allowlisted(rel_path, allowlist_patterns)

            try:
                with open(filepath, 'r', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if old_name in line or old_name_lower in line.lower():
                            hit = {
                                'file': rel_path,
                                'line_num': line_num,
                                'line_text': line.strip()[:120],
                                'allowlisted': path_allowlisted,
                                'reason': 'historical-allowlist' if path_allowlisted else 'LIVE straggler',
                            }
                            hits.append(hit)
            except (OSError, IOError):
                continue

    return hits


def main():
    parser = argparse.ArgumentParser(description='OC-14: Rename-propagation completeness')
    parser.add_argument('--old-name', required=True,
                        help='The old identifier being renamed FROM')
    parser.add_argument('--new-name', required=True,
                        help='The new identifier being renamed TO')
    parser.add_argument('--search-root', required=True,
                        help='Root directory to search')
    parser.add_argument('--allowlist-patterns', default=None,
                        help='JSON array of additional regex patterns for historical allowlist')
    args = parser.parse_args()

    search_root = os.path.expanduser(args.search_root)
    if not os.path.isdir(search_root):
        print(json.dumps({'error': f'search root is not a directory: {search_root}',
                           'verdict': 'FAIL'}))
        sys.exit(1)

    # Merge default + custom allowlist patterns
    allowlist = list(DEFAULT_ALLOWLIST_PATTERNS)
    if args.allowlist_patterns:
        try:
            custom = json.loads(args.allowlist_patterns)
            if isinstance(custom, list):
                allowlist.extend(custom)
        except json.JSONDecodeError:
            pass

    hits = search_for_old_name(search_root, args.old_name, allowlist)

    live_hits = [h for h in hits if not h['allowlisted']]
    exempt_hits = [h for h in hits if h['allowlisted']]

    output = {
        'check': 'OC-14',
        'check_name': 'rename-propagation-completeness',
        'old_name': args.old_name,
        'new_name': args.new_name,
        'verdict': 'PASS' if len(live_hits) == 0 else 'FAIL',
        'summary': (f'{len(live_hits)} live straggler(s), '
                    f'{len(exempt_hits)} historical/exempt hit(s)'),
        'live_hits': live_hits[:50],  # Cap output for readability
        'exempt_hit_count': len(exempt_hits),
        'total_hits': len(hits),
    }

    if live_hits:
        output['details'] = (
            f'The old name "{args.old_name}" still appears in {len(live_hits)} '
            f'live file(s). These must be updated to "{args.new_name}".'
        )

    print(json.dumps(output, indent=2))
    sys.exit(0 if len(live_hits) == 0 else 1)


if __name__ == '__main__':
    main()
