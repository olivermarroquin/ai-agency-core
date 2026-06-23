#!/usr/bin/env python3
"""Hermes daemon adapter for the mandatory review gate (Tier C).

Runs on the VPS co-located with Hermes. This is the supervisor wrapper that
gates every autonomous Hermes run: no artifact lands and no run is marked
complete until an ISOLATED-PROCESS reviewer has passed a verdict.

Adapter questions (per substrate-adapter-contract.md):
  (a) Artifact discovery: filesystem diff before/after agent run.
       Hermes agents on the VPS are NOT Claude Code sessions — no PostToolUse
       hook exists. The adapter snapshots the workspace before spawning the
       agent, then diffs after the agent completes. New/modified files become
       dirty entries. This is the "Supervisor polls work dir" mechanism named
       in the adapter contract.
  (b) Blocking: the adapter controls the run lifecycle. On unreviewed
       artifacts it halts the run, writes an escalation file, and does NOT
       advance. The producing agent never marks its own run complete.

Reviewer process identity:
  The reviewer is a SEPARATE OS PROCESS — a Python subprocess that calls the
  Anthropic API directly (not Claude CLI, which is not installed on the VPS).
  This is the strongest independence form: genuinely different PID, no shared
  memory, no shared session. The adapter captures the reviewer's verdict file
  and writes the review-pass marker itself. The producing agent never authors
  its own pass.

Session IDs: hermes-<run-id> (the run_id is passed by the caller or generated).

Source tag: 'hermes' (new, registered in substrate-adapter-contract.md).

Communication path:
  hermes_daemon_adapter.py (supervisor on VPS)
    ├→ snapshot workspace (before)
    ├→ spawn: work agent (Hermes sub-agent or any command)
    │    writes artifacts → VPS filesystem
    ├→ snapshot workspace (after) → diff → dirty entries
    ├→ engine.check_gate() on VPS-local .review-gate/state/
    ├→ if blocked: spawn reviewer process (isolated_reviewer.py)
    │    reads artifacts, writes verdict → VPS .review-gate/state/
    ├→ adapter writes review-pass marker from verdict
    └→ PASS → advance run | BLOCKING → halt-and-escalate

Created by [RGH-3] (2026-06-22).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Optional

# Engine import — works on both Mac (development) and VPS (deployment).
# On VPS, the scripts directory is copied/symlinked to a known location.
sys.path.insert(0, os.path.dirname(__file__))
import engine


# ============================================================================
# Configuration
# ============================================================================

# Default workspace root on VPS (overridable via env or --workspace)
DEFAULT_VPS_WORKSPACE = os.environ.get(
    'HERMES_WORKSPACE', '/opt/hermes/workspace')

# Directories to exclude from filesystem diff (noise reduction)
DIFF_EXCLUDE_DIRS = frozenset({
    '.git', '__pycache__', 'node_modules', '.review-gate',
    '.venv', '.env', '.cache', '.tmp',
})

# File extensions to exclude (binary/generated)
DIFF_EXCLUDE_EXTENSIONS = frozenset({
    '.pyc', '.pyo', '.so', '.o', '.a', '.dylib',
    '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg',
    '.woff', '.woff2', '.ttf', '.eot',
    '.zip', '.tar', '.gz', '.bz2',
})

# Maximum file size to track (skip large binaries)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ============================================================================
# Filesystem snapshot + diff (artifact discovery mechanism)
# ============================================================================

def _should_include(path: str, base_dir: str) -> bool:
    """Check if a file path should be included in the snapshot."""
    rel = os.path.relpath(path, base_dir)
    parts = rel.split(os.sep)

    # Exclude directories
    for part in parts[:-1]:
        if part in DIFF_EXCLUDE_DIRS:
            return False

    # Exclude by extension
    _, ext = os.path.splitext(path)
    if ext.lower() in DIFF_EXCLUDE_EXTENSIONS:
        return False

    return True


def snapshot_workspace(workspace_root: str) -> dict:
    """Take a snapshot of the workspace: {abs_path: (mtime, size, content_hash)}.

    This is the artifact discovery mechanism for Tier C. The adapter diffs
    two snapshots (before/after agent run) to find new/modified files.
    """
    snap = {}
    for dirpath, dirnames, filenames in os.walk(workspace_root):
        # Prune excluded directories in-place (os.walk respects this)
        dirnames[:] = [d for d in dirnames if d not in DIFF_EXCLUDE_DIRS]

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            if not _should_include(fpath, workspace_root):
                continue

            try:
                st = os.stat(fpath)
                if st.st_size > MAX_FILE_SIZE:
                    continue
                # Content hash for reliable diff (mtime can be misleading)
                with open(fpath, 'rb') as f:
                    content_hash = hashlib.sha256(f.read()).hexdigest()
                snap[os.path.realpath(fpath)] = (st.st_mtime, st.st_size, content_hash)
            except (OSError, PermissionError):
                continue

    return snap


def diff_snapshots(before: dict, after: dict) -> list:
    """Diff two workspace snapshots. Returns list of changed/new file paths."""
    changed = []

    # New or modified files
    for fpath, (mtime, size, content_hash) in after.items():
        if fpath not in before:
            changed.append(fpath)
        elif before[fpath][2] != content_hash:  # content hash differs
            changed.append(fpath)

    return sorted(changed)


# ============================================================================
# Dirty-ledger population
# ============================================================================

def record_dirty_artifacts(
    changed_files: list,
    state_dir: str,
    session_id: str,
    run_id: str,
) -> int:
    """Record changed files as dirty entries in the ledger.

    Returns count of entries written.
    """
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    count = 0

    for fpath in changed_files:
        entry = {
            'timestamp': now,
            'iso_time': iso,
            'file_path': engine.normalize_path(fpath),
            'tool': 'hermes-agent',
            'tier': engine.classify_tier(fpath, {}, 'Write'),
            'source': 'hermes',
            'run_id': run_id,
            'session_id': session_id,
        }
        engine.append_dirty_entry(state_dir, session_id, entry)
        count += 1

    return count


# ============================================================================
# Isolated-process reviewer
# ============================================================================

def run_isolated_reviewer(
    dirty_files: list,
    workspace_root: str,
    state_dir: str,
    session_id: str,
    run_id: str,
    mandate_path: str,
    api_key: Optional[str] = None,
    model: str = 'claude-sonnet-4-6',
    timeout: int = 300,
    reviewer_script: Optional[str] = None,
) -> dict:
    """Spawn the reviewer as a SEPARATE OS PROCESS.

    The reviewer is a Python script (isolated_reviewer.py) that:
    1. Reads the mandate from disk (immutable, not authored by the producer)
    2. Reads the dirty files
    3. Runs the review checks
    4. Writes a verdict file to state_dir
    5. Returns the verdict via stdout JSON

    This is the strongest independence form: separate PID, separate memory
    space, no shared session context. The producing agent cannot influence
    the reviewer's execution.

    Returns the parsed verdict dict, or an error dict on failure.
    """
    if not reviewer_script:
        reviewer_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'isolated_reviewer.py'
        )

    if not os.path.isfile(reviewer_script):
        return {
            'verdict': 'ERROR',
            'error': f'Reviewer script not found: {reviewer_script}',
        }

    # Build the reviewer invocation
    env = os.environ.copy()
    if api_key:
        env['ANTHROPIC_API_KEY'] = api_key
    env['REVIEW_GATE_STATE_DIR'] = state_dir

    cmd = [
        sys.executable, reviewer_script,
        '--files', *dirty_files,
        '--session', session_id,
        '--run-id', run_id,
        '--workspace', workspace_root,
        '--mandate', mandate_path,
        '--model', model,
        '--state-dir', state_dir,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            cwd=workspace_root,
        )

        if result.returncode == 0:
            # Parse verdict from stdout
            try:
                verdict = json.loads(result.stdout.strip())
                return verdict
            except json.JSONDecodeError:
                return {
                    'verdict': 'ERROR',
                    'error': f'Reviewer stdout not valid JSON: {result.stdout[:500]}',
                    'stderr': result.stderr[:500],
                }
        else:
            return {
                'verdict': 'ERROR',
                'error': f'Reviewer exited with code {result.returncode}',
                'stderr': result.stderr[:1000],
            }

    except subprocess.TimeoutExpired:
        return {
            'verdict': 'ERROR',
            'error': f'Reviewer timed out after {timeout}s',
        }
    except Exception as e:
        return {
            'verdict': 'ERROR',
            'error': f'Failed to spawn reviewer: {e}',
        }


# ============================================================================
# Halt-and-escalate
# ============================================================================

def halt_and_escalate(
    run_id: str,
    session_id: str,
    verdict: dict,
    unreviewed: list,
    workspace_root: str,
) -> str:
    """Write a hard-escalation file and return its path.

    On a blocking verdict during an unattended run:
    - Write escalation to _meta/escalations/
    - The run does NOT advance
    - The operator returns to a halted-with-reason run
    """
    escalations_dir = os.path.join(
        workspace_root, 'second-brain', '_meta', 'escalations')
    os.makedirs(escalations_dir, exist_ok=True)

    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    esc_filename = f'escalation-hermes-{run_id}-{int(time.time())}.md'
    esc_path = os.path.join(escalations_dir, esc_filename)

    catches = verdict.get('catches', [])
    catch_lines = []
    for c in catches:
        if isinstance(c, dict):
            catch_lines.append(
                f"- **{c.get('severity', '?')}**: {c.get('description', '?')} "
                f"(surface: {c.get('surface', '?')})")
        else:
            catch_lines.append(f"- {c}")

    unreviewed_lines = []
    for e in unreviewed:
        fp = e.get('file_path', '?')
        tier = e.get('tier', '?')
        unreviewed_lines.append(f"- `{fp}` (tier: {tier})")

    content = f"""---
