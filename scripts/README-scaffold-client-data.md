# scaffold-client-data.py

Phase 3c of the client-SEO-onboarding automation roadmap. Reads a Phase 2d
client-fact brief and produces every per-client artifact the Core 30 pipeline
needs to start scaffolding pages.

## What it produces

Run once per new client and you get:

1. **`data/client-<slug>.json`** — the data file `scaffold-core-30-page.py`
   reads for brand, owner, address, contact, review, area, and license fields.
   Same shape as the existing `data/client-ev-electric-services.json`.

2. **`<client-slug>.config.example.json`** — the WP-publish config skeleton with
   `wp_base_url` + `client_slug` pre-filled and `wp_username` blank for the
   operator to fill in. Same shape as `ev-electric.config.example.json`.

3. **Tier-3 credentials template** (markdown) — one section per credential the
   pipeline needs (WP app password, GBP manager, GSC owner, GA4 admin, Imagify
   license, social accounts, etc.). Written to `--tier3-template-out` if you
   pass that flag; otherwise printed to stdout so you can copy it into the
   air-gapped vault by hand.

4. **stdout checklist** — a numbered list Oliver can paste into an email to
   the client or into a working doc. Plus a "fields that still need
   confirmation" list pulled from the `_scaffolded.needs_confirmation` array
   inside the JSON, so nothing TBD goes unflagged.

5. **Optional `--test-wp-auth`** — given a config file and a WordPress
   application password (env var or tier-3 markdown lookup), hits
   `GET /wp-json/wp/v2/users/me` and reports whether auth works, what role the
   user has, and whether they can upload to the Media Library, edit pages, and
   publish.

## Inputs

| Flag | Required? | Purpose |
|---|---|---|
| `--brief` | Yes (except in `--test-wp-auth` standalone mode) | Path to the Phase 2d client-fact brief markdown file. |
| `--client-slug` | Always | kebab-case slug. Must match `data/client-<slug>.json` and the `04_projects` folder name. |
| `--meeting-notes` | No | Path to meeting-notes markdown. v1 doesn't auto-parse notes — the brief is the contract. The flag is reserved for future use. |
| `--output-dir` | No | Where to write `data/client-<slug>.json` and `<slug>.config.example.json`. Defaults to the script's own directory (`repos/ai-agency-core/scripts/`). |
| `--tier3-template-out` | No | If set, write the credentials template directly here. Typical value: `~/workspace/second-brain-tier3/clients/<slug>/credentials.md`. If omitted, the template prints to stdout instead — the operator copies it into the vault manually. |
| `--overwrite` | No | Replace existing data/config/tier-3 files instead of writing to a `.scaffolded.json` sibling for diff review. Off by default — non-destructive. |
| `--test-wp-auth` | No | After scaffolding, hit `GET /wp-json/wp/v2/users/me` to confirm the WP REST API credentials work. Or use standalone with `--config` to test an existing client's config. |
| `--config` | Only with `--test-wp-auth` standalone | Path to an existing client config JSON. |

## Examples

### Scaffold a fresh client

```bash
python scaffold-client-data.py \
    --brief ~/workspace/second-brain/05_shared-intelligence/research-briefs/clients/s-and-h-contracting/brief.md \
    --client-slug s-and-h-contracting
```

Output (abbreviated):

```
→ Wrote data file:           .../data/client-s-and-h-contracting.json
→ Wrote config example:       .../s-and-h-contracting.config.example.json

Tier-3 template (paste into ~/workspace/second-brain-tier3/clients/s-and-h-contracting/credentials.md):
----------------------------------------------------------------------
# Credentials — S&H Contracting Unlimited
...

======================================================================
CREDENTIALS CHECKLIST — s-and-h-contracting
======================================================================

[ ]  1. WordPress admin login ...
[ ]  2. WordPress Application Password ...
...

FIELDS IN data/client-<slug>.json THAT STILL NEED CONFIRMATION
======================================================================
  • address.street
  • hours
  • license.category
  ...
```

