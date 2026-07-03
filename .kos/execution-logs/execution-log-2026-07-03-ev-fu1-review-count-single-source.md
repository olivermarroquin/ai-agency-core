---
type: execution-log
status: draft
created: 2026-07-03
updated: 2026-07-03
venture: ev-electric-services
tags: [execution-log, ev-electric-services, EV-FU1, review-count, wpcode, shortcode, schema]
---

## 2026-07-03 — EV-FU1: Dynamic review count single-source mechanism

**What was built:** A single-source system so EV Electric's review count (currently 91 Google reviews, 5.0★) never drifts again across body text and schema. Three components:

1. **WP option as single source** — `ev_review_count` and `ev_review_rating` stored as WP options, editable via Settings → General in wp-admin. One place to update when the GBP count changes.

2. **WPCode Snippet 1 (shortcodes + admin UI)** — Registers `[ev_review_count]` and `[ev_review_rating]` shortcodes + adds input fields to Settings → General page. PHP snippet, auto-insert, run everywhere.

3. **WPCode Snippet 2 (schema filter)** — `the_content` filter at priority 5 that dynamically replaces `"reviewCount"` and `"ratingValue"` in any JSON-LD AggregateRating block. Catches ALL pages automatically (existing + future) without per-page schema edits.

**Pages converted (via REST API, automated):**
- WP 6098 (electrical-troubleshooting-vienna-va): 1 body replacement
- WP 6167 (panel-upgrade-vienna-va): 1 body replacement
- WP 6177 (ev-charger-vienna-va): 1 body replacement
- WP 6178 (electrical-troubleshooting-fairfax-va): 2 body replacements (main + FAQ)
- WP 6179 (panel-upgrade-fairfax-va): 1 body replacement

Body text pattern: `5.0-star average across 91 Google reviews` → `[ev_review_rating]-star average across [ev_review_count] Google reviews`

**Pages NOT converted (deferred):**
- Homepage (WP 8) + About (WP 95): Elementor-built, use generic "100+ Reviews" — no drift, conversion optional
- ~25 older Core-30 pages: EV-FU2 scope (body text still hardcoded; Snippet 2's schema filter auto-fixes their JSON-LD once active)

**Decision made:** Schema via `the_content` filter, NOT centralized `wp_head` JSON-LD.
- **Why:** Each page has page-specific LocalBusiness JSON-LD (areaServed, services). A centralized snippet would need to replicate per-page data or output conflicting blocks. The content filter approach catches any page with `"reviewCount"` in its content — zero per-page maintenance, auto-covers EV-FU2 pages without editing them.
- **Alternative rejected:** Shortcodes inside `<script>` tags — `wpautop` (priority 10) can insert `<p>` tags inside script blocks; content filter at priority 5 runs before `wpautop`, avoiding this.

**Client config updated:** `client-ev-electric-services.json` review_count 87→91, review_count_source updated with EV-FU1 note.

**Reusable for future apps?:** Yes — the WP-option + shortcode + content-filter pattern generalizes to any WordPress client where a business fact (review count, years in business, service area count) appears across multiple pages. Could become a `scaffold-review-shortcode.py` generator.

**Artifacts:**
- `outputs/ev-fu1-review-count-snippets.md` — operator paste instructions for both WPCode snippets
- `repos/ai-agency-core/scripts/ev-fu1-swap-review-shortcodes.py` — REST API swap script
- `repos/ai-agency-core/scripts/data/client-ev-electric-services.json` — review_count updated
- `second-brain/04_projects/clients/_active/ev-electric-services/_deployment-status.md` — EV-FU1 status added
- `second-brain/05_shared-intelligence/_levelup-register-differentiation-pipeline.md` — EV-FU1 row updated

**Blocking operator action:** Create both WPCode snippets in wp-admin. Until then, shortcode tags render as literal text on Wave-1 pages. Instructions in `outputs/ev-fu1-review-count-snippets.md`.

**Verification done:**
- All 5 Wave-1 pages: shortcodes present, zero stale 87/148, zero hardcoded 91 in body, schema reviewCount=91 in JSON-LD
- Cache-busted curl confirms no residual hardcoded counts
