#!/usr/bin/env python3
"""Stop / SubagentStop hook: blocks turn-end if dirty files lack review-pass markers.

Invoked by Claude Code before the model stops (returns control to the user).
Checks the per-session dirty ledger against review-pass markers. If any artifact
has been written/edited since its last review pass — or has never been reviewed —
the hook exits with code 2, blocking the stop and instructing the model to run
the mandatory pre-land review.

Stdin:  JSON from Claude Code hook system (session_id, stop_reason, etc.)
Stdout: JSON acknowledgment when all clean (exit 0)
Stderr: Block message with unreviewed file list (exit 2)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR


def load_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def get_unreviewed(dirty_entries: list, reviewed_entries: list) -> list:
    """Return dirty entries whose file_path has no review pass at or after
    the LATEST dirty timestamp for that path."""
    # Latest dirty timestamp per path
    dirty_map = {}
    for d in dirty_entries:
        fp = d.get('file_path', '')
        ts = d.get('timestamp', 0)
        if fp not in dirty_map or ts > dirty_map[fp]['timestamp']:
            dirty_map[fp] = d

    # Latest reviewed timestamp per path
    reviewed_map = {}
    for r in reviewed_entries:
        fp = r.get('file_path', '')
        ts = r.get('timestamp', 0)
        if fp not in reviewed_map or ts > reviewed_map[fp]:
            reviewed_map[fp] = ts

    unreviewed = []
    for fp, entry in dirty_map.items():
        dirty_ts = entry.get('timestamp', 0)
        reviewed_ts = reviewed_map.get(fp, 0)
        if dirty_ts > reviewed_ts:
            unreviewed.append(entry)

    return unreviewed


def determine_review_tier(unreviewed: list) -> str:
    for entry in unreviewed:
        if entry.get('tier') == 'full':
            return 'full'
    return 'fast-path'


def format_file_list(entries: list) -> str:
    """Format unreviewed entries. Shows display text for readability,
    but the file_path key (BASH:<hash> or absolute path) is what goes
    into --files for log-review-pass."""
    lines = []
    for e in entries:
        fp = e.get('file_path', '?')
        display = e.get('display', '')
        tool = e.get('tool', '?')
        tier = e.get('tier', '?')
        if display:
            lines.append(f'  - {fp}  [{display}]  (tool: {tool}, tier: {tier})')
        else:
            lines.append(f'  - {fp}  (tool: {tool}, tier: {tier})')
    return '\n'.join(lines)


def format_files_argv(entries: list) -> str:
    """Build the --files argument string with only the file_path keys.
    These are either absolute paths or BASH:<hash> — both are argv-safe."""
    return ' '.join(f'"{e["file_path"]}"' for e in entries)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        data = {}

    session_id = data.get('session_id', 'unknown')

    dirty_path = os.path.join(STATE_DIR, f'{session_id}-dirty.jsonl')
    reviewed_path = os.path.join(STATE_DIR, f'{session_id}-reviewed.jsonl')

    dirty_entries = load_jsonl(dirty_path)
    reviewed_entries = load_jsonl(reviewed_path)

    if not dirty_entries:
        # Stop hook approve: plain exit 0, no hookSpecificOutput (not valid for Stop)
        print(json.dumps({'continue': True}))
        return

    unreviewed = get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        # Stop hook approve: plain exit 0, no hookSpecificOutput (not valid for Stop)
        print(json.dumps({'continue': True}))
        return

    # Unreviewed files exist -> BLOCK
    tier = determine_review_tier(unreviewed)
    file_list = format_file_list(unreviewed)
    files_argv = format_files_argv(unreviewed)
    count = len(unreviewed)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')

    fast_desc = (
        'fast-path = grep-based surface sweep + source-client-leak-audit '
        '+ link-resolution (no Sonar/LLM-research)'
    )
    full_desc = (
        'full review = 7-surface sweep + source-client-leak-audit + link-resolution '
        '+ placeholder sweep + ground-truth value cross-check + '
        'live-rendered-cache-busted-verification Phase C (if live URL/external state touched)'
    )

    block_msg = f"""MANDATORY PRE-LAND REVIEW GATE — BLOCKED

{count} unreviewed artifact(s) detected. Review tier: {tier}.

Unreviewed:
{file_list}

REQUIRED ACTION before you can stop:
1. Dispatch gate-peer-reviewer on the touched artifacts/state.
   - Use the registered gate type if one applies; otherwise use G-default.
   - Tier '{tier}': {fast_desc if tier == 'fast-path' else full_desc}.
2. Run output-quality-loop on the produced artifacts.
3. The reviewer dispatch logs the verdict:
   python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-default --evidence "<REQUIRED: specific checks run, per-surface counts, actual findings — boilerplate rejected>"
4. Then try stopping again.

If blocking findings exist, use --verdict BLOCKING --findings "description" instead.
The BLOCKING verdict still clears the gate (findings were surfaced, not hidden)."""

    print(block_msg, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
