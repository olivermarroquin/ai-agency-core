# Known Issue: page-restore route double-wraps serialized-string meta

**Status:** mitigated (guard in place) — do not chase
**Found:** 2026-08-24, during B1 testimonial replacement on ev-electric-services
**Affects:** `wp_page_snapshot.py` restore and selftest commands; any caller of the
keelworks `page-restore` REST endpoint

## The bug

The `page-restore` endpoint writes postmeta via WordPress's `add_post_meta()`, which
internally calls `maybe_serialize()`. When the meta value is a string that *looks like*
serialized PHP data (e.g. the string `a:0:{}` — a serialized empty array), WordPress
wraps it in an extra serialization layer:

```
a:0:{}  →  s:6:"a:0:{}";  →  s:13:"s:6:"a:0:{}";";  →  …
```

Each round-trip through the restore route adds one nesting layer. This is WordPress
core behavior (`maybe_serialize` in `wp-includes/functions.php`), not a bug in the
keelworks plugin.

## Blast radius

Only two meta keys are affected: `_aioseo_keywords` and `_aioseo_og_article_tags`.
Both store empty arrays (`a:0:{}`) on pages that have no keywords or tags configured.

**User-visible impact: none.** AIOSEO reads these values from its own `aioseo_posts`
table via a `get_post_metadata` filter, not from `wp_postmeta`. The double-wrapped
postmeta values are invisible to AIOSEO, to crawlers, and to page rendering. All four
affected pages (8, 95, 97, 103) were independently verified: meta descriptions emit
correctly, schema blocks render, pages load at full size.

## What was tried

1. **v1.1.0 raw-write endpoint** (`page-meta-raw`): writes to `wp_postmeta` via
   `$wpdb->update()`, bypassing `maybe_serialize()`. The postmeta write succeeds at
   the DB level, but `get_post_meta()` still returns the AIOSEO table's value. This
   confirmed that AIOSEO's `get_post_metadata` filter is the canonical read path for
   `_aioseo_*` keys — postmeta is a shadow copy.

2. **v1.2.0 with `aioseo_posts` table writes** was designed but not deployed. Writing
   directly into a third-party plugin's private table to tidy an invisible empty value
   carries more risk than the defect.

## The fix (guard)

`wp_page_snapshot.py` now strips `_aioseo_keywords` and `_aioseo_og_article_tags` from
the restore payload before sending it to the endpoint. This prevents the nesting from
getting deeper on future restores and selftests. The guard applies to both the `restore`
and `selftest` code paths.

```python
RESTORE_SKIP_META = {"_aioseo_keywords", "_aioseo_og_article_tags"}
```

The snapshot *captures* these keys (they're still in the backup file for completeness),
but the restore *skips writing* them.

## Current state of the affected keys

| Page | get_post_meta returns | Visible impact |
|---|---|---|
| 95 | `'s:6:"a:0:{}";'` | None |
| 97 | `'s:6:"a:0:{}";'` | None |
| 8  | `'s:6:"a:0:{}";'` | None |
| 103 | `'s:21:"s:13:"s:6:"a:0:{}";";";'` | None |

## Do not chase

The cost of fixing the existing wrapped values exceeds the benefit. The values are
empty keyword/tag arrays with zero functional impact. The guard prevents further
nesting. If AIOSEO is ever removed or these fields gain actual content, the
`aioseo_posts` table is the source of truth — not the postmeta shadow.
