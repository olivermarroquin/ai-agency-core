---
type: execution-log
status: draft
created: 2026-06-22
updated: 2026-06-22
venture: ai-agency-core
tags: [execution-log, review-gate, rgh-10, independent-review, source-tagging]
---

# Execution Log — [RGH-10] Source-Tagging Independent Review (2026-06-22)

**Reviewer session** for the RGH-10 producer (reviewer-gate-exemption source-tagging).
Running alongside the producer per Phase R of the independent-reviewer-mandate v1.2.

## Baseline (before producer output)

- UTC: 2026-06-22T16:12:07Z
- Handoff: `handoff-2026-06-22-reviewer-gate-exemption-source-tagging.md` (status: queued at baseline)
- No RGH-10 dirty ledger at baseline
- Last event-log: RGH-9 reviewer-orchestrator ready-for-review at 2026-06-22T16:04:58Z

## Round 1 — First paste-back verification

### What was verified on disk
- Read all 4 changed source files: `engine.py`, `dirty-ledger-track.py`, `test_source_tagging.py`, tracker/changelog/handoff
- Ran producer's test suite myself: 37/37 pass (confirmed)
- Ran existing conformance suite: 91/100 pass (same 9 pre-existing failures — no regression)
- CR-012 regression check: 7 state-changing commands all still tracked as producer (PASS)

### Adversarial probes (7 probes, 3 blocking catches)

| Probe | Input | Result | Expected | Verdict |
|---|---|---|---|---|
| 1. Code file with 'reviewer' in name | `src/components/reviewer-dashboard.tsx` | producer | producer | SAFE |
| 2. Execution-log with '-reviewer' in name | `execution-logs/...-reviewer-notes.md` | **reviewer** | producer | **BYPASS** |
| 3. /tmp file with 'reviewer' in name | `/tmp/reviewer-output.md` | producer | producer | SAFE |
| 4. Verdict file outside .review-gate | `repos/.../verdict-independent-fake.json` | producer | producer | SAFE |
| 5. Actual firing tracker edit | `_review-skill-firing-tracker.md` | reviewer | reviewer | Expected |
| 6. Echo containing 'log-review-pass' | `echo log-review-pass is a script` | **gate-clearing** | producer | **BYPASS** |
| 7. grep on firing tracker (read-only) | `grep foo ...firing-tracker.md` | reviewer | N/A (never tracked) | Mitigated |

### Blocking findings

**BLOCKING-1: Execution-log filename bypass.**
`REVIEWER_ARTIFACT_PATH_PATTERNS` includes `r'execution-logs/.*-reviewer'` which matches any execution log whose filename contains `-reviewer`. A producer can name its own execution log `execution-log-2026-06-22-reviewer-*.md` → classified as `reviewer` → exempt from gate. The producer controls execution-log filenames.
- **Fix:** Tighten the pattern to only match known reviewer-session naming conventions, OR require the path to be in a reviewer-specific directory, OR use a more specific regex anchor.

**BLOCKING-2: Gate-clearing substring match too broad.**
`GATE_CLEARING_PATTERNS` uses `if pat in bash_cmd` (substring match). Any Bash command that *mentions* `log-review-pass` or `gate-skip` anywhere in the string gets classified as `gate-clearing` → exempt. Examples: `echo log-review-pass`, `cat log-review-pass.py`, `grep log-review-pass`. A producer running `echo "log-review-pass" && npm run deploy` would get its deploy exempted.
- **Fix:** Match only when the pattern is the *invoked command* (after python3 or as a standalone script), not a substring of arbitrary text.

**BLOCKING-3: Bash-command reviewer regex too broad.**
`_REVIEWER_ARTIFACT_RE.search(bash_cmd)` at line 630 matches any non-read-only Bash command that *mentions* a reviewer artifact path. E.g. `python3 some-script.py --input _review-skill-firing-tracker.md` → classified as `reviewer`. Partially mitigated by `$` anchor on some patterns (prevents mid-string matches), but `execution-logs/.*-reviewer` has no `$` anchor and will match mid-command.
- **Fix:** The Bash-command reviewer check should verify the path is the *target* of a write operation (e.g. `>>`, `>`, or a known write command), not just mentioned anywhere in the command.

### Minor findings

**minor-1: test_tampered_ledger_entry_still_blocks_on_recheck has no assertion.**
Lines 278-314 of `test_source_tagging.py`: the test runs the gate on a tampered ledger but never asserts the return code. Documents an "honest limit" but inflates the test count (37 claimed, but 1 is a no-op). Should either assert or be removed/renamed to a documentation function.

## Round 2 — Verification of Round 1 fixes + new adversarial probes

### Round 1 fixes verified on disk
- Read `engine.py` lines 560-700: BLOCKING-1 fix (exact suffixes), BLOCKING-2 fix (`_is_gate_clearing_bash()` with segment splitting), BLOCKING-3 fix (Bash reviewer regex removed), BLOCKING-4 fix (patterns match anywhere in path). All verified.
- Read `classify_entry_source()`: clean structure — gate-clearing check first, then file-path reviewer check, then default producer. Bash commands ONLY get gate-clearing or producer (BLOCKING-3 fix confirmed).
- Read `test_source_tagging.py`: 54 tests now (was 37). Added 17 regression tests for BLOCKING-1..4, plus tampered-ledger test now has assertion (minor-6 fix).
- MAJOR-5 documented as honest limit in docstring (lines 672-677).

