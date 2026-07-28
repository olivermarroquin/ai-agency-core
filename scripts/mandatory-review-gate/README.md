# Mandatory Pre-Land Review Gate — Scripts

The enforcement layer for the mandatory pre-land review gate. Substrate-agnostic
engine + per-substrate adapters + Layer-A deterministic check suite + conformance tests.

**Version:** RGH-CR244 (2026-07-27) — close-friction fix

## Architecture (RGH-2)

```
engine.py (substrate-agnostic business logic)
  ├── CC adapter: dirty-ledger-track.py + mandatory-review-gate.py (Tier A)
  ├── Git hook adapter: git_hook_adapter.py (Tier B — universal)
  └── (future) Hermes daemon adapter (Tier C)
```

The engine handles: dirty-ledger I/O, reviewed-ledger I/O, unreviewed computation,
tier classification, fast-path auto-clear, protocol-file exemption, clear-check
(`check_gate → GateResult`), verdict-file validation, metrics logging, and
block-message formatting.

Adapters are thin shells that answer two substrate-specific questions:
**(a)** How do I learn a tool produced/changed an artifact?
**(b)** How do I block "done"?

See `references/substrate-adapter-contract.md` for the full contract.

## Scripts

### Engine

| Script | Purpose |
|---|---|
| `engine.py` | Substrate-agnostic gate engine — all business logic |
| `_paths.py` | Shared path derivation (WORKSPACE_ROOT, STATE_DIR) |

### Claude Code adapter (Tier A)

| Script | Hook / CLI | Purpose |
|---|---|---|
| `dirty-ledger-track.py` | PostToolUse | Tracks state-changing tool calls in a per-session JSONL ledger |
| `mandatory-review-gate.py` | Stop | Blocks turn-end if unreviewed dirty entries exist; delegates to `engine.check_gate()` |
| `log-review-pass.py` | (called by reviewer) | Logs a review-pass marker backed by a verdict file |
| `gate-status.py` | CLI | Shows what's blocking the gate and why (`--json` for structured output) |
| `gate-skip.py` | CLI (OPERATOR-ONLY) | Emergency skip with mandatory `--reason`; refuses deliverable blocks (CR-224); writes loud event-log + metrics |
| `operator-clear.py` | CLI (OPERATOR) | One-command operator-dispatched gate clear (CR-256): registers reviewer UUID, builds verdict, logs review-pass in one invocation. For non-CC reviewers (Cowork, strategic chat, manual verification) |
| `metrics-readout.py` | CLI | Analyzes `metrics.jsonl` — p50/p95/max wall-clock by outcome, slow-invocation flags (RGH-1b) |
| `gate-canary.py` | CLI / scheduled | Fail-open block-test canary — writes synthetic dirty entry, confirms Stop hook blocks (RGH-1b) |

### Git hook adapter (Tier B — universal enforcement)

| Script | Purpose |
|---|---|
| `git_hook_adapter.py` | Pre-commit hook — blocks commit if unreviewed agent-produced files are staged |
| `install-git-hooks.sh` | Idempotent installer/uninstaller for the pre-commit hook across repos |

### Layer-A deterministic checks (OC-12..16, $0/no-LLM)

Added by [RGH-7] (2026-06-15). Five exhaustive checks that hit every deliverable/file, not a spot-check.
Each is `$0`, no-LLM, and fail-closed. Consumed by `dod-check.py`; cited by [RGH-6] (G-chat-close);
run by [RGH-5] (independent dispatch). Source: COA-4b 25-catch replay corpus.

| Script | Check ID | Catches | What it does |
|---|---|---|---|
| `oc-12-per-deliverable-existence.py` | OC-12 | C-11/C-18/C-19 | Enumerates DoD deliverables; `ls` + non-empty + placeholder-grep each. C-14 (empty field values) needs a `count` assertion on array length (OC-13), not file-level `non-stub` |
| `oc-13-count-reconciliation.py` | OC-13 | C-12/C-13/C-15/C-16/C-17 | Count assertions reconcile against a named source fetched fresh; `value matches` deferred to RGH-5 judgment (C-20) |
| `oc-14-rename-propagation.py` | OC-14 | C-03/C-04/C-05/C-08/C-09/C-10/C-25 | Grep old name across live paths with historical allowlist |
| `oc-15-frontmatter-freshness.py` | OC-15 | C-24 | Dirty-ledger files with `updated:` field must equal today |
| `oc-16-commit-staging-audit.py` | OC-16 | C-07/C-21/C-22/C-23 | `git status` vs dirty-ledger: staged-not-touched, touched-not-staged, build artifacts |

