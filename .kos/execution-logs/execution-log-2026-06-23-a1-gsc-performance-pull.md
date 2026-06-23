---
type: execution-log
status: draft
created: 2026-06-23
updated: 2026-06-23
venture: ai-agency-core
tags: [execution-log, gsc, search-analytics, skill-build, client-growth, A1]
---

## 2026-06-23 — [A1] GSC performance-pull skill build

**What was built:** Complete GSC Search Analytics query engine (`gsc_search_analytics.py`) + reusable skill (`skills/gsc-performance-pull/SKILL.md`) that pulls Google Search Console performance data for any client from config alone and produces a dated markdown report + JSON companion.

**Files created/modified:**
- `repos/ai-agency-core/scripts/gsc_search_analytics.py` — new (auth, query, analysis, report writing, CLI)
- `skills/gsc-performance-pull/SKILL.md` — new (skill spec v1.0)
- `repos/ai-agency-core/scripts/ev-electric.config.json` — added `gsc_property` key
- `repos/ai-agency-core/scripts/s-and-h-contracting.config.json` — added `gsc_property` key
- `second-brain/04_projects/clients/_active/ev-electric-services/reports/gsc-performance-2026-06-23.md` + `.json`
- `second-brain/04_projects/clients/_active/s-and-h-contracting/reports/gsc-performance-2026-06-23.md` + `.json`

**Decision made:** Auth mirrors `gsc_indexing.py` (SA-first, ADC-fallback) but with `webmasters.readonly` scope. GSC property URL stored in client config's `gsc_property` key — supports both domain properties (`sc-domain:`) and URL-prefix properties (`https://...`).

**Alternatives considered:** Could have used the `googleapiclient.discovery` client library (heavier dependency, more abstraction). Chose raw `requests` + REST API to match the existing `gsc_indexing.py` pattern and keep dependencies minimal.

**Why this approach:** Consistency with the existing codebase. The Search Analytics API is a single POST endpoint — a full client library would be over-engineering.

**Proof results:**
- **EV Electric** (`sc-domain:evelectric.pro`): 339 queries, 2743 impressions, 11 clicks, 15 pages, 56 striking-distance queries. Healthy impression volume for a ~6-week-old site.
- **S&H Contracting** (`https://shcontractingunlimited.com/`): 68 queries, 321 impressions, 5 clicks, 23 pages, 12 striking-distance queries. Thinner data (URL-prefix only, newer pages). Report honestly flags thin data.
- **Hardcode scan:** grep for client-specific strings in engine source → all matches in comments/docstrings only (usage examples). Zero hardcoded client values in logic.

**ADC friction:** ADC token was expired (last refreshed 2026-06-08). Re-auth required: `gcloud auth application-default login` interactively in standalone terminal. This is the known recurring friction class — no fix until SA keys are unblocked by org policy.

**Reusable for future apps?:** Yes — the `query_search_analytics()` function is a general-purpose GSC Search Analytics wrapper. Any client with a GSC property can use it. The report engine + JSON companion pattern is the same two-file split used elsewhere.
