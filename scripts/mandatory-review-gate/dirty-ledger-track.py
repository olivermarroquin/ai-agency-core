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


# CR-230: benign navigation commands that are safe companions to
# self-referential gate commands. These never produce state changes.
_BENIGN_NAV_CMDS = frozenset({
    'cd', 'pushd', 'popd', 'source', '.', 'export', 'true', 'echo',
    'printf', 'set', 'unset', 'alias', 'hash', 'pwd',
})


def _is_benign_nav_segment(segment: str) -> bool:
    """Check if a command segment is benign navigation (cd, pushd, etc.).

    Also handles pure variable assignments like VAR=value (no command runs).
    """
    segment = segment.strip()
    if not segment:
        return True
    # Pure variable assignment: VAR=value with no command
    tokens = segment.split()
    all_assignments = all(
        '=' in t and not t.startswith('-') and t.split('=')[0].replace('_', '').isalnum()
        for t in tokens
    )
    if all_assignments:
        return True
    # First word is a benign nav command
    first = tokens[0]
    base = os.path.basename(first)
    return base in _BENIGN_NAV_CMDS


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
    # exclude ONLY if EVERY segment is either self-referential (gate script)
    # or benign navigation (cd, pushd, popd, env assignment). A mixed command
    # (state-change + gate command) must be tracked.
    #
    # CR-230: the previous check required EVERY segment to contain a
    # SELF_PATTERNS substring. This failed for cd-prefixed invocations:
    #   cd ~/workspace && python3 .../log-review-pass.py --session ...
    # because "cd ~/workspace" doesn't contain any gate script name.
    # Fix: classify each segment as benign-nav OR self-ref OR other.
    # Exclude only if no "other" segments exist and at least one is self-ref.
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
        has_self_ref = False
        all_safe = True
        for seg in segments:
            seg_stripped = seg.strip()
            if not seg_stripped:
                continue
            if any(pat in seg_stripped for pat in SELF_PATTERNS):
                has_self_ref = True
            elif _is_benign_nav_segment(seg_stripped):
                pass  # benign navigation — neither self-ref nor problematic
            else:
                all_safe = False
                break
        if segments and has_self_ref and all_safe:
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
                        event_log = engine.get_event_log_path()
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

            # CR-232 Fix 5: Self-clearing tripwire (identity stopgap).
            # Detect when a session runs log-review-pass.py --session <itself>
            # or register-reviewer-session.py where it registers itself.
            # This makes the fabricated-reviewer path NOISY instead of silent.
            if 'log-review-pass' in bash_cmd:
                # Extract --session value from the command
                session_match = re.search(
                    r'--session\s+(\S+)', bash_cmd)
                if session_match:
                    target_session = session_match.group(1)
                    if target_session == session_id:
                        d09_msg = (
                            f'[review-gate] ⚠️  D-09 SELF-CLEAR WARNING: '
                            f'session {session_id} is running '
                            f'log-review-pass.py --session <itself>. '
                            f'A session clearing its own gate is a D-09 '
                            f'class defect (fabricated-reviewer path). '
                            f'The real fix is the GIT-GATE daemon (Phase 2/3).'
                        )
                        print(d09_msg, file=sys.stderr)
                        try:
                            event_log = engine.get_event_log_path()
                            iso = time.strftime(
                                '%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                            row = (
                                f'\n| {iso} | D-09 self-clear attempt | '
                                f'd-09 | {session_id} | '
                                f'**D-09 SELF-CLEAR: session ran '
                                f'log-review-pass.py --session <itself>.** '
                                f'Fabricated-reviewer path detected. |'
                            )
                            with open(event_log, 'a') as f:
                                f.write(row)
                        except OSError:
                            pass

            if 'register-reviewer-session' in bash_cmd:
                # Detect self-registration as reviewer
                session_match = re.search(
                    r'--session\s+(\S+)', bash_cmd)
                if session_match:
                    target_session = session_match.group(1)
                    if target_session == session_id:
                        d09_msg = (
                            f'[review-gate] ⚠️  D-09 SELF-REGISTER WARNING: '
                            f'session {session_id} is registering itself as '
                            f'a reviewer via register-reviewer-session.py. '
                            f'Self-registration is a D-09 class defect.'
                        )
                        print(d09_msg, file=sys.stderr)
                        try:
                            event_log = engine.get_event_log_path()
                            iso = time.strftime(
                                '%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                            row = (
                                f'\n| {iso} | D-09 self-register attempt | '
                                f'd-09 | {session_id} | '
                                f'**D-09 SELF-REGISTER: session ran '
                                f'register-reviewer-session.py --session '
                                f'<itself>.** |'
                            )
                            with open(event_log, 'a') as f:
                                f.write(row)
                        except OSError:
                            pass

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
    #
    # CR-230 Part 2: also scan ANY .sh file's content for --force-deliverables.
    # A session can compose a skip with --force-deliverables under a non-skip
    # filename to bypass the filename-based D-09 detection.
    # CR-238 fix: drop leading-dot requirement in gate-skip filename regex.
    # The old regex `\.gate-skip.*\.sh$` missed files like `test-gate-skip.sh`
    # (no leading dot). Now matches any filename containing 'gate-skip'.
    # Also extend force-deliverables content scan to .py/.zsh/.bash (B-3 fix).
    d09_skip_file = (tool_name in ('Write', 'Edit') and
                     re.search(r'gate-skip[^/]*\.sh$', file_path))
    d09_force_content = False
    _EXECUTABLE_EXTENSIONS = ('.sh', '.py', '.zsh', '.bash')
    if tool_name in ('Write', 'Edit') and any(
            file_path.endswith(ext) for ext in _EXECUTABLE_EXTENSIONS):
        content = tool_input.get('content', '') or tool_input.get('new_string', '')
        if 'force-deliverables' in content:
            d09_force_content = True

    if d09_skip_file or d09_force_content:
        if d09_force_content and not d09_skip_file:
            d09_detail = (
                f'Script content contains --force-deliverables '
                f'({os.path.basename(file_path)})')
            d09_label = 'D-09 --force-deliverables in script content'
        else:
            d09_detail = (
                f'gate-skip script authored via {tool_name} '
                f'({os.path.basename(file_path)})')
            d09_label = 'D-09 Write/Edit of gate-skip script'

        d09_msg = (
            f'[review-gate] ⚠️  D-09 WARNING: {d09_detail}. '
            f'gate-skip scripts are OPERATOR-ONLY. A model authoring a skip '
            f'script via {tool_name} is a D-09 class defect. '
            f'Session: {session_id}'
        )
        print(d09_msg, file=sys.stderr)
        # Write D-09 event-log row for audit trail
        try:
            event_log = engine.get_event_log_path()
            iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            row = (
                f'\n| {iso} | {d09_label} | '
                f'd-09 | {session_id} | '
                f'**D-09 WARNING: {d09_detail}.** '
                f'gate-skip scripts are OPERATOR-ONLY per CLAUDE.md. '
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
