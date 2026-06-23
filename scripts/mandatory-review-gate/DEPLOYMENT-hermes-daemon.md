# Hermes Daemon Adapter — VPS Deployment Guide

> Operator-facing deployment instructions for RGH-3 (Tier C).
> The daemon adapter runs on the VPS co-located with Hermes.
> All steps below are operator-run — the producing agent never touches the VPS.

## Prerequisites

- Hermes Phase 1 deployed and running on VPS (2.25.210.133)
- Python 3.10+ on VPS
- Anthropic API key configured for the reviewer process
- `anthropic` Python package installed (`pip install anthropic`)

## Files to deploy

Copy these files from `repos/ai-agency-core/scripts/mandatory-review-gate/` to
the VPS (e.g., `/opt/hermes/review-gate/`):

```bash
scp hermes_daemon_adapter.py isolated_reviewer.py engine.py _paths.py \
    user@2.25.210.133:/opt/hermes/review-gate/
```

Also copy the independent reviewer mandate:

```bash
scp ~/workspace/skills/gate-peer-reviewer/references/independent-reviewer-mandate.md \
    user@2.25.210.133:/opt/hermes/review-gate/mandate.md
```

## VPS setup

```bash
# On the VPS:

# 1. Create workspace + state directories
mkdir -p /opt/hermes/workspace/.review-gate/state
mkdir -p /opt/hermes/workspace/second-brain/_meta/escalations

# 2. Create a minimal _event-log.md for graceful-degradation logging
echo '| timestamp | chat-id | event | files |' \
    > /opt/hermes/workspace/second-brain/_meta/_event-log.md

# 3. Install anthropic package for the isolated reviewer
pip install anthropic

# 4. Set the API key (add to Hermes env or .bashrc)
export ANTHROPIC_API_KEY='sk-ant-...'

# 5. Override _paths.py workspace root for VPS layout
# The default _paths.py derives WORKSPACE_ROOT from __file__ (4 levels up).
# On VPS the scripts are at /opt/hermes/review-gate/, not under ~/workspace/.
# Set the env var to override:
export REVIEW_GATE_STATE_DIR=/opt/hermes/workspace/.review-gate/state
```

## Usage

### Gate a single agent run

```bash
python3 /opt/hermes/review-gate/hermes_daemon_adapter.py \
    --workspace /opt/hermes/workspace \
    --mandate /opt/hermes/review-gate/mandate.md \
    --run-id "task-build-page-42" \
    --reviewer-model claude-sonnet-4-6 \
    --reviewer-timeout 300 \
    --agent-timeout 600 \
    -- hermes run task-build-page-42
```

The adapter:
1. Snapshots the workspace
2. Runs `hermes run task-build-page-42`
3. Diffs the workspace to find changed files
4. Checks the gate (fast-path auto-clear for trivial changes)
5. If blocked: spawns `isolated_reviewer.py` as a separate process
6. On PASS: writes review-pass marker, exits 0
7. On BLOCKING: writes escalation file, exits 1
8. On reviewer error: logs skip, writes escalation, exits 1

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | Run passed (clean or reviewer PASS) |
| 1    | Run halted (BLOCKING verdict or reviewer unavailable) |
| 2    | Error (agent timeout, spawn failure, etc.) |

### JSON output

The adapter prints a JSON result to stdout:

```json
{
  "status": "passed",
  "run_id": "task-build-page-42",
  "session_id": "hermes-task-build-page-42",
  "changed_files_count": 3,
  "verdict": "PASS"
}
```

## Operator acceptance tests (VPS)

These prove the adapter works on the real VPS with real Hermes — the
complement to the local-logic conformance suite (28 tests on Mac).

### Test 1: Clean run (no changes)

```bash
# Run an agent that does nothing
python3 /opt/hermes/review-gate/hermes_daemon_adapter.py \
    --workspace /opt/hermes/workspace \
    --mandate /opt/hermes/review-gate/mandate.md \
    --run-id "test-clean" \
    -- echo "no-op"

# Expected: exit 0, status "clean"
```

### Test 2: Defect blocked

