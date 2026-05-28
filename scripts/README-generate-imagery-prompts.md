# generate-imagery-prompts.py

Phase 3d of the [[client-seo-onboarding-automation]] roadmap. Reads a Core 30 page folder, gathers the research briefs (service / city / intersection / client) and any prior `imagery-prompts-log.md` files for the same client, and writes a fresh `imagery-prompts-log.md` into the page folder with three ready-to-paste Higgsfield prompts (Hero, About, optional Scene) plus outcome-tag placeholders.

**Sibling scripts in the same toolkit:**

- [`scaffold-core-30-page.py`](README-scaffold-core-30-page.md) — runs upstream. Produces the `draft-v1.md` this script reads for page metadata.
- [`organize-image-downloads.py`](README-generate-maps-iframe.md) — runs downstream. Moves Higgsfield outputs into the page folder once the operator has picked keepers.
- [`wire-page-images.py`](README-wire-page-images.md) — runs further downstream. Wires the keeper images into the published HTML.

## What it does

1. Reads `<page-folder>/draft-v1.md` frontmatter — pulls `service`, `city`, `page-slug`, `core-30-position`.
2. Loads four research briefs (gracefully degrades on missing files):
   - Service base brief at `briefs/services/<service-slug>.md`
   - City base brief at `briefs/cities/<city-slug>.md`
   - Intersection brief at `briefs/intersections/<service-slug>--<city-slug>.md`
   - Client-fact brief at `briefs/clients/<client-slug>/brief.md`
3. Loads the client config at `data/client-<client-slug>.json` (owner name, brand colors, owner reference photo URL).
4. Scans every `imagery-prompts-log.md` under the client's core-30 folder — extracts:
   - Keeper counts per slot type (Hero / About / Scene)
   - "Lesson worth carrying forward to SOP" callouts (carried into new prompts)
   - Latest keeper wardrobe block (carried forward verbatim)
   - City-keyed About-portrait keeper (suggests reuse instead of regen)
5. Composes three prompts following the SOP's canonical 8-part structure (subject / action / scene / wardrobe / lighting / composition / style / avoid).
6. Writes `<page-folder>/imagery-prompts-log.md` — frontmatter, setup context, generations, follow-ups, related links.

## What it does NOT do

- **Doesn't call Higgsfield.** Higgsfield has no stable public API as of 2026-05. The operator runs Higgsfield in-browser, pastes each prompt, picks the keeper, fills in the placeholders.
- **Doesn't overwrite an existing log** unless `--overwrite` is passed. Mirrors the non-destructive default of the other Phase 3 scaffolders.
- **Doesn't pick variants.** Variant selection stays a human-in-the-loop step. The script provides 3 ready-to-paste prompts; the operator does the picking.
- **Doesn't run the downstream pipeline.** Use `organize-image-downloads.py` and `wire-page-images.py` once keepers are picked.

## Setup

Pure stdlib — no `pip install`. Python 3.8+.

## Usage

### Basic

```bash
cd ~/workspace/repos/ai-agency-core/scripts
python3 generate-imagery-prompts.py \
    --page-folder ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/06-light-fixture-installation-vienna-va/ \
    --client       ev-electric-services
```

Writes `imagery-prompts-log.md` into the page folder.

### Different client

```bash
python3 generate-imagery-prompts.py \
    --page-folder /path/to/s-and-h-page/ \
    --client       s-and-h-contracting
```

### Skip the optional Scene prompt

The Scene prompt is the "what the work output looks like" shot — useful for some services (panel upgrade close-up, finished EV charger install) but redundant for others (troubleshooting, where the "work output" is invisible). Default is to include it.

```bash
python3 generate-imagery-prompts.py \
    --page-folder /path/to/page/ \
    --no-scene
```

### Preview without writing

```bash
python3 generate-imagery-prompts.py --page-folder /path/to/page/ --dry-run
```

Prints the full rendered log to stdout; writes nothing.

### Overwrite an existing log

```bash
python3 generate-imagery-prompts.py --page-folder /path/to/page/ --overwrite
```

