# BTF-1 Gap Audit + Wave Plan — Business-Type-Agnostic Factory

**Created:** 2026-06-22
**Source:** Full inventory of scripts/, templates/, wordpress-plugins/, skills/, and data/ in ai-agency-core

---

## 1. Classification Summary

| Classification | Count | Description |
|---|---|---|
| **UNIVERSAL** | 18 | Works for any business type as-is |
| **TYPE-SPECIFIC** | 8 | Engine is agnostic but needs per-type profile/config/data |
| **ELECTRICIAN-ONLY** | 16 | Hardcoded electrician assumptions that break for other types |

---

## 2. Full Classification Table

### UNIVERSAL (18) — No changes needed

| Component | What It Does |
|---|---|
| `publish-core-30-page.py` | Publishes HTML to WordPress via REST API |
| `scaffold-restaurant-proof.py` | BTF-1 proof scaffolder (the architecture target) |
| `generate-maps-iframe.py` | Google Maps embed iframe |
| `wire-page-images.py` | Image upload/wire pipeline |
| `organize-image-downloads.py` | Sorts Higgsfield downloads |
| `ingest-real-photos.py` | Ingests real photos into page folders |
| `upload-image-to-wp.py` | Single image upload to WP |
| `optimize-image.py` | Image compression (PNG→WebP) |
| `refresh-cached-image.py` | Refresh cached image entry |
| `audit-published-links.py` | Dead internal link scanner |
| `gsc_indexing.py` | GSC Indexing API wrapper |
| `submit-gsc-indexing.py` | CLI for GSC URL submission |
| `_load_secrets.py` | Secrets resolver |
| `wire-images-into-html.py` | Rewrite image src URLs in HTML |
| `keelworks-jsonld-head.php` | JSON-LD injection plugin |
| `keelworks-aioseo-bridge.php` | AIOSEO meta writer plugin |
| `keelworks-yoast-bridge.php` | Yoast meta writer plugin |
| `keelworks-litespeed-bridge.php` | LiteSpeed cache management plugin |

### TYPE-SPECIFIC (8) — Engine is agnostic, needs per-type profile/data

| Component | What It Does | What Restaurant Needs |
|---|---|---|
| `facts-completeness-gate.py` | Pre-scaffold field validation | `profiles/restaurant-completeness.json` |
| `hardcode-scanner.py` | Scans output for leaked defaults | `profiles/restaurant-defaults.json` |
| `verify-artifact.py` | Pre-publish verification | `profiles/verify-restaurant-page.json` |
| `choose-image-variant.py` | Vision-scored image selection | Works as-is (rubric classes are generic) |
| `service-seo-research` skill | Produces service briefs | Restaurant-adapted brief template |
| `city-base-research` skill | Produces city briefs | Make `housing_patterns` optional |
| `client-fact-research` skill | Produces client fact briefs | Adaptable (license → food permit) |
| `house-voice-rewrite` skill | Voice file + rewrite | Point at restaurant editorial sources |
| `output-quality-loop` skill | Artifact quality gate | Add restaurant page spec to routing table |

### ELECTRICIAN-ONLY (16) — Hardcoded, needs refactoring or replacement

| Component | Key Blocker | Fix Approach |
|---|---|---|
| `scaffold-core-30-page.py` | Crashes on missing `license`, `ev_charger_homes_phrase`; hardcoded `@type: LocalBusiness`; Dominion Energy default | **Replace** with profile-driven `scaffold-page.py` (BTF-1 architecture) |
| `scaffold-client-data.py` | License hardcoded to Virginia; contractor credential list | Make license optional by `business_type`; parameterize credential list |
| `scaffold-service-data.py` | "master electrician" in alt text, headings, about | Replace with `{owner_title}` tokens |
| `scaffold-city-data.py` | `ev_charger_homes_phrase` required; electrical-troubleshooting in wikilinks | Make EV fields optional; parameterize wikilink href |
| `bulk-scaffold-pages.py` | Inherits from scaffold-core-30-page.py | Follows scaffold-page.py refactor |
| `generate-imagery-prompts.py` | Prompt composition assumes job-site photography | Parameterize prompt style from type profile |
| `generate-and-distribute-heroes.py` | SERVICE_PROMPTS hardcoded for electrician services | Read prompts from type profile |
| `insert-internal-links.py` | Service × city grid model assumed | Define restaurant link-axis model in profile |
| `core-30-page.html.tmpl` | Wrong page model entirely; `evp-` CSS prefix; license tokens | **Replace** with composable section templates (Wave 2) |
| `draft-v1.md.tmpl` | Checklist hardcodes section numbers, schema types, FAQ count | Create `draft-v1-restaurant.md.tmpl` |
| `data/services/*.json` (8 files) | Authored electrician content | Not reusable — restaurant has no service×city data files |
| `data/cities/*.json` | `ev_charger_homes_phrase` required; housing symptoms | Not needed for single-location restaurant |
| `client-seo-onboarding` skill | Orchestrates the electrician pipeline | Needs restaurant-adapted orchestrator (Wave 3) |
| `intersection-research` skill | EV field; service×city concept doesn't map | Not applicable to restaurant |
| `custom-html-build` skill | EV Electric-specific | Not applicable |
| `hub-and-nav-build` skill | C1-C6 taxonomy is electrician-specific | Define restaurant hub taxonomy |

