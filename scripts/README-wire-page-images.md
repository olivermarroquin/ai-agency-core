# wire-page-images.py

The imagery pipeline orchestrator. Takes a Core 30 page from "placeholder images" to "real images live on WordPress" in one command — about 30 seconds of script time per page.

**Canonical SOP:** [`sop-ai-imagery-for-core-30-pages.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-ai-imagery-for-core-30-pages.md) — read that for the full operator procedure. This README covers script-level usage.

## What it does

For each image slot in the page (hero, about portrait, optional contextual scene), the orchestrator:

1. Builds a cache key. Hero and scene are per-page (`<client>/<city>/<page-slug>/<type>`). About is per-city (`<client>/<city>/about`) so 30 pages for one city share one upload.
2. Checks the per-client image cache at `scripts/cache/<client-slug>-images.json`. If the slot is cached, reuses the existing WordPress URL without re-uploading.
3. If the slot has a canonical PNG at `<page-folder>/images/` root and isn't cached: shrinks PNG → WebP (via `optimize-image.py` helper), uploads to the WordPress Media Library (via `upload-image-to-wp.py` helper), records the URL in the cache.
4. Rewrites the page HTML to point at the real WordPress URL with SEO alt text.
5. Saves the result as `draft-v(N+1)-WP-WRAPPED.html`.
6. Unless `--skip-publish` is passed, re-publishes the page to the live WordPress site via `publish-core-30-page.py`.

## One-time migration — about-slot cache seeding

Pages built before this pipeline existed already have real WordPress URLs in their about-portrait slots (typically pointing at a portrait uploaded by hand for page 1). On the first run of `wire-page-images.py` against any such page, the orchestrator auto-seeds the per-city about cache entry from the existing HTML URL. From that point forward, every subsequent page in the same city reuses the seeded URL without re-uploading.

The seed only fires for the about slot, and only when no local about PNG is present at `images/` root. If you want to replace the about portrait, drop a new PNG in `images/` and re-run — the local file takes priority over the cached URL.

## Cache file

Per-client JSON at `scripts/cache/<client-slug>-images.json`. Structure:

```json
{
  "client_slug": "ev-electric-services",
  "entries": {
    "ev-electric-services/vienna-va/about": {
      "wp_url": "https://evelectric.pro/wp-content/uploads/2026/05/vienna-portrait.webp",
      "wp_media_id": 1234,
      "filename": "vienna-portrait.webp",
      "alt_text": "Ahmad Shaban, Master Electrician at EV Electric Services serving Vienna, VA",
      "uploaded_at": "2026-05-26T03:00:00+00:00",
      "file_size_bytes": 198765,
      "scope": "per-city-shared"
    }
  }
}
```

Inspect with: `python upload-image-to-wp.py --config ev-electric.config.json --list`.

## Setup

### One-time per client site

1. **Install Python deps:**
   ```bash
   pip3 install requests Pillow --break-system-packages
   ```
2. **WordPress application password + client config JSON** — same setup as `publish-core-30-page.py`. See its README for the full procedure. The image pipeline reuses the same config + WP_APP_PASSWORD env var.
3. **Confirm the WP user can upload to Media Library.** Editor and Administrator roles both work; Author cannot.

### Per session

```bash
export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
```

## Usage

### Standard run

```bash
python wire-page-images.py \
    --page-folder /Users/oliver/workspace/.../02-panel-upgrade-vienna-va \
    --config       /Users/oliver/workspace/repos/ai-agency-core/scripts/ev-electric.config.json
```

About 30 seconds. Processes every slot, uploads what needs uploading, wires HTML, re-publishes.

### Process some slots only

```bash
python wire-page-images.py --page-folder ... --config ... --only-types hero
python wire-page-images.py --page-folder ... --config ... --only-types hero,about
```

### Skip republish (review the HTML draft first)

```bash
python wire-page-images.py --page-folder ... --config ... --skip-publish
```

Writes the new `draft-v(N+1)-WP-WRAPPED.html` but doesn't touch the live page. Run `publish-core-30-page.py` separately when ready.

### Dry run

```bash
python wire-page-images.py --page-folder ... --config ... --dry-run
```

Prints what would happen without making changes. Cache seeding still occurs (it only records facts already visible in the HTML).

## Daily workflow

For a brand-new page after Higgsfield generates 4 variants:

1. `organize-image-downloads.py --client <slug> --page-folder <path> --image-type hero --selected-variant <N>` — moves the chosen variant to `images/` root, the rest to alternates.
2. `wire-page-images.py --page-folder <path> --config <config>` — does everything else.

For pages 2+ in the same city, the about-portrait step is silent (cache hit, no upload).

## Helper scripts (under the hood)

Each is independently callable from the CLI but typically invoked by the orchestrator:

- **`optimize-image.py`** — PNG → WebP, sized to budget per slot type (hero <300KB, about <200KB, scene <500KB).
- **`upload-image-to-wp.py`** — POST to `/wp-json/wp/v2/media`, then PATCH alt text. Cache-aware.
- **`wire-images-into-html.py`** — mutates one image slot in a Core 30 page draft, writes a new versioned file. The orchestrator imports the same helpers and applies all slot mutations in one new draft instead of one per slot.
- **`organize-image-downloads.py`** — stage 1, the only step with a human-in-the-loop decision (which variant won).

## Troubleshooting

- **"$WP_APP_PASSWORD is not set"** — export it from the tier-3 vault. See `publish-core-30-page.py` README for vault location.
- **"WP media upload failed: 401"** — the application password is wrong or the WP user lacks Editor/Administrator role.
- **"Could not find a `<div class='evp-X-image'>` in the HTML"** — the page wasn't scaffolded with that slot type. Check that the v17 template was used; older drafts may not have all three slots.
- **About slot keeps re-uploading** — the local `images/` folder has an about PNG that's overriding the cache. Move it to `_drafts-and-alternates/` if you want the cached URL to take over.
- **"Cache seed: about → ..."** during a dry-run — that's expected. Seeding is safe in dry-run because it only records URLs that are already in the live HTML.

## When to use `refresh-cached-image.py` instead

`wire-page-images.py` is the normal-path orchestrator: scaffold a page, drop the winning PNGs in `images/`, run wire. It always trusts the cache — if a slot is cached, the cached URL wins and no upload happens.

`refresh-cached-image.py` is the override for the moments when the cache is the wrong answer:

| Situation | Use this |
|---|---|
| Building a new page from scratch | `wire-page-images.py` |
| Owner shipped a new portrait, want it on every page in a city | `refresh-cached-image.py --cache-key <city>/about --new-image <new.png> --also-rewire-pages` |
| Higgsfield re-generated one hero, want it replaced everywhere it was used | `refresh-cached-image.py --cache-key <page>/hero --new-image <new.png> --also-rewire-pages` |
| Operator dropped fresh PNGs into 30 page folders at once (e.g., new uniform photo across the corpus) | `refresh-cached-image.py --refresh-all-for-client --dry-run`, review plan, then re-run without `--dry-run` |

Key behaviors of `refresh-cached-image.py`:

- **Invalidates the cache entry first**, so the next upload doesn't cache-hit.
- **Old WordPress media stays put** unless you pass `--delete-old-wp-image`. WordPress doesn't auto-clean orphaned uploads; treat that as a separate decision.
- **`--also-rewire-pages` (Mode 1)** greps the corpus for the old URL and runs `wire-page-images.run()` against each affected page, propagating the new URL into the live HTML and re-publishing.
- **`--refresh-all-for-client` (Mode 2)** walks every page folder, diffs local PNGs against cached filenames, builds an invalidation + upload + rewire plan, and (in non-dry-run) executes it in dependency order — about-portrait uploads happen before the city's other pages re-wire to the new URL.
- **Dry-run is the default starting point.** Always run with `--dry-run` first to inspect the plan before any uploads or republishes happen.

The corpus root defaults to `~/workspace/second-brain/04_projects/clients/_active/<client_slug>/website-archive/new/core-30`. Override with `--corpus-root` if your layout differs.

Read the full docstring at the top of `refresh-cached-image.py` for the complete option list and behavior contract.

## Related

- [`refresh-cached-image.py`](refresh-cached-image.py) — the cache-override companion script (Phase 4d). Use it when you need a regenerated image to propagate beyond a single page.
- [`sop-ai-imagery-for-core-30-pages.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-ai-imagery-for-core-30-pages.md) — the operator-facing imagery SOP.
- [`sop-core-30-page-build.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-core-30-page-build.md) — the per-page operator procedure.
- [`README-publish-core-30-page.md`](README-publish-core-30-page.md) — the downstream republish script.
- [`README-scaffold-core-30-page.md`](README-scaffold-core-30-page.md) — the upstream page-generation script.
- [`blueprint-client-seo-onboarding-automation`](../../../second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md) — the full roadmap this script is Phase 1 of.
