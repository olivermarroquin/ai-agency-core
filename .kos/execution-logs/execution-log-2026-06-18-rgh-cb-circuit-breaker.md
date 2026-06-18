---
type: execution-log
status: draft
created: 2026-06-18
updated: 2026-06-18
venture: ai-agency-core
tags: [execution-log, ai-agency-core, review-gate, circuit-breaker, RGH-CB]
---

## 2026-06-18 — RGH-CB: Stop-hook circuit breaker (P0 reliability fix)

**What was built:** Circuit breaker for the mandatory review gate stop hook that prevents infinite loops when dirty-ledger entries cannot be cleared.

**Producer chat:** `review-gate-circuit-breaker-producer-202606181305`
**Reviewer chat:** `rgh-cb-peer-review-202606181400`

### Changes

1. **Circuit breaker (`mandatory-review-gate.py`):**
   - Append-only JSONL history file (`<session>-stop-hook-history.jsonl`) with SHA-256 fingerprinting of the sorted unreviewed entry key set
   - Counts trailing consecutive rows with the same fingerprint
   - Auto-skips after N=3 consecutive identical blocks with LOUD box warning to stderr
   - Logs `circuit-breaker-triggered` event to `metrics.jsonl` with entry list
   - Counter resets: on new entries (different fingerprint), on entries cleared (gate passes), and after CB fires

2. **Read-only classification (`engine.py`):**
   - Added `verify-artifact.py` to `READ_ONLY_PYTHON_SCRIPTS`
   - **Removed `READ_ONLY_PYTHON_FLAGS`** — the flag-based heuristic (`--skip-http`, `--dry-run`, `--json`, `--profile`, `--validate`, `--check`) was added then removed after adversarial review proved it was a gate bypass (CR-012)

3. **Regression tests (`test_circuit_breaker.py`):**
   - 8 tests: CB fires on 3rd block, history file created, metrics logged, counter resets on new entry, counter resets after CB fires, CR-012 flag regression, verify-artifact.py exempt, plain python3 tracked
   - 108/108 total (8 new + 100 existing conformance)

### Adversarial review catches

**CR-011 (Blocking):** History file poisoning. Original design used a JSON file with `consecutive_count` field — a producer could overwrite it with `count: 2` and the CB would fire on the first real block. `.review-gate/` paths are excluded from dirty-ledger tracking, making the poisoning write invisible. **Fix:** switched to append-only JSONL with SHA-256 fingerprint of the actual runtime entry set. Pre-seeded rows cannot match.

**CR-012 (Blocking):** `READ_ONLY_PYTHON_FLAGS` gate bypass. The flag heuristic exempted ANY `python3` command with `--json`/`--dry-run`/`--profile` etc., including `publish-core-30-page.py --profile live` and `gsc_indexing.py --dry-run` — full publish/indexing operations bypassed the gate. **Fix:** removed the flag heuristic entirely; only exact basename whitelist remains.

### Decisions made

- **Append-only JSONL over JSON overwrite:** The JSON overwrite design was vulnerable to single-write poisoning. Append-only means an attacker must append rows with the correct fingerprint, but the fingerprint is derived from the actual unreviewed set at runtime — not predictable before the session creates dirty entries.
- **Remove flag heuristic over denylist:** Considered a STATE_CHANGING_SCRIPTS denylist to override the flag heuristic, but decided the gate cannot verify that a script actually respects `--dry-run`. The original exact-basename whitelist is the only safe pattern.

### Key insight

The producer's own independent reviewer agent returned PASS without probing the escape hatch. The adversarial peer reviewer (this chat) caught both bypasses by specifically trying to break the auto-skip mechanism. **Lesson: security-sensitive gates need adversarial probing of the escape hatch itself, not just happy-path verification.** The independent reviewer that confirms "the code does what it says" is different from the adversarial reviewer that asks "can this be abused?"

**Reusable for future apps?:** Yes — the append-only-JSONL-with-fingerprint pattern is reusable for any circuit breaker that needs to resist state poisoning. The "whitelist by exact name, not by flag" principle applies to any read-only classifier.
