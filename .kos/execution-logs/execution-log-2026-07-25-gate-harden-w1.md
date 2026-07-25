---
type: execution-log
status: draft
created: 2026-07-25
updated: 2026-07-25
venture: ai-agency-core
tags: [execution-log, review-gate, security, red-team-hardening]
---

## 2026-07-25 — Gate Hardening Wave 1 (Red-Team Findings CR-232..239)

**Tier:** Productize (gate code — safety-critical core)
**Source:** gate-redteam-findings-2026-07-11.md (adversarial red-team report)
**Paired run:** producer (this session) + independent reviewer

### What Was Built

8 fixes closing dangerous-direction + audit-integrity holes found by the red-team:

| Fix | CR | Severity | What Changed |
|---|---|---|---|
| 1 | CR-233 | Critical | `python3 -c` / heredoc: default state-changing, allowlist-only read-only idioms |
| 2 | CR-235 | Major | `curl -o/--output/-O` added to write flags; `wget` separated, default state-changing |
| 3 | CR-234 | Critical | Git pre-commit hooks installed (ai-agency-core, second-brain, ai-factory) |
| 4 | CR-238 | Medium | D-09 filename regex drops leading-dot; content scan extended to .py/.zsh/.bash |
| 5 | CR-232 | Critical (stopgap) | Self-clearing tripwire: D-09 event-log row on self-clear/self-register |
| 6 | CR-232 | Critical (stopgap) | RGH-15 inert reviewer: WARNING → DENY unless operator-dispatched |
| 7 | CR-236 | Major | Event-log path hardening: validates REVIEW_GATE_EVENT_LOG, rejects /dev/null |
| 8 | — | Durable guard | 161 table-driven classification tests (dangerous + safe directions) |

### Files Modified

- `engine.py`: `_is_python_inline_read_only()` + `_PYTHON_READ_ONLY_IDIOMS` + `_PYTHON_WRITE_INDICATORS` (Fix 1); curl/wget separation (Fix 2); `get_event_log_path()` validator (Fix 7)
- `dirty-ledger-track.py`: D-09 filename regex fix (Fix 4); self-clear/self-register tripwire (Fix 5); event-log path hardening (Fix 7)
- `log-review-pass.py`: RGH-15 inert reviewer DENY (Fix 6)
- `gate-skip.py`: event-log path hardening (Fix 7)
- `tests/test_classification_normalization.py`: 161 new tests (Fix 8)
- `tests/test_reviewer_independence_and_failclosed.py`: fixed pre-existing UUID v4 failures
- `tests/test_cr228_cr229.py`: updated test to match Fix 4's extended .py scan

### Red-Team Vectors Closed (acceptance criteria for Phase 4 re-run)

| Vector | Red-team command | Expected result after fix |
|---|---|---|
| C-1 repro 1 | `python3 -c 'open("/tmp/x","w").write("pwned")'` | BLOCKED (state-changing) |
| C-1 repro 2 | `python3 -c 'import shutil; shutil.rmtree("/tmp/dir")'` | BLOCKED (state-changing) |
| C-1 repro 3 | `python3 -c 'import os; os.remove("/tmp/x")'` | BLOCKED (state-changing) |
| C-1 repro 4 | `python3 -c '__import__("os").system("rm -rf /tmp")'` | BLOCKED (state-changing) |
| C-2 repro 1 | `curl -s https://example.com -o output.txt` | BLOCKED (state-changing) |
| C-2 repro 2 | `curl -sL https://example.com -o /tmp/file.bin` | BLOCKED (state-changing) |
| C-2 repro 3 | `wget https://example.com` | BLOCKED (state-changing) |
| B-1 | `test-gate-skip.sh` (no leading dot) | D-09 FIRES |
| B-3 | `.py` file with `--force-deliverables` | D-09 FIRES |
| B-4 | `REVIEW_GATE_EVENT_LOG=/dev/null` | REJECTED, default used |
| D-3 | Direct `git commit` (no .commit.sh) | BLOCKED by pre-commit hook |
| D-1 | `.commit.sh` with wrong session ID | BLOCKED by pre-commit hook (cross-session check) |

### Vectors NOT closed (deferred to roadmap)

| Vector | Why deferred |
|---|---|
| A (full fabricated reviewer chain) | Full fix = GIT-GATE Phase 2 daemon. Stopgaps (Fix 5 tripwire + Fix 6 inert-deny) make the path NOISY but not mechanically blocked. |
| E-1 (FINAL detection) | Out of scope (relay/spawn fragility cluster — FLEET roadmap) |
| E-2/E-3/E-4 (relay/spawn fragility) | Out of scope (FLEET roadmap) |

### Decisions Made

- **python3 -c default to state-changing:** allowlist approach (known safe idioms) rather than denylist. This causes friction for unknown python inline code but prevents the dangerous direction entirely.
- **wget separated from curl:** wget always writes to disk by default; only `-O -` (stdout-only) is read-only. Separate handler rather than sharing curl's flag-based approach.
- **Event-log path validation accepts STATE_DIR parent:** test isolation puts event-log as a sibling of the state subdir, so we accept paths under STATE_DIR's parent directory too.
- **Pre-existing test failures fixed:** 4 tests in `test_reviewer_independence_and_failclosed.py` were failing due to non-UUID session IDs; updated to use valid UUID v4 + proper role markers + reviewer activity.

### Review Rounds

- **R1:** BLOCKING — getattr dynamic dispatch bypass in `_PYTHON_READ_ONLY_IDIOMS` (reviewer-found). Fixed: removed getattr from allowlist.
- **R2:** PASS — getattr fixed, 3 regression tests added. Gate cleared.
- **R3:** Operator-found 5 regressions in `test_read_only_loops.py` (curl -o /dev/null over-blocked by Fix 2). Fixed: target-aware curl -o handling (exempt /dev/null and - stdout). Reviewer R3 PASS on full 731-test suite.

### Test Results (full module-dir suite)

- 731 total tests: 713 passed, 18 pre-existing failures (confirmed by stash-test against HEAD)
- 0 new regressions from this run's 8 fixes
- Pre-existing failures: circuit breaker timing (3), git push in GIT_PLUMBING_SUBCMDS (2), non-UUID session IDs in conformance (8), reviewer doc-write integration (2), protocol exemption (1), gate status (1), self-ref (1)
- New tests: 238 (161 classification normalization + 6 getattr + 6 curl target-aware + 4 reviewer independence + remaining test updates)
