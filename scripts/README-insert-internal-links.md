# insert-internal-links.py

Phase 4b internal-linking automation. Reads a Core 30 page corpus and a
link-map synthesis (the reference architecture written by the
`competitor-deep-research` skill in link-map mode), then proposes
cross-links for each page across four axes:

- **Axis A** — same service across nearby cities ("Panel upgrades in nearby cities" block)
- **Axis B** — same city across other services (the existing `related_cards` JSON block)
- **Axis C** — category hub link (Phase 2 hubs C1-C6 — skipped when the hub isn't live)
- **Axis D** — sentence-embedded contextual links inside body prose (semantic mode)

## Non-destructive default

The script **never** modifies an existing draft HTML file. Two passes:

1. **Propose pass** (default, no `--apply` flag). Writes a markdown diff
   and a JSON diff into the page folder. The operator reviews the
   markdown, deletes any rejected proposals from the markdown, and saves.
2. **Apply pass** (`--apply --diff-file <path>`). Reads the curated JSON
   diff and writes a new `draft-vN+1-WP-WRAPPED.html` alongside the
   existing one. The old draft stays untouched. Versioning means rollback
   is `rm draft-vN+1-WP-WRAPPED.html`.

## Setup

No setup. Pure Python stdlib. The script lives at
`repos/ai-agency-core/scripts/insert-internal-links.py` and reads
service data from `data/services/<slug>.json` (already there).

## Quick start

### Propose links for one page

```sh
cd ~/workspace/repos/ai-agency-core/scripts

python3 insert-internal-links.py \
  --page-folder ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/02-panel-upgrade-vienna-va \
  --reference-architecture ~/workspace/second-brain/05_shared-intelligence/research-briefs/link-maps/_synthesis-ev-electric.md \
  --build-order ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/_build-order.md \
  --mode both
```

Output:

```
[propose] panel-upgrade-vienna-va: 10 proposals (A=3, B=2, C=0, D=5)  → _internal-link-proposals-2026-05-31.md
Done. Pages processed: 1. Total proposals: 10.
```

Two files land in the page folder:

- `_internal-link-proposals-2026-05-31.json` — machine-readable
- `_internal-link-proposals-2026-05-31.md` — operator-readable diff,
  grouped by axis

### Batch propose across the whole corpus

```sh
python3 insert-internal-links.py \
  --corpus-root ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30 \
  --reference-architecture ~/workspace/second-brain/05_shared-intelligence/research-briefs/link-maps/_synthesis-ev-electric.md \
  --build-order ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/_build-order.md \
  --mode data-driven
```

Hits every `NN-<slug>` folder. Skips folders the script can't parse.

### Apply approved diffs

After the operator reviews the markdown diff and (optionally) hand-edits
the JSON to remove rejected proposals:

```sh
python3 insert-internal-links.py \
  --page-folder ~/workspace/.../02-panel-upgrade-vienna-va \
  --reference-architecture ~/workspace/.../_synthesis-ev-electric.md \
  --build-order ~/workspace/.../_build-order.md \
  --mode data-driven \
  --apply \
  --diff-file ~/workspace/.../02-panel-upgrade-vienna-va/_internal-link-proposals-2026-05-31.json
```

A new `draft-vN+1-WP-WRAPPED.html` lands in the folder. Old drafts
preserved.

## Flags

| Flag | Required | What it does |
|---|---|---|
| `--page-folder` | one of two | A single page folder. |
| `--corpus-root` | one of two | A root containing many page folders. Batch mode. |
| `--reference-architecture` | yes | Path to the `_synthesis-<client>.md` link-map synthesis. |
| `--build-order` | no | Path to `_build-order.md`. Drives Axis A. Without it, Axis A is silent. |
| `--mode` | no | `data-driven` (A+B+C, default), `semantic` (D only), `both` (all four). |
| `--category-hubs-live` | no | Service slugs whose category hub is live (enables Axis C). Example: `--category-hubs-live panel-upgrade troubleshooting`. Default: none live. |
| `--apply` | no | Write a new draft instead of a diff. Requires `--page-folder` (operator confirms per page). |
| `--diff-file` | with `--apply` | Path to the curated JSON diff. |

## How the four axes work

### Axis A — same service, nearby cities

For the page `panel-upgrade-vienna-va`, the script looks at the build-order
and finds every other city that also has a panel-upgrade page (Fairfax,
McLean, Oakton, Tysons, Rockville). For each one not already linked from
the page, proposes adding a link. Capped at 5 destinations per page.

Apply mode inserts a new `<div class="evp-section evp-nearby-cities">`
block before the existing related-services section, with a sentinel
comment `<!-- evp-nearby-cities-block -->` so re-runs don't duplicate.

### Axis B — same city, other services

For each `related_cards` entry in `data/services/<service-slug>.json`,
expands `{city_slug}` against the page's city and checks whether the
resulting URL appears in the page's HTML. If not, proposes adding a card.

Apply mode inserts each missing card into the existing
`<div class="evp-related-grid">` container — the data-driven layer that
already ships with every Core 30 page.

### Axis C — category hub

For pages where the category hub (Phase 2 slot C1-C6) is live, proposes
a breadcrumb-style link to the hub. **Default: hubs are not live, so
Axis C is a no-op.** Enable by passing `--category-hubs-live
panel-upgrade` (etc.) once a hub ships.

### Axis D — sentence-embedded contextual links

Scans `<p>` body paragraphs for keyword mentions of related services
(EV charger, panel upgrade, troubleshooting, light fixture, chandelier,
smoke alarm, etc.). For each match in a paragraph that doesn't already
contain a link, proposes wrapping the matched phrase in an `<a>` linking
to the service-in-current-city page.

Capped at 5 proposals per page. Skips paragraphs that already contain
an `<a>` tag (no nested links). Skips matches that would point to the
current page.

Apply mode wraps the first occurrence of the matched phrase inside a
matching `<p>` with the proposed anchor.

## Worked example output (page 02 — panel-upgrade-vienna-va)

Running propose-only mode against page 02 with `--mode both`:

```
[propose] panel-upgrade-vienna-va: 10 proposals (A=3, B=2, C=0, D=5)  → _internal-link-proposals-2026-05-31.md
```

Reading the markdown diff:

- **Axis A (3 proposals):** Oakton, Tysons, Rockville panel-upgrade pages
  not yet linked from the Vienna page. These would land in a "Panel
  upgrades in nearby cities" block.
- **Axis B (2 proposals):** Whole-House Rewiring and Standby Generator
  Installation — both listed in `data/services/panel-upgrade.json`
  `related_cards` but not yet present in the rendered HTML. These would
  drop into the existing related-services grid.
- **Axis C (0 proposals):** No category hub live for panel-upgrade yet
  (Phase 2 slot C2 still queued).
- **Axis D (5 proposals):** Five "EV charger" mentions in the page body
  that could be wrapped to link to `/ev-charger-vienna-va/`.

## Operator review tips

- **Axis D per-destination cap is 2.** If a page mentions "EV charger"
  5 times, the inserter proposes 2 wraps to `/ev-charger-vienna-va/`
  and skips the rest. Multiple identical-anchor links to the same
  destination on one page read templated to Google (only the first
  link's anchor counts under "first link priority" anyway) and dilute
  editorial-quality signals. Two is the natural max before the page
  reads spammy.
- **Future-page candidates are surfaced, not silently dropped.** When
  Axis A or Axis B points at a destination that doesn't yet have a
  page folder in the corpus, the proposal routes into a separate
  "🟡 Future-page candidates" section in the diff and is skipped on
  `--apply` (so you can't accidentally ship a 404). The list itself
  is the build-next prioritization signal — keep the references in
  `data/services/<slug>.json` `related_cards`; build the pages when
  the build-order says, and the inserter will start linking them
  automatically.
- **Batch mode writes a corpus-wide future-page report.** When you run
  with `--corpus-root`, the script writes
  `_future-page-candidates-YYYY-MM-DD.md` at the corpus root listing
  every distinct future-page destination, sorted by reference count
  descending. Destinations referenced by many pages should be built
  sooner.
- **Axis A picks cities in build-order priority.** If the next 5 cities
  in build order aren't geographically adjacent to the page's city,
  the proposals will reflect priority over adjacency. Hand-pick a
  better 5 by editing the JSON diff before applying.
- **Always review the new draft before publishing.** `--apply` writes a
  new draft-vN+1 file; the operator should diff vN against vN+1
  before pushing to WordPress via `publish-core-30-page.py`.

## What this script does not do

- **Live HTML mutation on the WordPress site.** The publishing step is
  the existing `publish-core-30-page.py` flow. This script only edits
  the local draft files.
- **Off-site link acquisition.** Phase 4b out-of-scope.
- **Schema-markup linking.** Separate concern.
- **Building the Phase 2 category hub pages.** Those are queued
  separately (C1-C6 in the build-order). When they exist, pass
  `--category-hubs-live <slug>` to enable Axis C.
- **Smart anchor-text rotation.** The current implementation uses the
  destination's primary label. Rotation across pages (per the
  synthesis §3 rule 5) is a planned follow-up.

## Related

- [Skill: competitor-deep-research](~/workspace/skills/competitor-deep-research/SKILL.md) — the link-map mode that produces the synthesis this script consumes
- [Synthesis: _synthesis-ev-electric](~/workspace/second-brain/05_shared-intelligence/research-briefs/link-maps/_synthesis-ev-electric.md) — the reference architecture
- [Blueprint: client-seo-onboarding-automation](~/workspace/second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md) — Phase 4b context
- `scaffold-core-30-page.py` — produces the draft HTML this script edits
- `publish-core-30-page.py` — pushes the edited draft to WordPress
- `data/services/<slug>.json` — `related_cards` data source for Axis B