### Scaffold AND write the tier-3 template directly

```bash
python scaffold-client-data.py \
    --brief .../brief.md \
    --client-slug s-and-h-contracting \
    --tier3-template-out ~/workspace/second-brain-tier3/clients/s-and-h-contracting/credentials.md
```

The script always writes the file the operator points it at — it doesn't try
to read the tier-3 vault back. (Per the standing convention, Cowork doesn't
read tier-3.)

### Regenerate an existing client (sanity check)

```bash
python scaffold-client-data.py \
    --brief .../ev-electric-services/brief.md \
    --client-slug ev-electric-services \
    --output-dir /tmp/regen-test
```

Compare against the production file:

```bash
diff repos/ai-agency-core/scripts/data/client-ev-electric-services.json \
     /tmp/regen-test/data/client-ev-electric-services.json
```

Or use the built-in safety net — if `data/client-<slug>.json` already exists,
the script writes `data/client-<slug>.scaffolded.json` next to it and prints
the diff command. Pass `--overwrite` only when you've reviewed the diff.

### Test WP REST API connectivity standalone

```bash
export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
python scaffold-client-data.py \
    --client-slug ev-electric-services \
    --test-wp-auth \
    --config ev-electric.config.json
```

Output on success:

```
→ GET https://evelectric.pro/wp-json/wp/v2/users/me
✓ Auth succeeded.
  user_id:  2
  name:     Oliver
  roles:    ['administrator']
  upload_files (media library):  True
  edit_pages:                    True
  publish_pages:                 True
✓ User has full Core-30 publishing capability.
```

Output on a bad password:

```
→ GET https://evelectric.pro/wp-json/wp/v2/users/me
AUTH FAILED (401). Check that:
  - The application password was generated for user 'oliver'
  - The user has Editor or Administrator role
  - WP REST API isn't blocked by a security plugin (Wordfence, AIOS)
  - The wp_base_url 'https://evelectric.pro' is correct (try with/without www)

Response body:
{"code":"rest_not_logged_in","message":"You are not currently logged in.","data":{"status":401}}
```

Exit codes:
- `0` — auth succeeded (capability gap is a warning, not a hard failure)
- `1` — auth failed (401/403 or unexpected response)
- `2` — missing credentials or network error

### Scaffold AND test the WP auth in one pass

```bash
python scaffold-client-data.py \
    --brief .../brief.md \
    --client-slug s-and-h-contracting \
    --test-wp-auth
```

The script uses the freshly scaffolded config. If `wp_username` is still
empty (it always is right after scaffolding — the operator fills it in),
the auth test is skipped with an actionable prompt:

```
→ --test-wp-auth skipped: wp_username is empty in the config. Fill it in
  and rerun:
  python scaffold-client-data.py --client-slug s-and-h-contracting \
      --test-wp-auth --config .../s-and-h-contracting.config.example.json
```

## How field extraction works

The brief is the source of truth. The script walks the numbered sections
(§1-§8) defined in `_template-client-brief.md` and pulls field values out of
the two-column markdown tables each section opens with.

| Brief section | JSON fields fed |
|---|---|
| §1 Business identity | `client_slug`, `name`, `alternate_name`, `owner_name`, `owner_first_name`, `owner_title`, `business_description_schema` |
| §2 Brand surface | `website_url`, `website_url_no_slash`, `brand_logo_url`, `brand_image_url`, `primary_color`, `navy`, `accent_yellow`, `heading_color`, `hero_gradient_dark/mid/bright`, `price_range_token` |
| §3 Contact surface | `phone_display/tel/e164`, `email`, `address.*`, `geo.*`, `hours`, `contact_page_path`, `primary_cta_label_template`, `secondary_cta_label`, `final_cta_response_promise` |
| §6 Reviews | `review_count`, `review_rating`, `review_count_phrase`, `review_pitch` |
| §7 Service area | `brand_areas_served`, `brand_area_county`, `brand_extended_coverage_phrase` |
| §8 Licensing | `license.category`, `license.issuer`, `license.state`, `license.state_full` |

