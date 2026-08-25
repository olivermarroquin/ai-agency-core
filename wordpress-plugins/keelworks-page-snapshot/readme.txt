=== Keelworks Page Snapshot Bridge ===
Contributors: keelworks
Requires at least: 5.7
Requires PHP: 7.4
Stable tag: 1.0.0
License: MIT

Take and restore complete, verifiable page backups over the REST API — including
page-builder data that core WordPress will not expose.

== Description ==

WordPress core REST does not return protected (underscore-prefixed) postmeta. That is
exactly where page builders store their layouts — Elementor keeps a page's entire
structure in `_elementor_data`. A backup taken through core REST therefore contains the
builder's rendered OUTPUT and none of its SOURCE, and cannot rebuild the page.

This plugin adds two capability-gated routes that close that gap.

  GET  /wp-json/keelworks/v1/page-snapshot/<post_id>
       Returns raw post_content, every postmeta key, the AIOSEO row, the page template,
       and md5 checksums for post_content and _elementor_data.

  POST /wp-json/keelworks/v1/page-restore/<post_id>
       Restores from a snapshot payload. Requires "confirm" set to the post ID, or
       "dry_run": true. Verifies checksums after writing and reports whether the
       round-trip was exact.

Auth is WordPress Basic Auth via Application Password. Both routes require the
`edit_others_pages` capability — Administrators and Editors only.

== The _elementor_data slash problem ==

Elementor stores its layout as a JSON string. WordPress runs wp_unslash() on meta values
as they are written, so restoring that meta without re-applying wp_slash() strips a level
of escaping and yields JSON Elementor cannot parse — the page renders blank. This plugin
applies wp_slash() on write and validates that the restored value still parses as JSON.

== Skipped meta keys ==

_edit_lock, _edit_last, _wp_old_slug and _elementor_css are neither captured nor restored.
The first three are transient editor state; _elementor_css is a rebuildable cache, and
restoring a stale copy is worse than letting Elementor regenerate it.

== Changelog ==

= 1.0.0 =
* Initial release. Snapshot and restore routes, checksum verification, dry-run support.
