#!/usr/bin/env python3
"""Git hook adapter for the mandatory review gate (Tier B).

Runs as pre-commit (and optionally pre-push). This is the universal
enforcement point — it fires for Claude Code, Codex, local models,
manual edits, or any substrate that ultimately commits.

Adapter questions:
  (a) Artifact discovery: git diff --cached --name-only (pre-commit)
  (b) Blocking: sys.exit(1) on unreviewed files (git hook convention)

Session ID: 'git-<branch>-<YYYY-MM-DD>' (deterministic, no CC session).
The hook does NOT write dirty entries — it reads the existing dirty ledger
(written by whatever substrate produced the artifacts) and cross-references
against staged files. If staged files have unreviewed dirty entries, the
commit is blocked.

For files staged that have NO dirty-ledger entry at all (e.g., manual edits
outside any agent), the hook treats them as pre-reviewed (the gate only
tracks agent-produced artifacts). This avoids blocking normal human commits.

Override: git commit --no-verify (standard git escape hatch). The installer
documents this and the audit trail notes it in metrics.

Usage (in .git/hooks/pre-commit):
    #!/bin/sh
    exec python3 /path/to/git_hook_adapter.py --hook pre-commit

Created by [RGH-2] (2026-06-16).
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
import engine


def get_repo_root() -> str:
    """Get the git repo root for the current working directory."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ''