### Other

| Script | Purpose |
|---|---|
| `dod-check.py` | Parses a handoff DoD manifest and runs bound Layer-A checks; emits verdict JSON |

## Git hook installation

### Install in specific repos

```bash
./install-git-hooks.sh ~/workspace/second-brain ~/workspace/repos/ai-agency-core
```

### Install in all workspace repos

```bash
./install-git-hooks.sh --all
```

### One-command uninstall

```bash
./install-git-hooks.sh --uninstall --all
```

The installer is:
- **Idempotent** — safe to run multiple times
- **Reversible** — `--uninstall` removes the hook and restores any backup
- **Non-destructive** — backs up existing pre-commit hooks before overwriting
- **Marker-identified** — only touches hooks it installed (identified by `REVIEW_GATE_HOOK` marker)

### Override

`git commit --no-verify` bypasses the hook (standard git escape hatch).
This is the operator-only override — documented but not audited at the git level.

## State directory

Default: `<WORKSPACE_ROOT>/.review-gate/state/`
Override: set `REVIEW_GATE_STATE_DIR` env var to an absolute path.

Contents:
- `<session>-dirty.jsonl` — per-session dirty ledger (append-only)
- `<session>-reviewed.jsonl` — per-session review markers (append-only)
- `metrics.jsonl` — per-invocation wall-clock timing (append-only)
- `verdict-<gate_id>-<timestamp>.json` — verdict files from gate-peer-reviewer

### Session ID conventions

| Adapter | Session ID format |
|---|---|
| Claude Code | UUID (assigned by CC) |
| Git hook | `git-<branch>-<YYYY-MM-DD>` |
| Hermes (future) | `hermes-<run-id>` |

### Source tagging

Dirty entries carry a `source` field for scoping:

| Source | Written by | CC Stop sees? | Git hook sees? |
|---|---|---|---|
| `claude-code` | CC PostToolUse | Yes | Yes |
| `git-hook` | (not produced — git hook is read-only) | No | Yes |
| `cowork` | Cowork sessions | No | Yes |
| `''` (legacy) | Pre-RGH-1 entries | Yes | Yes |

### State migration (RGH-1)

Prior to RGH-1, state lived at `.claude/state/`. The default is now
`.review-gate/state/`. Old ledgers in `.claude/state/` are **orphaned** — they
will not be read by the updated scripts and can be safely deleted.

To clean up: `rm -f .claude/state/*-dirty.jsonl .claude/state/*-reviewed.jsonl`

## Coverage contract

### Tier A — Claude Code native hook (best UX)

Catches Write/Edit/NotebookEdit/Bash at the agent turn boundary.
Sub-agent artifacts inherit the parent session_id (CC v2.1.85+).
Source-filtered to `claude-code` only.

### Tier B — Git pre-commit hook (universal guarantee)

Catches **any** agent-produced artifact at commit time, regardless of
which substrate edited the files. Cross-references staged files against
the dirty ledger from ALL sessions and ALL sources.

Files with no dirty-ledger entry pass through (human edits are not gated).

### What the gate does NOT catch

- Read-only Bash commands (by design)
- External state changes made outside any agent
- Manual edits with no dirty-ledger entry (by design — Tier B pass-through)
- `--no-verify` bypasses (standard git behavior)

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
`value-cross-check` in `checks_run`.

## Conformance test suites

| Suite | Tests | Covers |
|---|---|---|
| `test_conformance.py` | 93 | CC adapter (Tier A): state dir derivation, full cycle, read-only exempt, state-changing caught, self-ref exclusion, verdict file required, tier enforcement, sub-agent coverage, substrate tagging, own-ledger scoping, protocol exemption, fast-path auto-clear, metrics, gate-status, gate-skip |
| `test_git_hook_conformance.py` | 24 | Git hook adapter (Tier B): block-on-skip, approve-clean, substrate-agnostic catch (codex/local-model/cross-session), metrics, re-edit blocks, installer idempotent/reversible, real `git commit` blocked/approved, OC-16 git-integration |

### CONTRACT: No change ships without both suites passing.

```bash
python3 test_conformance.py && python3 test_git_hook_conformance.py
```

## Hook deployment after fresh clone (RGH-1b item 7)

