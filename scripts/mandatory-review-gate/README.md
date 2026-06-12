# Mandatory Pre-Land Review Gate — Scripts

The enforcement layer for the mandatory pre-land review gate. Three scripts +
one shared path module + one conformance test suite.

## Scripts

| Script | Hook / CLI | Purpose |
|---|---|---|
| `dirty-ledger-track.py` | PostToolUse | Tracks state-changing tool calls in a per-session JSONL ledger |
| `mandatory-review-gate.py` | Stop | Blocks turn-end if unreviewed dirty entries exist; auto-clears fast-path (F1); exempts protocol files (F2) |
| `log-review-pass.py` | (called by reviewer) | Logs a review-pass marker backed by a verdict file |
| `gate-status.py` | CLI | Shows what's blocking the gate and why (`--json` for structured output) |
| `gate-skip.py` | CLI (OPERATOR-ONLY) | Emergency skip with mandatory `--reason`; writes loud event-log + metrics |
| `_paths.py` | (shared module) | Derives WORKSPACE_ROOT + STATE_DIR from file location |

## State directory

Default: `<WORKSPACE_ROOT>/.review-gate/state/`
Override: set `REVIEW_GATE_STATE_DIR` env var to an absolute path.

Contents:
- `<session>-dirty.jsonl` — per-session dirty ledger (append-only)
- `<session>-reviewed.jsonl` — per-session review markers (append-only)
- `metrics.jsonl` — per-invocation wall-clock timing (append-only)
- `verdict-<gate_id>-<timestamp>.json` — verdict files from gate-peer-reviewer

### State migration (RGH-1)

Prior to RGH-1, state lived at `.claude/state/`. The default is now
`.review-gate/state/`. Old ledgers in `.claude/state/` are **orphaned** — they
will not be read by the updated scripts and can be safely deleted. The scoped
aggregation (D-24) excludes stale ledgers by timestamp, so even if old files
remain in the new location, they will not wedge new sessions.

To clean up: `rm -f .claude/state/*-dirty.jsonl .claude/state/*-reviewed.jsonl`

## Coverage contract (RGH-1)

**What the gate catches:**

