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

CIRCUIT BREAKER (RGH-CB): tracks consecutive identical blocks via a per-session
state file (<session>-stop-hook-history.json). If the same set of unreviewed
entry keys blocks N consecutive times (N=3, configurable) with zero new entries
between firings, auto-skips with a LOUD warning and logs a circuit-breaker-
triggered event to metrics.jsonl. Counter resets when new entries appear or
entries are cleared.

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

# Circuit breaker: max consecutive identical blocks before auto-skip (RGH-CB)
CIRCUIT_BREAKER_MAX = 3

# Path to the independent reviewer dispatch script
DISPATCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'independent-reviewer-dispatch.py')

# Path to the mandate (for the block message)
MANDATE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', '..',
    'skills', 'gate-peer-reviewer', 'references',
    'independent-reviewer-mandate.md'))


# ============================================================================
# Circuit breaker (RGH-CB)
# ============================================================================
#
# ANTI-POISONING (CR-011): The history file is append-only JSONL. Each stop-hook
# firing appends one row with the entry-key fingerprint (SHA-256 of the sorted
# entry keys). The circuit breaker reads ALL rows and counts trailing consecutive
# rows with the same fingerprint. A poisoned/pre-seeded file cannot skip the
# counter requirement — it would need to append rows with the correct fingerprint
# for a set of entries that hasn't been seen yet, and the fingerprint is derived
# from the actual unreviewed set at runtime. Overwriting the file resets the
# counter to 0 (no rows = no history).

import hashlib as _hashlib


def _history_path(session_id):
    """Path to the per-session stop-hook history file (append-only JSONL)."""
    return os.path.join(STATE_DIR, f'{session_id}-stop-hook-history.jsonl')


def _entry_fingerprint(entry_keys):
    """SHA-256 fingerprint of the sorted entry key list."""
    content = '\n'.join(entry_keys)
    return _hashlib.sha256(content.encode()).hexdigest()


def _read_history_tail(session_id):
    """Read the history log and return trailing consecutive count + fingerprint.

    Returns (consecutive_count, last_fingerprint). Counts how many trailing
    rows share the same fingerprint. If the file doesn't exist or is empty,
    returns (0, '').
    """
    path = _history_path(session_id)
    if not os.path.isfile(path):
        return 0, ''
    rows = []
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return 0, ''

    if not rows:
        return 0, ''

    # Count trailing rows with same fingerprint
    last_fp = rows[-1].get('fingerprint', '')
    count = 0
    for row in reversed(rows):
        if row.get('fingerprint', '') == last_fp:
            count += 1
        else:
            break
    return count, last_fp


def _append_history(session_id, entry_keys):
    """Append one row to the history log. Returns the fingerprint written."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fp = _entry_fingerprint(entry_keys)
    row = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'fingerprint': fp,
        'entry_count': len(entry_keys),
    }
    path = _history_path(session_id)
    with open(path, 'a') as f:
        f.write(json.dumps(row) + '\n')
    return fp


def _reset_history(session_id):
    """Truncate the history log (used after circuit breaker fires)."""
    path = _history_path(session_id)
    if os.path.isfile(path):
        with open(path, 'w') as f:
            pass  # truncate


def _check_circuit_breaker(session_id, unreviewed):
    """Check if the circuit breaker should fire.

    Returns (should_skip: bool, consecutive_count: int).
    Side effect: appends to the append-only history log.
    """
    current_keys = sorted(e.get('file_path', '') for e in unreviewed)
    current_fp = _entry_fingerprint(current_keys)

    # Read existing history BEFORE appending
    tail_count, tail_fp = _read_history_tail(session_id)

    # Append this firing
    _append_history(session_id, current_keys)

    if current_fp == tail_fp:
        # Same set as previous firings — count is tail + 1 (this one)
        count = tail_count + 1
    else:
        # Different set — this firing starts a new run of 1
        count = 1

    return count >= CIRCUIT_BREAKER_MAX, count


def _circuit_breaker_skip(session_id, unreviewed, consecutive_count):
    """Execute the circuit breaker: write gate-skip marker, log metrics, allow stop.

    Writes a LOUD warning to stderr and exits 0 (allow stop).
    """
    entry_keys = sorted(e.get('file_path', '') for e in unreviewed)

    # Log to metrics.jsonl
    engine.log_metrics(
        STATE_DIR, session_id, 'circuit-breaker-triggered',
        len(unreviewed), 'n/a', 0,
        extra={
            'event': 'circuit-breaker-triggered',
            'consecutive_blocks': consecutive_count,
            'entries': entry_keys,
        },
    )

    # Write gate-skip marker so reviewed ledger records the skip
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    reviewed_path = os.path.join(STATE_DIR, f'{session_id}-reviewed.jsonl')
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(reviewed_path, 'a') as f:
        for key in entry_keys:
            marker = {
                'timestamp': now,
                'iso_time': iso,
                'file_path': key,
                'verdict': 'SKIP',
                'tier': 'n/a',
                'gate_id': 'G-circuit-breaker',
                'evidence': (f'circuit-breaker: {consecutive_count} consecutive '
                             f'identical blocks, auto-skip'),
                'verdict_file': None,
                'verdict_data': None,
                'findings': None,
            }
            f.write(json.dumps(marker) + '\n')

    # Reset history after firing (truncate the append-only log)
    _reset_history(session_id)

    # LOUD warning
    warning = f"""
╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  CIRCUIT BREAKER TRIGGERED — AUTO-SKIP (RGH-CB)                ║
╠══════════════════════════════════════════════════════════════════════╣
║  The stop hook blocked on the SAME {len(unreviewed)} unreviewed entry/entries     ║
║  {consecutive_count} consecutive times with no new entries between firings.      ║
║  This is the infinite-loop protection — the gate is being skipped.  ║
║                                                                      ║
║  Skipped entries:                                                    ║"""
    for key in entry_keys:
        warning += f'\n║    - {key[:62]:<62} ║'
    warning += f"""
║                                                                      ║
║  ACTION REQUIRED: Investigate why these entries cannot be cleared.   ║
║  Common causes:                                                      ║
║    1. Read-only commands classified as state-changing                ║
║    2. Gate infrastructure writes creating unclearable entries        ║
║    3. Reviewer dispatch creating new dirty entries                   ║
╚══════════════════════════════════════════════════════════════════════╝
"""
    print(warning, file=sys.stderr)
    print(json.dumps({'continue': True}))


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
        # Reset circuit breaker on successful pass (entries were cleared)
        _reset_history(session_id)
        print(json.dumps({'continue': True}))
        return

    # Blocked — check circuit breaker BEFORE doing anything else (RGH-CB)
    unreviewed = result.unreviewed
    should_skip, consecutive_count = _check_circuit_breaker(session_id, unreviewed)
    if should_skip:
        _circuit_breaker_skip(session_id, unreviewed, consecutive_count)
        return

    # Blocked -> auto-run independent dispatch (RGH-5)
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

    # Still blocked — format the block message (RGH-20 Part B: compact,
    # operator-first, ≤8 lines on terminal, full detail in block file)
    file_list = engine.format_file_list(unreviewed)
    files_argv = engine.format_files_argv(unreviewed)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')
    skip_script = os.path.join(script_dir, 'gate-skip.py')

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

    # RGH-1b item 1: surface auto-clear refusal so operator can distinguish
    # "auto-clear attempted but failed" from "auto-clear never attempted"
    auto_clear_refusal = result.auto_clear_refusal

    # --- RGH-20 Part C: repeat-block dedupe ---
    # Hash the unreviewed set; if identical to last block → one-line reminder.
    # _check_circuit_breaker above already appended one row for THIS firing,
    # so tail_count >= 2 means the PREVIOUS firing also had the same set —
    # this is truly a repeat, not the first block.
    block_fingerprint = _entry_fingerprint(
        sorted(e.get('file_path', '') for e in unreviewed))
    last_count, last_fp = _read_history_tail(session_id)
    if last_fp == block_fingerprint and last_count >= 2:
        # Same set as last block — print one-line reminder, don't repeat
        _append_history(session_id,
                        sorted(e.get('file_path', '') for e in unreviewed))
        reminder = (f'REVIEW GATE -- still blocked (same {count} items). '
                    f'Waiting on operator skip or reviewer log-pass.')
        print(reminder, file=sys.stderr)
        sys.exit(2)

    # --- Classify entries as deliverables vs plumbing ---
    deliverable_entries = []
    plumbing_entries = []
    for e in unreviewed:
        fp = e.get('file_path', '')
        tool = e.get('tool', '')
        bash_cmd_e = e.get('bash_cmd', e.get('display', ''))
        annotation = engine.is_plumbing_exempt(fp, tool, bash_cmd_e,
                                                WORKSPACE_ROOT)
        if annotation:
            plumbing_entries.append(e)
        else:
            deliverable_entries.append(e)
    n_deliverables = len(deliverable_entries)
    n_plumbing = len(plumbing_entries)

    # --- Build compact terminal message (≤8 lines) ---
    # Line 1: headline
    headline = (f'REVIEW GATE BLOCKED -- {count} unreviewed '
                f'({n_deliverables} deliverables, {n_plumbing} plumbing). '
                f'Producer cannot self-clear.')

    # Lines 2..N: bulleted findings (basename only, one per line)
    bullets = []
    for e in unreviewed:
        fp = e.get('file_path', '')
        if fp.startswith('BASH:'):
            label = e.get('display', fp)[:60]
        else:
            label = os.path.basename(fp)
        tier_e = e.get('tier', '?')
        bullets.append(f'  - {label} ({tier_e})')

    # Decision line
    if n_deliverables > 0:
        decision = (
            f'-> Relay to your reviewer: "Producer blocked on '
            f'{n_deliverables} deliverables -- verify and log the '
            f'review-pass marker; full details in the block file."')
    else:
        # Plumbing only — write a skip script for the operator
        skip_dir = os.path.join(WORKSPACE_ROOT, '_scratch', 'skip')
        os.makedirs(skip_dir, exist_ok=True)
        skip_script_path = os.path.join(
            skip_dir,
            f'.gate-skip-{session_id[:12]}.sh')
        skip_cmd = (f'python3 {skip_script} --session {session_id} '
                    f'--reason "CR-219 plumbing-only block"')
        try:
            with open(skip_script_path, 'w') as f:
                f.write('#!/usr/bin/env bash\n')
                f.write(f'{skip_cmd}\n')
            os.chmod(skip_script_path, 0o755)
        except OSError:
            pass
        decision = (
            f'-> Operator skip OK: '
            f'bash {skip_script_path} && rm {skip_script_path}')

    # --- Write block file with full details + ready-made commands ---
    block_file_path = os.path.join(
        STATE_DIR,
        f'block-{session_id}-{int(time.time())}.md')
    os.makedirs(STATE_DIR, exist_ok=True)

    block_file_content = f"""# Review Gate Block Details

