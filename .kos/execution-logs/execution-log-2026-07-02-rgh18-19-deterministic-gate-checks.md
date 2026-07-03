---
type: execution-log
status: draft
created: 2026-07-02
updated: 2026-07-02
venture: ai-agency-core
tags: [execution-log, review-gate, rgh-18, rgh-19, deterministic-checks, productization]
---

# Execution Log — [RGH-18]+[RGH-19] Deterministic Build-Correctness + Doc-Completeness Gate Checks

**Tier:** Productize
**Chat ID:** `rgh18-19-deterministic-gate-checks-202607021800`
**Interface:** Claude Code in VS Code
**Blockers cleared:** [RGH-14]+[RGH-15]+[RGH-17]+[PR-1] all SHIPPED

## What Happened

- Built `rgh18-build-correctness.py` — Leg A: 3 BLOCKING checks as code (completeness diff, DIFF-AWARE all-dirty-file sweep, staging-reality audit)
- Built `rgh19-doc-completeness.py` — Leg B: 8 checks (OC-21..27 + OC-20 wired as code) enforcing documentation completeness
- Wired both into `independent-reviewer-dispatch.py` (full-tier runs now execute RGH-18+19 automatically)
- Registered OC-21..27 in `omission-check-registry.md` (v3.9→v4.0) with severity mappings, profile assignments, and seed incidents
- Wrote 17 regression tests — all pass; 0 new regressions on existing 127-pass conformance suite

## Steps / Procedure

1. Read handoff files, sprint plan, event-log, active-chats-tracker
2. Moved [RGH-18]+[RGH-19] Ready→Active (pass 346), appended event-log spawn row
3. Built `rgh18-build-correctness.py`:
   - Check 1: Completeness diff via dod-check.py — Productize tier requires manifest, missing = BLOCKING
   - Check 2: All-dirty-file sweep with DIFF-AWARE mode for append-only shared files (tracker, changelog, event-log, catch-register) — uses `git diff HEAD` to extract only added hunks, preventing false positives from pre-existing accumulated content
   - Check 3: Staging-reality audit via OC-16 across all repos with dirty-ledger entries
4. Built `rgh19-doc-completeness.py`:
   - OC-21: exec-log-substantive (exists + required headings + ≥3 list items)
   - OC-22: evidence-per-DoD-item (every acceptance criterion has recorded result)
   - OC-23: pattern-candidate-tracking (reusable-yes → pattern link or deferral)
   - OC-24: catches-referenced (CRs filed today appear in exec log)
   - OC-25: no-silent-deferrals (every deferral points to tracking surface)
   - OC-26: knowledge-capture-audit-ran (KCA checklist recorded)
   - OC-27: spec-vs-registry OC cross-check (CR-152 ratchet — OC-N in specs must match registry)
   - OC-20: productization-DoD B1-B6 (Productize-tier only, grep signals per PR-1 §B)
5. Added `_run_rgh18()` and `_run_rgh19()` helper functions to `independent-reviewer-dispatch.py`
6. Registered OC-21..27 in omission-check-registry with full procedure docs, seed incidents, script references
7. Updated universal checks list (added OC-21, OC-25, OC-27) and profile table (build + skill-build profiles)
8. Wrote 17 regression tests covering A3 replay (dropped target, leaked source, mis-staged commit), diff-aware mode, OC-21/25/27/20 checks
9. Fixed macOS `/var` vs `/private/var` realpath mismatch in diff-aware path resolution
10. Fixed B1 item_pattern regex (was not matching standard markdown bullet lists)

## Decisions Made

- **DIFF-AWARE approach for append-only files:** Used `git diff HEAD` to extract only added hunks. The `APPEND_ONLY_BASENAMES` set identifies shared files by basename (tracker, changelog, event-log, etc.). If `_get_added_lines()` returns `None` (untracked file), falls back to full-file check. This is the key fix for the "18 placeholder + 2 link FAILs on pre-existing content" problem that forced gate-skips.
- **Alternatives rejected:** (a) Timestamp-based filtering (fragile — requires knowing when the session started); (b) Content-hash before/after (requires storing pre-session state); (c) Whitelisting specific files entirely (unsafe — new placeholders would be missed).
- **workspace_root parameter threading:** OC-24 and OC-27 needed the workspace root to find the catch register and OC registry. Passed via `--workspace-root` CLI arg and threaded as function parameter (defaults to `_paths.WORKSPACE_ROOT` for production use, overridable for tests).
- **OC-20 B1 item_pattern fix:** Original pattern `r'^\s*[-*\d]+[.)]\s+\S'` required `.` or `)` after any character in `[-*\d]`, which didn't match standard markdown bullets like `- Step 1`. Fixed to `r'^\s*(?:[-*]\s+|\d+[.)]\s+)\S'` which properly handles both bullet and numbered lists.

