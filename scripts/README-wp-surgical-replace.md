# README — `wp_surgical_replace.py`

Targeted, counted, reversible edits to WordPress page/post content over the REST API.

**You already had this.** It was written 2026-08-24 for the sitewide copy sweep. The 2026-09-04
update adds three things that were missing for schema work: a **JSON-LD parse gate**,
**`skip_if_present`** for safe reruns, and **`--dump`** so you copy exact strings instead of
guessing them.

---

## Why this replaces editing in wp-admin

Three failure modes were hit doing the 2026-09-03 schema edit by hand. All three are structurally
impossible here.

| wp-admin | This script |
|---|---|
| Save button silently didn't register — twice | HTTP status + round-trip read-back |
| LiteSpeed served stale output; looked like the edit failed | Reads `context=edit` raw content, not the cached front end |
| Session expired mid-edit | Application password, no session |
| Edits one page at a time | `"slugs": "*"` hits all 54 in one pass |
| A typo in 74 KB of minified JSON saves happily | `validate_jsonld` parses first, skips the page if it would break |

---

## The run order — do not skip step 1

```bash
cd ~/workspace/repos/ai-agency-core/scripts

# 1. REAL backup. This script only backs up post_content;
#    _elementor_data lives in postmeta and is NOT in that backup.
python3 wp_page_snapshot.py backup ev-electric-services 8

# 2. Look before you write — read-only, writes dump-home-8.html to the current folder
python3 wp_surgical_replace.py --config configs/ev-electric-services.edits-2026-09-04.json --dump home

# 3. Dry run — prints every page it would touch and every substitution count
python3 wp_surgical_replace.py --config configs/ev-electric-services.edits-2026-09-04.json

# 4. Write it
python3 wp_surgical_replace.py --config configs/ev-electric-services.edits-2026-09-04.json --apply

# 5. Purge: LiteSpeed Cache -> Toolbox -> Purge All   (manual, no REST route)

# 6. Prove it: read-only sitewide sweep
python3 wp_rule_sweep.py --config configs/ev-electric-services.rules.json
```

Nothing is written without `--apply`. A count mismatch aborts **that page untouched** — a partial
edit across 54 pages is the exact failure this design exists to prevent.

---

## Writing an edit

```json
{
  "name": "human label, shows in the output",
  "find": "literal string, or a regex if regex:true",
  "replace": "literal replacement",
  "regex": false,
  "slugs": "*",
  "expect": "1",
  "skip_if_present": "the end-state string"
}
```

- **`expect`** — `"1"`, `"0"`, `">=1"`, `"<=3"`, `"*"` (any). Mismatch = abort that page.
  Use a tight count when you know exactly what you're changing; use `"*"` for sitewide sweeps
  where the per-page count genuinely varies.
- **`skip_if_present`** — if the end state is already there, the edit is a no-op instead of an
  abort. **Always set this**, or your second run fails on the pages the first run fixed.
- **`validate_jsonld: true`** (top level) — after edits, every `<script type="application/ld+json">`
  block must still parse. If an edit would break one, that page is skipped and reported. It also
  warns loudly if a page's JSON-LD was *already* broken before you touched it.

---

## The review-count question — answered

You asked me to walk you through verifying whether the snippet works. Here is what the evidence
already says, and it is more damning than the 09-03 session suggested.

`wp_rule_sweep.py`'s own header records a sitewide sweep on **2026-08-25** that found:

> the review-count shortcodes on **zero pages** and the number **hand-typed on 55**

That is the whole diagnosis.

- **Snippet 6592** registers shortcodes. **Zero pages use them.** It is dead code by definition —
  nothing calls it.
- **Snippet 6604** filters `the_content` to rewrite `reviewCount` from the WP option. On 09-03 the
  option was set to `106`, the cache was purged, and output stayed `105`. Only editing the page
  content moved it. So the filter is not reaching the JSON-LD — most likely because a page-builder
  page renders from `_elementor_data`, bypassing `the_content` entirely.
- **Snippet 6915** was created to set the option. It did set the option. **Output still didn't
  change** — which confirms the option was never the input to anything rendered.

### Recommendation: retire all three, don't debug them

The mechanism existed so the number would live in one place. It never did that, for six weeks,
while quietly making the documented maintenance process wrong — it sent whoever read it to a WP
option that changes nothing.

The script you're now running makes the original problem disappear anyway: updating the count
across all 55 pages is one command, run from a config that is itself version-controlled and
reviewable. **That is a better single source than a WP option, because you can see it.**

So:

1. Run the config above — it sweeps `105 -> 106` sitewide, in three JSON spacing variants.
2. In WPCode, **delete 6915** and **deactivate 6592 and 6604**.
3. Update the maintenance note: *the review count is changed by editing the edits config and
   running `wp_surgical_replace.py`.* One command, all pages.

> Dead code that looks like a working system is worse than no system. It doesn't just fail — it
> tells the next person the wrong place to look.

---

## Footer copyright, Site Title, and the `Golden Services` leftover

These are three different things living in three different places. Only one of them is reachable
from this script.

| Item | Where it lives | Fixable here? |
|---|---|---|
| `Copyright © EV ELECTRIC` | Page content **or** a theme/Elementor footer template | **Maybe** — the `--dump` tells you |
| Site Title (`EV Electric`) | `wp_options` -> `blogname` | No — Settings -> General |
| Text Logo (`Golden Services`) | Theme Customizer / Elementor header template | No — Customizer |

### Footer — the dump decides it

Run step 2 above, open `dump-home-8.html`, search for `Copyright`.

- **Found** → copy the exact string, paste it into the `_pending_needs_dump_first` entry in the
  config, move that object up into `"edits"`, and rerun. Keep `"slugs": "*"` — the footer string
  may be duplicated on other pages.
- **Not found** → the footer is template output, not page content. Fix it in
  **Appearance → Editor** (or Elementor → Templates → Footer), find the copyright widget, edit the
  text there. One place, applies sitewide.

### Site Title — 30 seconds, manual

**Settings → General → Site Title**: `EV Electric` → `EV-Electric Services`. Save.

This one matters more than it looks. Site Title feeds the browser tab, the RSS feed, and — on most
themes — the AIOSEO title-tag fallback. It is currently the last place on the site asserting the
old name, which is exactly the entity-resolution conflict the `sameAs` work was closing.

⚠️ Do **not** touch **Site Address (URL)** or **WordPress Address (URL)** on that screen. Changing
either locks you out of the site.

### `Golden Services` — theme demo leftover

**Appearance → Customize → Site Identity** (Ambery theme puts the text logo here). If it isn't
there, it's an Elementor header template: **Templates → Theme Builder → Header**, edit, replace the
text widget.

There is no Site Identity under Appearance if the theme registers it elsewhere — that's why you
couldn't find "Global Settings" earlier. Customizer settings are **not exposed over the REST API**,
so this genuinely has to be manual. It's the only item in this list that does.

---

## Failure modes worth knowing

- **`FATAL: not authenticated`** — the script proves identity via `/wp-json/wp/v2/users/me` before
  doing anything, because an unauthenticated request still returns `200` on public endpoints and
  `context=edit` silently degrades. "It fetched something" is not evidence that auth worked.
- **Bodyless `403`** — Hostinger's WAF rejects Python's default User-Agent. The browser UA constant
  at the top of the file is load-bearing. Don't remove it.
- **Round-trip differs from what was sent** — WordPress filtered something (usually `kses` stripping
  an attribute). Inspect before trusting it.
- **Everything "worked" but the site looks unchanged** — LiteSpeed. Purge before concluding
  anything.
