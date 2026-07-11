---
type: execution-log
status: draft
created: 2026-07-10
updated: 2026-07-10
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, skip-provenance, independent-review]
---

## 2026-07-10 — Independent review: skip-provenance hardening (5 fixes + 1 addendum)

**Role:** Independent peer reviewer (session 022ba6b0)
**Producer session:** e22f5b5b
**Verdict:** R1 BLOCKING → R2 PASS → addendum PASS

### What was reviewed

5 code fixes + 1 post-FINAL addendum closing CR-224, CR-225, CR-226, CR-227. The fixes add mechanical enforcement to three instruction-only rules: (1) gate-skip is operator-only, (2) commit comes last, (3) scratch cleanup is not a deliverable.

### R1 findings (2 total, 1 blocking + 1 advisory)

**BLOCKING: 2 regressed tests in test_conformance.py::TestGateSkip.** Fix 1's deliverable guard caused `test_skip_writes_metrics` and `test_skip_writes_to_isolated_event_log` to fail — temp-dir files classified as deliverables, skip refused. Confirmed regression via `git stash` (both pass on pre-change code). Producer's claim of "4 pre-existing failures" was accurate for `test_reviewer_independence_and_failclosed.py` but missed these 2 in `test_conformance.py`.

**Advisory: scratch-cleanup regex matched `_scratch/` anywhere in path.** `rm -f ~/workspace/repos/test/_scratch/foo.md` was plumbing-exempt because regex allowed `_scratch/` at any depth. Tightened to `/workspace/_scratch/`.

### R2 verification (both fixed)

- 7/7 TestGateSkip pass (was 5/7 at R1). `_run_skip()` gained `force=False` parameter.
- Advisory regex tightened. `repos/_scratch/` no longer matches. 43/43 plumbing tests pass.

### Post-FINAL addendum: traversal guard

Operator caught `rm -f ~/workspace/_scratch/../repos/x/file.ts` bypassing the regex. Producer added normpath resolution in `engine.py`. Reviewer ran 7 adversarial traversal attempts:
- `_scratch/../repos/` → blocked
- `_scratch/./../../etc/passwd` → blocked
- `_scratch/skip/../../../repos/engine.py` → blocked
- `_scratch/../repos/b.md` via mv → blocked
- `_scratch_backup/foo.sh` (not _scratch/) → blocked
- Positive cases still exempt

CR-227 filed for the traversal hole.

### Adversarial verification (Fix 1, highest value)

Recreated original defect: 3 dirty entries (2 deliverables + 1 plumbing `.commit.sh`):
1. `gate-skip --reason "..."` → exit 1 REFUSED, names 2 deliverables, reviewed ledger empty (zero markers written)
2. `assert-gate-clear --session ...` → exit 1 BLOCKED, lists 3 unreviewed
3. `gate-skip --force-deliverables` → exit 0 SKIPPED with LOUD FORCED warning + SKIP-FORCED metrics + event-log row
4. Plumbing-only skip (CR-219 regression check) → exit 0 SKIPPED, no regression

### Decisions made

- Classified R1 test regressions as BLOCKING rather than advisory — they fail in CI, not just edge cases.
- Filed CR-227 (traversal) as reviewer rather than deferring to producer — addendum was operator-initiated, reviewer re-engagement required.
- Deferred CR-228 (reviewer pytest misclassification) to next gate-adjacent pair rather than adding another addendum.

### Knowledge capture audit

1. **Bugs/failures:** 2 test regressions caught R1, fixed R2. Traversal hole caught by operator QC, fixed in addendum.
2. **Decisions:** Mechanical enforcement over instruction compliance (third confirmed instance of this pattern).
3. **Patterns:** "Instruction-level constraints need mechanical enforcement at the damage point" — already captured as lesson by producer (`lesson-instruction-rules-need-mechanical-enforcement-2026-07-10.md`). No additional pattern extraction needed.
4. **Lessons:** Adversarial testing of security-adjacent code changes should include path traversal checks as standard practice. Not promoted to shared-intelligence (too narrow for now; revisit if traversal class recurs).
5. **State updates:** CR-224/225/226 status → Applied. CR-227 filed. CR-228 filed (deferred).
6. **Reusable:** The adversarial recreation methodology (seed dirty ledger, run gate-skip, verify refusal + verify no markers written) is reusable for future gate tests. Noted in execution log, not promoted.