Default is to refuse if `imagery-prompts-log.md` already exists. `--overwrite` replaces it (the operator's filled-in `**Selected variant:**` / `**Outcome tag:**` data on the prior log will be lost — make a copy first if those matter).

## Operator workflow once the log is generated

1. Open the new `imagery-prompts-log.md`.
2. Check the **Keeper-bias notes from prior pages** section — confirms which lessons informed this run.
3. If an **About-portrait cache hit** is flagged for the city, consider reusing the prior About keeper instead of running the About prompt. Mark `**Selected variant:** REUSED from <page>` and skip the Higgsfield run for that slot.
4. Open Higgsfield, upload the reference photos listed in **Setup context** (owner face ref + logo art).
5. Paste the Hero prompt, **verify the aspect-ratio chip is set to 4:3 landscape** (it can flip between batches), generate 4 variants.
6. Pick the keeper. Fill in `**Selected variant:**`, `**Output filenames:**`, `**Verdict:**`, `**Outcome tag:**`, and optionally `**Lesson worth carrying forward to SOP:**`.
7. Repeat for About (3:4 portrait) and Scene (if running).
8. Run `organize-image-downloads.py` once per slot type to move the picked Higgsfield downloads into the page folder.
9. Run `wire-page-images.py` to upload + wire + republish.
10. Update the log's `status:` frontmatter to `active` once any keeper is wired, and `completed` once all slots are done.

## The learning loop

Future runs of this script bias toward prompts that produced keepers and away from prompts that produced misses. The signals it reads:

- `**Verdict:**` lines containing "publication-quality" mark keeper generations.
- `**Lesson worth carrying forward to SOP:**` callouts get pulled forward and listed at the top of the new log.
- The wardrobe block from the most-complete keeper Hero (or About, if Hero references another by bracket) gets lifted verbatim and inserted into new prompts.
- City-keyed About-portrait keepers suggest reuse rather than regen.

For this loop to work, the operator must fill in `**Verdict:**` and `**Outcome tag:**` honestly on every prior generation. Placeholder values (containing `_<` or `>_` markers) are skipped — they don't pollute keeper counts.

## Inputs the script handles gracefully

- **Missing service brief.** Falls back to the per-service action table baked into the script (`SERVICE_FALLBACK_ACTIONS`). Covers `troubleshooting`, `panel-upgrade`, `ev-charger`, `light-fixture-installation`, `smoke-alarm`. Add new services to the table when adding new service lines.
- **Missing city brief.** Derives city name + state from the page-slug suffix (last segment = state code). Multi-word cities like `falls-church-va` work.
- **Missing intersection brief.** Skips the local-competitor image-style synthesis and the local-wants review themes. The Hero prompt still composes from the service + city signals.
- **Missing client brief.** Falls back to the `data/client-<slug>.json` config for owner name and brand colors. The wardrobe block in that case comes from prior keeper Hero prompts in the same client's logs.
- **No prior logs.** The wardrobe block falls back to a generic clean-work-shirt template using the brand colors from the client config. The first run for a new client produces plausible-but-generic prompts; once the first keeper is logged, every subsequent run inherits the keeper's wardrobe verbatim.

## Service-slug variant handling

The script normalizes common slug variants before looking up the fallback action table:

- `ev-charger-installation` → `ev-charger`
- `electrical-troubleshooting` → `troubleshooting`
- `smoke-alarm-installation` → `smoke-alarm`
- `light-fixture` → `light-fixture-installation`

If a slug doesn't normalize cleanly, the Hero prompt uses a generic "mid-action on a residential <service> job" phrasing — still ships, but the action is non-specific. Add the service to `SERVICE_FALLBACK_ACTIONS` to fix.

## Adding a new service

Edit `SERVICE_FALLBACK_ACTIONS` in `generate-imagery-prompts.py`:

```python
"generator-installation": {
    "hero_action": "mid-installation of a residential standby generator on an exterior pad ...",
    "setting": "residential exterior side yard — concrete pad, conduit run to the meter ...",
    "scene_subject": "a finished residential standby generator on its pad ...",
},
```

The Hero prompt picks up `hero_action` + `setting`; the Scene prompt picks up `scene_subject`. If the service is a variant of an existing entry (e.g. `whole-house-rewire` is a panel-upgrade adjacent), either add a separate entry or rely on the generic fallback.

## Why this script exists

Manually authoring 3 Higgsfield prompts per page across 30 Core 30 pages is the per-page bottleneck after the page-build pipeline ships. Each prompt is ~250 words of structured imagery brief; getting 30 of them right by hand is hours of work per client. This script compresses the per-page imagery-prompt step to seconds while preserving the human-in-the-loop variant-pick step that actually requires judgment.

Per the [[client-seo-onboarding-automation]] blueprint, once Phase 5's `client-seo-onboarding` orchestrator skill exists, this script will be invoked automatically per page during a bulk Core-30 run — the operator never sees the prompt-composition step at all, they just see the Higgsfield queue.

## Related

- [[sop-ai-imagery-for-core-30-pages]] — the reusable SOP this script implements
- [[client-seo-onboarding-automation]] — Phase 3d of the roadmap
- [[scaffold-core-30-page|scaffold-core-30-page.py README]] — upstream sibling that produces `draft-v1.md`
- [[wire-page-images|wire-page-images.py README]] — downstream sibling that wires keepers into HTML
- [[organize-image-downloads|organize-image-downloads.py README]] — downstream sibling that organizes Higgsfield downloads
