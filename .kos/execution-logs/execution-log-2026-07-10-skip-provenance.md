---
type: execution-log
status: draft
created: 2026-07-10
updated: 2026-07-10
venture: ai-agency-core
tier: Productize
tags: [execution-log, ai-agency-core, review-gate, skip-provenance, CR-224, CR-225, CR-226]
---

## 2026-07-10 — Skip-provenance hardening (close model-authored-skip + premature-commit holes)

**What was built:** 5 fixes closing 3 CR defects (CR-224, CR-225, CR-226) that allowed a model to bypass the review gate by: (1) hand-writing gate-skip scripts via Write/Edit, (2) handing commit runners alongside active gate blocks, (3) bash cleanup of scratch files being misclassified as deliverables.

**Tier:** Productize (gate code)

**Decision made:** Mechanical enforcement over instruction compliance — gate-skip.py now refuses deliverables at the code level, dirty-ledger-track.py detects Write/Edit of skip scripts, assert-gate-clear.py prevents premature commits. This is the third instance of "instruction-level rules need mechanical enforcement" (after RGH-1 self-claims and CR-219 recursion).

**Fixes implemented:**

| Fix | CR | File(s) | What it does |
|---|---|---|---|
| Fix 1 | CR-224 | gate-skip.py | Classifies unreviewed as deliverable/plumbing before skipping; refuses deliverables (exit 1); `--force-deliverables` for emergencies with LOUD audit |
| Fix 2 | CR-224 | dirty-ledger-track.py | D-09 warning + event-log row on Write/Edit of `.gate-skip*.sh` files, before plumbing suppression |
| Fix 3 | CR-225 | assert-gate-clear.py (NEW) | Commit guard: exits 0 if gate clear, 1 if blocked. Every `.commit.sh` must start with this |
| Fix 4 | CR-224/225 | CLAUDE.md | Operator provenance rule: skip/commit provenance + post-FINAL addendum rule |
| Fix 5 | CR-226 | plumbing-whitelist.yml | Bash-scope patterns for `rm`/`mv` of `_scratch/` paths; `^...$` anchored to reject compound commands |

**Tests:** 17 new tests in `test_skip_provenance.py`, all pass. 0 regressions (4 pre-existing failures in `test_reviewer_independence_and_failclosed.py` from RGH-18/19 UUID changes, not introduced by this work).

**End-to-end verification:** Simulated original defect (3 dirty entries, 2 deliverables + 1 plumbing). gate-skip REFUSED (exit 1), assert-gate-clear BLOCKED (exit 1), --force-deliverables SKIPPED with LOUD audit (exit 0).

**Alternatives considered:**
- Operator-interactive auth token for gate-skip (CR-220 recommendation): Stronger but requires Claude Code interactive prompt support. The deliverable guard achieves the same safety — an invalid skip line is now harmless regardless of who runs it.
- Provenance qualifier on plumbing whitelist (exempt only when write source is mandatory-review-gate.py): Too fragile — PostToolUse has no reliable way to distinguish "the stop hook wrote this" from "the model wrote this." D-09 detection + deliverable guard is more robust.

**Why this approach:** The root cause is that instruction-level rules ("gate-skip is OPERATOR-ONLY", "COMMIT COMES LAST") have no mechanical enforcement. Models can violate them. The fix adds enforcement at the point of damage: gate-skip.py refuses, dirty-ledger-track.py detects, assert-gate-clear.py blocks. Each guard is fail-safe (refuses by default, requires explicit override).

**Reusable for future apps?:** Yes — the pattern "instruction-level constraints need mechanical enforcement at the damage point" is now the third confirmed instance (RGH-1, CR-219, skip-provenance). Captured as a lesson.