**Session:** {session_id}
**Tier:** {tier}
**Deliverables:** {n_deliverables}  |  **Plumbing:** {n_plumbing}

## Unreviewed entries
{file_list}
"""
    if auto_clear_refusal:
        block_file_content += f'\n## Auto-clear refusal\n{auto_clear_refusal}\n'
    if dispatch_findings:
        block_file_content += f'\n## Independent dispatch findings\n{dispatch_findings}\n'

    if needs_independent:
        block_file_content += f"""
## Ready-made commands

### For reviewer (log review-pass):
```
python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-independent --verdict-file <path> --reviewer-type independent --reviewer-session <REVIEWER_SESSION_ID> --run-id <chat-slug>
```

### For operator (emergency skip):
```
python3 {skip_script} --session {session_id} --reason "reviewer plumbing"
```

## Protocol
Full protocol: {MANDATE_PATH}
Producer cannot self-clear. A sub-agent does NOT count (shares session_id).
"""
    else:
        block_file_content += f"""
## Ready-made commands

### Self-review (fast-path):
```
python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-default --verdict-file <path-to-verdict.json>
```

Verdict file must be a valid gate-peer-reviewer return contract (JSON with verdict, checks_run[], catches[], cost_usd).
Use --verdict BLOCKING --findings "description" for blocking findings.
"""

    try:
        with open(block_file_path, 'w') as f:
            f.write(block_file_content)
    except OSError:
        block_file_path = '(failed to write block file)'

    # Assemble terminal message (compact)
    bullet_str = '\n'.join(bullets[:6])  # cap at 6 to stay within 8 lines
    if len(bullets) > 6:
        bullet_str += f'\n  ... and {len(bullets) - 6} more'

    block_msg = f"""{headline}
{bullet_str}
{decision}
Details + ready-made commands: {block_file_path}"""

    # NOTE: _check_circuit_breaker() already appended to history above —
    # no duplicate append needed here.

    print(block_msg, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
