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
    'assert-gate-clear',
    'register-reviewer-session',
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
        # Gate-infrastructure path exclusion: Bash commands that target
        # .review-gate/ are gate infrastructure (verdict files, ledgers,
        # metrics), not reviewable artifacts. Analogous to the Write/Edit
        # exclusion below (lines 78-81). Prevents the recursive trap where
        # a reviewer's verdict-write Bash creates a new dirty entry that
        # requires yet another reviewer to clear. (Bug: 2026-06-17 MI-8
        # Chat 2 producer looped for 1h17m on this.)
        if '/.review-gate/' in bash_cmd:
            return
        segments = engine.split_compound(bash_cmd.strip()) if bash_cmd.strip() else []
        if segments and all(
            any(pat in seg for pat in SELF_PATTERNS)
            for seg in segments
        ):
            # RGH-14B: D-09 guard — detect model-emitted gate-skip in a
            # producer session. gate-skip is OPERATOR-ONLY; a model running
            # it is a D-09 class defect. Flag loudly via stderr + event-log.
            # The command still executes (operator may have pasted it into CC),
            # but the D-09 signal is recorded for audit.
            if 'gate-skip' in bash_cmd:
                role = engine.classify_session_role(STATE_DIR, session_id)
                if role == 'producer':
                    d09_msg = (
                        f'[review-gate] ⚠️  D-09 WARNING: gate-skip.py invoked '
                        f'in a PRODUCER session ({session_id}). gate-skip is '
                        f'OPERATOR-ONLY. If the model authored this command, '
                        f'that is a D-09 class defect. If the operator pasted '
                        f'it manually, this warning is informational.'
                    )
                    print(d09_msg, file=sys.stderr)
                    # Write D-09 event-log row for audit trail
                    try:
                        from _paths import WORKSPACE_ROOT
                        event_log = os.environ.get(
                            'REVIEW_GATE_EVENT_LOG',
                            os.path.join(WORKSPACE_ROOT, 'second-brain',
                                         '_meta', '_event-log.md')
                        )
                        iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        row = (
                            f'\n| {iso} | D-09 gate-skip in producer session | '
                            f'd-09 | {session_id} | '
                            f'**D-09 WARNING: gate-skip.py invoked in producer '
                            f'session.** gate-skip is OPERATOR-ONLY per CLAUDE.md. '
                            f'If the model authored this invocation, it is a D-09 '
                            f'defect. Audit: check whether the command was '
                            f'model-generated or operator-pasted. |'
                        )
                        with open(event_log, 'a') as f:
                            f.write(row)
                    except OSError:
                        pass  # best-effort — don't crash tracking
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

    # --- D-09 guard for Write/Edit of skip scripts (CR-224 / skip-provenance) ---
    # A model writing a .gate-skip*.sh file via Write/Edit is a D-09 candidate.
    # The gate's own auto-written skip scripts go through mandatory-review-gate.py
    # (not PostToolUse), so they don't trigger this guard. Only model hand-writes
    # via the Write/Edit tools pass through here.
    if tool_name in ('Write', 'Edit') and re.search(
            r'\.gate-skip.*\.sh$', file_path):
        d09_msg = (
            f'[review-gate] ⚠️  D-09 WARNING: Write/Edit of gate-skip script '
            f'detected ({os.path.basename(file_path)}). gate-skip scripts are '
            f'OPERATOR-ONLY. A model authoring a skip script via {tool_name} '
            f'is a D-09 class defect. Session: {session_id}'
        )
        print(d09_msg, file=sys.stderr)
        # Write D-09 event-log row for audit trail
        try:
            from _paths import WORKSPACE_ROOT
            event_log = os.environ.get(
                'REVIEW_GATE_EVENT_LOG',
                os.path.join(WORKSPACE_ROOT, 'second-brain',
                             '_meta', '_event-log.md')
            )
            iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            row = (
                f'\n| {iso} | D-09 Write/Edit of gate-skip script | '
                f'd-09 | {session_id} | '
                f'**D-09 WARNING: model wrote gate-skip script '
                f'({os.path.basename(file_path)}) via {tool_name}.** '
                f'gate-skip scripts are OPERATOR-ONLY per CLAUDE.md. '
                f'The Write/Edit tool was used to author a skip script, '
                f'bypassing Bash-only D-09 detection. '
                f'Full path: {file_path} |'
            )
            with open(event_log, 'a') as f:
                f.write(row)
        except OSError:
            pass  # best-effort — don't crash tracking
        # NOTE: we still fall through to the plumbing suppression below.
        # The skip script IS plumbing (not a deliverable), but the D-09
        # signal was recorded. Detection, not gating.

    # RGH-20 / CR-219 Part C: plumbing-scratch suppression.
    # Workspace-root scratch files (.gate-skip*.sh, _relay-*.md, _spawn-*.md,
    # .pair-launch-*.sh) are NEVER deliverables regardless of session role.
    # Suppress dirty entries entirely to prevent the self-referential recursion
    # where writing .gate-skip.sh grows the unreviewed set while waiting for
    # the operator to run it. The audit trail is the file itself on disk.
    bash_cmd_raw = tool_input.get('command', '') if tool_name == 'Bash' else ''
    plumbing_annotation = engine.is_plumbing_exempt(
        file_path, tool_name, bash_cmd_raw)
    if plumbing_annotation:
        # Suppress: don't create a dirty entry. Print a quiet ack.
        print(json.dumps({
            'continue': True,
            'hookSpecificOutput': {
                'hookEventName': 'PostToolUse',
                'additionalContext': (
                    f'[review-gate] Plumbing-exempt (suppressed): '
                    f'{file_path} ({plumbing_annotation})'),
            },
        }))
        return

    tier = engine.classify_tier(file_path, tool_input, tool_name)

    # Substrate source: env override or default 'claude-code' (PostToolUse
    # only fires in Claude Code; other substrates set REVIEW_GATE_SOURCE)
    source = os.environ.get('REVIEW_GATE_SOURCE', 'claude-code')

    # Entry-level source classification (RGH-10): structurally infer whether
    # this entry is a reviewer working-doc, gate-clearing call, or producer
    # deliverable. This is path/command-based, not a self-declared flag.
    bash_cmd = tool_input.get('command', '') if tool_name == 'Bash' else ''
    entry_source = engine.classify_entry_source(file_path, tool_name, bash_cmd)

    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': tool_name,
        'file_path': file_path,
        'tier': tier,
        'source': source,
        'entry_source': entry_source,
    }
    if display is not None:
        entry['display'] = display
    # RGH-11: store full Bash command for reliable write-indicator detection
    # in session-level reviewer exemption (is_bash_entry_write_safe needs
    # the full command, not the 80-char truncated display).
    if tool_name == 'Bash' and bash_cmd:
        entry['bash_cmd'] = bash_cmd

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
