---
type: execution-log
status: draft
created: 2026-07-26
updated: 2026-07-26
venture: ai-agency-core
tier: Productize
tags: [execution-log, ai-agency-core, gate-infrastructure, pre-commit-hook]
---

## 2026-07-26 — Gate test suite pre-commit hook (gate-testhook)

### What Happened

Extended `git_hook_adapter.py` to auto-run the full gate test suite before any commit that touches gate code. Narrow trigger: only fires when staged files are under `scripts/mandatory-review-gate/`. Blocks on any red test. Fails closed if pytest itself can't run.

**Why this exists:** A reviewer passed a gate change with 5 test regressions because no one ran the full suite — a human caught it out-of-band. This hook makes "the tests pass" mechanical: if gate code is staged and any test is red, the commit is blocked.

### Decisions Made

**Decision:** Run the suite as a subprocess (`python3 -m pytest`) from the gate dir rather than importing pytest programmatically. Subprocess isolation prevents the hook's own imports from interfering with test collection, and matches how the suite is run manually.

**Alternatives considered:**
- Import pytest and run `pytest.main()` inline — rejected because it shares the process and risks import contamination
- Run only changed test files — rejected because gate changes can break tests anywhere in the suite (cross-module dependencies)

**Why this approach:** Subprocess with cwd set to the gate dir mirrors the exact invocation the gate-suite-green pair validated (731 passed). Timeout at 120s prevents hung hooks. Fail-closed on pytest errors (not installed, import error) ensures a broken checker never silently passes.

### Steps

1. Added `has_staged_gate_files()` — realpath prefix match against `GATE_CODE_DIR`
2. Added `run_gate_test_suite()` — subprocess pytest runner with timeout, returns (success, output, failed, total)
3. Added `_parse_pytest_count()` — regex extraction of "N passed" / "N failed" from summary line
4. Integrated into `main()` after the existing review-gate dirty-ledger check (additive, not modifying existing flow)
5. Wrote 18 unit tests covering trigger logic, success/failure/error paths, cwd/timeout forwarding
6. Self-demonstrated 3 live scenarios (broken test blocks, clean passes, non-gate skips)

### Files Produced

- `scripts/mandatory-review-gate/git_hook_adapter.py` — 3 new functions + main() integration
- `scripts/mandatory-review-gate/tests/test_gate_testhook.py` — 18 new unit tests

### Test Results

749 passed (731 baseline + 18 new), 0 failed, 0 regressions.

### Self-Demonstration (3 scenarios)

1. Broken test + gate file staged → hook BLOCKED, named failing test, exit 1
2. Clean gate change staged → all 749 passed, exit 0
3. Non-gate file staged → no suite run, instant exit 0

### Knowledge Capture Audit

- [x] Bugs and failures: none unexpected
- [x] Decisions made: subprocess vs inline, full suite vs changed-files-only (documented above)
- [x] Patterns emerging: PATTERN CANDIDATE: pre-commit-narrow-test-gate
- [x] Lessons learned: nothing unexpected; existing hook structure made this straightforward
- [x] State updates: no sprint-level change
- [x] Productization-readiness (B1-B6): repeatable steps documented, engine/config split (GATE_CODE_DIR constant), no external config needed, reusable for any subdir gating, fail-closed safety, not a skill candidate (git hook)

**Reusable for future apps?:** Yes — the narrow-trigger + subprocess-test-suite pattern is reusable for any repo that wants to gate commits to a specific subdirectory on its test suite passing.

**Pattern candidate:** PATTERN CANDIDATE: pre-commit-narrow-test-gate — trigger test suite only when staged files match a specific path prefix; block on red; fail closed on runner error.

---

## Addendum: CR-245 — silent config degradation in no-PyYAML environments

**Bug found by:** The gate-testhook hook itself — it correctly blocked its own commit on 5 red tests, which led to discovery that `_parse_config_minimal` was a stub (`return {}`) and subprocess `python3` resolved to Python 3.14 (no PyYAML).

### What Happened

The gate engine's `load_project_config` and `load_plumbing_whitelist` both had `try: import yaml except ImportError: <fallback>` paths. The project config fallback was literally `return {}` (never implemented stub). The plumbing config had a fragile hand-rolled regex parser. Both silently lost all project-aware behavior in any no-PyYAML environment — which includes the Stop/PostToolUse hook environment and any pre-commit subprocess.

### Decisions Made

**Decision:** Eliminate the YAML dependency entirely by converting gate configs to JSON (Python's stdlib `json` module — zero dependency, works in every environment). Operator decision after first attempt to require PyYAML broke the gate in its own hook runtime.

**Why:** The YAML dependency was the root cause, not the fallback parser. Python has no stdlib YAML parser; any environment without PyYAML degrades. JSON is stdlib — zero dependency, zero hand-rolled parsers, works in hooks, CI, Hermes, pre-commit, everywhere.

### Steps (6-part fix)

1. **FIND ALL YAML:** Identified 2 config files (`config.yml`, `plumbing-whitelist.yml`) and all code that reads/writes them (engine.py loaders, test fixtures in test_conformance.py and test_cr228_cr229.py).
2. **CONVERT TO JSON:** Wrote `config.json` and `plumbing-whitelist.json` via `yaml.safe_load` → `json.dump`, verified data identity by asserting equality before proceeding.
3. **REWRITE LOADERS:** `load_project_config` and `load_plumbing_whitelist` now use `json.loads()`. Removed all `import yaml`. Removed `_parse_config_minimal` (stub) and `_parse_plumbing_minimal` (fragile regex parser). Removed PyYAML pre-check from `git_hook_adapter.py` (no longer needed).
4. **FAIL SAFE:** Missing/malformed plumbing config → stderr warning + no exemptions (fail-closed). Missing/malformed project config → stderr warning + generic defaults. The gate never crashes on config problems.
5. **UPDATE ALL REFERENCES:** Test fixtures converted from YAML strings to JSON dicts. All 6 test files use `sys.executable` instead of bare `python3`. Config file paths updated from `.yml` to `.json` everywhere.
6. **VERIFIED IN NO-YAML ENV:** Created throwaway venv without PyYAML, ran full suite: 750 passed, 0 failed. Hook runs cleanly. `grep -rn "import yaml"` returns nothing in gate runtime.

### Files Touched (addendum)

- `engine.py` — JSON loaders, removed both minimal parsers + all `import yaml`
- `git_hook_adapter.py` — removed PyYAML pre-check (no longer needed)
- `test_conformance.py` — fixture JSON config, `sys.executable`
- `test_circuit_breaker.py` — `sys.executable`
- `test_session_tagging.py` — `sys.executable`
- `test_source_tagging.py` — `sys.executable`
- `test_rgh20_plumbing_exemption.py` — `sys.executable`, `.json` path
- `test_git_hook_conformance.py` — `sys.executable`
- `tests/test_cr228_cr229.py` — JSON fixture instead of hand-rolled YAML
- `.review-gate/config.json` — NEW (converted from config.yml)
- `.review-gate/plumbing-whitelist.json` — NEW (converted from plumbing-whitelist.yml)
- `_review-gate-catch-register.md` — CR-245 updated

### Test Results (addendum)

750 passed in both with-yaml and no-yaml environments. Zero `import yaml` in gate runtime.
