# `scaffold-city-data.py` — Phase 3b city scaffolder

Reads a Phase 2b city research brief plus zero or more Phase 2c
service-by-city intersection briefs and produces a populated
`data/cities/<slug>.json` data file that `scaffold-core-30-page.py`
consumes when rendering a Core 30 page.

## What this script does

The city JSON has a dual structure:

- **Top-level city-only fields** come from the Phase 2b city brief:
  `slug`, `name`, `name_with_state`, `state`, `state_full`, `county`,
  `county_full`, `geographic_anchor_paragraph`, `audience_descriptor`,
  `no_trip_charge_cities`, `neighborhoods`, `other_areas_paragraph`,
  `housing_patterns` (each pattern's `title`, `neighborhood_examples`,
  `context_paragraph`), `ev_charger_homes_phrase`.
- **Service-keyed nested dicts** come from Phase 2c intersection briefs,
  one entry per service slug: `quick_ref_localized_items[<service>]`,
  `most_common_problem_paragraph[<service>]`,
  `specific_problems_neighborhood_phrase[<service>]`, plus the per-pattern
  `housing_patterns[].symptoms` voicing (one service-voicing per pattern,
  not service-keyed in the JSON — see "Symptoms voicing" below).
- **`distance_from_hq_phrase`** is client-scoped — it depends on the HQ
  city of the client whose page consumes this JSON. When `--client-slug`
  is passed, the script reads `data/client-<slug>.json` and composes the
  phrase from the city brief's drive-time table. Otherwise the field is
  left as TBD for Phase 3c.

The script is **non-destructive** and **re-runnable**. Existing
service-keyed entries are preserved across re-runs unless you pass
`--overwrite`.

## Usage

First run for a new city, no intersection briefs yet (service-keyed dicts
will be empty — fine, they get added when intersection briefs land):

```bash
python3 scaffold-city-data.py \
    --city-brief ~/workspace/second-brain/05_shared-intelligence/research-briefs/cities/vienna-va.md \
    --output-slug vienna-va
```

With a single intersection brief:

```bash
python3 scaffold-city-data.py \
    --city-brief .../cities/vienna-va.md \
    --intersection-briefs panel-upgrade \
    --output-slug vienna-va \
    --client-slug ev-electric-services
```

With multiple intersection briefs (comma-separated service slugs):

```bash
python3 scaffold-city-data.py \
    --city-brief .../cities/vienna-va.md \
    --intersection-briefs panel-upgrade,troubleshooting,ev-charger \
    --output-slug vienna-va \
    --client-slug ev-electric-services
```

Re-run later when a new intersection brief lands. Existing services are
preserved — only the new one is folded in:

```bash
python3 scaffold-city-data.py \
    --city-brief .../cities/vienna-va.md \
    --intersection-briefs whole-house-rewire \
    --output-slug vienna-va \
    --client-slug ev-electric-services
```

Dry-run to preview the JSON without writing:

```bash
python3 scaffold-city-data.py \
    --city-brief .../cities/vienna-va.md \
    --intersection-briefs panel-upgrade \
    --output-slug vienna-va \
    --dry-run
```

Pass full paths for intersection briefs when the conventional location
doesn't apply:

```bash
python3 scaffold-city-data.py \
    --city-brief .../cities/vienna-va.md \
    --intersection-briefs /tmp/staging-brief.md,panel-upgrade \
    --output-slug vienna-va
```

## CLI reference

| Flag | Required | Description |
|---|---|---|
| `--city-brief` | yes | Path to the Phase 2b city brief markdown file. |
| `--output-slug` | yes | City slug for the output JSON filename: `data/cities/<output-slug>.json`. |
| `--intersection-briefs` | no | Comma-separated list. Each entry is either a service slug (resolved via convention: `<city-brief-parent>/../intersections/<service>--<city-slug>.md`) or a full path to a `.md` file. |
| `--client-slug` | no | If `data/client-<slug>.json` exists, the script uses its `address.locality` to resolve `distance_from_hq_phrase` from the city brief §9 drive-time table. |
| `--symptoms-from` | no | Service slug whose intersection brief fills `housing_patterns[].symptoms`. Defaults to the first brief in `--intersection-briefs`. |
| `--overwrite` | no | Drop the existing JSON entirely before writing. Default behavior is to merge (preserving service-keyed entries for services not in this run). |
| `--dry-run` | no | Render and validate but don't write the file. Prints the produced JSON to stdout. |
| `--output-folder` | no | Override the output directory (default: `data/cities/`). |

## How the brief feeds the JSON

### City brief sections → top-level fields

