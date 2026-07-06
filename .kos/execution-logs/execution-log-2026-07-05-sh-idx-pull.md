---
type: execution-log
status: draft
created: 2026-07-05
updated: 2026-07-05
venture: s-and-h-contracting
tier: capture-only
chat-id: sh-idx-pull-202607052148
tags: [execution-log, s-and-h-contracting, indexation, url-inspection, similarity-audit, phase-0]
---

## 2026-07-05 — [SH-IDX] Fresh S&H indexation pull + duplication×indexation join

**What was built:** Full 47-URL GSC URL Inspection pull for S&H, duplication×indexation cross-table via the similarity-audit engine, and CR-206 advisory fix.

### Task A — Fresh indexation pull

**Scope:** 47/47 sitemap URLs inspected (29 Core-30 leaves + 5 hubs + 13 legacy/other). CR-164 compliant: 47 of 47 inspected, zero silent partials.

**Results (2026-07-06 inspection date):**

| Category | Count |
|---|---|
| Indexed | 18 |
| Crawled - currently not indexed | 21 |
| Discovered - currently not indexed | 7 |
| Unknown to Google | 1 |
| **Total** | **47** |

**Core-30 leaves breakdown (29 total, 6 indexed = 21%):**

| Service | Total | Indexed | Crawled-not-indexed | Discovered-not-indexed | Unknown |
|---|---|---|---|---|---|
| emergency-electrician | 9 | 0 | 5 | 4 | 0 |
| ev-charger-installation | 7 | 2 | 3 | 2 | 0 |
| panel-upgrade | 9 | 3 | 4 | 0 | 1 |
| light-fixture-installation | 4 | 1 | 2 | 1 | 0 |

**Hubs (5 total):** 4 indexed (emergency-electrician, ev-charger-installation, panel-upgrade, light-fixture-installation), 1 crawled-not-indexed (service-areas).

**Legacy pages (13 total):** 8 indexed, 5 crawled-not-indexed (contact, electrical-upgrades-and-inspections, expert-electrical-repairs, privacy-policy, specialized-electrical-services).

**Unknown to Google (1):** `panel-upgrade-woodbridge-va` — discovery failure; check internal linking and sitemap.

**Delta vs 2026-06-24 proxy snapshot (7/29 leaves indexed):** Now 6/29 — slight decrease, but the proxy method (GSC impressions + `site:` SERP) is imprecise; URL Inspection is authoritative. The difference is likely measurement-method variance.

**Delta vs IDX-1 (2026-06-25, 14/35 indexed):** The IDX-1 on-disk JSON (`repos/ai-agency-core/scripts/output/index-status-s-and-h-contracting-2026-06-25.json`) contains only a 3-URL test run — NOT the full pull. The full IDX-1 data lives in the execution log (`second-brain/_meta/handoffs/client-growth-sprint/execution-logs/execution-log-2026-06-25-idx1-indexation-diagnosis.md`): **14/35 indexed (9 leaves + 4 hubs + 1 legacy)**. The wave-status "14/35" claim is correct; the on-disk JSON was a partial test file.

**IDX-1→IDX-2 leaf delta: 9 indexed → 6 indexed (3 pages LOST indexation):**

| Page | IDX-1 (2026-06-25) | IDX-2 (2026-07-06) | Last crawl (IDX-2) |
|---|---|---|---|
| panel-upgrade-manassas-va | ✅ Indexed (crawled 2026-06-20 MOBILE) | ❌ Crawled - not indexed | 2026-07-01 MOBILE |
| ev-charger-installation-lake-ridge-va | ✅ Indexed (crawled 2026-06-20 MOBILE) | ❌ Crawled - not indexed | 2026-07-01 MOBILE |
| light-fixture-installation-manassas-va | ✅ Indexed (crawled 2026-06-20 MOBILE) | ❌ Crawled - not indexed | 2026-07-02 MOBILE |

All 3 were re-crawled by MOBILE in early July and Google **actively de-indexed them.** This is not stale data — Google revisited these pages and reversed its indexation decision. Combined with the 21 "Crawled - not indexed" pages, this shows Google is actively evaluating and rejecting S&H's template pages on quality grounds.

**Urgency implication for differentiation wave:** The trend is negative — S&H is LOSING indexed pages, not gaining them. The longer the template pages stay live without differentiation, the more pages Google will de-index on re-crawl. The differentiation wave is not just the fix — it is time-sensitive. Every re-crawl of an undifferentiated page risks another de-indexation.

