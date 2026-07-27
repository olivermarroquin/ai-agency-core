---
type: execution-log
status: draft
created: 2026-07-26
updated: 2026-07-26
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, gate-infrastructure]
---

## 2026-07-26 — Commit-time gate exemption parity (commit-exempt)

**Tier:** Productize
**What was built:** Fixed `git_hook_adapter.py` so commit-time gate check honors the same bookkeeping (CR-161) and plumbing (CR-219) exemptions as the Stop hook (`engine.check_gate`).

**Bug:** The Stop hook exempted bookkeeping files (BOOKKEEPING_BASENAMES) and plumbing files (plumbing-whitelist patterns) from blocking. The commit hook (`git_hook_adapter.py`) did NOT — it blocked any staged file with an unreviewed dirty entry regardless of exemption status. Live occurrence 2026-07-26: operator blocked committing `_review-skill-firing-tracker.md` rows written by a concurrent pair's reviewer.

**Decision made:** Reuse engine's existing `is_bookkeeping_entry()` and `is_plumbing_exempt()` predicates in `check_staged_files` rather than reimplementing the logic. Single source of truth — if the Stop hook's exemption logic changes, the commit hook automatically inherits.

**Alternatives considered:**
- Reimplementing the exemption logic inline → rejected (divergence risk, the exact bug this fixes)
- Using `engine.check_gate()` directly → rejected (git hook scoping is fundamentally different: ALL sessions, cross-referenced with staged files)
- Adding session-role exemptions (RGH-10/11/12) → not applicable (git hook is substrate-agnostic, no session classification)

**Why this approach:** Parity principle — a file that does NOT block at turn-end must NOT block at commit-time. Same predicates, both checkpoints. Fail-safe: exception in exemption check → treat as non-exempt (block).

**Changes:**
- `git_hook_adapter.py`: added bookkeeping + plumbing exemption in `check_staged_files`, added `workspace_root` parameter
- `tests/test_commit_exemptions.py`: 20 tests + 5 subtests covering all cases

**Test results:** 770 passed, 5 subtests passed, 0 failed (full suite green, no regressions)

**Self-demonstration:** 3 cases verified — bookkeeping exempt, plumbing exempt, deliverable still blocks.

**Reusable for future apps?:** No — gate infrastructure specific.
