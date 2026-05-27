# scaffold-service-data.py

Builds `data/services/<slug>.json` from a Tier-1 service brief (Phase 2a output). The resulting JSON is what `scaffold-core-30-page.py` reads when it renders Core 30 pages for the service across cities.

**Sibling scripts in the same Phase 3 toolkit:**

- [`scaffold-client-data.py`](README-scaffold-client-data.md) — Phase 3c. Produces `data/client-<slug>.json` from a Phase 2d client-fact brief.
- `scaffold-core-30-page.py` — the downstream consumer. Reads the service JSON produced here plus a client + city JSON, renders a Core 30 page.

## What it does

1. Parses a Tier-1 service brief (markdown, in the shape defined by `_template-service-brief.md`).
2. Extracts the mechanical pieces — identity table, head keyword, long-tail keyword list, FAQ questions, related-cards block, problem cards table, process steps table, pricing items block, schema recommendations.
3. Fills every top-level key the consumer expects. Fields the brief explicitly authors get the brief's prose verbatim. Fields the brief leaves as structural guidance (hero subheading, what-it-means paragraphs, FAQ answers, about paragraphs) get a `FILL:` placeholder string that quotes the brief section the operator should consult.
4. Validates the produced JSON against the template (default `troubleshooting.json`) — refuses to write if a mandatory identity or schema field is missing.
5. Writes to `data/services/<slug>.json` (or to `<slug>.scaffolded.json` if a file already exists and `--overwrite` was not passed).

## What it does NOT do

- **Doesn't author prose.** The brief specifies what the prose should say, not the prose itself. Hero subheadings, what-it-means paragraphs, FAQ answers, about paragraphs, and final CTAs are emitted as `FILL:` placeholders with hints. The operator (or a follow-on prose-writer step) fills them.
- **Doesn't invent facts.** If a section the brief should provide is missing, the field is flagged in `_scaffolded.needs_confirmation` rather than guessed.
- **Doesn't touch city or client data files.** Adding a new service to an existing city requires editing each city file's `quick_ref_localized_items`, `most_common_problem_paragraph`, and `specific_problems_neighborhood_phrase` dicts — that's a manual step (or a future scaffold script).
- **Doesn't overwrite by default.** Pre-existing data files get a `.scaffolded.json` sibling so the operator can `diff` before replacing.

## Setup

Pure stdlib — no `pip install`. Python 3.8+.

## Usage

### Scaffold from a brief

```bash
cd ~/workspace/repos/ai-agency-core/scripts
python3 scaffold-service-data.py \
    --brief        ~/workspace/second-brain/05_shared-intelligence/research-briefs/services/panel-upgrade.md \
    --output-slug  panel-upgrade
```

Writes `data/services/panel-upgrade.json` and prints a summary to stdout with two lists:

- **needs_authoring** — fields populated with a `FILL:` placeholder. The operator needs to write the prose by hand.
- **needs_confirmation** — fields the script tried to extract but couldn't find in the brief. Should be confirmed against the brief or flagged as a gap.

### Dry run (no files written)

```bash
python3 scaffold-service-data.py \
    --brief        .../panel-upgrade.md \
    --output-slug  panel-upgrade \
    --dry-run
```

Useful for previewing the extraction before committing to a file.

### Custom template

By default the script validates the produced JSON against `data/services/troubleshooting.json`. To validate against a different template:

```bash
python3 scaffold-service-data.py \
    --brief        .../panel-upgrade.md \
    --output-slug  panel-upgrade \
    --template     /path/to/some-other-service.json
```

### Overwrite an existing data file

```bash
python3 scaffold-service-data.py \
    --brief        .../panel-upgrade.md \
    --output-slug  panel-upgrade \
    --overwrite
```

Without `--overwrite`, the script writes to `panel-upgrade.scaffolded.json` alongside the existing `panel-upgrade.json` so you can diff and merge.

### Force-write despite missing mandatory fields

Mandatory fields are the identity and schema strings the consumer absolutely needs: `slug`, `name`, `name_with_city`, `service_type_phrase`, `quoted_phrase`, `lowercase_phrase`, `tag_short`, `page_slug_template`, `schema_service_description_template`, `schema_service_terms`. If any are missing or stuck on a `FILL:` placeholder, the script refuses to write and lists what's missing. Pass `--force` to write anyway:

```bash
python3 scaffold-service-data.py \
    --brief        .../incomplete-brief.md \
    --output-slug  some-service \
    --force
```

## Brief → JSON field mapping

Every JSON top-level field traces to a brief section per the consumption-contract table at the bottom of `_template-service-brief.md`. The most load-bearing mappings:

| JSON field | Brief section | How |
|---|---|---|
| `slug`, `name`, `name_with_city`, `service_type_phrase`, `quoted_phrase`, `lowercase_phrase`, `tag_short`, `page_slug_template`, `wordpress_page_title_template` | §1 identity table | Direct extract from the 2-column `Field` / `Recommended value` table |
| `aioseo_focus_keyword_template` | §3a head keyword | First row of the §3a head keyword table; `{city}` normalized to `{city_slug}` |
| `aioseo_additional_keywords_template` | §3b long-tail bullets | Bullets parsed, `[city]` normalized to `{city_slug}` |
| `problem_cards` | §10a problem cards table | Every row of the `\| Title \| Body \|` table |
| `process_steps` | §10b process steps table | Every row of the `\| Title \| Body \|` table |
| `related_cards` | §9 related_cards code block | Parsed for `slug — ..., label "Label"` lines |
| `pricing_heading`, `pricing_items`, `pricing_closing_note` | §11 recommendations | Extracted from `Recommended \`pricing_*\` ...:` callouts |
| `schema_service_description_template`, `schema_service_terms` | §5 recommendations | Extracted from `Recommended \`schema_*\` value: "..."` callouts |
| `faq_items[].question` | §4d numbered list | The 8 questions; `{city}` normalized to `{city_name_with_state}` |
| `faq_items[].answer_html`, `answer_schema` | §7 hint notes only | `FILL:` placeholder with the §7 row's Notes column quoted as a hint — the brief carries hints, not authored prose |
| `what_it_means_paragraphs`, `about_text_paragraphs`, `hero_subheading_template`, `hero_heading`, `final_cta_*`, `pricing_intro_template`, `why_city_closing_note_template`, `quick_ref_intro`, `related_intro_template`, `aioseo_meta_description_template` | §6 tone hints, §12 trust signals | `FILL:` placeholder with brief-section hint — the brief tells you the shape and tone, not the words |

