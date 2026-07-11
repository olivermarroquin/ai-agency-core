---
type: execution-log
status: draft
created: 2026-07-10
updated: 2026-07-10
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, gate-hardening]
---

## 2026-07-10 — Gate hardening round 2: CR-228/229/230

**What was built:** Five fixes to the mandatory review gate system addressing reviewer test-run exemptions, BLOCKING verdict integrity, and self-referential exclusion gaps.

**Decision made:** Used `reviewer_only` flag on plumbing whitelist patterns (rather than a separate whitelist or extending `is_bash_entry_write_safe`) to scope pytest/unittest exemptions. This keeps the single-config-file pattern while adding per-pattern role discrimination.

**Alternatives considered:** (1) Extending `READ_ONLY_PYTHON_SCRIPTS` — rejected because pytest IS state-changing (creates cache files), the exemption is role-specific not universal. (2) Separate reviewer-only whitelist file — rejected, adds config sprawl.

**Why this approach:** The `reviewer_only` flag integrates naturally into the existing `plumbing-whitelist.yml` schema, and the compound-command splitting reuses `engine.split_compound()` which is already battle-tested.

### Fixes delivered

| Fix | CR | Description | Tests |
|---|---|---|---|
| A | CR-228 | reviewer pytest/unittest plumbing exemption with compound-command safety | 14 |
| B | CR-229a | BLOCKING close-gap findings re-check (KCA, exec-log sections, substantive) | 8 |
| C | CR-229b | BLOCKING findings operator mirror verification | 4 |
| D | CR-229c | Omit emergency-skip section when deliverables > 0 | 3 |
| E | CR-230 | cd-prefix self-ref exclusion + force-deliverables content D-09 | 10 |

### Files changed

- `.review-gate/plumbing-whitelist.yml` — 2 new patterns with `reviewer_only: true`
- `engine.py` — `include_reviewer_only` param, compound-segment matching, `recheck_close_gap_catches()` + 3 deterministic re-check functions
- `dirty-ledger-track.py` — benign-nav segment classification for self-ref exclusion, `--force-deliverables` content D-09
- `log-review-pass.py` — `--operator-acknowledged`, `--relay-slug` args, close-gap re-check + operator mirror verification
- `mandatory-review-gate.py` — skip section conditional on `n_deliverables == 0`
- `tests/conftest.py` — autouse cache reset fixture
- `tests/test_cr228_cr229.py` — 39 tests covering all five fixes
- `second-brain/_meta/handoffs/_review-gate-catch-register.md` — CR-229/230 filed, CR-228 status flipped

### Review

- R1: BLOCKING on Fix A (2 gaps: compound smuggling + cd-prefix false negative)
- R2: PASS (0 catches, all 5 fixes verified)
- Independent reviewer session: `256a93a4-c84d-4838-b415-0d7986c37ebe`
- Full suite: 275 passed, 6 pre-existing, 0 new regressions

### Knowledge capture

**Pattern candidate:** `reviewer_only` plumbing whitelist flag — role-discriminated exemption in a shared config. Reusable when any future whitelist pattern needs to be scoped to a specific session role.

**Lesson:** Compound-command handling must be considered for ANY regex-based command matching. The `^` anchor + no `$` combination simultaneously causes false negatives (cd-prefixed legitimate commands) and false positives (destructive tails). Splitting and classifying segments independently is the robust pattern.

**Reusable for future apps?:** Yes — the `reviewer_only` flag pattern and compound-command segment matching are generalizable to any role-based command classification system.
