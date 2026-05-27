# bulk-scaffold-pages.py

Scaffold many Core 30 pages in one run. Reads a client's `_build-order.md`, loops `scaffold-core-30-page.py` for every selected row, and reports a summary at the end. Skips publish, skips imagery — just produces the HTML + markdown drafts so a batch authoring session goes from "30 commands typed by hand" to one command.

**Sibling scripts in the same toolkit:**

- [`scaffold-core-30-page.py`](README-scaffold-core-30-page.md) — the per-page workhorse this script calls in a loop.
- [`publish-core-30-page.py`](README-publish-core-30-page.md) — runs after scaffold, per-page.
- [`wire-page-images.py`](README-wire-page-images.md) — the image pipeline that wires real Higgsfield images into the scaffolded drafts.

## What it does

1. Locates the build-order file (default: the client's canonical `_build-order.md` under `second-brain/04_projects/clients/_active/<client>/website-archive/new/core-30/`).
2. Parses the table — pulls `(position, page-slug, service-label, city-label)` for every row whose slug is a real page slug. Reserved rows (`(reserved)`, `tbd`, blank cells) are skipped.
3. Filters rows by `--positions` if given.
4. Derives `(service-slug, city-slug)` for each page slug using the data files already on disk:
   - Loads every `data/cities/*.json` to know valid city slugs.
   - Loads every `data/services/*.json` to know how `page_slug_template` maps to a service slug.
   - Finds the longest matching city-slug as a suffix of the page slug, strips it, and matches the remainder against known service prefixes.
5. Pre-flight check for existing output folders:
   - With `--skip-existing` — flags existing folders as `SKIP (exists)`.
   - Without it — aborts the whole batch up front and lists the conflicts so the operator can decide.
6. Calls `scaffold-core-30-page.py` for each remaining page as a subprocess. A failure on one page doesn't stop the batch; the reason gets captured and the batch continues.
7. Prints a summary at the end: scaffolded, skipped, failed (with reasons).

## What it does NOT do

- **Doesn't publish.** That's `publish-core-30-page.py`'s job, run per-page after the operator reviews each draft.
- **Doesn't handle imagery.** The Phase 1 image pipeline (organize → optimize → upload → wire) runs per-page.
- **Doesn't insert internal links.** Phase 4b.
- **Doesn't submit to GSC.** Phase 4a.
- **Doesn't overwrite existing pages by default.** Default behavior is refuse-to-overwrite; pass `--skip-existing` to skip conflicts and continue.

## Setup

Same environment as `scaffold-core-30-page.py`:

1. Python 3.8+ (stdlib only).
2. `GOOGLE_MAPS_EMBED_API_KEY` exported from your tier-3 vault (needed by `generate-maps-iframe.py`, which is called transitively per page).
3. Data files for every `(service, city)` pair you're scaffolding must exist in `data/services/` and `data/cities/`. Missing data files produce clean per-page errors — the batch keeps going.

## Usage

### Scaffold everything from position 6 onward

```bash
cd ~/workspace/repos/ai-agency-core/scripts
export GOOGLE_MAPS_EMBED_API_KEY="AIzaSyD…"

python3 bulk-scaffold-pages.py \
    --client ev-electric-services \
    --positions 6-30 \
    --skip-existing
```

`--skip-existing` makes it safe to re-run after a partial batch: pages whose folders already exist are skipped silently, and only the remaining rows get scaffolded.

### Dry run — see what would happen, write nothing

```bash
python3 bulk-scaffold-pages.py \
    --client ev-electric-services \
    --positions 6-15 \
    --dry-run
```

Prints the pre-flight plan: which positions would scaffold, which would skip, which would error early. No subprocess calls, no files written.

### Pick specific positions

```bash
python3 bulk-scaffold-pages.py \
    --client ev-electric-services \
    --positions "6,8,11"
```

Comma-list, range, or a mix (`"1,4-6,9"`). Whitespace around items is allowed.

### Use a build-order file outside the canonical path

```bash
python3 bulk-scaffold-pages.py \
    --client ev-electric-services \
    --build-order /path/to/some-other/_build-order.md \
    --positions 1-10
```

Useful for testing a draft build-order before promoting it into the client folder.

## How (service, city) is derived from a page slug

The script avoids hardcoded mapping tables. For each page slug in the build-order, it:

1. Loads every `data/cities/*.json` and uses each file's `slug` field as a known city slug (longest first).
2. Finds the longest known city-slug that is a suffix of the page slug, separated by `-`. That's the city.
3. Strips the city suffix. The remainder is the service-page-prefix.
4. Loads every `data/services/*.json` and looks for one whose `page_slug_template` (with `{city_slug}` removed) matches the prefix. That's the service.

If step 2 fails, the page errors with "no city data file matches the suffix of page slug 'X' — create `data/cities/<slug>.json`." If step 4 fails, the page errors with the right `page_slug_template` to put in a new service file.

This means the script never needs an explicit slug map — adding a new city or service is just dropping the JSON file in the right place.

## Output

Per-page subprocess stderr is streamed to the operator's terminal so they see progress in real time. At the end:

```
============================================================
Summary
============================================================
  Scaffolded:        14
  Skipped (exists):  5
  Failed:            2

Scaffolded pages:
  ✓ [06] light-fixture-installation-vienna-va
  ✓ [07] electrical-troubleshooting-mclean-va
  …

Skipped (already had a folder):
  - [01] electrical-troubleshooting-vienna-va
  …

Failed pages:
  ✗ [24] electrical-troubleshooting-bethesda-md
        no city data file matches the suffix of page slug 'electrical-troubleshooting-bethesda-md'.
        Create data/cities/<slug>.json for the city this page targets.
  …
```

Exit codes:
- `0` — all attempted pages scaffolded successfully.
- `1` — one or more pages failed (script still ran to completion).
- `2` — bad input (build-order missing, empty positions filter).
- `3` — pre-flight abort because existing folders were detected and `--skip-existing` wasn't passed.

## Build-order table format

The script parses rows that look like this:

```markdown
| 06 | light-fixture-installation-vienna-va | Light fixtures + chandeliers | Vienna | per synthesis: uncontested city-level. |
```

It expects:

- A leading number (the position).
- A page-slug column with only `[A-Za-z0-9_-]` characters (no spaces, no parentheses).
- Two label columns (service, city) — used only for the pre-flight printout, not for slug derivation.
- A trailing status/notes column (may be empty).

Rows with `(reserved)`, `tbd`, em-dashes, or non-slug content in the slug column are skipped automatically.

## Common errors

### `ABORTED: these output folders already exist and --skip-existing was not passed`

The default behavior is refuse-to-overwrite. Either pass `--skip-existing` or remove the conflicting folders, then re-run.

### `ERROR: build-order file not found`

Either the client slug is wrong, or the build-order isn't at the canonical path. Pass `--build-order` to point at the right file.

### `no city data file matches the suffix of page slug 'X'`

The page wants a city you haven't authored a `data/cities/<slug>.json` for yet. Create it (or wait for Phase 3b's `scaffold-city-data.py` to produce it from a city research brief), then re-run.