**Pages that GAINED indexation (IDX-1→IDX-2):** ev-charger-installation-alexandria-va was "Crawled - not indexed" in IDX-1, now indexed in IDX-2 (crawled 2026-06-29 MOBILE). This is the lowest-similarity page in the ev-charger group (56.5%–60.2% vs siblings), consistent with the pattern that lower-similarity pages survive Google's quality gate.

**Key pattern — DESKTOP-only crawl = preliminary scan:** All 5 crawled-not-indexed emergency-electrician pages with 2026-06-11 last-crawl dates were crawled as DESKTOP only. Pages later promoted to the indexing pipeline (e.g., ev-charger-installation-alexandria-va, indexed 2026-06-29) were crawled as MOBILE. The DESKTOP-only crawl is Google's preliminary scan on a low-authority site — not a full mobile-first-indexing pass.

### Task B — Duplication × indexation join (D-02 answer)

**Method:** Pointed `gsc_reason_code_file` in `s-and-h-contracting.audit.json` at the extracted reason-code array (`gsc-reason-codes-s-and-h-2026-07-06.json`, 47 entries). Re-ran `sitewide_similarity_audit.py`. Cross-table generated: 99 pairs with per-pair similarity + per-URL index state.

**D-02 answer — emergency-electrician reason codes:**

All 9 emergency-electrician city pages carry either:
- **"Crawled - currently not indexed"** (5 pages: alexandria, burke, lorton, springfield, stafford) — verdict NEUTRAL
- **"Discovered - currently not indexed"** (4 pages: dale-city, lake-ridge, manassas, woodbridge) — verdict NEUTRAL

**Zero "Duplicate" codes. Zero "Canonical issue" codes.** Google has NOT flagged S&H pages as duplicates via canonical consolidation. The rejection is **"Crawled - currently not indexed"** — Google crawled the pages and chose NOT to index them. This is a **content-quality/authority gate**, not a duplicate-content technical classification.

**How S&H differs from EV:** EV's problem was crawl budget (Discovered - not indexed = Google hadn't crawled them yet). S&H's problem is worse: Google HAS crawled 5 of the 9 emergency-electrician pages and **actively declined to index them** ("Crawled - currently not indexed"). The other 4 haven't even been crawled yet (Discovered). This means:
- EV's fix was crawl signals (sitemap, internal links, Indexing API submission) → wait
- S&H needs **content differentiation** — Google saw the content and rejected it. Crawl signals alone won't fix this. The differentiation wave is the correct intervention.

**Cross-table pattern:** All 36 emergency-electrician pairs are CRITICAL (62.9%–81.4% similarity), and all carry NEUTRAL×NEUTRAL index states. The similarity-to-rejection correlation is total for this service group.

**Indexed leaf pages tend to be lower-similarity outliers:** ev-charger-installation-alexandria-va (indexed, PASS) appears in 6 pairs at 56.5%–60.2% (FLAGGED range) — the lowest-similarity pages are the ones Google indexes. Panel-upgrade-dale-city-va, panel-upgrade-springfield-va, panel-upgrade-lake-ridge-va (all indexed) also appear at the lower end of their group's range.

### Task C — CR-206 fix (help-text client literals)

**What changed:** Replaced 5 client-specific config filenames in `sitewide_similarity_audit.py` docstring (lines 30-31) and argparse epilog (lines 884-890) with generic `<client-slug>.audit.json` placeholders.

**Engine MD5:** `0d24195760c983a79035f84f6275d3e8` → `8ce89c0da771668bc73e06e8ad94b27a`

**Why the change:** CR-206 advisory from [SH-G1] independent review — help text contained `s-and-h-contracting.audit.json` and `ev-electric-services.audit.json` literals. Zero behavioral impact (help text only), but violated the CR-155/156 "zero client literals in engine" standard.

**Verification:** `grep -c 's-and-h-contracting\|ev-electric-services\|shcontractingunlimited\|evelectric' sitewide_similarity_audit.py` = 0 hits.