```bash
# Create a defective agent script
cat > /tmp/defect-agent.sh << 'EOF'
echo "PLACEHOLDER: TODO fill this in" > /opt/hermes/workspace/test-defect.md
EOF

python3 /opt/hermes/review-gate/hermes_daemon_adapter.py \
    --workspace /opt/hermes/workspace \
    --mandate /opt/hermes/review-gate/mandate.md \
    --run-id "test-defect" \
    -- bash /tmp/defect-agent.sh

# Expected: exit 1, status "halted", escalation file created
ls /opt/hermes/workspace/second-brain/_meta/escalations/escalation-hermes-test-defect-*.md
# Should exist

# Clean up
rm /opt/hermes/workspace/test-defect.md
```

### Test 3: Clean artifact passes

```bash
# Create a clean agent script
cat > /tmp/clean-agent.sh << 'EOF'
echo "This is a clean, complete artifact." > /opt/hermes/workspace/test-clean.txt
EOF

python3 /opt/hermes/review-gate/hermes_daemon_adapter.py \
    --workspace /opt/hermes/workspace \
    --mandate /opt/hermes/review-gate/mandate.md \
    --run-id "test-clean-pass" \
    -- bash /tmp/clean-agent.sh

# Expected: exit 0, status "passed" (auto-cleared for tiny clean file)

# Clean up
rm /opt/hermes/workspace/test-clean.txt
```

### Test 4: Reviewer unavailable

```bash
# Point at a nonexistent mandate to see graceful degradation
python3 /opt/hermes/review-gate/hermes_daemon_adapter.py \
    --workspace /opt/hermes/workspace \
    --mandate /nonexistent/mandate.md \
    --run-id "test-no-reviewer" \
    --reviewer-timeout 5 \
    -- bash -c 'echo "state-path content" > /opt/hermes/workspace/second-brain/_meta/test-noreview.md'

# For full-tier files: should halt (not silently pass)
# Check event log for peer-reviewer-skipped
grep "peer-reviewer-skipped" /opt/hermes/workspace/second-brain/_meta/_event-log.md

# Clean up
rm /opt/hermes/workspace/second-brain/_meta/test-noreview.md
```

## Integration with Hermes harness

Once Phase 2 vault-bridge ships, the Hermes harness can invoke the daemon
adapter at run/wave boundaries:

```python
# In the Hermes harness orchestration loop:
import subprocess, json

result = subprocess.run(
    ['python3', '/opt/hermes/review-gate/hermes_daemon_adapter.py',
     '--workspace', '/opt/hermes/workspace',
     '--mandate', '/opt/hermes/review-gate/mandate.md',
     '--run-id', f'wave-{wave_id}',
     '--', 'hermes', 'run', task_id],
    capture_output=True, text=True)

outcome = json.loads(result.stdout)
if outcome['status'] != 'passed':
    # Halt the wave, notify operator via Telegram
    send_escalation(outcome)
```

## Security model

- The adapter runs as a supervisor wrapper — it spawns the agent, not the
  other way around. The agent cannot bypass the gate.
- The reviewer is a separate OS process (different PID). The producing agent
  never sees the reviewer's verdict file or writes its own review-pass marker.
- Dirty entries are written by the daemon AFTER the agent exits, with fresh
  timestamps. Pre-planted fake markers from the agent are stale.
- Option A security model: the adapter code is built on the Mac, deployed by
  the operator via scp. The producing chat never touches the VPS directly.

## Honest limits

- **Filesystem-diff discovery has latency.** The full workspace is snapshotted
  before and after each run. For large workspaces this adds seconds. Tune
  `DIFF_EXCLUDE_DIRS` and `DIFF_EXCLUDE_EXTENSIONS` if needed.
- **The reviewer uses the Anthropic API directly.** If the API is down or the
  key is expired, the reviewer fails. The adapter halts (never silently passes)
  but the run is blocked until the operator resolves it.
- **Sync to Mac is Phase 2's job.** Artifacts gated on the VPS don't
  automatically reach the Mac. When Phase 2 vault-bridge ships a sync
  mechanism, the Mac-side git hook (Tier B) provides a second enforcement layer.
- **Content-hash diffing reads all files.** The adapter reads file contents for
  SHA-256 hashing. Exclude sensitive directories via `DIFF_EXCLUDE_DIRS` if the
  workspace contains credentials (which it shouldn't per Option A).
