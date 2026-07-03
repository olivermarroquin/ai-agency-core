---
type: execution-log
status: draft
created: 2026-07-03
updated: 2026-07-03
venture: ai-agency-core
tier: productize
tags: [execution-log, ai-agency-core, demand-capture, lu-r9, productize]
---

## 2026-07-03 — [LU-R9] demand-capture automation routine (Productize-tier)

**Tier:** Productize
**What was built:** `demand-capture` CLI routine that composes the §4 runbook
from `workflow-demand-ai-data-capture.md` into a single command emitting a
per-slug demand dossier the content brief consumes.

**Decision made:** Decompose into three modules (engine/helpers/emitter) +
per-client config JSON, keeping the engine zero-hardcoded and calling tier-3
scripts (`dataforseo_query.py`, `perplexity_sonar.py`) via subprocess.

**Alternatives considered:**
1. Import tier-3 scripts directly — rejected (would pull secrets-handling into
   the engine's import path; subprocess isolation is cleaner)
2. Single monolithic script — rejected (parsers are reusable by other tools;
   emitter separable for future templating)
3. CLI-only with no config — rejected (PR-1 B2 requires engine/config split;
   volume head terms and query templates are client-specific)

**Why this approach:** Subprocess calls preserve the tier-3 auth discipline
(credentials never leave the secrets files). Per-client config JSON makes the
routine instantly duplicable for new clients — proven by the S&H dry-run.

## Procedure (B1 — repeatable steps)

1. Create demand config JSON at `configs/<client>.demand.json` using the schema
   at `configs/demand-capture.config-schema.json`
2. Ensure the client has a `*.config.json` with `gsc_property` in the scripts dir
3. Run:
   ```
   cd ~/workspace/repos/ai-agency-core/scripts
   python3 demand_capture.py <service> <city> \
     --client <client-slug> --domain <domain> \
     --gsc-window <YYYY-MM-DD:YYYY-MM-DD>
   ```
4. Use `--dry-run` first to verify query shapes without API cost
5. After live run, fill the gated `s5-chatgpt-gemini-<slug>.md` manually via
   SOP-S5 (personal Chrome automation profile)
6. Dossier gate passes only when all in-scope sources are pulled or labelled

## Engine / config split (B2)

**Engine (reusable, zero client values):**
- `demand_capture.py` — CLI orchestrator
- `dfs_demand.py` — DataForSEO payload builders + response parsers + subprocess callers
- `demand_capture_emitter.py` — dossier markdown + JSON emitter + source-status gate

**Config (client-specific, lives in `configs/`):**
- `<client>.demand.json` — occupation, volume head terms, query templates,
  service display name overrides, GSC property, domain
- `<client>.config.json` — existing client config with `gsc_property` for GSC auth

All query terms are built from CLI args (`service`, `city`) + config templates
(`{service}`, `{city}`, `{state}`, `{occupation}` placeholders). The engine
never contains client names, cities, domains, or competitor names.

## Config schema (B3)

File: `configs/demand-capture.config-schema.json`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| client_slug | string | yes | Client identifier matching folder + config names |
| domain | string | yes | Client's primary domain |
| gsc_property | string | yes | GSC property URL (domain or URL-prefix) |
| state_abbrev | string | yes | Two-letter state code |
| location_code | int | no | DataForSEO location (default 2840 = US) |
| occupation | string | yes | Trade/occupation for head-term queries |
| volume_head_terms | string[] | no | Generic head terms always included in volume pull |
| volume_templates | string[] | no | Per-service/city keyword templates |
| sonar_query_templates | string[] | no | Perplexity Sonar query templates |
| chatgpt_gemini_query_templates | string[] | no | ChatGPT/Gemini manual query templates |
| service_display_names | object | no | Service slug → display name overrides |

## 2nd-instance verdict (B4)

**PASS.** Proven on two clients from config alone:

**(a) EV Wave-1 reproduction (dry-run):**
```
python3 demand_capture.py electrical-troubleshooting fairfax \
  --client ev-electric-services --domain evelectric.pro \
  --gsc-window 2026-05-23:2026-06-20 --dry-run
```
- SERP query `"electrical troubleshooting fairfax va"` matches `serp-1.json`
- S4 city seed `"electrical troubleshooting fairfax"` matches `s4-electrical-troubleshooting-fairfax.json`
- Volume keywords include all per-slug terms from the Wave-1 batch
- All 3 automated sources probed OK (DataForSEO funded $45.99, Sonar key present, GSC config found)

**(b) S&H 2nd-instance (dry-run):**
```
python3 demand_capture.py panel-upgrade woodbridge \
  --client s-and-h-contracting --domain shcontractingunlimited.com \
  --gsc-window 2026-06-01:2026-06-28 --dry-run
```
- Zero EV bleed: all queries use "woodbridge", paths under `s-and-h-contracting`
- GSC property correctly resolves to `https://shcontractingunlimited.com/`
- Runs from args + config alone with zero engine changes

## Safety / quality rules (B5)

| Failure mode | Guard |
|---|---|
| CR-142 (assumed blocked) | Reachability probe runs FIRST for every source — balance check for DataForSEO, key-file check for Sonar, config scan for GSC |
| CR-155/156 (hardcoded client values) | Engine files contain zero client-specific strings. Hardcode scan confirms: all hits are in docstring examples or argparse help text only |
| CR-141 (city mirroring) | NO-MIRROR assertion in dossier: each city's SERP query, S4 seed, and volume keywords explicitly include the city name. Dossier header states "No data inherited from sibling city" |
| Silent skip | Source-status gate MUST FAIL (exit 1) if any in-scope source is neither pulled nor labelled with SOP + reason |
| S5 ChatGPT/Gemini headless | Scaffolded as gated manual step — emits query list + empty artifact; does NOT fake or silently drop it |
| No-undefended-zeros | Volume response parser preserves null/sub-threshold as explicit values; zero-item S4 results documented as "confirmed finding" |
| Cost runaway | DataForSEO cost receipt printed on stderr per call; total cost reported at end |

## Skill-candidacy verdict (B6)

**Yes — worth productizing into a skill.** The routine mechanizes a 7-source
runbook that was previously manual (45+ minutes per slug in the EV Wave-1 run).
It is already config-driven and proven on 2 clients. Recommended next step:
wrap as `skills/demand-capture/SKILL.md` with the CLI as the engine, after the
T5 live validation proves it end-to-end.

## Deliverable manifest

| # | Artifact | Path | Purpose |
|---|----------|------|---------|
| 1 | CLI entry point | `scripts/demand_capture.py` | Main orchestrator |
| 2 | DataForSEO helpers | `scripts/dfs_demand.py` | Payload builders + parsers |
| 3 | Dossier emitter + gate | `scripts/demand_capture_emitter.py` | Two-file dossier + source-status gate |
| 4 | Config schema | `scripts/configs/demand-capture.config-schema.json` | JSON Schema for client configs (B3) |
| 5 | EV config | `scripts/configs/ev-electric-services.demand.json` | EV client instance |
| 6 | S&H config | `scripts/configs/s-and-h-contracting.demand.json` | S&H client instance |
| 7 | Execution log | `.kos/execution-logs/execution-log-2026-07-03-lu-r9-demand-capture-build.md` | This file |

## S&H invocation for T5

```
cd ~/workspace/repos/ai-agency-core/scripts

# Dry-run first
python3 demand_capture.py panel-upgrade woodbridge \
  --client s-and-h-contracting --domain shcontractingunlimited.com \
  --gsc-window 2026-06-01:2026-06-28 --dry-run

# Live run (costs ~$0.10 DataForSEO + ~$0.03 Sonar)
python3 demand_capture.py panel-upgrade woodbridge \
  --client s-and-h-contracting --domain shcontractingunlimited.com \
  --gsc-window 2026-06-01:2026-06-28
```