type: escalation
source: hermes-daemon-adapter
run-id: {run_id}
session-id: {session_id}
created: {timestamp}
severity: blocking
status: open
tags: [escalation, hermes, review-gate, rgh-3, autonomous-run]
---

# Escalation — Hermes run halted by review gate

**Run ID:** `{run_id}`
**Session ID:** `{session_id}`
**Timestamp:** {timestamp}
**Verdict:** {verdict.get('verdict', 'BLOCKING')}

## What happened

An autonomous Hermes run produced artifacts that received a **BLOCKING** verdict
from the isolated-process reviewer. The daemon adapter has halted the run.
The run will NOT advance until the operator resolves this escalation.

## Reviewer findings

{chr(10).join(catch_lines) if catch_lines else '(no structured catches in verdict)'}

## Unreviewed artifacts

{chr(10).join(unreviewed_lines) if unreviewed_lines else '(none listed)'}

## Full verdict

```json
{json.dumps(verdict, indent=2)}
```

## Resolution

The operator must:
1. Review the findings above
2. Fix the issues or override
3. Re-run the review gate, or use `gate-skip.py` (operator-only)
4. Resume the Hermes run: `hermes_daemon_adapter.py --resume {run_id}`
"""

    with open(esc_path, 'w') as f:
        f.write(content)

    return esc_path


# ============================================================================
# Graceful degradation logging
# ============================================================================

def log_reviewer_skip(
    run_id: str,
    session_id: str,
    reason: str,
    state_dir: str,
    workspace_root: str,
) -> str:
    """Log a peer-reviewer-skipped event when the reviewer is unavailable.

    The run does NOT silently proceed as if reviewed. It halts with a
    visible skip record so the operator knows the gate didn't fire.
    """
    event_log_path = os.path.join(
        workspace_root, 'second-brain', '_meta', '_event-log.md')

    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    row = (
        f'| {timestamp} | rgh3-hermes-daemon-{run_id} | '
        f'peer-reviewer-skipped: {reason} — run {run_id} HALTED '
        f'(not silently proceeded) | '
        f'.review-gate/state/{session_id}-dirty.jsonl |\n'
    )

    try:
        with open(event_log_path, 'a') as f:
            f.write(row)
    except OSError:
        pass  # best-effort; the halt still happens

    # Also log to metrics
    engine.log_metrics(
        state_dir, session_id, 'reviewer-skipped',
        0, 'full', 0,
        extra={'run_id': run_id, 'reason': reason})

    return row


# ============================================================================
# Main daemon adapter flow
# ============================================================================

class RunResult:
    """Result of a gated Hermes run."""
    def __init__(self, status: str, run_id: str, session_id: str,
                 verdict: Optional[dict] = None,
                 escalation_path: Optional[str] = None,
                 changed_files: Optional[list] = None,
                 error: Optional[str] = None):
        self.status = status  # 'passed' | 'blocked' | 'halted' | 'clean' | 'error'
        self.run_id = run_id
        self.session_id = session_id
        self.verdict = verdict
        self.escalation_path = escalation_path
        self.changed_files = changed_files or []
        self.error = error

    def to_dict(self) -> dict:
        d = {
            'status': self.status,
            'run_id': self.run_id,
            'session_id': self.session_id,
            'changed_files_count': len(self.changed_files),
        }
        if self.verdict:
            d['verdict'] = self.verdict.get('verdict', '?')
        if self.escalation_path:
            d['escalation_path'] = self.escalation_path
        if self.error:
            d['error'] = self.error
        return d


def gate_run(
    agent_command: list,
    workspace_root: str,
    state_dir: str,
    run_id: Optional[str] = None,
    mandate_path: Optional[str] = None,
    api_key: Optional[str] = None,
    reviewer_model: str = 'claude-sonnet-4-6',
    reviewer_timeout: int = 300,
    agent_timeout: int = 600,
    agent_env: Optional[dict] = None,
    reviewer_script: Optional[str] = None,
) -> RunResult:
    """Execute a gated Hermes run.

    This is the main entry point. It:
    1. Snapshots the workspace (before)
    2. Runs the agent command
    3. Snapshots the workspace (after) → diff → dirty entries
    4. Checks the gate
    5. If blocked: spawns isolated reviewer, processes verdict
    6. Returns RunResult with status

    The producing agent NEVER writes its own review-pass marker.
    """
    # Generate run ID if not provided
    if not run_id:
        run_id = f"run-{int(time.time())}-{os.getpid()}"
    session_id = f"hermes-{run_id}"

    # Default mandate path
    if not mandate_path:
        mandate_path = os.path.join(
            workspace_root, 'skills', 'gate-peer-reviewer',
            'references', 'independent-reviewer-mandate.md')

    t_start = time.monotonic()

    # --- Step 1: Snapshot before ---
    print(f'[daemon] Snapshotting workspace before agent run...', file=sys.stderr)
    snap_before = snapshot_workspace(workspace_root)

    # --- Step 2: Run the agent ---
    print(f'[daemon] Spawning agent: {" ".join(agent_command)}', file=sys.stderr)
    env = os.environ.copy()
    if agent_env:
        env.update(agent_env)

    try:
        agent_result = subprocess.run(
            agent_command,
            capture_output=True,
            text=True,
            env=env,
            timeout=agent_timeout,
            cwd=workspace_root,
        )
        agent_exit = agent_result.returncode
        print(f'[daemon] Agent exited with code {agent_exit}', file=sys.stderr)
    except subprocess.TimeoutExpired:
        return RunResult(
            status='error', run_id=run_id, session_id=session_id,
            error=f'Agent timed out after {agent_timeout}s')
    except Exception as e:
        return RunResult(
            status='error', run_id=run_id, session_id=session_id,
            error=f'Agent spawn failed: {e}')

    # --- Step 3: Snapshot after → diff → dirty entries ---
    print(f'[daemon] Snapshotting workspace after agent run...', file=sys.stderr)
    snap_after = snapshot_workspace(workspace_root)
    changed = diff_snapshots(snap_before, snap_after)

    if not changed:
        print(f'[daemon] No artifacts changed — run is clean.', file=sys.stderr)
        wall_ms = (time.monotonic() - t_start) * 1000
        engine.log_metrics(state_dir, session_id, 'daemon-clean',
                           0, 'n/a', wall_ms, extra={'run_id': run_id})
        return RunResult(status='clean', run_id=run_id, session_id=session_id,
                         changed_files=changed)

    # --- SECURITY: Purge any pre-existing reviewed ledger for this session ---
    # The agent could have pre-planted fake reviewed entries (including with
    # future timestamps) in the daemon's session ledger. No legitimate reviewed
    # entry can exist before the daemon's own review cycle — the session_id is
    # freshly generated (hermes-<run_id>) and the daemon hasn't dispatched a
    # reviewer yet. Any pre-existing entry is agent-planted. Purge it.
    # (Addresses C-1: future-timestamp reviewed-entry bypass.)
    reviewed_ledger_path = os.path.join(state_dir, f'{session_id}-reviewed.jsonl')
    if os.path.exists(reviewed_ledger_path):
        print(f'[daemon] SECURITY: Purging pre-existing reviewed ledger '
              f'(agent may have planted fake markers).', file=sys.stderr)
        os.remove(reviewed_ledger_path)

    print(f'[daemon] {len(changed)} artifacts changed. Recording dirty entries...',
          file=sys.stderr)
    dirty_count = record_dirty_artifacts(changed, state_dir, session_id, run_id)

    # --- Step 4: Check the gate ---
    gate_result = engine.check_gate(
        state_dir=state_dir,
        session_id=session_id,
        workspace_root=workspace_root,
        included_sources=None,  # daemon sees all sources
        attempt_auto_clear=True,  # fast-path auto-clear for trivial changes
    )

    if gate_result.status in ('clean', 'clear', 'exempt', 'auto-cleared'):
        print(f'[daemon] Gate status: {gate_result.status} — run can advance.',
              file=sys.stderr)
        return RunResult(status='passed', run_id=run_id, session_id=session_id,
                         changed_files=changed)

    # --- Step 5: Blocked — spawn isolated reviewer ---
    print(f'[daemon] Gate BLOCKED ({len(gate_result.unreviewed)} unreviewed, '
          f'tier: {gate_result.tier}). Spawning isolated reviewer...',
          file=sys.stderr)

    dirty_file_paths = [e.get('file_path', '') for e in gate_result.unreviewed
                        if not e.get('file_path', '').startswith('BASH:')]

    if not dirty_file_paths:
        # All unreviewed are BASH entries — can't review files
        return RunResult(
            status='error', run_id=run_id, session_id=session_id,
            error='All unreviewed entries are BASH commands (no file paths to review)',
            changed_files=changed)

    reviewer_verdict = run_isolated_reviewer(
        dirty_files=dirty_file_paths,
        workspace_root=workspace_root,
        state_dir=state_dir,
        session_id=session_id,
        run_id=run_id,
        mandate_path=mandate_path,
        api_key=api_key,
        model=reviewer_model,
        timeout=reviewer_timeout,
        reviewer_script=reviewer_script,
    )

    # --- Step 6: Process reviewer verdict ---
    if reviewer_verdict.get('verdict') == 'ERROR':
        # Reviewer unavailable — log skip, halt (never silent)
        reason = reviewer_verdict.get('error', 'unknown reviewer error')
        print(f'[daemon] Reviewer ERROR: {reason}. Logging skip + halting.',
              file=sys.stderr)
        log_reviewer_skip(run_id, session_id, reason, state_dir, workspace_root)

        esc_path = halt_and_escalate(
            run_id, session_id,
            {'verdict': 'BLOCKING', 'error': reason,
             'catches': [{'severity': 'blocking',
                          'description': f'Reviewer unavailable: {reason}',
                          'surface': 'daemon-adapter'}]},
            gate_result.unreviewed, workspace_root)

        return RunResult(
            status='halted', run_id=run_id, session_id=session_id,
            verdict=reviewer_verdict, escalation_path=esc_path,
            changed_files=changed,
            error=f'Reviewer unavailable: {reason}')

    # Validate the verdict file
    verdict_file_path = reviewer_verdict.get('verdict_file')
    if verdict_file_path and os.path.isfile(verdict_file_path):
        verdict_data, val_err = engine.validate_verdict_file(
            verdict_file_path, gate_result.tier)
        if val_err:
            print(f'[daemon] Verdict file invalid: {val_err}', file=sys.stderr)
            esc_path = halt_and_escalate(
                run_id, session_id,
                {'verdict': 'BLOCKING', 'error': val_err},
                gate_result.unreviewed, workspace_root)
            return RunResult(
                status='halted', run_id=run_id, session_id=session_id,
                verdict=reviewer_verdict, escalation_path=esc_path,
                changed_files=changed)
    else:
        verdict_data = reviewer_verdict
        verdict_file_path = None

    # Use the verdict data from the file if available, otherwise from stdout
    effective_verdict = verdict_data if verdict_data else reviewer_verdict

    if effective_verdict.get('verdict') == 'PASS':
        # --- PASS: write review-pass markers (daemon writes, not the producer) ---
        print(f'[daemon] Reviewer PASSED. Writing review-pass markers...',
              file=sys.stderr)

        evidence = engine.derive_evidence(effective_verdict)
        markers = engine.build_review_markers(
            file_paths=dirty_file_paths,
            verdict='PASS',
            tier=gate_result.tier,
            gate_id='G-hermes-daemon',
            evidence=evidence,
            verdict_file=verdict_file_path or '',
            verdict_data=effective_verdict,
        )

        # Stamp reviewer metadata — the daemon writes this, proving isolation
        for m in markers:
            m['reviewer_type'] = 'independent'
            m['reviewer_process'] = 'isolated'  # provenance: separate OS process
            m['run_id'] = run_id

        engine.append_reviewed_entries(state_dir, session_id, markers)

        wall_ms = (time.monotonic() - t_start) * 1000
        engine.log_metrics(state_dir, session_id, 'daemon-pass',
                           len(dirty_file_paths), gate_result.tier, wall_ms,
                           extra={'run_id': run_id})

        return RunResult(
            status='passed', run_id=run_id, session_id=session_id,
            verdict=effective_verdict, changed_files=changed)

    else:
        # --- BLOCKING or unknown: halt and escalate ---
        print(f'[daemon] Reviewer verdict: {effective_verdict.get("verdict")}. '
              f'Halting run.', file=sys.stderr)

        esc_path = halt_and_escalate(
            run_id, session_id, effective_verdict,
            gate_result.unreviewed, workspace_root)

        wall_ms = (time.monotonic() - t_start) * 1000
        engine.log_metrics(state_dir, session_id, 'daemon-halt',
                           len(dirty_file_paths), gate_result.tier, wall_ms,
                           extra={'run_id': run_id})

        return RunResult(
            status='halted', run_id=run_id, session_id=session_id,
            verdict=effective_verdict, escalation_path=esc_path,
            changed_files=changed)


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Hermes daemon adapter — gates autonomous runs with '
                    'isolated-process review (Tier C)')
    parser.add_argument('--workspace', default=DEFAULT_VPS_WORKSPACE,
                        help=f'Workspace root (default: {DEFAULT_VPS_WORKSPACE})')
    parser.add_argument('--state-dir', default=None,
                        help='Review gate state dir (default: <workspace>/.review-gate/state/)')
    parser.add_argument('--run-id', default=None,
                        help='Run ID (default: auto-generated)')
    parser.add_argument('--mandate', default=None,
                        help='Path to independent-reviewer-mandate.md')
    parser.add_argument('--api-key', default=None,
                        help='Anthropic API key for the reviewer (or set ANTHROPIC_API_KEY)')
    parser.add_argument('--reviewer-model', default='claude-sonnet-4-6',
                        help='Model for the isolated reviewer')
    parser.add_argument('--reviewer-timeout', type=int, default=300,
                        help='Reviewer timeout in seconds')
    parser.add_argument('--agent-timeout', type=int, default=600,
                        help='Agent timeout in seconds')
    parser.add_argument('command', nargs='+',
                        help='Agent command to run (e.g. "hermes run task-xyz")')

    args = parser.parse_args()

    workspace = os.path.realpath(args.workspace)
    state_dir = args.state_dir or os.path.join(workspace, '.review-gate', 'state')

    result = gate_run(
        agent_command=args.command,
        workspace_root=workspace,
        state_dir=state_dir,
        run_id=args.run_id,
        mandate_path=args.mandate,
        api_key=args.api_key or os.environ.get('ANTHROPIC_API_KEY'),
        reviewer_model=args.reviewer_model,
        reviewer_timeout=args.reviewer_timeout,
        agent_timeout=args.agent_timeout,
    )

    # Output result as JSON to stdout
    print(json.dumps(result.to_dict(), indent=2))

    # Exit code: 0 for passed/clean, 1 for halted/blocked, 2 for error
    if result.status in ('passed', 'clean'):
        sys.exit(0)
    elif result.status in ('halted', 'blocked'):
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == '__main__':
    main()