| JSON field | Source |
|---|---|
| `slug`, `name`, `name_with_state`, `state`, `state_full`, `county`, `county_full` | §1 fields table (with frontmatter fallback) |
| `geographic_anchor_paragraph` | §2 blockquote labeled "Geographic anchor paragraph (ready for JSON)" |
| `neighborhoods` | §3 table, name + blurb columns |
| `audience_descriptor` | §4 blockquote labeled "Audience descriptor (ready for JSON)" |
| `housing_patterns[].title`, `.neighborhood_examples`, `.context_paragraph` | §5 each "### Pattern N:" subsection's field/value table |
| `housing_patterns[].symptoms` | LEFT AS `<TBD by intersection brief>` here; filled by Phase 2c |
| `no_trip_charge_cities` | §9 fenced code block after "Default `no_trip_charge_cities`" |
| `other_areas_paragraph` | §9 blockquote labeled "Other-areas paragraph (ready for JSON)". Wikilinks like `[[fairfax-va|Fairfax]]` are converted to HTML anchors. |
| `distance_from_hq_phrase` | §9 drive-time row whose first column matches the client's HQ locality (only when `--client-slug` is passed) |

### Intersection brief sections → service-keyed fields

The primary data source is the brief's "How the scaffolder consumes this
brief" section table at the bottom. It spells out literal proposed content
for each JSON field with the service slug bracketed in the row label.

| JSON field | Source |
|---|---|
| `most_common_problem_paragraph[<service>]` | Consumption table row, quoted string |
| `specific_problems_neighborhood_phrase[<service>]` | Consumption table row, quoted string |
| `ev_charger_homes_phrase` (shared, electrical-domain) | Consumption table row, longest quoted phrase |
| `housing_patterns[].symptoms` | Consumption table row, one `"..."` per pattern in document order |
| `quick_ref_localized_items[<service>]` | §4d distilled-questions table. Question column becomes `summary`; the body is emitted as a `<!-- TBD: ... -->` HTML comment with the brief's voicing notes so the operator authors the final body before publish. No invented facts. |

## Symptoms voicing — the tricky bit

The consumer `scaffold-core-30-page.py` reads `housing_patterns[].symptoms`
as a single string per pattern (`c['symptoms']`), not as a dict keyed by
service. That's the existing JSON shape.

But the intersection brief proposes per-service symptoms voicing — and
multiple services can populate the same JSON. The chosen design:

- The first intersection brief in `--intersection-briefs` is the "primary"
  service for symptoms voicing. Its `housing_patterns[].symptoms` content
  wins.
- Pass `--symptoms-from <service-slug>` to override.
- On re-run with no intersection brief, the previously written symptoms
  are preserved (the merge tolerates the placeholder coming back from the
  city brief and keeps the real string from the on-disk JSON).
- When the consumer renders for a service whose voicing isn't the symptoms
  primary, the pattern card still shows real symptoms text — it just
  reflects the primary service's lens. That's the same trade the existing
  `vienna-va.json` makes (its `symptoms` is troubleshooting-voiced and
  works for both troubleshooting and panel-upgrade pages, just with
  troubleshooting-flavored language).

If you want true per-service symptoms voicing in the rendered page, the
consumer's `render_pattern_cards()` would need a service-keyed lookup —
out of scope for Phase 3b.

## Validation

After assembly the script checks:

- All required top-level fields present with correct types
- `neighborhoods[]` entries have `name` and `blurb`
- `housing_patterns[]` entries have all four sub-fields
- `quick_ref_localized_items[<service>]` entries have `summary` and `body`

Validation errors block the write. Warnings (e.g. `TBD` still present in a
field) are printed but don't block — the file is still written so the
operator can iterate.

## Output shape

The produced JSON keys follow a stable order matching the reference
`data/cities/vienna-va.json`. Any keys not in the canonical order (operator
additions) are appended at the end and preserved across re-runs.

## Test against the Vienna + panel-upgrade case

```bash
python3 scaffold-city-data.py \
    --city-brief ~/workspace/second-brain/05_shared-intelligence/research-briefs/cities/vienna-va.md \
    --intersection-briefs panel-upgrade \
    --output-slug vienna-va-test \
    --client-slug ev-electric-services
```

Produces `data/cities/vienna-va-test.json` with the same top-level shape
as the reference `vienna-va.json`. The service-keyed dicts hold
`panel-upgrade` entries instead of `troubleshooting`. The `housing_patterns[].symptoms`
fields hold panel-upgrade-voiced symptom strings extracted from the
intersection brief's consumption table.

## Related

- Phase 2b template: `~/workspace/second-brain/05_shared-intelligence/research-briefs/_template-city-brief.md`
- Phase 2c template: `~/workspace/second-brain/05_shared-intelligence/research-briefs/_template-intersection-brief.md`
- Blueprint (Phase 3b section): `~/workspace/second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md`
- Reference JSON shape: `data/cities/vienna-va.json`
- Consumer: `scaffold-core-30-page.py` (see `build_context`, `render_quick_ref_items`, `render_pattern_cards`)
- Sibling scaffolder (style/pattern source): `scaffold-client-data.py`
