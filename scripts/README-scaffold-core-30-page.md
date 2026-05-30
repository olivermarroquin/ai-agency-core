# scaffold-core-30-page.py

Generates a finished Core 30 page draft from data files. Replaces the chat-side authoring of `draft-vN-WP-WRAPPED.html` for any new (service, city) page after page 1's design has been locked.

**Sibling scripts in the same toolkit:**

- [`generate-maps-iframe.py`](README-generate-maps-iframe.md) — called automatically for Section 8. The scaffolder uses whichever map is cached for the city (or fetches fresh if absent).
- [`publish-core-30-page.py`](README-publish-core-30-page.md) — runs downstream. Takes the draft this script produces and pushes it to WordPress.

## What it does

1. Loads three data files: client (brand facts), service (pricing, problem cards, FAQs), city (neighborhoods, housing patterns).
2. Substitutes them into the v17 HTML template and the markdown source template.
3. Calls `generate-maps-iframe.py` for the Section 8 map iframe.
4. Builds the full JSON-LD `@graph` (LocalBusiness + Service + FAQPage) from the data.
5. Writes `draft-v1.md`, `draft-v1-WP-WRAPPED.html`, and `_VERSION-LOG.md` into a fresh folder under `04_projects/clients/_active/<client>/website-archive/new/core-30/`.

## What it does NOT do

- **Doesn't overwrite an existing folder.** If `<NN>-<slug>/` already has files, the script refuses to write. Pass `--output-folder` to a fresh path if you need to regenerate.
- **Doesn't publish.** That's `publish-core-30-page.py`'s job. The scaffolder hands off paste-ready files.
- **Doesn't iterate on visual design.** Page 1 is still the design-iteration page. Once locked there, the scaffolder reproduces the locked design for every subsequent page.

## Setup

Same as `generate-maps-iframe.py`:

1. Python 3.8+ (stdlib only — no `pip install`).
2. `GOOGLE_MAPS_EMBED_API_KEY` exported from your tier-3 vault.
3. The maps script's cache file at `cache/maps-iframes.json` — gets populated automatically on first use per city.

That's it. The scaffolder pulls everything else from local data files.

## Usage

### Scaffold a new page

```bash
export GOOGLE_MAPS_EMBED_API_KEY="AIzaSyD…"
cd ~/workspace/repos/ai-agency-core/scripts
python3 scaffold-core-30-page.py \
    --service troubleshooting \
    --city    mclean-va \
    --position 7
```

Reports the substitutions on stderr, writes three files into:

```
~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/07-electrical-troubleshooting-mclean-va/
  ├── draft-v1.md
  ├── draft-v1-WP-WRAPPED.html
  └── _VERSION-LOG.md
```

Then review the draft, optionally polish city-specific phrasing in the hero subheading or About section, and publish via `publish-core-30-page.py`.

### Dry run (verify substitutions without writing)

```bash
python3 scaffold-core-30-page.py \
    --service troubleshooting \
    --city    mclean-va \
    --position 7 \
    --dry-run
```

Prints the rendered HTML and markdown sizes, plus the substitution context. No files touched.

### Different client

```bash
python3 scaffold-core-30-page.py \
    --service plumbing-repair \
    --city    fairfax-va \
    --client  sh-contracting \
    --position 1
```

Default client is `ev-electric-services`. Override with `--client <slug>`. Requires `data/client-<slug>.json` to exist.

## Data model

Three JSON files combine for any one page:

```
data/
├── client-ev-electric-services.json   # one per client
├── services/
│   ├── troubleshooting.json           # one per service
│   ├── panel-upgrade.json
│   ├── ev-charger.json
│   └── …
└── cities/
    ├── vienna-va.json                 # one per city
    ├── fairfax-va.json
    ├── mclean-va.json
    └── …
```

### Client file (brand-level facts)

`data/client-<slug>.json` holds the things that are identical across every page for one client: name, address, phone, owner, license, review count, brand colors, hours.

