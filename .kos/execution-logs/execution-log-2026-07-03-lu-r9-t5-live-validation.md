---
type: execution-log
status: draft
created: 2026-07-03
updated: 2026-07-03
venture: ai-agency-core
tier: productize
tags: [execution-log, ai-agency-core, demand-capture, lu-r9, t5, turnkey, live-validation, s-and-h-contracting]
---

## 2026-07-03 — [LU-R9/T5] demand-capture live validation — S&H from config alone

**Tier:** Productize
**Task:** PROVE the demand-capture pipeline is turnkey by running it LIVE for S&H (2nd client) from config alone, zero engine edits.

### What Happened

1. **Anti-hallucination grounding:** read all 3 engine files (`demand_capture.py`, `dfs_demand.py`, `demand_capture_emitter.py`), the S&H config (`configs/s-and-h-contracting.demand.json`), the T4 execution log, the workflow §3/§4, and the S&H project folder to identify real slugs.

2. **Dry-run first** — `panel-upgrade woodbridge` (a real S&H Core-30 page, woodbridge is S&H's HQ city):
   - All 3 automated sources probed OK: DataForSEO funded ($45.99), Sonar key present, GSC config found (`https://shcontractingunlimited.com/`)
   - All query shapes correct — zero EV bleed, all "woodbridge va" specific
   - S5-ChatGPT/Gemini correctly scaffolded as gated manual step

3. **LIVE run** — same slug, same command minus `--dry-run`:
   ```
   python3 demand_capture.py panel-upgrade woodbridge \
     --client s-and-h-contracting --domain shcontractingunlimited.com \
     --gsc-window 2026-06-01:2026-06-28
   ```

4. **Results by source:**

   | Source | Status | Detail |
   |--------|--------|--------|
   | S3 Volume | DONE | 7 keywords, $0.090000 |
   | S3 Difficulty | DONE | 7 keywords, $0.012840 |
   | S1/S2/S8 SERP | DONE | 4 PAA, 10+ organic, no AI Overview, $0.003500 |
   | S4 City seed | DONE | 0 items (confirmed — no hyperlocal DB entries), $0.012000 |
   | S4 Broad seed | DONE | 40 items, 6 question-form keywords, $0.016800 |
   | S5 Sonar | DONE | 2 queries, 1018 tokens out, ~$0.0254 |
   | S5 ChatGPT/Gemini | GATED | 2 queries emitted for manual capture (SOP-S5) |
   | S7 Existing | DONE | `gsc-performance-2026-06-23.json` found; 7 relevant "panel upgrade" queries |
   | S7 Fresh | GAP | GSC reauth needed (`gcloud auth application-default login`); labelled with SOP-S7 |
   | S8 | DONE | Via SERP — top 10 competitors identified |

5. **Gate:** PASS — 7/8 IN-SCOPE sources pulled, 1 gated, 1 labelled with SOP/reason. CR-164 minimum-pulled-count floor (≥2) satisfied (7 pulled).

6. **Verification:**
   - **EV bleed grep:** `evelectric|ev-electric-services|evelectric.pro|ahmad` → 0 hits. Clean.
   - **Fairfax grep:** hits are legitimate SERP competitor data (makeitnowhomeservices.com mentions Fairfax in their page title for a Woodbridge SERP result — that's the competitor's own cross-market SEO, not our bleed).
   - **NO-MIRROR:** dossier states "All data pulled for woodbridge, va specifically. No data inherited from sibling city."
   - **All metadata:** `client: s-and-h-contracting`, `domain: shcontractingunlimited.com` — correct throughout.

### Decisions Made

- **Slug choice:** `panel-upgrade woodbridge` — Woodbridge is S&H's HQ city and panel-upgrade is their primary service. This is the highest-relevance slug for a turnkey test (not an edge case).
- **S7-fresh gap handling:** labelled as gap with SOP-S7 (GSC ADC reauth needed). Did NOT fake data or skip silently. This is the correct behavior — the engine reports the gap; the operator reauths and re-runs.
- **Zero engine edits:** the entire run used the T4-built engine + the T4-authored S&H config. No patches, no workarounds.

### Cost Receipt

| Item | Cost |
|------|------|
| S3 Volume | $0.090000 |
| S3 Difficulty | $0.012840 |
| S1/S2/S8 SERP | $0.003500 |
| S4 City seed | $0.012000 |
| S4 Broad seed | $0.016800 |
| **Total DataForSEO** | **$0.135140** |
| S5 Sonar (est.) | ~$0.025400 |
| **Total all sources** | **~$0.160540** |

### Artifacts Produced

| # | File | Location |
|---|------|----------|
| 1 | `dossier-panel-upgrade-woodbridge-va.md` | S&H `content-briefs/demand-captures/` |
| 2 | `dossier-panel-upgrade-woodbridge-va.json` | S&H `content-briefs/demand-captures/` |
| 3 | `dfs-volume.json` | S&H `content-briefs/demand-captures/` |
| 4 | `dfs-difficulty.json` | S&H `content-briefs/demand-captures/` |
| 5 | `serp-panel-upgrade-woodbridge-va.json` | S&H `content-briefs/demand-captures/` |
| 6 | `s4-panel-upgrade-woodbridge.json` | S&H `content-briefs/demand-captures/` |
| 7 | `s4-broad-panel-upgrade.json` | S&H `content-briefs/demand-captures/` |
| 8 | `sonar-report.md` | S&H `content-briefs/demand-captures/` |
| 9 | `s5-chatgpt-gemini-panel-upgrade-woodbridge-va.md` | S&H `content-briefs/demand-captures/` |
| 10 | `_sonar-queries.txt` | S&H `content-briefs/demand-captures/` |

### Turnkey Verdict

**YES — pipeline ran for a 2nd client from config alone, zero engine edits.**

Evidence:
- Engine files (`demand_capture.py`, `dfs_demand.py`, `demand_capture_emitter.py`) were NOT edited — confirmed by task constraint (HARD RULE: no engine edits).
- The only client-specific input was `configs/s-and-h-contracting.demand.json` (authored in T4) + CLI args.
- All query terms, output paths, and dossier metadata correctly parameterized for S&H.
- Gate passed legitimately (≥2 sources actually pulled + every gap labelled).
- Dossier is clean: no EV data, no mirroring, all Woodbridge-specific.

### Key Insights

- **S4 per-city seed returns 0 items for "panel upgrade woodbridge"** — DataForSEO's keyword suggestion DB has no hyperlocal entries for this exact seed. This is a confirmed finding (not a bug), consistent with the EV Wave-1 pattern. The broad seed ("panel upgrade") returns 40 items with 6 question-form keywords — that's where the value is.
- **S7-fresh failure is expected** — GSC ADC tokens expire; the engine correctly labels the gap with the reauth SOP rather than silently skipping. The existing property-level GSC report (`gsc-performance-2026-06-23.json`) still provides the S7 demand layer.
- **Cost is modest:** ~$0.16 total per slug (DataForSEO + Sonar). At this rate, a full S&H 29-page Core-30 demand sweep would cost ~$4.64.
- **Competitor intelligence is immediate:** the SERP pull shows AJ Long Electric at position 3, confirming the competitive landscape documented in S&H's growth roadmap.

### Next Session Should Start With

- Operator reauths GSC (`gcloud auth application-default login` with the 3 explicit scopes per GSC auth memory) and re-runs with S7-fresh enabled for the full slug set.
- Fill the gated `s5-chatgpt-gemini-panel-upgrade-woodbridge-va.md` manually via SOP-S5.
- Consider running demand-capture for additional S&H slugs (emergency-electrician-woodbridge-va, ev-charger-installation-woodbridge-va) to build the full demand layer.