`.claude/settings.json` is **gitignored** in both `ai-agency-core` and
`second-brain` (and most workspace repos). Hook configs live on disk only,
not in git. After a fresh clone or any loss of the `.claude/` directory,
hooks must be regenerated.

**`deploy-hooks.sh` is the canonical regenerator.**

```bash
# PostToolUse only (default — tracking without blocking)
./deploy-hooks.sh ~/workspace/second-brain
./deploy-hooks.sh ~/workspace/repos/ai-agency-core

# PostToolUse + Stop hook (full enforcement)
./deploy-hooks.sh --with-stop ~/workspace

# All workspace repos at once (PostToolUse only)
for repo in ~/workspace ~/workspace/second-brain ~/workspace/repos/ai-agency-core \
            ~/workspace/ai-factory ~/workspace/skills; do
    ./deploy-hooks.sh "$repo"
done
```

The script is idempotent (safe to re-run) and merges into existing
`settings.json` without overwriting other config. It also powers the
`app-factory` bootstrap flow for new projects.

**Do not hand-edit `.claude/settings.json` hook entries** — use
`deploy-hooks.sh` so all repos stay consistent. If you need to add
non-hook settings, edit the file directly; deploy-hooks only touches
the `hooks` key.

## Reviewer recall eval (RGH-1b item 8 — deferred to Phase 5)

**Status:** Design documented here; implementation deferred to
`[[handoff-2026-06-11-phase-5-independent-reviewer-dispatch]]` where the
isolated reviewer makes honest measurement feasible.

**Problem:** Everything shipped so far enforces that reviews HAPPEN and are
WELL-FORMED; nothing measures whether reviews are GOOD (catch real defects
vs. rubber-stamp clean files).

**Design (when built):**
1. Maintain a corpus of planted-defect artifacts (seeds exist in
   `gate-peer-reviewer/references/regression-fixtures/coa4b-*`, 25 fixtures).
2. Periodically copy a real artifact, inject a known defect (placeholder,
   leaked client name, broken wikilink, stale frontmatter date).
3. Dispatch the reviewer blind on the injected copy.
4. Score: did it catch the planted defect? Record recall over time.
5. Alert if recall drops below threshold (e.g., <80% over 10 runs).

**Why Phase 5:** The producer and reviewer are currently the same model in
the same context. Measuring recall honestly requires the isolated reviewer
from Phase 5 — a separate process that can't see the planted-defect metadata.

## CC-upgrade tripwire

The Stop hook output schema and sub-agent session-id inheritance are both
**version-dependent** (captured on CC v2.1.85, 2026-06-12). After every Claude
Code upgrade, re-run the real-runner mini-check (see prior README version for
the full 8-step protocol).

## Changelog

### RGH-CR244 (2026-07-27) — Close-friction fix

Five fixes that eliminate close-time operator friction:

1. **CR-244 — Bookkeeping fast-path.** When all deliverable entries in a session
   have been reviewed PASS, subsequent close-time coordination writes (firing-tracker,
   catch-register, event-log, _chat-status.md, execution-logs, _scratch/ files)
   auto-clear with fast-path deterministic checks. Applied to Stop hook,
   `git_hook_adapter`, and `assert-gate-clear.py`. Guard: only fires when ALL
   deliverables are PASSed; never clears content files.

2. **CR-255 — Leak-audit own-client whitelist.** `_load_leak_identity_strings`
   now excludes the identity strings of the project resolved for the file under
   check. EV Electric's own name appearing in an EV Electric file is not a leak.
   Supports explicit `own_client_path_markers` config mapping.

3. **CR-256 — One-command operator-dispatched clear.** New `operator-clear.py`:
   registers a reviewer UUID (--operator-dispatched), builds a schema-valid verdict
   file with mandate_version, and logs the review-pass in one invocation. Full
   requirements contract printed on any rejection.

4. **CR-165 — Non-CC reviewer identity path.** Block files and `assert-gate-clear`
   now document `operator-clear.py` as the path for Cowork/strategic-chat reviewers.

5. **D-09 — Block-message hardening.** When deliverables are present, the block
   message states "Skip ineligible — deliverables present; relay to reviewer or
   use operator-clear.py. Do NOT author a gate-skip script (D-09)." The wrong
   path is never printed as available.

**Tests:** 24 new tests (794 total). Regression test: simulated wave close
(produce → review PASS → close bookkeeping → commit) with zero operator
interventions. Safety invariants: gate still blocks unreviewed deliverables,
producer self-clears, deliverable skips.