When you onboard a new Keelworks client, copy `client-ev-electric-services.json` and edit the values. Most fields are obvious; the brand colors map to CSS variables in the page template.

### Service file (one per service, reused across cities)

`data/services/<slug>.json` holds everything specific to one service across all cities: pricing, problem cards, process steps, FAQs, the "what this service actually means" intro.

Strings in service files use placeholders like `{city_name}`, `{city_slug}`, `{owner_first_name}`, `{phone_display}`. These get filled in by the scaffolder at render time. So a single `troubleshooting.json` produces correct copy for Vienna, Fairfax, McLean, Oakton, and every other city that gets a troubleshooting page.

The placeholders are documented in the service file's `_comment` field. Key ones:

| Placeholder | Substituted from |
|---|---|
| `{city_name}` | city `name` (e.g. "Vienna") |
| `{city_name_with_state}` | city `name_with_state` (e.g. "Vienna, VA") |
| `{city_slug}` | city `slug` (e.g. "vienna-va") |
| `{owner_name}` | client `owner_name` |
| `{owner_first_name}` | client `owner_first_name` |
| `{phone_display}` | client `phone_display` |
| `{phone_tel}` | client `phone_tel` |
| `{review_count_phrase}` | client `review_count_phrase` |
| `{city_distance_phrase}` | city `distance_from_hq_phrase` |
| `{city_ev_homes_phrase}` | city `ev_charger_homes_phrase` |
| `{city_most_common_problem_paragraph}` | city `most_common_problem_paragraph[service_slug]` |

### City file (one per city, reused across services)

`data/cities/<slug>.json` holds everything specific to one city: neighborhoods list, housing-stock 3-pattern data, geographic anchor paragraph, audience descriptor, the city-and-service-specific Quick Reference Q&As.

Some fields are nested by service (e.g. `quick_ref_localized_items`, `most_common_problem_paragraph`, `specific_problems_neighborhood_phrase`). This is because the Quick Reference Q&As for *troubleshooting in Vienna* are different from *panel upgrade in Vienna* — same city, different service. When you add a new service, you'll add a new entry under each existing city file for that service's localized quick-ref + problem phrasing.

## Adding a new city

1. Copy `data/cities/vienna-va.json` → `data/cities/<new-slug>.json`.
2. Update: `slug`, `name`, `name_with_state`, `county` (if different), `distance_from_hq_phrase`, `geographic_anchor_paragraph`, `audience_descriptor`, `no_trip_charge_cities`, `neighborhoods`, `housing_patterns`, `other_areas_paragraph`.
3. For each service the city will have a page for, add an entry under `quick_ref_localized_items`, `most_common_problem_paragraph`, and `specific_problems_neighborhood_phrase`. (Or leave them missing — the scaffolder will report MISSING with a clear instruction.)
4. Run `scaffold-core-30-page.py --service <s> --city <new-slug> --position <N>`.

## Adding a new service

1. Copy `data/services/troubleshooting.json` → `data/services/<new-slug>.json`.
2. Update everything inside — pricing, problem cards, process steps, FAQs, schema description, AIOSEO templates.
3. For each existing city file, add the corresponding service entry under `quick_ref_localized_items`, `most_common_problem_paragraph`, and `specific_problems_neighborhood_phrase`. (City files have a per-service sub-dict for these — when you add a new service, you extend each city.)
4. Run `scaffold-core-30-page.py --service <new-slug> --city <c> --position <N>`.

## Adding a new client

1. Copy `data/client-ev-electric-services.json` → `data/client-<new-slug>.json`.
2. Update brand name, address, phone, owner, license, review count, brand colors, hours, contact-page path.
3. The same `services/*.json` and `cities/*.json` files are reusable across clients IF the clients share services and territories. Realistically, each client gets their own service files because pricing and problem phrasing differ. Cities can sometimes be shared (e.g. two Fairfax-area contractors share `vienna-va.json` if their service-area framing is similar).

## Validation

The scaffolder writes a paste-ready page but doesn't validate every aspect. Before publishing, eyeball:

