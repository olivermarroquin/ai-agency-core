#!/usr/bin/env python3
"""Enqueue a structured commit request for the git-gate processor.

Called by a finished run to enqueue a commit instead of stopping for the
operator to paste a block. The run moves on immediately — no stop-and-wait.

Writing the queue file is a normal artifact write (no git command), so it
does NOT create a gate-loop in the dirty ledger.

Usage:
  python3 enqueue.py \
    --repo ~/workspace/second-brain \
    --files file1.md file2.md \
    --message "Update focus file" \
    --verdict-file .review-gate/state/verdict-xxx.json \
    --run-id cowork-ev-growth-202607021900 \
    --chat-id git-gate-autonomous-commit-202607021900 \
    [--tier reversible]  # default: reversible
    [--push]             # include push after commit
    [--branch main]      # default: current branch
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import QUEUE_DIR


def get_current_branch(repo_path: str) -> str:
    """Get the current git branch of a repo."""
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 'unknown'


def compute_diff(repo_path: str, files: list) -> str:
    """Compute the diff for the declared files."""
    try:
        # Show both staged and unstaged changes for declared files
        result = subprocess.run(
            ['git', '-C', repo_path, 'diff', 'HEAD', '--'] + files,
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ''


def main():
    parser = argparse.ArgumentParser(
        description='Enqueue a structured commit request for git-gate.')
    parser.add_argument('--repo', required=True,
                        help='Absolute path to the git repo')
    parser.add_argument('--files', nargs='+', required=True,
                        help='Files to commit (explicit list, never glob)')
    parser.add_argument('--message', required=True,
                        help='Commit message')
    parser.add_argument('--verdict-file', required=True,
                        help='Path to the review verdict JSON file')
    parser.add_argument('--run-id', required=True,
                        help='Run ID (chat-slug-based)')
    parser.add_argument('--chat-id', required=True,
                        help='Chat ID for traceability')
    parser.add_argument('--tier', default='reversible',
                        choices=['reversible', 'irreversible'],
                        help='Reversibility tier (default: reversible)')
    parser.add_argument('--push', action='store_true',
                        help='Include push after commit')
    parser.add_argument('--branch', default=None,
                        help='Target branch (default: current branch)')
    parser.add_argument('--session-id', default=None,
                        help='Producer session ID (for reviewer registry lookup)')
    parser.add_argument('--reviewer-session-id', default=None,
                        help='Reviewer session ID (for independent sign-off)')
    args = parser.parse_args()

    repo_path = os.path.realpath(args.repo)
    if not os.path.isdir(os.path.join(repo_path, '.git')):
        print(f'REJECTED: {repo_path} is not a git repository.',
              file=sys.stderr)
        sys.exit(1)

    # Validate verdict file exists
    verdict_path = os.path.realpath(args.verdict_file)
    if not os.path.isfile(verdict_path):
        print(f'REJECTED: verdict file does not exist: {verdict_path}',
              file=sys.stderr)
        sys.exit(1)

    branch = args.branch or get_current_branch(repo_path)
    diff = compute_diff(repo_path, args.files)

    # Build the commit request
    request = {
        'version': 1,
        'repo': repo_path,
        'files': [os.path.realpath(os.path.join(repo_path, f))
                  if not os.path.isabs(f) else os.path.realpath(f)
                  for f in args.files],
        'files_relative': args.files,
        'message': args.message,
        'verdict_file': verdict_path,
        'run_id': args.run_id,
        'chat_id': args.chat_id,
        'tier': args.tier,
        'push': args.push,
        'branch': branch,
        'session_id': args.session_id,
        'reviewer_session_id': args.reviewer_session_id,
        'diff': diff,
        'enqueued_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'enqueued_ts': time.time(),
        'status': 'pending',
    }

    # Write to queue
    os.makedirs(QUEUE_DIR, exist_ok=True)
    queue_file = os.path.join(
        QUEUE_DIR, f'{args.run_id}-{int(time.time() * 1000)}.json')

    with open(queue_file, 'w') as f:
        json.dump(request, f, indent=2)

    print(json.dumps({
        'status': 'enqueued',
        'queue_file': queue_file,
        'repo': repo_path,
        'files': args.files,
        'branch': branch,
        'tier': args.tier,
        'push': args.push,
    }))


if __name__ == '__main__':
    main()
