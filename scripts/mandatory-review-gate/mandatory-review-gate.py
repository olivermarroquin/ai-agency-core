#!/usr/bin/env python3
"""Stop hook: blocks turn-end if dirty files lack review-pass markers.

Invoked by Claude Code before the model stops (returns control to the user).
This is the Claude Code adapter for the Stop hook. Substrate-agnostic gate
logic lives in engine.py.

OWN-LEDGER-ONLY SCOPING (RGH-1.6): scans only the current session's dirty
ledger. Sub-agent coverage is preserved because sub-agent PostToolUse events
inherit the parent session_id in Claude Code (confirmed by real-runner evidence
2026-06-12, CC v2.1.85).

INDEPENDENT REVIEWER DISPATCH (RGH-5): when blocking on full-tier items,
auto-runs the deterministic independent reviewer (OC-12..16 + fast-path sweeps)
and instructs the producer to spawn the LLM adversarial reviewer agent.
Full-tier items require reviewer_type='independent' to clear. Fast-path items
are auto-cleared by the deterministic dispatch ($0).

Stdin:  JSON from Claude Code hook system (session_id, stop_reason, etc.)
Stdout: JSON acknowledgment when all clean (exit 0)
Stderr: Block message with unreviewed file list (exit 2)
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT
import engine

# Sources that the CC Stop hook includes in its scope.
# Cowork/other-substrate entries are excluded — coverage deferred to
# Tier B git pre-commit hook (RGH-2).
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})  # '' = legacy entries without source field

# Path to the independent reviewer dispatch script
DISPATCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'independent-reviewer-dispatch.py')

# Path to the mandate (for the block message)
MANDATE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', '..',
    'skills', 'gate-peer-reviewer', 'references',
    'independent-reviewer-mandate.md'))


def _run_independent_dispatch(session_id, tier, unreviewed):
    """Auto-run the deterministic independent reviewer dispatch.

    Returns (dispatch_result_dict, stderr_output) or (None, error_msg).
    """
    file_paths = [e.get('file_path', '') for e in unreviewed
                  if not e.get('file_path', '').startswith('BASH:')]

    if not os.path.isfile(DISPATCH_SCRIPT):
        return None, 'independent-reviewer-dispatch.py not found'

    cmd = [
        sys.executable, DISPATCH_SCRIPT,
        '--session', session_id,
        '--state-dir', STATE_DIR,
        '--workspace-root', WORKSPACE_ROOT,
        '--tier', tier,
    ]
    if file_paths:
        cmd.extend(['--files'] + file_paths)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120)
        try:
            result = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            result = {}
        return result, proc.stderr
    except subprocess.TimeoutExpired:
        return None, 'independent dispatch timed out (120s)'
    except Exception as e:
        return None, f'independent dispatch error: {e}'


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

    # Blocked -> auto-run independent dispatch (RGH-5)
    unreviewed = result.unreviewed
    tier = result.tier
    count = len(unreviewed)

    dispatch_result, dispatch_stderr = _run_independent_dispatch(
        session_id, tier, unreviewed)

    # NOTE: the dispatch reports findings but does NOT auto-clear the gate
    # through the Stop hook. The existing check_gate(attempt_auto_clear=True)
    # at the top already handles legitimate fast-path auto-clear. Running a
    # recheck here would defeat the "re-edit after review must re-block"
    # safety invariant (D-04). The dispatch's role is to surface deterministic
    # findings and instruct the LLM agent spawn — not to bypass the gate.

    # Still blocked — format the block message
    file_list = engine.format_file_list(unreviewed)
    files_argv = engine.format_files_argv(unreviewed)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')

    # Include dispatch findings if any
    dispatch_findings = ''
    if dispatch_result and dispatch_result.get('catches', 0) > 0:
        dispatch_findings = (
            f'\n[independent-review] Deterministic checks found '
            f'{dispatch_result["catches"]} issue(s):\n'
            f'{dispatch_result.get("findings", "see verdict file")}\n'
            f'Verdict file: {dispatch_result.get("verdict_file", "?")}\n')

    # Check if this needs independent review (full tier)
    needs_independent = tier == 'full'

    if needs_independent:
        block_msg = f"""MANDATORY PRE-LAND REVIEW GATE — BLOCKED (independent review required)

{count} unreviewed artifact(s) detected. Review tier: {tier}.
{dispatch_findings}
Unreviewed:
{file_list}

REQUIRED ACTION — INDEPENDENT REVIEW (RGH-5):

The gate requires an INDEPENDENT reviewer for full-tier items. You (the producer)
cannot clear your own gate. Spawn a separate adversarial reviewer agent:

1. Use the Agent tool to spawn a general-purpose agent with this prompt:

   Read and execute the independent reviewer mandate at:
   {MANDATE_PATH}

   Session: {session_id}
   Tier: {tier}
   Files to review: {files_argv}

   Run the full review protocol (Phases A-E) per the mandate.
   Write the verdict file and log the review-pass marker with:
   python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-independent --verdict-file <verdict-path> --reviewer-type independent

2. The agent reads the mandate FROM DISK (fixed, you cannot edit it),
   does its own disk verification, and writes its own verdict.

3. Once the independent reviewer logs PASS with --reviewer-type independent,
   try stopping again.

DO NOT attempt to self-clear (log --reviewer-type independent yourself).
That is a D-09 class defect. Only the independent reviewer agent does this."""
    else:
        # Fast-path — deterministic dispatch should have handled it,
        # but if it didn't (e.g., dispatch failed), fall back to self-review
        fast_desc = (
            'fast-path = grep-based surface sweep + source-client-leak-audit '
            '+ link-resolution (no Sonar/LLM-research)')

        block_msg = f"""MANDATORY PRE-LAND REVIEW GATE — BLOCKED

{count} unreviewed artifact(s) detected. Review tier: {tier}.
{dispatch_findings}
Unreviewed:
{file_list}

REQUIRED ACTION before you can stop:
1. Dispatch gate-peer-reviewer on the touched artifacts/state.
   - Use the registered gate type if one applies; otherwise use G-default.
   - Tier '{tier}': {fast_desc}.
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
