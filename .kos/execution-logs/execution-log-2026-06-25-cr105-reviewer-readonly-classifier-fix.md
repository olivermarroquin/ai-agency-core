---
type: execution-log
status: draft
created: 2026-06-25
updated: 2026-06-25
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, classifier, CR-105, CR-106, CR-107]
---

## 2026-06-25 — Review-gate read-only classifier fix (reviewer capture-then-inspect false-positives)

**Authored in Cowork (no Stop hook).** This change touches the safety system itself
and landed on disk UNREVIEWED. It must ride through an independent review pass
before commit. The conformance suite is the safety net and is green except for 12
pre-existing failures (see Open Issues). See `[[_review-gate-catch-register]]`
CR-105/107/108 and `[[pattern-shell-classifier-substitution-quote-aware]]`.

### Trigger
The independent A3 reviewer (ev-electric 16-page build) was blocked by the Stop hook
on 5 of its OWN read-only checks (`curl`/`grep` verification of live pages), and the
operator had to clear it with `gate-skip` — repeatedly, all day. Root cause was the
read-only Bash classifier mis-judging reviewer "capture then inspect" command shapes
as state-changing.

### What was built
Three safety-preserving fixes in `repos/ai-agency-core/scripts/mandatory-review-gate/engine.py`,
plus 12 tests in `test_conformance.py`:

1. **Command substitution + assignment awareness** (`_is_segment_read_only` + new
   `_is_pure_assignment`). `x=$(curl ...)` is now read-only iff its inner command(s)
   are read-only (inner is run through `split_compound`, so a pipe-to-writer like
   `$(curl | tee f)` still gates). Bare assignments (`f=~/path`) run no command → read-only.
2. **`split_compound` is substitution-aware.** Operators (`| ; && ||`) inside `$(...)`
   or backticks are no longer split points, so `total=$(curl ... | grep -c ...)` stays
   one unit instead of being mangled into fragments that mis-classified.
3. **`_has_stdout_file_redirect` is quote-aware.** A `>`/`<` inside quotes (e.g.
   `grep "<loc>"` parsing a sitemap) is data, not a redirect.
   Plus two follow-on false-positive fixes found by adversarial testing: arithmetic
   `$((n+1))` and quoted-space assignment values (`msg="a b"`) are now read-only.

### Decision made
Fix only the read-only classification (what gates the reviewer's *looking*). Do NOT
touch the path that gates the reviewer's *deliverables* (verdict file, firing-tracker
row, catch-register row, reviewer execution logs). Verified those are a separate code
path (`classify_entry_source` + `REVIEWER_ARTIFACT_PATH_PATTERNS`) untouched by this change.

**Alternatives considered:** (a) widen the reviewer-session exemption
(`register-reviewer-session.py`) instead — rejected because fixing classification at
track-time removes the entries at the source and doesn't depend on the reviewer
remembering to register; (b) whitelist `curl` broadly — rejected, `curl` can POST.

### Why this approach
Classifying correctly at track-time means the reviewer's read-only checks never enter
the dirty ledger, so there is nothing to clear and no `gate-skip` temptation. Every
write/POST/redirect/`rm` variant still gates (proven adversarially).

### Reusable for future apps? Yes
Pattern: a shell-command permission/safety classifier must be substitution-, quote-,
and arithmetic-aware; naive first-word/whitespace parsing produces false-positives
that erode trust and drive unsafe overrides (gate-skip). Captured as
`[[pattern-shell-classifier-substitution-quote-aware]]`.

### Verification (on disk, not at face value)
- `test_conformance.py`: 113 tests; 12 pre-existing failures (unchanged — proven via
  HEAD baseline diff: identical failure set before/after). 12 new CR-105 tests pass.
- All sibling suites green: `test_session_tagging` (122), `test_source_tagging` (61),
  `test_git_hook_conformance` (24), `test_circuit_breaker` (8) — all OK. `split_compound`
  is used by these, so this was a required check (initially missed — see Lessons).
- The 5 exact command shapes that blocked the A3 reviewer now classify read-only;
  `x=$(rm)`, `$(curl|tee f)`, `curl -X POST`, real redirects all still gate.

### Lessons learned
- **The reviewer gate is deliberate, not accidental.** (Corrected an earlier wrong
  claim.) The system explicitly recognizes + gates the reviewer's deliverables so the
  reviewer can't skip producing its firing-tracker / catch-register / execution-log
  outputs. The bug was narrowly the *read-only inspection* gating, which the system
  already tried to exempt and did incompletely.
- **Don't claim "no regressions" from one test file.** First pass ran only
  `test_conformance.py`; `split_compound` is shared, so the other four suites had to be
  run too. They were, and passed — but the gap was real negligence risk.
- **Baseline-diff is the honest regression check**, not "the suite is green" (it isn't —
  12 red). Captured before/after failure sets and diffed.

### Open issues / gaps surfaced (NOT brushed over)
- **CR-106 (Major, open):** `python3 -c "...write..."` is classified read-only by the
  *universal* read-only path — write-detection only exists in the reviewer-exemption
  path. A producer could write deliverables via inline python and escape the ledger.
  Pre-existing (not introduced here). A quick fix backfires (would newly gate benign
  `sys.stdout.write` / `shutil.which`), so it needs a careful write-detector. Deferred.
- **CR-107 (Process, open):** `test_conformance.py` ships with 12 failing tests
  (git-push-whitelist tests + reviewer-session-contract tests are stale vs current
  code). The safety net's "is it green?" signal is therefore unusable. Triage/update needed.
- **Reviewer execution-log naming dependency:** reviewer logs only get the gate
  exemption if named with exact suffixes (`-reviewer-session`/`-independent-review`/
  `-peer-review`). A differently-named reviewer log will gate. Document in the reviewer mandate.
- **Operator friction:** commit blocks handed to the operator use `\`-continuation
  multi-line form, which drops the terminal into a heredoc/continuation on paste.
  Producer/reviewer should emit SINGLE-LINE git commands. Captured to memory.
