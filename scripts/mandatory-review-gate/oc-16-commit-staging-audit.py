#!/usr/bin/env python3
"""OC-16: Commit-staging audit — git status vs dirty-ledger reconciliation.

Deterministic Layer-A check ($0, no LLM, exhaustive).
Compares the session's dirty-ledger (what the chat touched) against
git status (what's staged/unstaged) to detect:
  - staged-not-touched: foreign chat's deliverables staged (C-21)
  - touched-not-staged: artifacts the chat produced but forgot to stage (C-22)
  - rename deletions not staged: old file not deleted/staged after rename (C-23)
  - build artifacts committed: __pycache__, *.pyc, etc. (C-07)

Usage:
  python3 oc-16-commit-staging-audit.py --ledger <path-to-dirty.jsonl> \
    --repo-root <git-repo-root>

  Output: JSON with per-issue findings + overall verdict.
"""

import argparse
import json
import os
import re
import subprocess
import sys


# Build artifacts / junk that should never be committed
BUILD_ARTIFACT_PATTERNS = [
    r'__pycache__/',
    r'__pycache__$',
    r'\.pyc$',
    r'\.pyo$',
    r'\.egg-info/',
    r'\.DS_Store$',
    r'node_modules/',
    r'\.next/',
    r'dist/',
    r'\.env$',
    r'\.env\.local$',
]

BUILD_ARTIFACT_RE = re.compile('|'.join(BUILD_ARTIFACT_PATTERNS))


def load_dirty_ledger(ledger_path: str) -> set:
    """Load touched file paths from the dirty-ledger.

    Returns a set of absolute, normalized file paths.
    Excludes BASH: entries (state-changing commands tracked by hash, not by file).
    """
    paths = set()
    if not os.path.isfile(ledger_path):
        return paths

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
            if fp.startswith('BASH:'):
                continue
            if '/.review-gate/' in fp:
                continue
            if fp:
                paths.add(os.path.realpath(fp))

    return paths


def get_git_status(repo_root: str) -> dict:
    """Get git status as categorized file sets.

    Returns {staged: set, unstaged: set, untracked: set}.
    All paths are absolute.
    """
    result = {'staged': set(), 'unstaged': set(), 'untracked': set()}

    try:
        # Staged files (index vs HEAD)
        proc = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACDMR'],
            capture_output=True, text=True, cwd=repo_root)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line:
                    result['staged'].add(os.path.realpath(os.path.join(repo_root, line)))

        # Unstaged modifications (working tree vs index)
        proc = subprocess.run(
            ['git', 'diff', '--name-only', '--diff-filter=ACDMR'],
            capture_output=True, text=True, cwd=repo_root)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line:
                    result['unstaged'].add(os.path.realpath(os.path.join(repo_root, line)))

        # Untracked files
        proc = subprocess.run(
            ['git', 'ls-files', '--others', '--exclude-standard'],
            capture_output=True, text=True, cwd=repo_root)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line:
                    result['untracked'].add(os.path.realpath(os.path.join(repo_root, line)))

        # Deleted files (staged)
        proc = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=D'],
            capture_output=True, text=True, cwd=repo_root)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line:
                    result['staged'].add(os.path.realpath(os.path.join(repo_root, line)))

    except (OSError, subprocess.SubprocessError) as e:
        result['error'] = str(e)

    return result


def check_build_artifacts(staged: set, unstaged: set, untracked: set) -> list:
    """Check for build artifacts in staged/unstaged/untracked files."""
    findings = []
    all_files = staged | unstaged | untracked
    for fp in sorted(all_files):
        if BUILD_ARTIFACT_RE.search(fp):
            location = []
            if fp in staged:
                location.append('staged')
            if fp in unstaged:
                location.append('unstaged')
            if fp in untracked:
                location.append('untracked')
            findings.append({
                'file': fp,
                'issue': 'build-artifact',
                'severity': 'blocking' if fp in staged else 'warning',
                'detail': f'build artifact ({", ".join(location)}): {os.path.basename(fp)}',
            })
    return findings


def main():
    parser = argparse.ArgumentParser(description='OC-16: Commit-staging audit')
    parser.add_argument('--ledger', required=True,
                        help='Path to the dirty-ledger JSONL file')
    parser.add_argument('--repo-root', required=True,
                        help='Git repository root directory')
    args = parser.parse_args()

    repo_root = os.path.realpath(os.path.expanduser(args.repo_root))
    if not os.path.isdir(os.path.join(repo_root, '.git')):
        print(json.dumps({'error': f'not a git repo: {repo_root}', 'verdict': 'FAIL'}))
        sys.exit(1)

    dirty = load_dirty_ledger(args.ledger)
    git = get_git_status(repo_root)

    if 'error' in git:
        print(json.dumps({'error': f'git status failed: {git["error"]}', 'verdict': 'FAIL'}))
        sys.exit(1)

    staged = git['staged']
    unstaged = git['unstaged']
    untracked = git['untracked']

    findings = []

    # Filter dirty-ledger paths to only those under this repo
    dirty_in_repo = {p for p in dirty if p.startswith(repo_root + '/')}

    # 1. Staged-not-touched: files staged but NOT in dirty-ledger (foreign work)
    staged_not_touched = staged - dirty_in_repo
    # Exclude .review-gate files from this check
    staged_not_touched = {p for p in staged_not_touched if '/.review-gate/' not in p}
    for fp in sorted(staged_not_touched):
        findings.append({
            'file': fp,
            'issue': 'staged-not-touched',
            'severity': 'blocking',
            'detail': f'staged but not in dirty-ledger (foreign chat deliverable?)',
        })

    # 2. Touched-not-staged: files in dirty-ledger but not staged/committed
    # Only flag files that still exist on disk (deleted files need separate handling)
    touched_not_staged = dirty_in_repo - staged
    for fp in sorted(touched_not_staged):
        if os.path.exists(fp):
            # File exists but isn't staged
            if fp in unstaged or fp in untracked:
                findings.append({
                    'file': fp,
                    'issue': 'touched-not-staged',
                    'severity': 'blocking',
                    'detail': 'touched by this chat but not staged for commit',
                })
        else:
            # File was touched but no longer exists — might be a rename deletion
            # Check if it's staged as a deletion
            if fp not in staged:
                findings.append({
                    'file': fp,
                    'issue': 'deletion-not-staged',
                    'severity': 'blocking',
                    'detail': 'file deleted (likely rename) but deletion not staged',
                })

    # 3. Build artifacts
    artifact_findings = check_build_artifacts(staged, unstaged, untracked)
    findings.extend(artifact_findings)

    blocking = [f for f in findings if f['severity'] == 'blocking']
    warnings = [f for f in findings if f['severity'] == 'warning']

    output = {
        'check': 'OC-16',
        'check_name': 'commit-staging-audit',
        'verdict': 'PASS' if len(blocking) == 0 else 'FAIL',
        'summary': (f'{len(blocking)} blocking issue(s), '
                    f'{len(warnings)} warning(s)'),
        'dirty_ledger_files': len(dirty_in_repo),
        'staged_files': len(staged),
        'findings': findings,
    }

    print(json.dumps(output, indent=2))
    sys.exit(0 if len(blocking) == 0 else 1)


if __name__ == '__main__':
    main()
