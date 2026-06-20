---
type: execution-log
status: draft
created: 2026-06-20
updated: 2026-06-20
venture: ai-agency-core
tags: [execution-log, ai-agency-core, page-factory, reusability, zero-hardcoded, multi-client]
---

## 2026-06-20 — [F3] Page-factory reusability hardening

**What was built:** Zero-hardcoded audit + fixes across the entire page-build pipeline, toolkit-wide reuse map, 3rd-client proof from config alone, WP-vs-substrate-agnostic split doc, pattern bump with source-parameterization variation.

**Chat ID:** `f3-page-factory-reusability-hardening-202606190000`
**Substrate:** Claude Code (VS Code, ~/workspace)
**Duration:** ~4h across 2 sessions (2026-06-19 to 2026-06-20)
**Independent peer-review:** PASS (5 rounds total, converged [7,1,0,2,0], 9 total catches)

### Script-level fixes (4)

| Script | Line(s) | Was | Fix |
|--------|---------|-----|-----|
| `generate-imagery-prompts.py` | 82, 1181-1182 | `DEFAULT_CLIENT = "ev-electric-services"` + argparse `default=` | `DEFAULT_CLIENT = None`, `--client required=True` |
| `scaffold-core-30-page.py` | 928 | `default="ev-electric-services"` | `required=True` |
| `preflight-credentials` | 150 | Hardcoded `["ev-electric-services", "s-and-h-contracting"]` | Auto-discover from `data/client-*.json` glob |
| `load-secrets.sh` | 31-34, 54-57 | Per-client env var names for EV + S&H only | Dynamic glob over `wp-app-password-*.key` and `gsc-sa-*.json` |

### Data-level fixes (7)

| File(s) | Was | Fix |
|---------|-----|-----|
| `scaffold-service-data.py:849,1016` | `https://evelectric.pro/...` in runtime code | `{website_url_no_slash}/...` |
| 6 shared service JSONs (troubleshooting, ev-charger, light-fixture, outlet, panel-upgrade, smoke-alarm) | `https://evelectric.pro/...` in image URL templates | `{website_url_no_slash}/...` |
| `troubleshooting.json` AIOSEO title | `\| EV Electric` | `{client_alt_name}` |
| `generate-maps-iframe.py` | Cache served stale client-branded HTML to different client | Client-mismatch detection + regeneration |

### Decisions made

1. **`required=True` over config-file-default:** Making `--client` required (fail-loud) rather than reading a default from a config file. Rationale: a config-file default still silently picks a client — the operator should always explicitly name the client at invocation time.

2. **Glob-based auto-discovery for `preflight-credentials` and `load-secrets.sh`:** Rather than maintaining a hardcoded client list, both now discover clients from `data/client-*.json` files on disk. This is zero-maintenance — adding a 3rd client means creating one JSON file.

3. **Cache client-mismatch detection (not client-scoped keys):** The maps iframe cache key stays city-based (`"Bethesda, MD @z12"`), but now checks if the cached entry's `client` field matches the current client. Rationale: client-scoped keys would bloat the cache for shared cities; mismatch detection + regeneration is cheaper.

4. **Task 5 (re-wire client-seo-onboarding Step 6/7) explicitly deferred:** Step 6/7 re-wire depends on Soul Character + chooser calibration completing first (imagery-pivot handoff items still unchecked). Doing it now would produce a half-wired state. Tracked in imagery-pivot handoff remaining items.

### Alternatives considered

- **Per-client service data forks for ALL shared services:** Considered moving all shared service JSONs into `data/services/<client-slug>/` overrides. Rejected — the shared files are genuinely shared (service descriptions, FAQ structures); only the URL templates and AIOSEO titles had client bleed. Template variables (`{website_url_no_slash}`, `{client_alt_name}`) are the right fix.

- **Environment variable for default client:** Considered `CLIENT_SLUG` env var as the default. Rejected — env vars are invisible state; `required=True` on the CLI is explicit and fail-loud.

### Bugs / failures

- **B1 (CR-046):** The initial zero-hardcoded audit falsely marked `scaffold-service-data.py` as PASS. The audit agent read the argparse section (correctly `required=True`) but did not read the template-generation body where `evelectric.pro` was hardcoded at lines 849 and 1016. Caught by independent peer-reviewer Round 1. Root cause: audit-of-own-work blind spot — verified the output layer (JSON files) but not the generation layer (the script that creates them).

- **Maps cache client bleed:** The 3rd-client proof initially showed EV-branded maps iframe HTML in the Acme output. The cache was city-keyed without client awareness. Fixed by adding client-mismatch detection.

### Verification

- `py_compile` clean on all 5 edited Python scripts
- `bash -n` clean on `load-secrets.sh`
- `json.load()` valid on all 7 edited JSON data files
- Brand-leak guard (`check_brand_leak_gate` at line 363/1400 of `publish-core-30-page.py`) confirmed present and callable
- Placeholder gate (`check_placeholder_gate` at line 402/1375 of `publish-core-30-page.py`) confirmed present and callable
- 3rd-client bleed scan: 0 EV, 0 S&H, 32 correct Acme identity hits

### Reusable for future apps?

Yes — the source-parameterization pattern (template variables in data files + `required=True` on CLI + auto-discovery globs + cache client-mismatch guards) generalizes to any multi-tenant pipeline where artifacts are templated from per-tenant config. The `_substrate-split.md` doc directly feeds the Next.js website-factory build.

### Artifacts produced

- `repos/ai-agency-core/scripts/_audit-zero-hardcoded-2026-06-19.md`
- `repos/ai-agency-core/scripts/_substrate-split.md`
- `repos/ai-agency-core/scripts/_3rd-client-proof-acme/` (4 files: draft-v1.md, draft-v1-WP-WRAPPED.html, _VERSION-LOG.md, _bleed-scan-result.txt)
- `repos/ai-agency-core/scripts/data/client-test-client-acme.json`
- `second-brain/05_shared-intelligence/tools/_toolkit-reuse-map.md` (scripts section populated)
- `second-brain/05_shared-intelligence/patterns/pattern-source-client-leak-audit.md` (bumped + variation)
- CR-046 in `_review-gate-catch-register.md`
