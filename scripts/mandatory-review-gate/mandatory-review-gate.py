#!/usr/bin/env python3
"""Stop hook: blocks turn-end if dirty files lack review-pass markers.

Invoked by Claude Code before the model stops (returns control to the user).
This is the Claude Code adapter for the Stop hook. Substrate-agnostic gate
logic lives in engine.py.

OWN-LEDGER-ONLY SCOPING (RGH-1.6): scans only the current session's dirty
ledger. Sub-agent coverage is preserved because sub-agent PostToolUse events
inherit the parent session_id in Claude Code (confirmed by real-runner evidence
2026-06-12, CC v2.1.85).

Stdin:  JSON from Claude Code hook system (session_id, stop_reason, etc.)
Stdout: JSON acknowledgment when all clean (exit 0)
Stderr: Block message with unreviewed file list (exit 2)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
import engine

# Sources that the CC Stop hook includes in its scope.
# Cowork/other-substrate entries are excluded — coverage deferred to
# Tier B git pre-commit hook (RGH-2).
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})  # '' = legacy entries without source field


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        data = {}

    session_id = data.get('session_id', 'unknown')

    # Delegate to the substrate-agnostic engine
    result = engine.check_gate(
        state_dir=STATE_DIR,
        session_id=session_id,
        workspace_root=WORKSPACE_ROOT,
        included_sources=CC_INCLUDED_SOURCES,
        attempt_auto_clear=True,
    )

    # Map GateResult to CC Stop hook protocol
    if result.status != 'blocked':
        # Clean / clear / exempt / auto-cleared -> approve
        print(json.dumps({'continue': True}))
        return

    # Blocked -> format message and exit 2
    unreviewed = result.unreviewed
    tier = result.tier
    count = len(unreviewed)

    file_list = engine.format_file_list(unreviewed)
    files_argv = engine.format_files_argv(unreviewed)

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
3. The reviewer dispatch emits its verdict to a file, then logs the marker:
   python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-default --verdict-file <path-to-verdict.json>
   The verdict file must be a valid gate-peer-reviewer return contract (JSON with verdict, checks_run[], catches[], cost_usd).
4. Then try stopping again.

If blocking findings exist, use --verdict BLOCKING --findings "description" instead.
The BLOCKING verdict still clears the gate (findings were surfaced, not hidden)."""

    print(block_msg, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