## Engine / Config Split

The engine is the two Python scripts (`rgh18-build-correctness.py` and `rgh19-doc-completeness.py`). Configuration is:
- `APPEND_ONLY_BASENAMES` frozenset (which files get diff-aware treatment)
- `EXEC_LOG_REQUIRED_HEADINGS` list (what headings satisfy OC-21)
- `MIN_LIST_ITEMS` constant (minimum bullet items for substantiveness)
- B1-B6 grep signal patterns in `b_checks` dict (what patterns satisfy each DoD item)
- All tier-gating via `--tier` arg (Productize/Capture-only/Throwaway)

Zero hardcoded client values. The checks are fully client/venture-agnostic.

## Config Schema

```yaml
# RGH-18 configuration (embedded in rgh18-build-correctness.py)
append_only_basenames:
  type: frozenset
  values: [_active-chats-tracker.md, _active-chats-tracker-changelog.md,
           _recently-closed.md, _event-log.md, _review-skill-firing-tracker.md,
           _review-gate-catch-register.md]

# RGH-19 configuration (embedded in rgh19-doc-completeness.py)
exec_log_required_headings:
  type: list
  values: ["What Was Built|Happened", "Steps", "Procedure", "Decisions Made"]

min_list_items: 3

b_checks:
  B1: {patterns: [Steps, Procedure, "What Happened"], min_items: 3}
  B2: {patterns: [engine/config, config split, client-specific, reusable engine]}
  B3: {patterns: [config schema, "```yaml|json", field|key|parameter table]}
  B4: {patterns: [2nd-instance, proven on, non-electrician proof]}
  B5: {patterns: [safety, failure mode, guard, cross-contamination]}
  B6: {patterns: [skill-candidacy, worth productizing]}
```

## 2nd-instance verdict

The checks are fully client/venture-agnostic by design — they operate on dirty-ledger file paths, exec-log content, and registry files. The OC-27 spec-vs-registry cross-check was proven on the real workspace's registry (20 OC entries, 4+ spec files) during development. All 17 regression tests run in isolated temp workspaces with their own git repos. No 2nd-client proof needed — the checks don't reference any client.

## Safety and Quality Rules

- **Failure mode: false positive on pre-existing content** — mitigated by DIFF-AWARE mode for append-only files. Only added hunks are checked, not the whole file.
- **Failure mode: macOS symlink path mismatch** — mitigated by using `os.path.realpath()` on both sides of `os.path.relpath()` computation.
- **Failure mode: missing exec log causes silent skip** — mitigated by FAIL-CLOSED design (missing exec log = FAIL, not SKIP) for OC-21 and OC-26.
- **Guard: OC-27 cross-check prevents spec/registry divergence** — the CR-152 class of bug (OC numbers in specs don't match registry) is now caught automatically.
- **Guard: Productize-tier enforcement** — B1-B6 checks only fire on Productize tier; Capture-only and Throwaway skip them (tier-gated per PR-1 §E3).
- **Honest limit (stated in gate output):** Code enforces PRESENCE + SHAPE + EVIDENCE-FOR-EVERY-CLAIM, NOT insight quality. A mediocre-but-present lesson passes; a MISSING one BLOCKS.

## Skill-candidacy verdict

Skill candidacy: No — these are gate-machinery scripts, not standalone skills. They compose into the existing `gate-peer-reviewer` skill (bumped v3.9→v4.0) and are dispatched by `independent-reviewer-dispatch.py`. No separate skill packaging needed.

## Reusable for future apps?

Yes — the DIFF-AWARE append-only sweep pattern (check only git-diff added hunks for shared files) is reusable for any gate that sweeps accumulated files. PATTERN CANDIDATE: diff-aware-append-only-sweep — tracked for extraction after review.

## Gotchas

- `engine.PLACEHOLDER_PATTERNS` only matches standalone uppercase words (FILL, TBD, PLACEHOLDER, TODO, FIXME, XXX, CHANGEME) — NOT `[FILL_ME]` or `{{CITY}}`. Tests must use the right pattern vocabulary.
- `_paths.WORKSPACE_ROOT` is derived from the script's own location, not from CLI args. Functions that need to reference workspace paths must accept an optional `workspace_root` parameter for test isolation.
- B1 item_pattern must handle both `- bullet` and `1. numbered` list formats.

## Knowledge Capture Audit

- [x] Lessons / D-rows filed for every surprise (macOS realpath, B1 regex, placeholder vocabulary)
- [x] Execution log exists + complete
- [x] Event-log clean (spawn row appended)
- [x] State / status accurate (handoffs flipped to active, tracker updated pass 346)
- [x] Tool bugs: none new
- [x] Patterns/SOPs: diff-aware-append-only-sweep pattern candidate identified, tracked for extraction