`owner_bio_paragraphs` is intentionally left empty — generating prose
mechanically from facts produces wooden results. The needs_confirmation
flag marks it for the operator/copywriter.

### Missing-value handling

Briefs flag TBD facts with markers like "TBD", "NOT captured", "(pending
verification)", "⚠️ NOT YET CAPTURED". The scaffolder treats any value
whose leading text matches these markers as missing, emits `null` in the
JSON, and appends the field name to `_scaffolded.needs_confirmation` at the
top of the JSON. The same list prints to stdout at the end of the run so
nothing slips through.

Detection is intentionally conservative: a passing mention of "pending"
deep inside a long caveat does NOT trigger missing-marking, because the
brief often pairs a real value with a caveat (e.g. an existing photo plus
"pending the new uniform shot"). Only leading markers count.

### Non-destructive default

If `data/client-<slug>.json` already exists, the script writes
`data/client-<slug>.scaffolded.json` instead and prints a diff command.
This matches the standing rule for the second-brain vault — don't overwrite
existing material without operator review. Pass `--overwrite` to replace.

## Credentials checklist coverage

The 14 checklist items the script surfaces (and writes into the tier-3
template):

1. WordPress admin login — for a Keelworks-owned admin user
2. WordPress Application Password — for `publish-core-30-page.py`
3. Hosting control panel — Hostinger / WP Engine / etc.
4. Domain registrar
5. DNS provider
6. Google Business Profile manager access
7. Google Search Console Owner access
8. GA4 (Analytics) Administrator access
9. Imagify license key
10. Business email account
11. HireNimbus / review-platform login (if used)
12. Bing Webmaster Tools owner access
13. Thumbtack / lead-aggregator logins (if applicable)
14. Facebook / Instagram / LinkedIn business accounts

Items the operator can add by hand in the tier-3 file as they surface
during onboarding. The script just gives a defensible default set.

## Auth pattern (matches publish-core-30-page.py)

The WP REST API check uses Basic Auth with a base64-encoded
`username:app_password` header — the same pattern `publish-core-30-page.py`
already uses for `POST /wp-json/wp/v2/pages`. The application password is
resolved via `_load_secrets.load_wp_app_password()`, which checks the
`WP_APP_PASSWORD` env var first, then falls back to the tier-3 markdown
file at `~/workspace/second-brain-tier3/personal/business-keelworks.md`.

If neither lookup yields, the script prints an actionable error. It never
prompts for or logs the password value.

## Limitations and out-of-scope

- **No meeting-notes parsing.** v1 takes the brief as the contract. If the
  notes contain new facts not in the brief, update the brief first.
- **No client research.** That's Phase 2d. This script consumes the Phase
  2d output.
- **No credential collection.** This script surfaces WHAT credentials are
  needed; gathering them is a human step.
- **No automated WordPress configuration.** Setting up the AIOSEO Bridge
  plugin, adding the Keelworks admin user, granting GBP manager access —
  all separate operator steps with their own SOPs.
- **WordPress + Elementor only.** Custom-coded Next.js clients (post the
  2026-05-23 pivot) need a separate publishing module. The config schema
  is WP-shaped today.

## Related files

- `_template-client-brief.md` — brief template (Phase 2d output contract)
- `data/client-ev-electric-services.json` — reference JSON shape
- `publish-core-30-page.py` — downstream consumer of `data/client-<slug>.json`
- `_load_secrets.py` — WP password loader (env → tier-3 fallback)
- `ev-electric.config.example.json` — config-file shape this scaffolds
- `client-seo-onboarding-automation.md` blueprint — Phase 3c spec