---

## 3. Wave Plan — Path to "Any Business Type"

### Wave 1 (THIS SESSION — DONE)
**Goal:** Prove the architecture on a second business type (restaurant). Build the unified engine entry point with profile-driven JSON-LD + restaurant path.

| Deliverable | Status |
|---|---|
| Architecture spec (engine/profile/config split) | DONE — 4-round review PASS |
| `profiles/restaurant/` (5 profile files) | DONE |
| `profiles/electrician/` (5 profile files) | DONE |
| `client-asian-delight.json` config | DONE |
| Existing configs updated with `business_type` + `electrician` section | DONE |
| Unified `scaffold-page.py` engine (JSON-LD + fixed-list path profile-driven) | DONE |
| Asian Delight 6-page scaffold with Restaurant JSON-LD | DONE |
| Electrician regression (byte-identical HTML + JSON-LD, 2 pairs) | DONE |
| Pipeline wiring (bulk-scaffold + 7 scripts repointed to scaffold-page.py) | DONE |
| Gap audit + wave plan (this document) | DONE |

**What Wave 1 delivered — stated precisely:**
- **Unified engine entry point** (`scaffold-page.py`) handles both matrix (electrician) and fixed-list (restaurant) modes via one CLI
- **Profile-driven JSON-LD builder** — zero type-specific conditionals; schema structure comes entirely from `profiles/<type>/schema-template.json`; page-gated conditional blocks (hasMenu, potentialAction) only emit on their declared pages
- **Profile-driven restaurant/fixed-list path** — token resolution, template rendering, section layout, and page generation all driven by `profiles/restaurant/` with zero type literals in the engine
- **Electrician HTML via legacy delegation** — `_run_matrix_mode()` imports `scaffold-core-30-page.py` as a module and calls its existing `build_context()` + section renderers for HTML. This is a SAME-CODE delegation (zero-risk, byte-identical output), NOT a profile-driven reproduction. The legacy engine is retained as a library, not the entry point.
- **Electrician regression** reflects same-code delegation: HTML is byte-identical because it's the same rendering code; JSON-LD is value-identical because the profile-driven builder reproduces the same graph. Verified on 2 pairs (mclean-va + falls-church-va).
- Zero type-specific literals in engine executable code (confirmed by grep)
- The existing electrician pipeline is unbroken — `bulk-scaffold-pages.py` and all callers now point to `scaffold-page.py`

**What Wave 1 did NOT do (deferred to Wave 2):**
- Electrician HTML is NOT profile-driven yet — it delegates to the imported legacy renderers
- The electrician profile's `template_renders` (34 entries) are JSON-LD-only today; they go live when Wave 2 moves electrician HTML to the generic renderer
- Composable section templates (`templates/sections/`) are not yet built

---

### Wave 2 — Electrician HTML Generalization
**Goal:** Retire the legacy import. Move electrician HTML rendering onto the profile-driven generic renderer + composable section templates. After Wave 2, `scaffold-core-30-page.py` can be deleted.