**Execution-log correction:** The [SH-G1] execution log stated "grep engine for all known client/city/owner literals = 0 hits" — that was technically inaccurate (the grep found 0 hits in executable logic but the help-text examples weren't covered). Accurate statement: "0 hits in executable logic; help text examples referenced specific configs (caught by CR-206, fixed by [SH-IDX])."

### Output files

| File | Location |
|---|---|
| Index status report (md) | `second-brain/04_projects/clients/_active/s-and-h-contracting/index-status-s-and-h-contracting-2026-07-06.md` |
| Index status report (json) | `second-brain/04_projects/clients/_active/s-and-h-contracting/index-status-s-and-h-contracting-2026-07-06.json` |
| Reason-code flat array (for audit join) | `second-brain/04_projects/clients/_active/s-and-h-contracting/gsc-reason-codes-s-and-h-2026-07-06.json` |
| Updated similarity audit (yaml) | `second-brain/04_projects/clients/_active/s-and-h-contracting/_s-and-h-contracting-similarity-audit.yaml` |
| Updated duplicate-content map (md) | `second-brain/04_projects/clients/_active/s-and-h-contracting/s-and-h-contracting-duplicate-content-map.md` |
| URL list (input) | `repos/ai-agency-core/scripts/sh-all-urls.txt` |

### Decisions made

1. **Inspected all 47 sitemap URLs, not just 29+5.** Rationale: complete coverage per CR-164; legacy pages provide baseline indexation context (8/13 legacy indexed = Google trusts the domain at the legacy-page level, confirming the leaf rejection is content-specific not domain-wide).
2. **Extracted results array to flat JSON for audit join.** The `gsc_url_inspection.py` outputs nested JSON (`{meta, summary, categories, results}`); the similarity audit engine expects a flat array (`[{url, coverageState, ...}, ...]`). Created `gsc-reason-codes-s-and-h-2026-07-06.json` as the bridge file rather than modifying either engine.
3. **Used `coverageState` as the reason-code field.** The engine reads `entry.get("indexing_state", entry.get("verdict", entry.get("coverageState", "")))` — `coverageState` is the most descriptive field ("Crawled - currently not indexed" vs "NEUTRAL"). The engine resolves it correctly.

### Reusable for future apps?

Yes — the full workflow (URL file from sitemap → inspection → flat-array extraction → audit config update → re-run with join) is the repeatable pattern for any client's indexation×duplication analysis. Pattern candidate: `pattern-seo-indexation-duplication-join`.

### Task Completion Checkpoint

**Step 1 — Bugs and failures:** ADC credentials were expired at first run; resolved by operator re-auth. Python stdout buffering caused background-task output to appear empty — workaround: `-u` flag + line-buffered fdopen. The IDX-1 on-disk JSON (`repos/ai-agency-core/scripts/output/index-status-s-and-h-contracting-2026-06-25.json`) is a 3-URL test file, not the full pull — the full IDX-1 data is in the execution log at `second-brain/_meta/handoffs/client-growth-sprint/execution-logs/execution-log-2026-06-25-idx1-indexation-diagnosis.md`. Initial producer assumption ("on-disk JSON is ground truth, wave-status 14/35 is wrong") was corrected by the independent reviewer (CR-214).

**Step 2 — Decisions made:** (1) Inspected all 47 sitemap URLs for full coverage. (2) Created flat JSON bridge file rather than modifying either engine. (3) Used `coverageState` for the reason-code join. All documented in "Decisions made" section above.

**Step 3 — Patterns emerging:** Pattern candidate `pattern-seo-indexation-duplication-join` — the sitemap→inspection→flat-extraction→audit-join workflow. Awaits 2nd instance (EV re-check at ~07-09 Wave-2 recheck) for promotion.

**Step 4 — Lessons learned:** (1) Always cross-check on-disk artifacts against execution logs — a partial test file can masquerade as a full run. (2) The `gsc_url_inspection.py` output format (nested JSON) is incompatible with the `sitewide_similarity_audit.py` input format (flat array) — a format-bridge step is needed; consider adding a `--flat` flag to the inspection engine for direct consumption. (3) Google actively de-indexes template pages on re-crawl — the differentiation wave is time-sensitive, not just beneficial.

**Step 5 — State updates:** `02_current-focus.md` not changed (Phase 0 is still in progress — this is one step within it). `_sh-differentiation-wave-status.md` should be updated at the next tracker pass to reflect IDX-2 results (6/29 leaves, 3 lost, urgency increased). Known issue: `panel-upgrade-woodbridge-va` unknown to Google — check internal linking.

**Step 6 — Productization readiness:** N/A — tier is capture-only.