def get_staged_files(repo_root: str) -> set:
    """Get absolute paths of files staged for commit."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR'],
            capture_output=True, text=True, cwd=repo_root)
        if result.returncode != 0:
            return set()
        paths = set()
        for line in result.stdout.strip().splitlines():
            if line:
                paths.add(os.path.realpath(os.path.join(repo_root, line)))
        return paths
    except (subprocess.SubprocessError, FileNotFoundError):
        return set()


def get_current_branch() -> str:
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return 'unknown'


def derive_session_id() -> str:
    """Derive a deterministic session ID from branch + date.
    Format: git-<branch>-<YYYY-MM-DD>"""
    branch = get_current_branch()
    date = time.strftime('%Y-%m-%d')
    # Sanitize branch name for use as filename
    safe_branch = branch.replace('/', '-').replace(' ', '-')[:50]
    return f'git-{safe_branch}-{date}'


def find_matching_sessions(state_dir: str) -> list:
    """Find all session IDs that have dirty ledgers in state_dir.
    The hook needs to check ALL sessions' ledgers (not just its own)
    because it enforces at the commit point — artifacts may have been
    produced by any session/substrate."""
    if not os.path.isdir(state_dir):
        return []
    sessions = set()
    for fname in os.listdir(state_dir):
        if fname.endswith('-dirty.jsonl'):
            sid = fname[:-len('-dirty.jsonl')]
            sessions.add(sid)
    return sorted(sessions)


def collect_all_dirty_entries(state_dir: str) -> dict:
    """Collect dirty entries from ALL sessions, keyed by file_path.
    Returns {abs_path: latest_entry} — the latest dirty entry per file
    across all sessions. This is the "universal" view the git hook needs."""
    all_dirty = {}
    for sid in find_matching_sessions(state_dir):
        # No source filter — git hook sees everything
        entries = engine.load_scoped_dirty(state_dir, sid,
                                           included_sources=None)
        for e in entries:
            fp = e.get('file_path', '')
            if fp.startswith('BASH:'):
                continue  # git hook only cares about file paths
            ts = e.get('timestamp', 0)
            if fp not in all_dirty or ts > all_dirty[fp]['timestamp']:
                all_dirty[fp] = {**e, '_session_id': sid}
    return all_dirty


def collect_all_reviewed_entries(state_dir: str) -> dict:
    """Collect reviewed entries from ALL sessions, keyed by file_path.
    Returns {abs_path: latest_reviewed_timestamp}."""
    all_reviewed = {}
    for sid in find_matching_sessions(state_dir):
        entries = engine.read_reviewed_ledger(state_dir, sid)
        for e in entries:
            fp = e.get('file_path', '')
            ts = e.get('timestamp', 0)
            if fp not in all_reviewed or ts > all_reviewed[fp]:
                all_reviewed[fp] = ts
    return all_reviewed


def check_staged_files(staged: set, state_dir: str) -> list:
    """Check staged files against the dirty ledger.

    Returns a list of unreviewed entries for staged files.
    Files that are staged but have NO dirty-ledger entry are assumed clean
    (they were edited outside any tracked agent — the gate doesn't block
    normal human commits).

    NOTE (C-02, RGH-2 peer review): This does NOT use engine.check_gate()
    because the git hook's scoping is fundamentally different — it reads ALL
    sessions' ledgers (not just one session) and cross-references against
    staged files. If unreviewed-computation logic in engine.get_unreviewed()
    changes, verify this function's equivalent logic stays in sync.
    """
    all_dirty = collect_all_dirty_entries(state_dir)
    all_reviewed = collect_all_reviewed_entries(state_dir)

    unreviewed = []
    for fp in sorted(staged):
        if fp not in all_dirty:
            continue  # not agent-produced — pass through

        dirty_entry = all_dirty[fp]
        dirty_ts = dirty_entry.get('timestamp', 0)
        reviewed_ts = all_reviewed.get(fp, 0)

        if dirty_ts > reviewed_ts:
            unreviewed.append(dirty_entry)

    return unreviewed


def main():
    parser = argparse.ArgumentParser(
        description='Git hook adapter for the mandatory review gate')
    parser.add_argument('--hook', choices=['pre-commit', 'pre-push'],
                        default='pre-commit',
                        help='Hook type (default: pre-commit)')
    # pre-push passes remote/url as positional args per git convention
    parser.add_argument('remote', nargs='?')
    parser.add_argument('url', nargs='?')
    args = parser.parse_args()

    t_start = time.monotonic()

    repo_root = get_repo_root()
    if not repo_root:
        # Not in a git repo — pass through silently
        sys.exit(0)

    # Check if state dir exists — if not, no gate is active
    if not os.path.isdir(STATE_DIR):
        sys.exit(0)

    staged = get_staged_files(repo_root)
    if not staged:
        sys.exit(0)

    unreviewed = check_staged_files(staged, STATE_DIR)

    session_id = derive_session_id()
    wall_ms = (time.monotonic() - t_start) * 1000

    if not unreviewed:
        engine.log_metrics(STATE_DIR, session_id, 'git-hook-approve',
                           0, 'n/a', wall_ms,
                           extra={'hook_type': args.hook,
                                  'repo_root': repo_root,
                                  'staged_count': len(staged)})
        sys.exit(0)

    # Blocked — unreviewed agent-produced files are being committed
    tier = engine.determine_review_tier(unreviewed)
    count = len(unreviewed)

    engine.log_metrics(STATE_DIR, session_id, 'git-hook-block',
                       count, tier, wall_ms,
                       extra={'hook_type': args.hook,
                              'repo_root': repo_root,
                              'staged_count': len(staged)})

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')

    print('', file=sys.stderr)
    print('=' * 60, file=sys.stderr)
    print('MANDATORY REVIEW GATE — COMMIT BLOCKED', file=sys.stderr)
    print('=' * 60, file=sys.stderr)
    print(f'', file=sys.stderr)
    print(f'{count} unreviewed agent-produced file(s) staged for commit.',
          file=sys.stderr)
    print(f'Review tier: {tier}', file=sys.stderr)
    print(f'', file=sys.stderr)
    print(f'Unreviewed files:', file=sys.stderr)
    for e in unreviewed:
        fp = e.get('file_path', '?')
        src = e.get('source', '?')
        sid = e.get('_session_id', '?')
        print(f'  - {fp}  (source: {src}, session: {sid})', file=sys.stderr)
    print(f'', file=sys.stderr)
    print(f'To resolve:', file=sys.stderr)
    print(f'  1. Run the review gate on these files', file=sys.stderr)
    print(f'  2. Log the review pass:', file=sys.stderr)

    files_argv = ' '.join(f'"{e["file_path"]}"' for e in unreviewed)
    # Use the session that produced the artifact for the review marker
    sessions = set(e.get('_session_id', session_id) for e in unreviewed)
    for sid in sessions:
        sid_files = [e for e in unreviewed if e.get('_session_id') == sid]
        sid_argv = ' '.join(f'"{e["file_path"]}"' for e in sid_files)
        print(f'     python3 {log_script} --session {sid} '
              f'--files {sid_argv} --verdict PASS --tier {tier} '
              f'--gate-id G-default --verdict-file <verdict.json>',
              file=sys.stderr)

    print(f'', file=sys.stderr)
    print(f'To bypass (operator-only, audited):', file=sys.stderr)
    print(f'  git commit --no-verify', file=sys.stderr)
    print(f'', file=sys.stderr)

    sys.exit(1)


if __name__ == '__main__':
    main()