| Task | Effort | Depends On |
|---|---|---|
| Build composable HTML section templates (`templates/sections/`) | Medium | Wave 1 engine |
| Move electrician HTML rendering to generic renderer using electrician profile's `content-sections.json` + `template_renders` | Large | Section templates |
| Delete `_import_legacy_engine()` + `_render_legacy_html()` from scaffold-page.py | Small | Electrician HTML generalized |
| Delete `scaffold-core-30-page.py` (no longer imported) | Small | Above |
| Electrician regression: byte-level HTML + JSON-LD diff against stored fixture | Gate | All above |
| Update `facts-completeness-gate.py` profile for restaurant | Small | Restaurant profile |
| Update `hardcode-scanner.py` profile for restaurant | Small | Restaurant profile |
| Update `verify-artifact.py` profile for restaurant | Small | Restaurant profile |
| Update `publish-core-30-page.py` schema validation for Restaurant type | Small | Restaurant schema |
| Create `draft-v1-restaurant.md.tmpl` | Small | Restaurant page model |
| Parameterize `scaffold-client-data.py` (license optional, credential list from profile) | Medium | Profile config-schema |

**Wave 2 acceptance criteria:**
- `scaffold-page.py --client ev-electric-services --service troubleshooting --city mclean-va` produces byte-identical HTML + JSON-LD to the stored Wave 1 regression fixture — WITHOUT importing scaffold-core-30-page.py
- `scaffold-page.py --client asian-delight` produces the 6 restaurant pages (unchanged from Wave 1)
- `scaffold-core-30-page.py` is deleted
- Both types rendered by the same generic renderer, zero legacy delegation

---

### Wave 3 — Full Pipeline Generalization
**Goal:** The entire onboarding pipeline (not just scaffolding) works for any type.

| Task | Effort | Depends On |
|---|---|---|
| Parameterize `scaffold-service-data.py` (replace "master electrician" with `{owner_title}`) | Small | — |
| Parameterize `scaffold-city-data.py` (EV fields optional, wikilink href from profile) | Medium | Profile |
| Parameterize `generate-imagery-prompts.py` (prompt style from profile) | Medium | Profile |
| Parameterize `generate-and-distribute-heroes.py` (prompts from profile) | Medium | Profile |
| Define restaurant internal-link axis model | Medium | Restaurant page model |
| Adapt `insert-internal-links.py` for non-matrix page models | Medium | Link axis model |
| Adapt research skills for restaurant briefs | Medium | — |
| Build `client-seo-onboarding-restaurant` orchestrator skill | Large | All above |
| Update `output-quality-loop` spec routing for restaurant | Small | Restaurant spec |

**Wave 3 exit criteria:**
- A restaurant client can be onboarded through the full pipeline from intake to published site
- No manual workarounds needed for any pipeline step
- The same onboarding skill routes to electrician or restaurant pipeline based on `business_type`

---

### Wave 4 — Third Type Validation
**Goal:** Add business type #3 (e.g., plumber, HVAC, or another local service) to prove the pattern generalizes beyond 2 types.

| Task | Effort |
|---|---|
| Create `profiles/<type3>/` (5 files) | Medium |
| Create client config for type #3 | Small |
| Scaffold + publish from the universal engine | Gate |
| Document the "add a new type" playbook | Small |

**Wave 4 exit criteria:**
- Type #3 builds from `profiles/<type3>/` + config alone
- Zero engine changes for type #3
- "Add a new type" playbook captured in `second-brain/05_shared-intelligence/patterns/`

---

## 4. Effort Estimates (Relative)

| Wave | Scope | Relative Effort |
|---|---|---|
| Wave 1 | Architecture + proof (this session) | 1× (baseline) |
| Wave 2 | Extract universal engine + section templates | 3-4× |
| Wave 3 | Full pipeline generalization | 3-4× |
| Wave 4 | Third type validation | 0.5× |

Wave 2 is the largest single effort because it replaces the core scaffolder. Wave 3 is similarly sized but more parallelizable (each script can be parameterized independently). Wave 4 should be trivial if Waves 2-3 succeeded — that's the whole point of the architecture.

---

## 5. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Electrician regression failure during Wave 2 engine migration | Medium | High | Regression test suite comparing output before/after |
| Template refactor scope creep (Wave 2) | Medium | Medium | Strict "section partials only" scope — no full redesign |
| Restaurant page model evolves during real client work | High | Low | Profile files are updatable without engine changes |
| Third business type exposes gaps in the profile schema | Medium | Medium | Wave 4 is explicitly a validation wave |
| `scaffold-core-30-page.py` callers break during rename | Low | Medium | Update all 4 referencing scripts in the same commit |
