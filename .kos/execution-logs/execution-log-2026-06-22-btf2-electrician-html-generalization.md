---
type: execution-log
status: reviewed
created: 2026-06-22
updated: 2026-06-22
venture: ai-agency-core
tags: [execution-log, btf, wave-2, website-factory, electrician, html-generalization]
---

## 2026-06-22 — BTF-2: Electrician HTML Generalization

**What was built:** Retired the legacy electrician HTML renderer (`scaffold-core-30-page.py`) and moved electrician HTML markup into composable section templates rendered by the unified engine. Both business types (electrician + restaurant) now render through one engine with zero legacy delegation. All markup is externalized to templates (§7.4 satisfied); the matrix-mode section *sequence* remains type-associated (the electrician Core 30 section order is hardcoded in `_render_matrix_html` via fixed data keys). Making the section sequence itself profile-declared is a tracked Wave 3+ item.

**Scope:**
- 13 composable section templates created under `templates/sections/` (hero-image-landscape, hero-image-portrait, paragraph-indented-8, paragraph-indented-10, quick-ref-item, pattern-card, problem-card, process-step, pricing-item, neighborhood-item, related-card-linked, related-card-plain, faq-item)
- Page template `core-30-page.html.tmpl` universalized: `evp-` → `{css_prefix}-` (194 occurrences), icon SVGs extracted to profile tokens (`pattern_card_icon_svg`, `problem_card_icon_svg`, `pattern_card_icon_bg`, `problem_card_icon_bg`)
- Generic section renderers built in `scaffold-page.py`: `_render_template_items()` drives most sections; specialized renderers for related cards (disk-existence check), FAQ (question/answer format_map), quick-ref (variant field), hero image (orientation detection), maps iframe
- `_run_matrix_mode()` rewritten: markup externalized to composable templates, no legacy import. Section sequence remains type-associated (hardcoded to the electrician Core 30 section order via fixed data keys like `problem_cards`, `housing_patterns`, `process_steps`). A 3rd matrix-style business type would need its own section-assembly function or a profile-declared section sequence — tracked as a Wave 3+ scope item, not a Wave 2 deliverable.
- `_validate_internal_links()` parameterized: service keywords from client config, not hardcoded
- `scaffold-core-30-page.py` deleted
- `_import_legacy_engine()` + `_render_legacy_html()` removed
- Electrician profile `_wave_status` note removed, `page_template` field added
- Regression test rewritten: compares against stored Wave 1 fixtures (not old-vs-new same-run)
- 5 HTML+JSON-LD baseline fixtures captured (mclean-va, falls-church-va, tysons-va, bethesda-md, potomac-md)

**Decision made:** Section templates are ITEM-level (one card, one step, one FAQ) while the page-level template remains type-associated (the profile declares `page_template`). This lets different business types have completely different page layouts while sharing universal section partials.

**Alternatives considered:** Full Jinja2-style template engine with loops and conditionals vs simple `format_map()` with Python iteration. Chose the simpler approach — `format_map()` templates + Python `_render_template_items()` — because it produces byte-identical output without adding a template engine dependency.

**Why this approach:** The page template already uses `str.format_map()`. Adding a loop construct would require a template engine (Jinja2, Mako) that doesn't exist in the project. The Python iteration in `_render_template_items()` is 20 lines of generic code that handles all section types. The templates themselves are pure HTML with `{token}` placeholders — maximally simple, no learning curve.

**Regression coverage:**
- **5 of ~56** service×city pairs regression-tested (troubleshooting × mclean-va, falls-church-va, tysons-va, bethesda-md, potomac-md)
- The remaining ~51 pairs are blocked by pre-existing city-data gaps (missing variant fields like `ev_neighborhood_phrase`, `distance_phrase`). This is the same coverage limit as Wave 1.
- Restaurant: 6 pages generated. hasMenu/ReserveAction page-gating code untouched by this diff (confirmed by grep — zero changes to `build_jsonld`, `conditional_blocks`, or `page_conditional_blocks` logic). The placeholder config emits 0 gated blocks on all pages because `menu_url`/`accepts_reservations` are empty (CR-068 blind spot); a populated config was not tested this session.

**Reusable for future apps?:** Yes — the section template pattern (item-level `.html.tmpl` files + generic Python iterator) is reusable for any page scaffolder. The `_render_template_items()` function handles string items, dict items, enumeration, and configurable separators.