### Re-ran all Round 1 adversarial probes (24 probes, all PASS)
- BLOCKING-1: 7 probes — reviewer-hack/notes/my-reviewer → producer; legitimate suffixes → reviewer
- BLOCKING-2: 8 probes — echo/cat/grep mentions → producer; actual invocations → gate-clearing
- BLOCKING-3: 4 probes — Bash mentioning reviewer paths → all producer
- BLOCKING-4: 5 probes — handoff folder logs with reviewer suffixes → reviewer; producer log → producer

### New Round 2 adversarial probes (8 probes, 2 new catches)

| Probe | Input | Result | Expected | Verdict |
|---|---|---|---|---|
| A. compound: gate-clearing && deploy | `python3 log-review-pass.py && npm run deploy` | **gate-clearing** | producer | **BLOCKING** |
| A2. compound: deploy && gate-clearing | `npm run deploy && python3 log-review-pass.py` | **gate-clearing** | producer | **BLOCKING** |
| B. code-review suffix | `execution-log-*-code-review.md` | producer | producer | SAFE |
| C. extended suffix | `...-independent-review-and-more-stuff.md` | reviewer | reviewer | Acceptable |
| D. fake date | `execution-log-FAKE-independent-review.md` | producer | producer | SAFE |
| E. .py not .md | `...-independent-review.py` | producer | producer | SAFE |
| F. firing-tracker in src/ | `src/_review-skill-firing-tracker.md` | **reviewer** | producer | **advisory** |
| F2. firing-tracker in /tmp/ | `/tmp/_review-skill-firing-tracker.md` | **reviewer** | producer | **advisory** |

### Round 2 findings

**BLOCKING-7: Compound Bash commands with gate-clearing segment exempt the WHOLE entry.**
`_is_gate_clearing_bash()` returns True if ANY segment invokes a gate-clearing script. But `classify_entry_source()` returns a single source for the whole command. So `python3 log-review-pass.py && npm run deploy` → `gate-clearing` → the deploy is exempted.
Attack vector: a producer chains a gate-clearing invocation with a state-changing command.
**Fix:** `_is_gate_clearing_bash()` should return True ONLY if ALL segments are gate-clearing or read-only. If any segment is state-changing and non-gate-clearing, classify the whole entry as `producer`.

**advisory-8: `_review-skill-firing-tracker.md` / `_review-gate-catch-register.md` patterns have no directory constraint.**
A producer could create `src/_review-skill-firing-tracker.md` and get it exempted. Low practical risk (the filename is highly specific and would raise suspicion), but violates the stated invariant that "reviewer-artifact classes are structurally disjoint from deliverables."
**Fix (optional):** Add a path constraint requiring these to be under `_meta/handoffs/` or `second-brain/`. Or accept as honest limit since the filename is so specific.

## Round 3 — Zero new catches. PASS.

### Verified on disk
- Read `engine.py` lines 589-663: BLOCKING-7 fix (`_is_gate_clearing_bash` requires ALL segments gate-clearing or read-only), advisory-8 fix (`_meta/handoffs/` prefix on tracker/register patterns). Both correct.
- Re-ran all Round 1+2 adversarial probes (24 probes, all pass)
- Ran 6 new edge cases: env-prefixed gate-clearing, direct `./script` invocation, semicolon compound, pipe compound, pure echo, `_is_segment_read_only` existence. All correct.
- 61/61 tests confirmed independently
- DoD table verified: Check-IDs present (RGH10-1..5), paths correct, assertions concrete
- CR-010/044/054 all updated to Resolved with accurate descriptions

### Convergence
3 rounds: [8, 2, 0] → converged. PASS issued.

## Close-out

### Deliverables
- Verdict JSON: `.review-gate/state/verdict-independent-rgh10-source-tagging-r3-PASS.json`
- Firing-tracker: 3 rows authored (Peer-review A, Gate-blocking B+, Quality-control Exempt)
- Gate cleared: `log-review-pass.py` with reviewer session `9eec04cc` ≠ producer `8d764cac`

### Catches summary (10 total across 3 rounds)
| Round | Count | Severity | Key catches |
|---|---|---|---|
| 1 | 8 | 4 BLOCKING + 1 MAJOR + 1 minor + 2 implicit | B1-B4 bypass vectors, M5 reviewer Bash honest limit, m6 no-op test |
| 2 | 2 | 1 BLOCKING + 1 advisory | B7 compound command bypass, a8 path constraint |
| 3 | 0 | — | Converged |

### Lessons
- **Path-based source inference is sound but regex precision is critical.** The producer's architecture (structural inference, not self-declared flags) was correct. Every blocking catch was a regex/matching precision issue, not an architecture flaw.
- **Adversarial probing catches what happy-path tests miss.** All 5 BLOCKING catches were bypass vectors the producer's own tests didn't cover — they only tested legitimate reviewer artifacts and legitimate producer deliverables, never the boundary where a producer *names things like* a reviewer artifact.
- **The compound-command interaction is a class.** BLOCKING-7 (gate-clearing segment exempts state-changing sibling segments) is a pattern that applies to any per-entry classifier that returns a single tag for compound commands. Worth remembering for future classifiers.
- **This review itself demonstrated the MAJOR-5 honest limit.** The reviewer's own Bash test-run commands hit the gate 3 times, requiring operator gate-skip each time. Session-level tagging (deferred) would fix this.