- Hero subheading reads naturally for this city (auto-generated text is generic — "Vienna and surrounding areas" — and may need a hand-tuned list of neighborhoods).
- Pricing section matches the service's actual published rates.
- Internal links in Section 9 point at slugs that exist or will exist soon.
- About-section paragraph 2 names the right service in lowercase form (e.g. "most residential troubleshooting calls" vs. "most residential panel upgrade calls" — the service file's `lowercase_phrase` controls this).

Then run a JSON-LD validation:

```bash
python3 -c "
import re, json
with open('<page-folder>/draft-v1-WP-WRAPPED.html') as f:
    s = f.read()
m = re.search(r'<script type=\"application/ld\+json\">\s*(.*?)\s*</script>', s, re.DOTALL)
data = json.loads(m.group(1))
print(f'{len(data[\"@graph\"])} entities:')
for x in data['@graph']:
    print(f'  - {x[\"@type\"]}')
"
```

Expected: 3 entities (LocalBusiness, Service, FAQPage). The publish script also validates this before posting.

## Common errors

### `ERROR: data file not found: data/services/<slug>.json`

You asked for a service that doesn't have a data file yet. Either copy `troubleshooting.json` and fill in for the new service (see "Adding a new service" above), or fix the typo in `--service`.

### `ERROR: data file not found: data/cities/<slug>.json`

Same as above, for cities.

### `KeyError: '<placeholder>'` from `format_map`

A service or city data file references a placeholder the scaffolder hasn't put in the context dict. Either the data file is using a placeholder that doesn't exist, or the scaffolder needs to add it. Look at `build_context()` in the script for the full set of available substitutions.

### `MISSING: city data file has no quick_ref_localized_items for service 'X'`

The HTML renders fine but the Section 2 Quick Reference sidebar will have an HTML comment instead of 6 Q&As. Edit the city file to add a `quick_ref_localized_items.<service>` array.

### `WARNING: folder already has contents`

The target folder isn't empty. Either pass `--output-folder` to a fresh path, or `rm -rf` the existing folder first. The scaffolder refuses to overwrite because regenerating a page you've already polished is a footgun.

## Comparison to manual chat-authoring

| | Chat-authoring | Scaffolder |
|---|---|---|
| Time per page | 25–40 min | ~30 sec script + 5–10 min city-specific polish |
| Mechanical consistency | Drift across pages | Locked design |
| Schema correctness | Easy to miss a field | Generated from data, always complete |
| Quality of city-specific prose | High (chat-authored) | Generic by default — needs hand-tuning for best results |
| Best when | Page 1 of a new client (design still iterating) | Pages 2+ on an established client |

The scaffolder is the right path once page 1's design is locked. Page 1 still gets chat-authored so the design system can iterate.

## Future enhancements

- **Marker substitution in HTML drafts.** Add `<!-- TODO:CITY-SPECIFIC-HERO -->` markers in templates that the operator fills in with city-tuned phrases. Could combine with a follow-up Claude pass that fills the markers based on city context.
- **City-tuned hero subheading.** Add an optional `hero_subheading_override` field per city per service so the hero gets a hand-tuned phrase like "Residential service across Vienna, Tysons-edge, Hunter Mill, and Wolf Trap" rather than the generic "across Vienna and surrounding areas."
- **Auto-version-bump on regenerate.** Today the script refuses to overwrite. A future flag could write to `draft-v2-WP-WRAPPED.html` and append to `_VERSION-LOG.md` instead.
- **Bulk mode.** Read `_build-order.md` and scaffold every "pending" row in one run.

## Related

- Pattern: [`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md)
- SOP: [`sop-core-30-page-build.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-core-30-page-build.md)
- Sibling scripts: [`generate-maps-iframe.py`](README-generate-maps-iframe.md), [`publish-core-30-page.py`](README-publish-core-30-page.md)
- Canonical reference HTML (the design source-of-truth): `04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/01-electrical-troubleshooting-vienna-va/draft-v17-WP-WRAPPED.html`