Anything left over after the brief is consumed is either a static template string the consumer uses across services (e.g. `quick_ref_footer_html_template`, `neighborhoods_heading_template`, `about_heading_template`) or a derived template built from extracted identity fields (e.g. `hero_eyebrow_template`, `quick_ref_heading_template`).

## After the scaffold

A scaffolded service JSON is rarely shippable as-is. The expected workflow:

1. **Run the scaffolder.** Get the JSON + needs_authoring list.
2. **Open the data file.** Grep for `FILL:` — every match is a string the operator (or a follow-on prose-writer step) needs to replace.
3. **For each FILL, read the cited brief section.** The hint quotes the section number (§4.5A, §7, §6, etc.). Use the brief as the source of truth for the prose direction.
4. **Add per-service entries to each city file.** Open each `data/cities/<city>.json` and add an entry under `quick_ref_localized_items.<new-service>`, `most_common_problem_paragraph.<new-service>`, and `specific_problems_neighborhood_phrase.<new-service>`. Without these, the rendered page will have empty sections (with `<!-- MISSING: ... -->` comments).
5. **Render with `scaffold-core-30-page.py --dry-run`** to confirm the JSON parses and the page assembles.
6. **Render for real** once the FILL prose is in.

## Validation

Built-in:

- **Shape validation** — produced JSON must have the same top-level keys as the template (default `troubleshooting.json`). Missing keys = error; extra keys = warning.
- **Mandatory fields** — listed in `MANDATORY_KEYS` constant near the top of the script. The script refuses to write if any are missing/null/FILL unless `--force` is passed.

External (run after the scaffold to validate the rendered page):

```bash
# Render the page
export GOOGLE_MAPS_EMBED_API_KEY="..."
python3 scaffold-core-30-page.py \
    --service panel-upgrade \
    --city    vienna-va \
    --position 2 \
    --output-folder /tmp/test-panel

# Validate the JSON-LD
python3 -c "
import re, json
with open('/tmp/test-panel/draft-v1-WP-WRAPPED.html') as f:
    s = f.read()
m = re.search(r'<script type=\"application/ld\+json\">\s*(.*?)\s*</script>', s, re.DOTALL)
data = json.loads(m.group(1))
print(f'{len(data[\"@graph\"])} entities:')
for x in data['@graph']:
    print(f'  - {x[\"@type\"]}')
"
```

Expected: 3 entities (LocalBusiness, Service, FAQPage).

## Common errors

### `ERROR: mandatory fields missing or unfilled: name, name_with_city, ...`

The §1 identity table in the brief didn't parse. Open the brief and confirm:

- The §1 header reads exactly `## 1. Service identity and naming` (or any text after the period).
- The table is a 2-column `\| Field \| Recommended value \|` shape.
- Field names appear in the first column as backticked tokens like `` `slug` ``.

Once the §1 table is correct, rerun the scaffold.

### `Fields needing CONFIRMATION: pricing_closing_note` (or similar)

The brief didn't include a `Recommended \`<field>\` ...:` callout for that field. Either:

- Add the recommendation to the brief and rerun the scaffold (the value will be extracted).
- Or leave the field empty in the data file (most pricing fields tolerate empty strings — see the template for the safe defaults).

### `Fields needing AUTHORING (...): faq_items[*].answer_html ...`

Expected. FAQ answers are FILL placeholders by design — the brief carries answer hints (§7 Notes column), not authored prose. The operator writes the answers using the hint + their domain knowledge.

### `WARNING: produced JSON has top-level keys NOT in template`

A new field was added to `build_service_data` but the template (default `troubleshooting.json`) doesn't carry it. Either update the template or remove the new field. Either way, `scaffold-core-30-page.py` will need a corresponding extractor in `build_context()` for the new field to actually render.

### Existing `data/services/<slug>.json` not overwritten

Default behavior — the script writes to `<slug>.scaffolded.json` alongside. Diff the two, merge by hand, then optionally rerun with `--overwrite` to replace the original.

## Plain-language convention

Every FILL placeholder hint and every generated comment follows the plain-language conventions at `~/workspace/second-brain/_meta/plain-language-conventions.md`. The scaffolder doesn't enforce this on prose the operator writes — that's a downstream check via the `plain-language-translation` skill.

## Related

- Brief template: `~/workspace/second-brain/05_shared-intelligence/research-briefs/_template-service-brief.md`
- Example brief: `~/workspace/second-brain/05_shared-intelligence/research-briefs/services/panel-upgrade.md`
- Blueprint: `~/workspace/second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md` (Phase 3a)
- Reference JSON: `data/services/troubleshooting.json`
- Downstream consumer: [`README-scaffold-core-30-page.md`](README-scaffold-core-30-page.md)
- Sibling Phase 3 scaffolder: [`README-scaffold-client-data.md`](README-scaffold-client-data.md)