| Write source | Caught by | How |
|---|---|---|
| Top-level agent Write/Edit/NotebookEdit | PostToolUse → dirty-ledger-track | Direct tool match |
| Top-level agent Bash (state-changing) | PostToolUse → dirty-ledger-track | Read-only classifier excludes safe commands |
| Sub-agent Write/Edit/NotebookEdit | PostToolUse → dirty-ledger-track (sub-agent's session ledger) | Same hook fires in sub-agent context |
| Sub-agent Bash (state-changing) | PostToolUse → dirty-ledger-track (sub-agent's session ledger) | Same hook fires in sub-agent context |

**How the parent Stop catches sub-agent artifacts:**

The Stop hook scans the current session's dirty ledger plus any sibling dirty
ledgers whose earliest entry timestamp is >= the current session's earliest
entry. This catches sub-agents spawned during this session (D-13) while
excluding stale ledgers from prior/parallel sessions (D-24).

**Scoping policy (D-24):**

| Scenario | Included in Stop scan? |
|---|---|
| Current session's own dirty ledger | Always |
| Sub-agent spawned during this session (concurrent timestamps) | Yes |
| Prior session's stale ledger (earlier timestamps) | No |
| Parallel CC chat's ledger (may be earlier or concurrent) | Only if concurrent |
| Cowork session (any) | No — precautionary exclusion via source tag (F3). Empirically verified (2026-06-12): Cowork does NOT run workspace PostToolUse hooks, so no Cowork dirty ledger is created in practice. The source-tag filter is a belt-and-suspenders guard in case this changes. |

**Substrate scoping (F3).** Dirty entries carry a `source` field (`claude-code`,
`cowork`, `other`). The CC Stop hook only includes entries with `source:
claude-code` (or missing/legacy). Cowork and other-substrate entries are
excluded from CC Stop — their coverage is deferred to Tier B (RGH-2 git
pre-commit hook), which fires regardless of substrate.

**Empirical note (2026-06-12):** Cowork does NOT run workspace PostToolUse
hooks, so no Cowork dirty ledger is created in practice. The source-tag filter
is a precautionary guard in case this changes in a future Claude Code release.

**Cleanup of orphan ledgers:** Stale ledgers from prior sessions accumulate
harmlessly (excluded by timestamp scoping + source filtering). Clean up
periodically: `rm .review-gate/state/*-dirty.jsonl` between sessions (safe
when no session is active).

**What the gate does NOT catch (honest boundary):**

- Read-only Bash commands (by design — classified by the read-only whitelist)
- Sub-agent writes when the parent has no dirty entries of its own (no session
  start timestamp to scope from — the sub-agent output hasn't been incorporated
  into the parent's work). **Operator-accepted boundary (2026-06-12):** no
  recency-window fix planned; RGH-2's git pre-commit hook is the structural
  close (every substrate commits, so the hook catches everything regardless of
  session scoping).
- Tool calls in substrates other than Claude Code (no hook fires). Covered by
  Tier B (git pre-commit hook, Phase 2) and Tier C (Hermes daemon, Phase 3)
- External state changes made outside the agent (manual edits, other processes).
  The gate enforces at the agent boundary, not at the filesystem level

## Verdict file contract (RGH-1)

`log-review-pass.py` requires `--verdict-file <path>` — a JSON file containing
the structured return contract from `gate-peer-reviewer`:

```json
{
  "verdict": "PASS|BLOCKING|FAIL",
  "checks_run": [
    {"name": "placeholder-sweep", "result": "PASS", "count": 0},
    {"name": "leak-audit", "result": "PASS", "count": 0},
    {"name": "ground-truth-cross-check", "result": "PASS", "count": 0}
  ],
  "catches": [],
  "cost_usd": 0.0
}
```

**Required:** `verdict` (enum), `checks_run` (non-empty list, each with `name`).
**Tier enforcement:** `full`-tier entries require `ground-truth-cross-check` or
`value-cross-check` in `checks_run`. A full entry cleared by a fast-path-shaped
verdict is rejected naming the missing checks.

**Honest limit:** The verdict file is model-authored until the reviewer runs as
an isolated process (Phase 2/3). The operator remains the integrity backstop.

## Conformance test suite

`test_conformance.py` — 72 tests covering:
- State dir derivation (D-14/D-15): absolute path assertions from subdirectory cwd
- Full block/review/approve cycle from subdirectory cwd
- Production state isolation (tests use temp dirs, never touch live state)
- Read-only exemption (D-08): cd, grep, curl GET, python3 -c, heredoc
- State-changing caught: curl POST, git push, compound commands
- Self-referential exclusion (D-16): gate scripts don't dirty-track themselves
- Verdict file required (RGH-1): bare evidence rejected, schema validation
- Tier enforcement (RGH-1): full-tier needs ground-truth checks
- Sub-agent coverage (D-13): parent Stop catches child session ledger
- Cost metrics: approve and block paths both log to metrics.jsonl
- Re-edit after review (D-04): re-block on edit after review pass
- Approve schema (D-11): no hookSpecificOutput on Stop

### CONTRACT: No hook change ships without this suite passing against the real runner.

Every change to any script in this directory MUST:
1. Pass `python3 test_conformance.py` (subprocess-level unit tests)
2. Be validated against the actual Claude Code hook runner with captured output
   before the Stop hook is re-enabled (real-runner acceptance evidence per D-12)

A subprocess.run harness validates the scripts' behavior. It does NOT validate
that the Claude Code hook runner accepts the output. Both levels of evidence
are required. See the lesson D-12 for why.

### Real-runner evidence session protocol

Operator-driven. Execute in a fresh CC session, capture all output.

1. **Temporarily re-enable Stop hook** in `.claude/settings.json`
2. **Start from a subdirectory cwd** (e.g. `repos/ai-agency-core/scripts/`)
3. **Capture raw hook-event JSON** — add a debug line to `dirty-ledger-track.py`
   and `mandatory-review-gate.py` that writes the raw stdin JSON to
   `.review-gate/state/raw-hook-events.jsonl`. This captures the actual
   Claude Code hook schema including any session lineage fields.
4. **Block→review→clear cycle** — write a file, observe the Stop block
   (exit 2 + block message), run gate-peer-reviewer + log-review-pass with
   verdict file, observe the Stop approve (exit 0)
5. **Sub-agent demo (both variants)**:
   - Parent-with-writes: spawn a Task sub-agent that writes a file; observe
     parent Stop blocks on the sub-agent artifact until reviewed
   - Parent-clean: demonstrate the documented escape (parent with zero dirty
     entries → sub-agent ledger not scanned → Stop approves)
6. **Deliberate re-break** — introduce a known bug (e.g. add
   `hookSpecificOutput` back to the approve path), re-run the conformance
   suite, show it fails on the re-break; revert
7. **Park the Stop hook again** — remove from `.claude/settings.json`
8. **Package** — deliver raw-hook-events.jsonl + session transcript +
   exit codes + conformance suite output to operator for final review

**Lineage field investigation:** If the raw hook-event JSON contains a
`parent_session_id` or equivalent lineage field, file it as a candidate fix
for the timestamp-heuristic sub-agent scoping — lineage-based scoping would
eliminate the parallel-chat cross-block residual and the parent-clean escape.
