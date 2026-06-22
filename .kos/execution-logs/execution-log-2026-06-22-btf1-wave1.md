---
type: execution-log
status: complete
created: 2026-06-22
updated: 2026-06-22
venture: ai-agency-core
author: independent-reviewer (Cowork, session cowork-btf1-independent-reviewer) — co-authored from the producer run btf1-wave1-impl-2026062200
tags: [execution-log, ai-agency-core, btf, website-factory, business-type-agnostic, review-gate]
---

## 2026-06-22 — BTF-1 Wave 1: business-type-agnostic factory (unified engine)

**What was built:** A unified, profile-driven page scaffolder (`repos/ai-agency-core/scripts/scaffold-page.py`) that renders sites for multiple business types from `profiles/<type>/` + a `business_type`-keyed client config. Restaurant (Asian Delight) stood up as the second business type alongside electrician (EV / S&H). See `[[pattern-engine-business-type-agnostic-profile-driven]]` and `[[lesson-btf-wave1-retro]]`.

### Artifacts produced (verified on disk)
- `scaffold-page.py` — unified engine; two page-generation modes (matrix=electrician, fixed-list=restaurant); branch-free profile-driven JSON-LD builder (0 type-conditionals in executable code).
- `profiles/restaurant/` + `profiles/electrician/` — 5 files each (page-model, schema-template, keyword-research, content-sections incl. `token_bindings` + `template_renders`, config-schema).
- `data/client-asian-delight.json` (new, placeholder-marked) + `client-ev-electric-services.json` / `client-s-and-h-contracting.json` (added `business_type` + keyed type-section).
- `bulk-scaffold-pages.py` repointed to `scaffold-page.py`; `scaffold-client/service/city-data.py` + 4 doc-ref scripts updated.
- `tests/test_electrician_regression.py` + `tests/fixtures/mclean-va-troubleshooting-jsonld.json`.
- `docs/btf-1-architecture-spec.md`, `docs/btf-1-gap-audit-and-wave-plan.md`.
- Wave 2 handoff: `second-brain/_meta/handoffs/website-factory/handoff-2026-06-22-btf-wave2-electrician-html-generalization.md`.

### Key decisions
- **Profile-not-if-else.** Adding a business type = adding `profiles/<type>/`; the engine has no `if business_type ==` branches. JSON-LD shape (incl. page-gated `hasMenu`/`ReserveAction`) is declared in `schema-template.json`.
- **Electrician HTML via legacy delegation (interim).** `scaffold-page.py` imports the unchanged `scaffold-core-30-page.py` and calls its `build_context()` + section renderers for electrician HTML; only JSON-LD + the restaurant/fixed-list path are profile-driven. Chosen to keep electrician output byte-identical (zero regression risk) rather than rewrite a working renderer. Electrician-HTML generalization is tracked Wave 2.

### Bugs / failures caught during the 5-round independent review (catches/pass [4,2,1,1,0])
1. **R1 — relabeling, not generalization.** First delivery was a separate `scaffold-restaurant-proof.py` + the untouched electrician engine, framed as "the split works." Root cause: building a proof path instead of the engine. Fix: build the real unified engine, delete the proof. (`[[CR-067]]` class.)
2. **R2 — unified engine not wired in.** `bulk-scaffold-pages.py` still subprocess-called the old engine. Fix: repoint `SCAFFOLD_SCRIPT`.
3. **R2 — restaurant page-specific schema leaked onto all pages + double-emitted**, masked by Asian Delight's empty placeholder config. Caught only by re-running with a populated config. Root cause: the global `conditional_blocks` loop emitted page-gated blocks everywhere. Fix: global loop skips keys listed in `page_conditional_blocks`. (`[[CR-068]]`.)
4. **R3 — undisclosed legacy delegation.** Electrician HTML ran on the imported legacy engine; "byte-identical regression" was same-code, not reproduction — not stated in docs. Fix: corrected gap-audit, spec §9, profile `_wave_status`.
5. **R4 — regression test committed failing.** Smoke test asserted a frozen HTML byte-length (59641) that varies by environment (Maps iframe). Caught by running it (got 59511). Fix: rewrote to run BOTH engines same-process and assert `old_html == new_html`. (`[[CR-069]]`.)

### Reusable for future apps? — YES
The profile-driven engine pattern generalizes to any business type (gym, retail, auto-dealer). See `[[pattern-engine-business-type-agnostic-profile-driven]]`. The legacy-delegation-during-refactor approach is a reusable de-risking tactic.

### Honest residual gaps (carried into Wave 2 / future)
- **Electrician JSON-LD regression coverage = 1 of ~56 service×city pairs** (troubleshooting/mclean-va). The other pairs are blocked by pre-existing city-data completeness gaps (missing `ev_neighborhood_phrase` etc.). Broaden the regression once city data is complete; until then the profile-driven JSON-LD builder's fidelity is proven for one service only.
- **Restaurant menu/reservation schema** (`hasMenu`/`ReserveAction`/`OrderAction`) is verified only via the reviewer's populated-config test — the shipped Asian Delight output has these gated off (empty placeholder menu, ACCESS-GATED pending Siang's final menu). Add a populated-restaurant test fixture in Wave 2.
- **Fragile legacy coupling:** `scaffold-page.py` reaches into legacy internals by name (`render_pattern_cards`, etc.); guarded by the smoke test but a legacy rename would break electrician HTML.
