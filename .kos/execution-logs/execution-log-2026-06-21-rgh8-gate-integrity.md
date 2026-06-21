---
type: execution-log
status: draft
created: 2026-06-21
updated: 2026-06-21
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, gate-integrity, CR-045, CR-049, RGH-8]
---

## 2026-06-21 — [RGH-8] Gate integrity: reviewer-independence + fail-closed verification

**What was built:** Two gate-integrity enforcement fixes in the mandatory review gate scripts, closing CR-045 (reviewer-independence) and CR-049 (fail-closed firing-tracker verification).

**Fix 1 — CR-045 (reviewer-independence enforcement):**
- Added `--reviewer-session` argument to `log-review-pass.py`, required when `--reviewer-type independent`
- Rejects (exit 1, logs nothing) when `reviewer_session == session` — sub-agents inherit the parent `session_id` (confirmed real-runner 2026-06-12, CC v2.1.85), so in-session sub-agents are caught
- Stamps `reviewer_session` on each review marker for audit trail
- Updated `mandatory-review-gate.py` REQUIRED-ACTION message: removed "Agent tool to spawn" language, added explicit sub-agent warning, included `--reviewer-session` in example command

**Fix 2 — CR-049 (fail-closed + path-anchoring):**
- Replaced `os.path.expanduser('~/workspace/...')` with `WORKSPACE_ROOT` from `_paths.py` (derived from script location on disk, cwd-independent)
- Converted the `else` branch from `WARNING ... Proceeding` to `REJECTED` (exit 1) — missing tracker now blocks instead of waving through
- Audited both scripts for other "proceed on warning" D-11 class fail-open paths — none found

**Decision made:** Simple string equality (`reviewer_session == session`) for independence detection, rather than more complex approaches (agent lineage tracking, process-level isolation). Rationale: sub-agents inherit the parent `session_id`, so string equality is both sufficient and deterministic. Process-level isolation is RGH-3's scope (Hermes daemon).

**Alternatives considered:** (1) Agent lineage tracking via `agent_type`/`agent_id` fields — rejected as belt-and-suspenders complexity when session equality is already definitive. (2) Full process isolation — deferred to RGH-3 (Hermes daemon enforcement). (3) Checking `WORKSPACE_ROOT` env var before `_paths.py` derivation — rejected because `_paths.py` already derives WORKSPACE_ROOT deterministically from its own file location, which is the more robust approach.

**Why this approach:** The simplest enforcement that closes the hole. Sub-agents share session_id = string match catches them. Separate chats have distinct session_id = string mismatch allows them. No false positives, no false negatives for the current substrate (Claude Code v2.1.85+).

**Reusable for future apps?:** Yes — the pattern of "verify caller identity via session-id comparison" is applicable to any system where sub-processes inherit parent context and need to be distinguished from genuinely independent processes.

**Artifacts produced:**
- `repos/ai-agency-core/scripts/mandatory-review-gate/log-review-pass.py` (edited)
- `repos/ai-agency-core/scripts/mandatory-review-gate/mandatory-review-gate.py` (edited)
- `repos/ai-agency-core/scripts/mandatory-review-gate/tests/test_reviewer_independence_and_failclosed.py` (new)
- `second-brain/_meta/handoffs/_review-gate-catch-register.md` — CR-045 + CR-049 → Resolved

**Test results (real output, 4/4 pass):**
```
Test 1 — CR-045: same-session reviewer rejected: PASS
Test 2 — distinct-session reviewer accepted: PASS
Test 3 — CR-049: non-root cwd path-anchoring: PASS
Test 4 — fail-closed: missing run-id rows: PASS
```

**Independent review:** Separate-session reviewer (session 4a9a0633) ran 5 adversarial bypass attempts — all correctly rejected. 0 blocking catches across 2 QC rounds. 1 advisory (pre-existing, out of scope): log-review-pass.py allows `--reviewer-type producer` on full-tier items; enforcement is in the stop hook's `has_independent_review()`, not in log-review-pass.py.
