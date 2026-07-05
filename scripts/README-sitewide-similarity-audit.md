# Sitewide Similarity Audit Tool

Canonical duplicate-content measurement engine. Mechanizes the manual audit that was done for EV (2026-06-25) so every client gets measured, not vibed.

## Quick start

```bash
# S&H full audit
python3 scripts/sitewide_similarity_audit.py \
  --config scripts/configs/s-and-h-contracting.audit.json

# EV 2nd-instance (calibration proof)
python3 scripts/sitewide_similarity_audit.py \
  --config scripts/configs/ev-electric-services.audit.json

# Dry run (shows what would be fetched, no network calls)
python3 scripts/sitewide_similarity_audit.py \
  --config scripts/configs/s-and-h-contracting.audit.json --dry-run
```

## Requirements

- Python 3.9+
- `pyyaml` (`pip install pyyaml`)
- No authentication needed (reads published pages only)

## How it works

1. **Probe** sitemap + one page (CR-142 probe-first)
2. **Collect** all pages in configured sibling groups from rendered live HTML (cache-busted, LU-Q6)
3. **Collection floor** (CR-164): compare fetched vs expected, fail loud with named misses
4. **Compare** pairwise within each sibling group using word-level `difflib.SequenceMatcher` (RGH-18 aligned)
5. **Emit** two-file output: machine YAML + human markdown

## Evidence classes

| # | Evidence | Method |
|---|---|---|
| 1 | Pairwise word-level similarity | `difflib.SequenceMatcher` on tokenized words, city/brand/owner tokens stripped |
| 2 | Verbatim long-sentence share | Fraction of 8+ word sentences shared; longest common passages extracted with word counts |
| 3 | Shared-asset detection | Image URL intersection, og:image match, CTA block detection |
| 4 | Section-level diff | Split by headings; classify each section identical / near-duplicate / city-varied |
| 5 | Rendered vs WP REST | Primary = live rendered HTML (cache-busted); secondary = WP REST post_content; report divergence |
| 6 | Indexation join | Optional GSC reason-code file cross-table (similarity × coverage state) |
| 7 | Two-file output | `_<client>-similarity-audit.yaml` (machine) + `<client>-duplicate-content-map.md` (human) |

## Config schema (B3)

```json
{
  "client_slug": "string — kebab-case client identifier",
  "domain": "string — bare domain (no protocol)",
  "sitemap_url": "string — full URL to sitemap index or sitemap",
  "wp_rest_base": "string — WP REST API base URL (usually https://domain)",
  "expected_page_count": "int — total live pages (for collection floor)",
  "flag_threshold": "float — similarity >= this = FLAGGED (default 0.40)",
  "critical_threshold": "float — similarity >= this = CRITICAL (default 0.60)",
  "output_dir": "string — absolute path for output files",
  "gsc_reason_code_file": "string|null — path to GSC index-status JSON (optional)",
  "strip_tokens": {
    "cities": ["list of city name tokens to strip before comparison"],
    "brand": ["list of brand tokens"],
    "owner": ["list of owner name tokens"],
    "state": ["list of state abbreviations/names"]
  },
  "grouping_rules": {
    "service_prefixes": ["list of service slug prefixes"],
    "city_suffixes": ["list of city-state slug suffixes"],
    "hub_slugs": ["list of hub page slugs to exclude from comparison"],
    "exclude_slugs": ["list of slugs to exclude entirely (e.g. 301'd pages)"]
  }
}
```

## Safety / quality rules (B5)

- **Collection floor (CR-164):** exits non-zero with named miss list if any page fails to fetch
- **Probe-first (CR-142):** probes sitemap + one page before bulk collection
- **Rendered-source discipline (LU-Q6):** live rendered HTML is the primary surface; WP REST is secondary
- **Client-agnostic (CR-155/156):** zero client literals in the engine; all client data in config
- **RGH-18 alignment:** uses the same `difflib.SequenceMatcher` word-level method as the review-gate similarity check
- **Dry-run mode:** `--dry-run` shows what would be fetched without making network calls
- **No credentials:** reads only published pages; no auth tokens needed
- **Polite crawl:** 1s delay between fetches

## Output files

| File | Content | Purpose |
|---|---|---|
| `_<client>-similarity-audit.yaml` | Full machine-readable results | Downstream tooling, programmatic queries |
| `<client>-duplicate-content-map.md` | Human-readable audit report | Operator review, decision-making, same shape as ev-duplicate-content-map |

## Adding a new client

1. Copy an existing `.audit.json` config
2. Update: domain, sitemap_url, wp_rest_base, strip_tokens, grouping_rules, output_dir
3. Run with `--dry-run` first to verify grouping
4. Run full audit

Zero engine edits required (B4 2nd-instance criterion).

## Skill candidacy (B6)

**Verdict: YES** — register as `sitewide-similarity-audit` skill after both S&H and EV proof runs pass. The engine is fully config-driven, zero-hardcoded, and proven on 2 clients with a single command.
