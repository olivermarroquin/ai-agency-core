---
type: audit
status: complete
created: 2026-06-19
client: all
scope: repos/ai-agency-core/scripts/
chat-id: f3-page-factory-reusability-hardening-202606190000
tags: [audit, zero-hardcoded, source-client-leak, reusability]
---

# Zero-hardcoded audit — `repos/ai-agency-core/scripts/`

**Date:** 2026-06-19
**Auditor chat:** f3-page-factory-reusability-hardening-202606190000
**Scope:** Every `.py` and `.sh` file under `repos/ai-agency-core/scripts/` (including `mandatory-review-gate/` subtree)
**Pattern:** `[[pattern-source-client-leak-audit]]` (times-observed: 6 after this audit)

## Search terms

Grepped for: `EV Electric`, `ev-electric`, `evelectricservices`, `S&H`, `s-and-h`, `Shaban`, `Ahmad`, `Mohammad`, `danny`, `keelworks` (case-insensitive), plus `default=` patterns in argparse and variable assignments.

## Findings — BLOCKING (fixed in this session)

| # | File | Line(s) | Hardcoded value | Type | Fix applied |
|---|------|---------|-----------------|------|-------------|
| 1 | `generate-imagery-prompts.py` | 82, 1181-1182 | `DEFAULT_CLIENT = "ev-electric-services"` + argparse `default=DEFAULT_CLIENT` | Silent argparse default | Removed default; `--client` now `required=True` |
| 2 | `scaffold-core-30-page.py` | 928 | `default="ev-electric-services"` | Silent argparse default | Removed default; `--client` now `required=True` |
| 3 | `preflight-credentials` | 150 | `["ev-electric-services", "s-and-h-contracting"]` fallback list | Hardcoded client list | Auto-discovers from `data/client-*.json` glob; fails loud if none found |
| 4 | `load-secrets.sh` | 31-34, 54-57 | Per-client env var names + file paths for EV + S&H only | Hardcoded discovery | Dynamic glob over `wp-app-password-*.key` and `gsc-sa-*.json` |

## Findings — PASS (already parameterized or client-agnostic)

| File | Status | Notes |
|------|--------|-------|
| `publish-core-30-page.py` | PASS | Reads client from config file (`--config`), no default client |
| `bulk-scaffold-pages.py` | PASS | `--client required=True` |
| `generate-and-distribute-heroes.py` | PASS | `--client required=True` |
| `generate-maps-iframe.py` | PASS (post-F3) | `client_name: None` with fail-loud comment; requires config. **Cache client-mismatch guard added** — cached entry from different client triggers regeneration instead of serving stale branded HTML. |
| `choose-image-variant.py` | PASS | No client default |
| `facts-completeness-gate.py` | PASS | No client default |
| `hardcode-scanner.py` | PASS | Profile-driven scanner; no client specifics |
| `verify-artifact.py` | PASS | Reads from `--vars`; no default client |
| `scaffold-client-data.py` | PASS | `--client-slug required=True` |
| `scaffold-city-data.py` | PASS | `--client-slug required=True` |
| `scaffold-service-data.py` | BLOCKING→FIXED (lines 849, 1016) | `--client-slug required=True`. **Lines 849/1016 hardcoded `evelectric.pro` in `hero_image_url_template` and `about_portrait_url_template` runtime code.** Fixed to `{website_url_no_slash}`. |
| `submit-gsc-indexing.py` | PASS | `--client-config required=True` |
| `gsc_indexing.py` | PASS | Library; no argparse |
| `audit-published-links.py` | PASS | Reads from page folder; no client default |
| `insert-internal-links.py` | PASS | Reads from page folder; no client default |
| `optimize-image.py` | PASS | Generic image tool |
| `organize-image-downloads.py` | PASS | `--client required=True` |
| `ingest-real-photos.py` | PASS | `--client required=True` |
| `upload-image-to-wp.py` | PASS | Reads from config file |
| `wire-images-into-html.py` | PASS | Reads from config |
| `wire-page-images.py` | PASS | Reads from config |
| `refresh-cached-image.py` | PASS | Reads from config |
| `fetch_youtube_transcript.py` | PASS | Client-agnostic |
| `_load_secrets.py` | PASS | Generic key loader |
| `append_event_log.sh` | PASS | Client-agnostic |

### `mandatory-review-gate/` subtree — all PASS

All 18 files are project-agnostic infrastructure. No client-specific values in runtime code. Test fixtures (`test-fixtures/verify-artifact/planted-defects/`) contain EV Electric test data by design (they test the brand-leak detector on known-EV content).

## Findings — DOC-ONLY references (not runtime, no fix needed)

Client names appear in docstrings, `--help` example text, README `.md` files, and `config.example.json` files. These are appropriate as usage examples and don't affect runtime behavior:

- `scaffold-core-30-page.py` lines 26, 31, 65-66 (docstring examples)
- `upload-image-to-wp.py` lines 23-57 (docstring examples)
- `ev-electric.config.example.json` (template file — expected)
- `s-and-h-contracting.config.example.json` (template file — expected)
- All `README-*.md` files (documentation)
- `data/client-ev-electric-services.json` (per-client data file — expected)
- `data/client-s-and-h-contracting.json` (per-client data file — expected)
- `data/services/*.json` (per-service data, some with client-specific overrides in subfolders — expected by design)
- `data/cities/*.json` (per-city data with client context in Q&A bodies — expected)

## Findings — DATA-LAYER (fixed during 3rd-client proof)

Discovered when the `test-client-acme` scaffolder run still produced EV-branded output despite script fixes.

| # | File(s) | Hardcoded value | Fix applied |
|---|---------|-----------------|-------------|
| 5 | `data/services/troubleshooting.json` | `aioseo_page_title_template` had `\| EV Electric` | Changed to `{client_alt_name}` |
| 6 | `data/services/troubleshooting.json`, `ev-charger.json`, `light-fixture-installation.json`, `outlet-installation.json`, `panel-upgrade.json`, `smoke-alarm.json` | `hero_image_url_template` and `about_portrait_url_template` had `https://evelectric.pro/...` | Changed to `{website_url_no_slash}/...` |
| 7 | `generate-maps-iframe.py` cache | City-only cache key served stale EV-branded iframe to new client | Added client-mismatch detection — regenerates when cached `client` differs from current |

## Data-layer references (acceptable by design)

The `data/` directory under scripts contains per-client JSON files (`client-<slug>.json`) and per-client service overrides (`data/services/<slug>/`). These are the parameterization target — scripts read FROM these files. The files themselves naming a client is correct and expected. Adding a 3rd client means creating `data/client-<slug>.json` + optional service overrides, not editing existing files.

## Keelworks references

`keelworks` appears in `publish-core-30-page.py` (WP plugin endpoint paths like `/wp-json/keelworks/v1/...`), `scaffold-client-data.py` (credentials template mentioning `oliver@keelworks.ai`), and `preflight-credentials` (GCP project `keelworks-seo-automation`). These are agency-level infrastructure constants (the agency IS Keelworks), not per-client values. They're correct to remain.

## Verification

```
python3 -m py_compile generate-imagery-prompts.py   # OK
python3 -m py_compile scaffold-core-30-page.py       # OK
python3 -c "import ast; ast.parse(open('preflight-credentials').read())"  # OK
bash -n load-secrets.sh                               # OK
```
