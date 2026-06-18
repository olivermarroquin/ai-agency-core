---
type: execution-log
status: draft
created: 2026-06-18
updated: 2026-06-18
venture: ai-agency-core
tags: [execution-log, ai-agency-core, verify-artifact, DA4, autonomy, pre-publish-gate]
---

## 2026-06-18 — [DA4] Build verify-artifact consolidated pre-publish tool

**What was built:** `verify-artifact.py` — a single runnable command that performs the full on-disk verification sweep across all configured surfaces and returns a per-check pass/fail verdict. Agnostic engine + per-artifact-type profiles (Core-30 page-folder is ONE profile).

**Architecture:**
- Engine (`repos/ai-agency-core/scripts/verify-artifact.py`): loads a verification profile (JSON), loads artifact surfaces from a directory, loads ground truth data, runs each configured check, emits per-check verdict (JSON + human summary).
- 7 check types implemented: `pattern-sweep`, `identity-leak`, `value-cross-check` (presence + structured-extract modes), `schema-coverage`, `image-integrity`, `link-resolution`, `hardcode-scan` (SC-1 delegation).
- Profile-driven: engine carries zero SEO/Core-30 hardcoding.
- Importable API: `verify_from_code()` for composition with other scripts.

**Profiles created:**
1. `profiles/verify-core-30-page.json` — Core 30 page folder (HTML body + AIOSEO .md). 8 checks: placeholder-sweep (with PLACEHOLDER token), source-client-leak, value-cross-check (9 facts: dispatch_time, county, utility, owner, phone, city_name, review_rating, review_count, jsonld_area_served), schema-coverage (JSON-LD types + fields), aioseo-meta (FILL in meta block), image-integrity, internal-link-resolution, hardcode-scan (SC-1 delegation).
2. `profiles/verify-research-brief.json` — Non-page proof (research brief .md). 3 checks: placeholder-sweep, source-client-leak, value-cross-check.

**Test results:**
- Clean page (EV Bethesda page 24): 8/8 checks PASS, 0 findings.
- Clean page (EV Vienna page 01): 8/8 checks PASS, 0 findings — verifies cities missing dispatch_time_short/electric_utilities don't false-positive (8/22 cities lack these fields).
- Planted-defects fixture (city: bethesda-md — has all ground-truth fields): 17 blocking catches across 7/8 checks: meta-FILL (placeholder-sweep + aioseo-meta), S&H brand leak (identity-leak), PLACEHOLDER.png image (image-integrity), wrong review rating 4.9 vs 5.0 + count 68 vs 148 (value-cross-check structured-extract), missing Service JSON-LD type (schema-coverage), 45-minute hardcode default (hardcode-scan).
- S&H Woodbridge page 01 (real page, not fixture): caught real FILL: placeholders, image FILL, stale review_rating/count, hardcode-scanner defaults — all genuine issues.
- **Not exercised:** internal-link-resolution requires HTTP checks (`--skip-http` disables it). The planted dead link `/ev-charger-installation-nonexistent-city/` is in the fixture but was not caught in offline mode. Live-site verification needed to exercise this check. A local-path-matching approach (checking known slugs on disk without HTTP) is a potential future enhancement.

**Publish integration:**
- `publish-core-30-page.py` gains `--preflight-verify` + `--verify-skip-http` flags.
- Guard 0 (verify-artifact sweep) runs before the existing Guards 1+2 (placeholder + brand-leak). Defense in depth — the consolidated sweep subsumes the slice gates but they remain as a fast inner layer.
- Derives city/client/service slugs from the page folder's draft-v1.md frontmatter.

**Decision made:** value-cross-check uses TWO modes instead of one:
- **presence**: verify ground-truth value appears in surface (simple string search, no regex). Used for most facts (dispatch_time, county, owner_name, phone, city_name).
- **structured-extract**: precise regex extraction from structured locations (JSON-LD ratingValue, reviewCount). Used only when the extraction target is unambiguous.
The original approach (regex-extract everything, compare) produced 280+ false positives on a clean page. The hybrid approach: 0 false positives on clean, catches all planted defects.

**Alternatives considered:** Building the value-cross-check as a pure regex-extraction approach (extract all claimed values from the page, diff against ground truth). Rejected: overly broad extraction patterns match prose text, producing hundreds of false positives. The presence + structured-extract hybrid is more reliable.

**Reusable for future apps?:** Yes — the engine is fully agnostic. Any artifact-verification pipeline provides a JSON profile declaring surfaces, ground truth, and checks. The research-brief profile proves this. Future profiles: WP-vs-custom migration verification, custom-HTML site build, research brief quality check.

**Files produced:**
- `repos/ai-agency-core/scripts/verify-artifact.py` (engine)
- `repos/ai-agency-core/scripts/profiles/verify-core-30-page.json` (Core-30 profile)
- `repos/ai-agency-core/scripts/profiles/verify-research-brief.json` (non-page proof)
- `repos/ai-agency-core/scripts/test-fixtures/verify-artifact/planted-defects/` (test fixture)
- Modified: `repos/ai-agency-core/scripts/publish-core-30-page.py` (--preflight-verify integration)
- Modified: `second-brain/_meta/handoffs/handoff-2026-06-07-da4-verify-page-consolidated-tool.md` (status → active)
- Modified: `second-brain/_meta/handoffs/_active-chats-tracker.md` (DA4 row Ready→Active)
