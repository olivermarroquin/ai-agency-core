#!/usr/bin/env python3
"""gate-whoami: discover the current Claude Code session ID (RGH-15B).

The reviewer needs its session ID to register via register-reviewer-session.py,
but CC doesn't expose it via env var. This script discovers it by scanning
the most recent dirty-ledger or stop-hook-history files in the state dir.

Usage:
    python3 gate-whoami.py
    # Prints the session ID to stdout (machine-readable, single line).

If the session ID cannot be determined (no dirty entries yet), prints a
diagnostic message to stderr and exits 1. In that case, the reviewer should
wait for the first gate-block message, which always contains --session <ID>.

Note: With RGH-17A (dispatch wrapper), this script is rarely needed — the
dispatch wrapper pre-registers the reviewer before it runs any commands.
This is the fallback for operator hand-dispatch without the wrapper.
"""

import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR


def main():
    # Strategy: find the most recently modified dirty-ledger or stop-hook
    # history file and extract the session ID from its filename.
    # Files follow the pattern: <session_id>-dirty.jsonl or
    # <session_id>-stop-hook-history.jsonl
    patterns = [
        os.path.join(STATE_DIR, '*-dirty.jsonl'),
        os.path.join(STATE_DIR, '*-stop-hook-history.jsonl'),
    ]

    candidates = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            basename = os.path.basename(path)
            # Extract session ID (everything before the suffix)
            for suffix in ('-dirty.jsonl', '-stop-hook-history.jsonl'):
                if basename.endswith(suffix):
                    session_id = basename[:-len(suffix)]
                    mtime = os.path.getmtime(path)
                    candidates.append((mtime, session_id, path))
                    break

    if not candidates:
        print('gate-whoami: no dirty-ledger or stop-hook-history files found '
              f'in {STATE_DIR}. Wait for the first gate-block message — it '
              'will contain --session <your-session-id>.', file=sys.stderr)
        sys.exit(1)

    # Sort by mtime descending — most recent file is likely the current session
    candidates.sort(key=lambda x: -x[0])
    session_id = candidates[0][1]

    # Print the session ID to stdout (machine-readable)
    print(session_id)

    # MAJOR-1 fix: warn that this may be the WRONG session if the caller
    # just started (their dirty-ledger doesn't exist yet, so we return
    # the most recently active session — often the producer's).
    print(
        'WARNING: This is the most recently active session ID. If you just '
        'started a new session, this may be the PRODUCER\'s session, not '
        'yours. The AUTHORITATIVE source for your session ID is the first '
        'gate-block message (look for --session <ID>). Do NOT use this for '
        'register-reviewer-session.py unless you have confirmed it matches '
        'your actual session.',
        file=sys.stderr)


if __name__ == '__main__':
    main()
