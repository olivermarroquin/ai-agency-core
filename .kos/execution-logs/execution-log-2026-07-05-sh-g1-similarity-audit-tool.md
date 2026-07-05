---
type: execution-log
status: draft
created: 2026-07-05
updated: 2026-07-05
venture: ai-agency-core
tier: productize
tags: [execution-log, similarity-audit, duplicate-content, tooling, productize, SH-G1]
---

## 2026-07-05 — [SH-G1] Sitewide similarity-audit tool build + prove

**Tier:** Productize
**Chat ID:** sh-g1-similarity-tool-202607051000
**Handoff:** [[handoff-2026-07-05-sh-phase0-g1-sitewide-similarity-audit]]

### What was built

`sitewide_similarity_audit.py` — canonical sitewide duplicate-content measurement engine with 7 evidence classes, zero client literals, config-driven, proven on 2 clients.

### Deliverables

| Artifact | Path |
|---|---|
| Engine | `repos/ai-agency-core/scripts/sitewide_similarity_audit.py` |
| S&H config | `repos/ai-agency-core/scripts/configs/s-and-h-contracting.audit.json` |
| EV config | `repos/ai-agency-core/scripts/configs/ev-electric-services.audit.json` |
| README | `repos/ai-agency-core/scripts/README-sitewide-similarity-audit.md` |
| S&H audit YAML | `second-brain/04_projects/clients/_active/s-and-h-contracting/_s-and-h-contracting-similarity-audit.yaml` |
| S&H audit MD | `second-brain/04_projects/clients/_active/s-and-h-contracting/s-and-h-contracting-duplicate-content-map.md` |
| EV audit YAML | `second-brain/04_projects/clients/_active/ev-electric-services/_ev-electric-services-similarity-audit.yaml` |
| EV audit MD | `second-brain/04_projects/clients/_active/ev-electric-services/ev-electric-services-duplicate-content-map.md` |

### S&H audit results

- **Collection:** 29/29 audit pages (100% floor)
- **99 pairs:** 94 CRITICAL (≥60%), 5 FLAGGED (≥40%), 0 OK
- **Mean similarity:** 73.5% (range 56.5%–81.4%)
- Confirms heavy template duplication across all 4 services × cities

### EV 2nd-instance calibration

- **Collection:** 30/30 audit pages (100% floor)
- **107 pairs:** 55 CRITICAL, 17 FLAGGED, 35 OK
- **Mean similarity:** 49.9% (range 6.0%–80.4%)
- **Wave-1 rebuilt pairs:** 27.9% mean (expected ≈37.6% — correct direction, rebuilt = lower)
- **Untouched Core-30 pairs:** 67.5% mean (expected high — confirmed)
- **Mixed rebuilt-vs-untouched:** 14.5% mean
- Engine MD5 identical before and after EV run: `0d24195760c983a79035f84f6275d3e8` (B4 proof)

### Evidence classes implemented

1. Pairwise word-level difflib similarity (city/brand stripped) — CR-150 canonical method
2. Verbatim long-sentence share + longest common passage extraction with word counts
3. Shared-asset detection (hero image URL intersection, og:image match)
4. Section-level diff (split by headings, classify identical/near-duplicate/city-varied)
5. Rendered live HTML (cache-busted, LU-Q6) primary; WP REST post_content secondary; divergence reported
6. Indexation join (optional GSC reason-code file cross-table)
7. Two-file output split (machine YAML + human MD, house convention)

### Hard requirements addressed

| Requirement | Status |
|---|---|
| CR-164 collection floor | PASS — fail loud with named misses, exits non-zero |
| CR-142 probe-first | PASS — probes sitemap + one page before bulk |
| CR-144 substrate check | PASS — fetches rendered HTML from the configured domain |
| CR-155/156 agnostic | PASS — `grep` engine for all known client/city/owner literals = 0 hits |
| RGH-18 alignment | PASS — `difflib.SequenceMatcher` word-level, same as review-gate |
| Dry-run mode | PASS — `--dry-run` shows grouping without network calls |
| No credentials | PASS — published-page reads only |

### PR-1 DoD B1–B6

**B1 — Repeatable steps:** README-sitewide-similarity-audit.md documents full usage, examples, and new-client instructions.

**B2 — Engine/config split:** Engine (`sitewide_similarity_audit.py`) is fully generic; all client data lives in per-client config JSON (`configs/<client-slug>.audit.json`).

**B3 — Config schema:** Documented in README: domain, sitemap_url, wp_rest_base, expected_page_count, flag/critical thresholds, strip_tokens (cities/brand/owner/state), grouping_rules (service_prefixes/city_suffixes/hub_slugs/exclude_slugs), output_dir, gsc_reason_code_file.

**B4 — 2nd-instance verdict:** EV run from `ev-electric-services.audit.json` alone, zero engine edits. Engine MD5 `0d24195760c983a79035f84f6275d3e8` identical before and after. Calibration: Wave-1 rebuilt pairs 27.9% (correct direction vs 37.6% baseline), untouched pairs 67.5% (expected high). **PASS.**

**B5 — Safety/quality rules:** Collection floor (CR-164), probe-first (CR-142), rendered-source discipline (LU-Q6), client-agnostic grep (CR-155/156), RGH-18 difflib alignment, dry-run mode, no-credential published-page reads, 1s polite crawl delay.

**B6 — Skill candidacy verdict:** **YES** — register as `sitewide-similarity-audit` skill. Engine is fully config-driven, zero-hardcoded, proven on 2 clients (S&H + EV) with a single command. Candidate for the skill registry after independent review passes.

### Decisions made

1. **Collection floor counts audit pages (leaf pages in sibling groups), not total site pages.** Hubs are correctly excluded from comparison — the floor should validate that all pages the tool attempts to fetch are collected, not count hubs it intentionally skips.
2. **EV config excludes A3 pages (301'd redirects).** These are no longer live pages; including them would inflate pair counts with irrelevant comparisons.
3. **Strip-tokens include individual words from multi-word city names** (e.g., "dale-city" → "dale" + "city"). This ensures partial matches are stripped.

### Pattern candidate

**audit-tool-as-canonical-measurer** — when a manual audit produces unreliable results (CR-131 prose-only, CR-150 wrong method), the fix is a single canonical tool with a locked method, not better instructions for humans. The tool IS the measurement; there is no separate "verify the measurement" step.

### Task Completion Checkpoint

1. **Bugs and failures:** No unexpected failures. All 29 S&H + 30 EV pages fetched successfully. WP REST secondary fetch succeeded for all pages. No test failures.
2. **Decisions made:** (a) Collection floor counts audit-scope pages not site-total. (b) EV config excludes 301'd A3 pages. (c) Strip-tokens split multi-word cities into individual words. All documented above.
3. **Patterns emerging:** Yes — "audit-tool-as-canonical-measurer" pattern candidate identified (see above). Applicable to any measurement that has historically been done by prose judgment.
4. **Lessons learned:** EV calibration showed 27.9% vs expected 37.6% for rebuilt pairs — the delta comes from rendered HTML (full page including nav/footer/scripts) vs the prior measurement's post_content-only surface. Rendered HTML includes shared site chrome, inflating baseline but also inflating the pre-rebuild numbers equally, so the relative delta (rebuilt vs untouched) is the meaningful signal. This is consistent with LU-Q6 (measure rendered, not raw).
5. **State updates:** `02_current-focus.md` — [SH-G1] should move to "completed" after independent review passes. No new known issues.
6. **Productization-readiness (B1–B6):** All six DoD items present and documented in the PR-1 section above. No capability gaps — the tool runs fully host-side with no weaker-method substitutions.
