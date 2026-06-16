#!/usr/bin/env python3
"""PostToolUse hook: tracks dirty (written/edited/state-changed) files in a per-session ledger.

Invoked by Claude Code after every Write, Edit, NotebookEdit, or Bash tool use.
Appends an entry to .review-gate/state/<session>-dirty.jsonl when the tool call is
artifact-producing or state-changing. Read-only Bash commands are exempt.

This is the Claude Code adapter for dirty-ledger tracking. Substrate-agnostic
logic (read-only classification, bash ID generation, tier classification, path
normalization) lives in engine.py.

Stdin:  JSON from Claude Code hook system (session_id, tool_name, tool_input, etc.)
Stdout: JSON acknowledgment (hookSpecificOutput)
Exit 0: always (tracking is non-blocking)
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR
import engine

# --- Self-referential exclusion (CC-specific) ---
# Match these substrings anywhere in the Bash command. Covers:
#   python3 /abs/.../mandatory-review-gate.py ...
#   python3 /abs/.../log-review-pass.py --session ...
#   python3 /abs/.../dirty-ledger-track.py
#   python3 -c "...from _paths import STATE_DIR..." (inline inspection)
SELF_PATTERNS = [
    'mandatory-review-gate',
    'log-review-pass',
    'dirty-ledger-track',
    'gate-status',
    'gate-skip',
    '_paths.py',
    'from _paths import',
]


def main():
    try:
        raw = sys.stdin.read()

        if not raw.strip():
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return

    session_id = data.get('session_id', 'unknown')
    tool_name = data.get('tool_name', '')
    tool_input = data.get('tool_input', {})

    # Self-referential exclusion — per-segment: split compound commands and
    # exclude ONLY if EVERY segment is self-referential. A mixed command
    # (state-change + gate command) must be tracked.
    if tool_name == 'Bash':
        bash_cmd = tool_input.get('command', '')
        segments = engine.split_compound(bash_cmd.strip()) if bash_cmd.strip() else []
        if segments and all(
            any(pat in seg for pat in SELF_PATTERNS)
            for seg in segments
        ):
            return

    file_path = None
    display = None  # Human-readable display for Bash commands

    if tool_name in ('Write', 'Edit', 'NotebookEdit'):
        file_path = tool_input.get('file_path', '')
        if not file_path:
            return
        file_path = engine.normalize_path(file_path)
        # Exclude .review-gate/ paths — verdict files, ledgers, metrics
        # are gate infrastructure, not reviewable artifacts (kills verdict-loop)
        if '/.review-gate/' in file_path:
            return
    elif tool_name == 'Bash':
        bash_cmd = tool_input.get('command', '')
        if engine.is_read_only_bash(bash_cmd):
            return
        file_path = engine.bash_entry_id(bash_cmd)
        # Store human-readable truncation in display, NOT in the key
        display = re.sub(r'\s+', ' ', bash_cmd.strip())[:80]
    else:
        return

    tier = engine.classify_tier(file_path, tool_input, tool_name)

    # Substrate source: env override or default 'claude-code' (PostToolUse
    # only fires in Claude Code; other substrates set REVIEW_GATE_SOURCE)
    source = os.environ.get('REVIEW_GATE_SOURCE', 'claude-code')

    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': tool_name,
        'file_path': file_path,
        'tier': tier,
        'source': source,
    }
    if display is not None:
        entry['display'] = display

    engine.append_dirty_entry(STATE_DIR, session_id, entry)

    show = display if display else os.path.basename(file_path)
    print(json.dumps({
        'continue': True,
        'hookSpecificOutput': {
            'hookEventName': 'PostToolUse',
            'additionalContext': f'[review-gate] Tracked: {file_path} ({tier})',
        },
    }))


if __name__ == '__main__':
    main()
