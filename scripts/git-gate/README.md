# Git-gate — Autonomous commit service

Replaces the operator-as-commit-bottleneck with a deterministic pipeline:
runs **enqueue** a structured commit → a **git-gate processor** runs safety
checks + verifies independent reviewer sign-off → auto-commits the reversible
99% → escalates only the irreversible set.

**Hard prerequisite for Hermes autonomy.**

## Architecture

```
Finished run                    Git-gate processor
    │                               │
    ├─ enqueue.py ──────────────►  .commit-queue/<run-id>.json
    │  (writes JSON,                │
    │   no git command,             │  reads queue, runs OUTSIDE gated session
    │   no gate-loop)               │
    │                               ├─ B1: Deterministic safety checks
    │                               │   ├─ file-list-match (no git add .)
    │                               │   ├─ secret-scan (AWS/PEM/GH/Slack)
    │                               │   ├─ cross-client-leak
    │                               │   ├─ verdict-exists (== PASS)
    │                               │   ├─ branch-check
    │                               │   ├─ tier3-paths (no .env/creds)
    │                               │   └─ no-force-push
    │                               │
    │                               ├─ B2: Independent commit sign-off
    │                               │   └─ reviewer-session registry (RGH-15/17)
    │                               │
    │                               ├─ C: Self-healing flag cascade
    │                               │   ├─ Tier 1: auto-fix (lock, stray file)
    │                               │   ├─ Tier 2: investigate + record + CR
    │                               │   └─ Tier 3: escalate to cockpit
    │                               │
    │                               ├─ D: Reversibility line
    │                               │   ├─ auto: normal commit+push (reversible)
    │                               │   └─ gated: force-push/secrets/live-state
    │                               │
    │                               ├─ E: Audit + tripwire
    │                               │   ├─ .git-gate/audit/git-gate-audit.jsonl
    │                               │   └─ .git-gate/tripwire/pause.json
    │                               │
    │                               └─ F: Autonomous-safe action contract
    │                                   └─ Reusable pattern for deploys/publishes
    └─ moves on (no stop-and-wait)
```

## Usage

### Enqueue a commit (from a run)

```bash
python3 repos/ai-agency-core/scripts/git-gate/enqueue.py \
  --repo ~/workspace/second-brain \
  --files file1.md file2.md \
  --message "Update focus file" \
  --verdict-file .review-gate/state/verdict-xxx.json \
  --run-id my-run-001 \
  --chat-id my-chat-001 \
  --session-id <producer-session-uuid> \
  --reviewer-session-id <reviewer-session-uuid> \
  --push
```

### Process the queue

```bash
# Process all pending, then exit
python3 repos/ai-agency-core/scripts/git-gate/git-gate-processor.py --once

# Watch mode (poll every 5s)
python3 repos/ai-agency-core/scripts/git-gate/git-gate-processor.py --watch

# Dry run (checks only, no commits)
python3 repos/ai-agency-core/scripts/git-gate/git-gate-processor.py --dry-run

# Status
python3 repos/ai-agency-core/scripts/git-gate/git-gate-processor.py --status

# Clear tripwire (operator only)
python3 repos/ai-agency-core/scripts/git-gate/git-gate-processor.py --clear-tripwire
```

### Run tests

```bash
cd repos/ai-agency-core/scripts/git-gate
python3 -m pytest tests/test_git_gate.py -v
```

## Files

| File | Purpose |
|---|---|
| `_paths.py` | Shared path derivation (QUEUE_DIR, AUDIT_DIR, etc.) |
| `enqueue.py` | CLI to enqueue a structured commit request |
| `engine.py` | All business logic (safety checks, healing, audit, tripwire) |
| `git-gate-processor.py` | Queue processor (runs OUTSIDE gated sessions) |
| `tests/test_git_gate.py` | 41 regression tests covering AC-3 through AC-10 |

## Design principles

1. **No self-certification** — producer never certifies its own commit
2. **Reversibility is the safety net** — auto-handle reversible, gate irreversible
3. **Self-healing, not flag-piling** — auto-fix mechanical, escalate only judgment
4. **Compounding to zero** — recurring flags → CR/CG + upstream fix
5. **Auditable + tripwired** — watch the watcher

## Dependencies

- Review-gate verdict files (consumed, not modified)
- Reviewer-session registry (RGH-15/17) — `register-reviewer-session.py`
- Does NOT modify gate-check scripts — parallel-safe with RGH-18/19
