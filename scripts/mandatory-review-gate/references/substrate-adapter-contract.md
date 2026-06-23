# Substrate Adapter Contract

> Defines the interface between the substrate-agnostic gate engine (`engine.py`)
> and per-substrate enforcement adapters. Any new adapter must implement this
> contract and pass the per-adapter conformance suite.
>
> Created by [RGH-2] (2026-06-16). Source: `[[handoff-2026-06-09-phase-2-substrate-abstraction-git-hook]]`.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    engine.py                          │
│  Substrate-agnostic business logic:                   │
│  - Dirty-ledger I/O (read, write, append)            │
│  - Reviewed-ledger I/O                               │
│  - Unreviewed computation (get_unreviewed)            │
│  - Tier classification (fast-path vs full)            │
│  - Fast-path auto-clear                              │
│  - Protocol-file exemption                           │
│  - Clear-check (check_gate → GateResult)             │
│  - Verdict-file validation                           │
│  - Metrics logging                                   │
│  - Block-message formatting                          │
└────────────┬──────────────┬──────────────┬───────────┘
             │              │              │
    ┌────────▼──────┐ ┌────▼────────┐ ┌───▼──────────────┐
    │ CC Adapter    │ │ Git Hook    │ │ Hermes Daemon    │
    │ (Tier A)      │ │ (Tier B)    │ │ (Tier C)         │
    │               │ │             │ │                  │
    │ Discovers via:│ │ Discovers:  │ │ Discovers via:   │
    │ PostToolUse   │ │ git diff    │ │ filesystem diff  │
    │ hook stdin    │ │ --cached    │ │ (before/after)   │
    │               │ │             │ │                  │
    │ Blocks via:   │ │ Blocks via: │ │ Blocks via:      │
    │ exit 2 (Stop) │ │ exit 1      │ │ halt run +       │
    │               │ │ (pre-commit)│ │ escalation file  │
    └───────────────┘ └─────────────┘ └──────────────────┘
```

## Two Questions Every Adapter Answers

An adapter is a thin shell that answers two substrate-specific questions,
then delegates all logic to `engine.py`:

### (a) How do I learn a tool produced/changed an artifact?

| Adapter | Discovery mechanism |
|---|---|
| **Claude Code (Tier A)** | `PostToolUse` hook stdin → JSON with `tool_name`, `tool_input` → extract `file_path` |
| **Git hook (Tier B)** | `git diff --cached --name-only` → staged file list cross-referenced against dirty ledger |
| **Hermes daemon (Tier C)** | Filesystem diff: snapshot workspace before agent run, snapshot after, diff content hashes → new/modified files become dirty entries. No PostToolUse hook exists on the VPS — the adapter wraps the agent invocation and discovers artifacts structurally. |
| **Codex (future)** | Inherits Tier B for free (commits go through git hook) |

### (b) How do I block "done"?

| Adapter | Blocking mechanism | Exit code |
|---|---|---|
| **Claude Code** | `sys.exit(2)` on Stop hook → CC blocks the turn | 2 |
| **Git hook** | `sys.exit(1)` on pre-commit → git refuses the commit | 1 |
| **Hermes daemon** | Supervisor halts the run + writes escalation file to `_meta/escalations/` + logs `peer-reviewer-skipped` event if reviewer unavailable. Run does NOT advance. | 1 (halted), 2 (error) |

## Engine API (public surface)

All adapters call these functions from `engine.py`:

### Core gate check

```python
engine.check_gate(
    state_dir: str,
    session_id: str,
    workspace_root: str,
    included_sources: frozenset | None = None,  # None = all sources
    attempt_auto_clear: bool = True,
) -> GateResult
```

Returns a `GateResult` dataclass:
- `status`: `'clean'` | `'clear'` | `'exempt'` | `'auto-cleared'` | `'blocked'`
- `unreviewed`: list of unreviewed entry dicts
- `tier`: `'n/a'` | `'fast-path'` | `'full'` | `'protocol'`
- `wall_ms`: float

The adapter maps `status` to its blocking mechanism.

### Dirty-ledger operations

```python
engine.append_dirty_entry(state_dir, session_id, entry) -> str
engine.load_scoped_dirty(state_dir, session_id, included_sources=None) -> list
engine.read_dirty_ledger(state_dir, session_id) -> list
```

### Reviewed-ledger operations

```python
engine.append_reviewed_entries(state_dir, session_id, entries) -> str
engine.read_reviewed_ledger(state_dir, session_id) -> list
engine.build_review_markers(file_paths, verdict, tier, gate_id, evidence,
                            verdict_file, verdict_data, findings=None) -> list
