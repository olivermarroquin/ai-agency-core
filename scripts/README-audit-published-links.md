# audit-published-links.py

Corpus-wide dead-link auditor. Walks every page's draft HTML, finds every
internal link (href starting with `/`), and reports any destination that
doesn't exist as a `NN-<slug>/` folder in the corpus.

**Read-only.** Never modifies HTML. Pairs with `insert-internal-links.py`:

- `insert-internal-links.py` **proposes new links** and refuses to add
  ones pointing at non-existent destinations.
- `audit-published-links.py` **reports the dead links already sitting**
  in HTML from earlier hand-edits, scaffolder runs, or copy-paste.

## When to use

- Before publishing a batch of pages: confirm nothing ships a 404.
- After scaffolding a new page: confirm the scaffolder didn't introduce
  references to siblings that don't exist yet.
- Periodically across the corpus: identify which "next page to build"
  would close the most dead links — usually the highest-leverage build.

## Quick start

### Audit one page

```sh
cd ~/workspace/repos/ai-agency-core/scripts

python3 audit-published-links.py \
  --page-folder ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/02-panel-upgrade-vienna-va
```

Output: `_dead-link-audit-YYYY-MM-DD.md` lands in the page folder.

### Audit the whole corpus

```sh
python3 audit-published-links.py \
  --corpus-root ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30
```

Output:

- stdout per-page summary
- `_dead-link-audit-YYYY-MM-DD.md` at the corpus root with two views:
  **By destination** (sorted by total refs — top entries are the
  highest-leverage pages to build next) and **By source page** (what
  each individual page has wrong)

### Extend the allowlist

Default allowlist contains standard utility paths (`/`, `/contact/`,
`/about/`, `/reviews/`, `/services/`, `/service-areas/`, `/blog/`,
`/faq/`, `/privacy/`, `/terms/`, `/sitemap/`) — these are valid even
though they're not in the Core 30 corpus.

If a page links to something else valid (e.g. `/gallery/`), tell the
auditor:

```sh
python3 audit-published-links.py \
  --corpus-root ~/workspace/.../core-30 \
  --allowlist /gallery /financing
```

## What "dead" means here

A dead link is an internal `<a href="/...">` whose destination doesn't
appear as a `NN-<slug>/` folder in the corpus root AND isn't in the
allowlist.

The auditor doesn't distinguish between:

1. **Forward references** — the operator linked to a page assuming it'd
   be built soon (true for most current EV Electric "outside this city,
   we serve…" blocks).
2. **Scaffolder leftovers** — the page template hard-coded a link to a
   sibling that doesn't exist for this client yet.
3. **Genuine bugs** — copy-paste or typo errors.

All three look the same to Google: a link to a URL that returns 404.
You decide which it is.

## What it does NOT check

- **External links** — `https://...` URLs to other domains. The
  auditor skips these. Use a separate link-checker (e.g.
  `linkchecker` CLI or Google Search Console's coverage report) for
  external 404 hunting.
- **Anchor fragments** — `/services/#power-panels` is treated as
  `/services/` for comparison. The auditor doesn't try to verify the
  fragment exists on the destination page.
- **The live WordPress site** — only the local draft HTML. To audit
  what's actually live, point this at the published page corpus on
  staging or use a crawler against the production URL.

## Flags

| Flag | Required | What it does |
|---|---|---|
| `--page-folder` | one of two | A single page folder. |
| `--corpus-root` | one of two | A root containing many page folders. Batch mode. |
| `--allowlist` | no | Extra hrefs to treat as valid (defaults already include `/contact/`, `/about/`, etc.) |

## Related

- `insert-internal-links.py` + `README-insert-internal-links.md` — the
  link inserter; surfaces the same future-page candidates from a
  different angle (proposed but not built, vs. already-linked but not
  built).