### `no service data file has page_slug_template prefix 'X'`

The page wants a service you haven't authored a `data/services/<slug>.json` for yet. Create it (Phase 3a's `scaffold-service-data.py`), then re-run.

### `KeyError: '<placeholder>'` (surfaced from inside a per-page subprocess)

A service or city file references a placeholder the scaffolder's context dict doesn't include. The per-page failure printout points at which page hit it — go fix the data file or extend `build_context()` in `scaffold-core-30-page.py`.

## Typical batch authoring session

Plain language version of how this fits into the operator's day:

1. The client's research foundation (Phase 2) and data files (Phase 3) are in place. You've got a populated `_build-order.md`.
2. Run the dry-run first: `python3 bulk-scaffold-pages.py --client <slug> --positions 6-30 --dry-run`. This tells you which positions are ready to scaffold and which still need data.
3. Fix any "no data file" errors — author the missing service or city JSON.
4. Run for real: same command without `--dry-run`, plus `--skip-existing` if pages 1-5 already published.
5. The script writes scaffolds to disk, one folder per page.
6. Review each `draft-v1-WP-WRAPPED.html`, tune city-specific phrasing where it reads generic, then run the image pipeline + publish script per-page.

The bulk scaffolder doesn't change the per-page polish step — the value is removing the "type the same command 25 times" overhead so you can spend that time on the parts that need human judgment.

## Related

- Phase doc: [`phase-4c-bulk-scaffold-pages.md`](../../../second-brain/_meta/handoffs/roadmap-client-seo-onboarding-automation/phase-4c-bulk-scaffold-pages.md)
- Blueprint: [`client-seo-onboarding-automation.md`](../../../second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md) — Phase 4c
- Workhorse: [`scaffold-core-30-page.py`](README-scaffold-core-30-page.md)
- Build-order example: `04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/_build-order.md`