```

### Classification + helpers

```python
engine.is_read_only_bash(command) -> bool
engine.bash_entry_id(command) -> str
engine.classify_tier(file_path, tool_input, tool_name) -> str
engine.normalize_path(path) -> str
engine.determine_review_tier(unreviewed) -> str
```

### Verdict validation

```python
engine.validate_verdict_file(path, tier) -> (data, error_msg)
engine.derive_evidence(verdict_data) -> str
```

### Metrics + formatting

```python
engine.log_metrics(state_dir, session_id, outcome, unreviewed_count,
                   tier, wall_ms, extra=None) -> None
engine.format_file_list(entries) -> str
engine.format_files_argv(entries) -> str
```

## Neutral state root

All adapters share the same state directory: `.review-gate/state/` under
the workspace root (configurable via `REVIEW_GATE_STATE_DIR` env var).

State files:
- `<session>-dirty.jsonl` — per-session dirty ledger (append-only)
- `<session>-reviewed.jsonl` — per-session review markers (append-only)
- `metrics.jsonl` — per-invocation wall-clock timing (append-only)
- `verdict-*.json` — verdict files from gate-peer-reviewer

Session IDs are substrate-scoped:
- CC sessions: UUID (assigned by Claude Code)
- Git hook: `git-<branch>-<YYYY-MM-DD>` (deterministic)
- Hermes daemon: `hermes-<run-id>` (run_id passed by caller or auto-generated)

## Source tagging

Every dirty entry carries a `source` field:
- `'claude-code'` — CC PostToolUse adapter (default)
- `'git-hook'` — reserved for future git hook writes (currently read-only — no entries produced)
- `'cowork'` — Cowork sessions (env override)
- `'hermes'` — Hermes daemon adapter (Tier C). Written by the daemon's filesystem-diff discovery.
- `''` — legacy entries (treated as claude-code by CC adapter)

The CC Stop adapter filters on `included_sources={'claude-code', ''}`.
The git hook adapter uses `included_sources=None` (sees everything).
This prevents double-counting while ensuring Tier B catches all sources.

## Conformance requirements

Every adapter must pass a conformance suite that proves these behaviors
against its **real surface** (not a simulated harness):

1. **Block-on-skip**: unreviewed agent artifact → adapter blocks
2. **Approve-clean**: reviewed artifact → adapter approves
3. **Read-only exempt**: no dirty entry for staged file → pass through
4. **Tier enforcement**: full-tier entries require full evidence
5. **Re-edit after review**: re-block on edit after review pass
6. **Override audited**: bypass mechanism works and leaves a record

### Current conformance suites

| Adapter | Suite | Tests |
|---|---|---|
| Claude Code (Tier A) | `test_conformance.py` | 79 tests |
| Git hook (Tier B) | `test_git_hook_conformance.py` | 24 tests (incl. real `git commit` + OC-16 integration) |
| Hermes daemon (Tier C) | `test_hermes_daemon_conformance.py` | 28 tests (local-logic: real filesystem + subprocess). Operator-run VPS acceptance tests documented in `DEPLOYMENT.md`. |

### Adding a new adapter

1. Implement the adapter (thin shell answering the two questions)
2. Write a conformance suite proving the 6 behaviors above
3. Register the adapter in this document
4. Add the adapter's source tag to the source-tagging table
5. Run both existing suites to verify no regression

## Honest limits

- **The git hook only catches files with dirty-ledger entries.** Files edited
  outside any tracked agent (manual edits) pass through by design. The gate
  enforces at the agent boundary, not at the filesystem level.
- **`--no-verify` bypasses the git hook.** This is standard git behavior and
  cannot be prevented. The bypass is documented but not audited at the git
  level (unlike `gate-skip.py` which writes event-log rows).
- **The verdict file is model-authored (Tiers A/B).** For Claude Code and
  git-hook adapters, the producing model can author the verdict until Phase 5
  auto-dispatch lands. The operator remains the integrity backstop.
- **Tier C closes this gap.** The Hermes daemon adapter spawns the reviewer as
  a separate OS process (different PID, no shared memory). The producing agent
  never sees or writes the verdict file — the daemon captures it and writes
  the review-pass marker. This is the strongest independence form.
