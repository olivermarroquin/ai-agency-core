---
type: execution-log
status: draft
created: 2026-07-28
updated: 2026-07-28
venture: ai-agency-core
tier: Productize
tags: [execution-log, ai-agency-core, gate, review-gate, CR-244, CR-253]
---

## 2026-07-28 — Gate friction fix: CR-244 + CR-253

**What was built:** Two fixes to eliminate false-blocking of legitimate reviewer activity.

### CR-244 — gate-skip.py coordination-write exemption

**Problem:** `gate-skip.py:classify_unreviewed` only called `is_plumbing_exempt` (without `include_reviewer_only=True`) and did not check `is_bookkeeping_entry` or `is_close_coordination_entry`. Result: reviewer coordination writes (firing-tracker, catch-register, event-log) classified as "deliverables" → gate-skip REFUSED → operator forced into manual skip.

**Fix:** Extended `classify_unreviewed` to use the same three-layer exemption as the Stop hook and commit hook: (1) plumbing whitelist with `include_reviewer_only=True`, (2) bookkeeping basenames, (3) close-coordination surfaces. Achieves parity across all three gate paths.

**Audit result:** Stop hook (`check_gate`) and commit hook (`git_hook_adapter.py`, fc9d77d) already had full coverage — no changes needed there.

### CR-253 — read-only classifier gaps

**Problem:** Several inspection-only commands created dirty entries, blocking reviewer sessions.

**Fixes:**
1. `mkdir`/`mktemp` — carve-out in `_is_segment_read_only` and `_is_segment_write_safe`. Empty directory/temp-file creation produces no reviewable artifact.
2. `python3 -m pytest/unittest` — new handler recognizing `-m pytest` and `-m unittest` as read-only test runners. Unknown modules (`python3 -m http.server`, `python3 -m pip install`) still block correctly.
3. `grep|head`, `for`-loops with `grep`/`curl -I`, `cat` — confirmed already working (regression tests added).

### Deliberately NOT done
- **CR-258** (pair token reaching reviewer env) — part of the reverted forgeable token system; moot.
- **Reviewer-badge recovery** — proven forgeable 2026-07-27, reverted uncommitted. Recommitting would be false confidence. The real fix is the daemon (tracked in 02_current-focus.md).

### Tests
- 41 new tests in `tests/test_cr244_cr253_gate_friction.py`
- 1 existing test updated (`test_session_tagging.py:test_mkdir_command`)
- Full suite: 838 passed, 0 failed

### Self-demonstration results
All 4 mandatory demos passed:
1. Reviewer coordination writes → plumbing (not deliverable) in gate-skip ✓
2. mkdir -p, pytest, grep, cat, mktemp → no dirty entry ✓
3. Genuine deliverable (.py file) → STILL blocks ✓
4. No token/secret/badge code in diff ✓

**Reusable for future apps?:** Yes — the exemption-parity pattern (all gate paths must use the same exemption logic) is a reusable principle for any multi-path enforcement system.
