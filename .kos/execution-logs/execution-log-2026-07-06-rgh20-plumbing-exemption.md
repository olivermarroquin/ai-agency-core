---
type: execution-log
status: complete
created: 2026-07-06
updated: 2026-07-06
venture: ai-agency-core
tags: [execution-log, review-gate, rgh-20, cr-219, plumbing-exemption]
---

## 2026-07-06 — [RGH-20] Reviewer-plumbing gate exemption

**Chat ID:** rgh-cr219-plumbing-exemption-202607061300
**Tier:** Productize
**Duration:** ~2h
**Pairing:** producer + separate-session reviewer

### What was built

Three-part fix for CR-219 (5+ manual gate-skip commands in 24h, all plumbing):

**Part A — Plumbing whitelist (config-driven, engine-integrated)**
- Config file: `.review-gate/plumbing-whitelist.yml` — 7 plumbing patterns + 4 hard non-exempt path substrings
- Engine: `load_plumbing_whitelist()`, `is_plumbing_exempt()`, plumbing exemption in `check_gate()` for reviewer sessions only
- Patterns: relay files, gate-skip scripts, pair-launch scripts, spawn files, commit scripts, execution logs, firing-xlsx regen

**Part B — Compact operator-first block message**
- Terminal message capped at 8 lines: headline with counts (N deliverables, M plumbing), bulleted findings (basename only), decision line, block-file pointer
- Decision line: deliverables present → "Relay to your reviewer"; plumbing-only → operator skip with auto-written `.gate-skip-<sid>.sh`
- Full details + ready-made commands in `.review-gate/state/block-<session>-<ts>.md`

**Part C — Repeat-block dedupe + self-referential recursion**
- Dedupe: reuses circuit-breaker history; if same unreviewed fingerprint seen 2+ times, prints one-line reminder instead of full block
- Plumbing-scratch writes (`.gate-skip*.sh`, `_relay-*.md`, `_spawn-*.md`, `.pair-launch-*.sh`) suppressed at dirty-ledger-track level — never create dirty entries regardless of session role

### Key decisions

- **Suppress at track level (not just exempt at gate level):** plumbing-scratch files at workspace root are NEVER deliverables regardless of session role. Suppressing at track prevents the self-referential recursion (Part C's core bug) and keeps the dirty ledger clean.
- **Config-file-driven patterns:** satisfies DoD B2 (engine/config split). Adding new plumbing patterns requires only a YAML edit, no code changes.
- **Hard non-exemptions in config + code carve-outs:** `repos/` in hard_non_exempt catches the broad case; code carve-outs re-allow `.kos/execution-logs/` and `.commit.sh` (the only legitimate plumbing paths inside repos/).

### Reviewer catches

1. **BLOCKING (R1):** `repos/` was missing from `hard_non_exempt` in YAML, making the code carve-out dead code. A file named `execution-log-2026-07-06-smuggle.md` anywhere in repos/ would bypass the gate. Fixed by adding `'repos/'` to the YAML + adversarial test.

### Artifacts

| File | Action |
|---|---|
| `.review-gate/plumbing-whitelist.yml` | NEW — config file |
| `repos/ai-agency-core/scripts/mandatory-review-gate/engine.py` | EDITED — plumbing whitelist loading + matching + check_gate integration |
| `repos/ai-agency-core/scripts/mandatory-review-gate/dirty-ledger-track.py` | EDITED — plumbing suppression at track level |
| `repos/ai-agency-core/scripts/mandatory-review-gate/mandatory-review-gate.py` | EDITED — compact block message + dedupe + block file |
| `repos/ai-agency-core/scripts/mandatory-review-gate/test_conformance.py` | EDITED — 2 assertions updated for new message format |
| `repos/ai-agency-core/scripts/mandatory-review-gate/test_rgh20_plumbing_exemption.py` | NEW — 34 tests |

### Test results

- 34 new tests: ALL PASS
- 379+ pre-existing tests: ALL STILL PASS
- 17 pre-existing failures: unchanged
- Reviewer independent verification: PASS (2 rounds, AC-1..AC-7 + D-11 all verified)

### Reusable for future apps?

Yes — the plumbing whitelist is workspace-generic. Any project using the review gate benefits automatically. The config-file-driven approach means new plumbing patterns can be added without code changes.
