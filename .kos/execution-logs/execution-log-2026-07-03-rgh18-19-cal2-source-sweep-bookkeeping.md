---
type: execution-log
status: draft
created: 2026-07-03
updated: 2026-07-03
venture: ai-agency-core
tags: [execution-log, review-gate, rgh, calibration, cr-161, cr-165, cr-166]
---

## 2026-07-03 — [RGH-18/19-CAL-2] Source-sweep + bookkeeping fast-path + integrity

**Tier:** Capture-only

### What Happened

- **Fix A (CR-161 class 1):** Exempted source-code files (.py, .js, .ts, etc.) from the full-file bare-word placeholder sweep. Added `SOURCE_CODE_EXTENSIONS` frozenset + `_is_source_code_file()` classifier in `rgh18-build-correctness.py`. Gate code containing FILL/TODO/PLACEHOLDER as string literals / regex no longer self-flags. Deliverable markdown still catches.
- **Fix B (CR-161 class 2):** Added close-out bookkeeping fast-path in `engine.py`. `is_bookkeeping_entry()` classifies tracker/changelog/_recently-closed/_event-log/_review-skill-firing-tracker + handoff status-only flips as self-review-exempt. **Anti-smuggle guard:** `_is_handoff_status_only_edit()` inspects the git diff — a handoff edit that changes scope/AC content does NOT get the exemption. Integrated into `check_gate()` alongside existing source-tagged and session-level exemptions.
- **Fix C (CR-166):** OC-24 in `rgh19-doc-completeness.py` now scopes by session_id in the catch register's "surfaced by" column (col 3). A CR filed today by a *different* parallel chat no longer forces a block on this session's close.
- **Fix D (CR-165):** `log-review-pass.py` now requires `--reviewer-session` on ALL paths (not just `--reviewer-type=independent`). Universal checks: valid UUID v4, registered in the reviewer-session registry, distinct from producer session. The old `--reviewer-type=independent`-only block was moved to a universal position. Replays the client-schema-sync `cat >`-authored no-reviewer-session verdict → REJECT.
- **Fix E:** End-to-end synthetic clean close test in `test_rgh18_rgh19.py`. A fully-reviewed deliverable + normal bookkeeping files (tracker, changelog, _recently-closed, event-log, firing-tracker) → gate PASS with zero gate-skip.
- Updated `test_conformance.py` `_run_log()` helper to auto-create a registered reviewer session (CR-165 adaptation). Updated 3 conformance tests for bookkeeping exemption behavior.

### Decisions Made

- **Source-code exemption by file extension, not path pattern:** Chose extension-based classification (`.py`, `.js`, etc.) over path-based exemption. Simpler, broader, and correct — source code in any directory should be exempt. The spec/reference doc exemption (CR-160 class 2) remains separate for `.md` files in `references/` dirs.
- **Bookkeeping exemption in `check_gate()`, not in the Stop hook adapter:** The exemption is substrate-agnostic business logic, not CC-specific. Placed it in `engine.py` so future adapters (git pre-commit, daemon) also benefit.
- **Anti-smuggle via git diff inspection:** The handoff status-only classifier inspects the actual diff content, not just the file path. A `status:` flip + blockquote addition passes; any other content change fails closed. This is the load-bearing guard against laundering deliverables as bookkeeping.
- **CR-165: universal `--reviewer-session`:** Rather than adding it only to the `producer` path, made it universal. Every PASS must be attributable to a registered reviewer session. This closes ALL paths, not just the one CR-165 observed.

### Test Results

- `test_rgh18_rgh19.py`: **48/48 PASS** (29 existing + 19 new fixtures)
  - 3 fixtures for Fix A (2 false-positive .py files, 1 true-positive .md deliverable)
  - 10 fixtures for Fix B (3 bookkeeping basename true, 4 BASH bookkeeping/non-bookkeeping, 1 handoff status-only flip true, 1 anti-smuggle handoff scope change, 1 deliverable false)
  - 2 fixtures for Fix C (1 false-positive other-chat CR, 1 true-positive own-session CR)
  - 3 fixtures for Fix D (1 no-reviewer-session reject, 1 unregistered reject, 1 registered pass)
  - 1 fixture for Fix E (end-to-end synthetic clean close with BASH event-log append)
- `test_conformance.py`: **127 pass / 12 fail** — 0 new regressions (12 pre-existing)
- Updated old CR-160 OC-24 true-positive fixture for CR-166 format (session_id in surfaced-by column)

### Fix B2 (operator-caught incompleteness, same session)

First Fix B attempt missed two paths:
1. BASH `printf >> _event-log.md` entries — `is_bookkeeping_entry()` returned False for `BASH:` keys without inspecting the command target. Fixed: `_is_bash_bookkeeping()` parses `>>` / `>` redirect targets and checks basenames against `BOOKKEEPING_BASENAMES`.
2. Handoff status-only flip — `_is_handoff_status_only_edit()` regex was too narrow (only matched `status:` and `consumed:` frontmatter fields; missed `updated:`, `priority:`, and other YAML fields; blockquote continuation regex was too restrictive). Fixed: broadened to match any `key: value` YAML field and any `> ...` blockquote line.
3. `is_bookkeeping_entry()` signature changed to accept either a full entry dict (with `bash_cmd`/`display` fields) or a bare string path (backward compat). `check_gate()` call site passes the full entry dict.

### CRs Filed / Resolved This Run

- **CR-161:** Resolved — source-sweep exemption (Fix A) + bookkeeping fast-path (Fix B)
- **CR-165:** Resolved — `--reviewer-session` required on all paths (Fix D)
- **CR-166:** Resolved — OC-24 session-scoped CR ownership (Fix C)

### Artifacts Produced

- `repos/ai-agency-core/scripts/mandatory-review-gate/rgh18-build-correctness.py` — Fix A: `SOURCE_CODE_EXTENSIONS` + `_is_source_code_file()` + integration
- `repos/ai-agency-core/scripts/mandatory-review-gate/engine.py` — Fix B: `is_bookkeeping_entry()` + `_is_bookkeeping_basename()` + `_is_handoff_status_only_edit()` + `check_gate()` integration
- `repos/ai-agency-core/scripts/mandatory-review-gate/rgh19-doc-completeness.py` — Fix C: OC-24 session-scoped CR ownership
- `repos/ai-agency-core/scripts/mandatory-review-gate/log-review-pass.py` — Fix D: universal `--reviewer-session` requirement
- `repos/ai-agency-core/scripts/mandatory-review-gate/test_rgh18_rgh19.py` — 15 new paired fixtures
- `repos/ai-agency-core/scripts/mandatory-review-gate/test_conformance.py` — `_ensure_reviewer_session()` helper + 3 test updates

### Knowledge Capture Audit

1. Bugs/failures: 2 test failures on first run (old OC-24 fixture lacked session_id column; CR-165 registered-reviewer test needed firing-tracker at WORKSPACE_ROOT). Both fixed.
2. Decisions: documented above (extension-based, engine-level, diff-based anti-smuggle, universal reviewer-session).
3. Patterns: the "gate false-positive → calibration handoff" pattern is now at its 3rd instance ([RGH-18/19] → [CAL] → [CAL-2]). Consider a standing calibration cadence rather than reactive handoffs.
4. Lessons: none new beyond the pattern observation.
5. State updates: CR-161/165/166 marked Resolved (pending catch register update).
6. Productization: N/A (Capture-only tier).
