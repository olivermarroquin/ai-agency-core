---
type: execution-log
status: reviewed
created: 2026-06-23
updated: 2026-06-23
venture: ai-agency-core
tags: [execution-log, btf, wave-2b, website-factory, restaurant, tooling, verify, gate]
---

## 2026-06-23 — BTF-2b: Restaurant Tooling (Wave 2 completion)

**What was built:** The restaurant-tooling half of Wave 2 that the engine handoff (BTF-2) did not cover. Six deliverables making the restaurant business type pass the quality + publish tooling pipeline end-to-end:

1. **Restaurant completeness profile** (`profiles/restaurant-completeness.json`) — 21 blocking + 4 warning fields for `facts-completeness-gate.py`. Checks the client config JSON for every field the restaurant templates reference. No city/service data files (restaurant uses a single client config, not the electrician service×city matrix).

2. **Restaurant defaults profile** (`profiles/restaurant-defaults.json`) — 7 default families for `hardcode-scanner.py`. Includes placeholder phone/email/URL detection AND electrician-leak detection (master electrician, Dominion Energy, evp- prefix, LocalBusiness schema type).

3. **Restaurant verify profile** (`profiles/verify-restaurant-page.json`) — 11 checks across 6 per-page surfaces for `verify-artifact.py`. Each of the 6 restaurant pages (home, menu, about, location-hours, cuisine, reserve-order) is a named surface — no more `matches[0]` single-page-only verification. Includes 3 condition-gated schema checks (Menu, ReserveAction, OrderAction) that evaluate against ground truth config before firing.

4. **Restaurant draft template** (`templates/draft-v1-restaurant.md.tmpl`) — wired into the fixed-list scaffold path via `_render_fixed_list_markdown()`. Each page now emits `{slug}-draft-v1.md` alongside HTML + schema JSON. All tokens resolve from the profile's `build_context()` output.

5. **publish-core-30-page.py Restaurant schema validation** — `validate_jsonld()` auto-detects business type from JSON-LD `@type` values. Restaurant pages validate against `{"Restaurant"}`, electrician pages against `{"LocalBusiness", "Service", "FAQPage"}`. Backward compatible — default is electrician.

6. **scaffold-client-data.py restaurant parameterization** — `--business-type restaurant` skips §8 license extraction entirely. Adds `business_type` to the output JSON. License gaps are not recorded for restaurant configs.

**Scope explicitly NOT covered:**
- `scaffold-client-data.py` credential checklist (`CREDENTIALS_CHECKLIST`) is still electrician-oriented. Universal items apply; trade-specific items (Thumbtack, HireNimbus) are irrelevant for restaurants. Deferred — tracked in wave plan.
- Section-sequence generalization remains Wave 3 (CR-072).

**Decisions made:**

- **Per-page surfaces over engine-level glob fix.** CR-A (verify checking only 1 of 6 pages) was fixed by enumerating all 6 pages as named surfaces in the profile, not by changing `verify-artifact.py`'s `matches[0]` behavior. This is declarative, profile-level, and doesn't risk electrician regression.

- **Recursive `extract_jsonld_types`.** CR-D (gated schema checks non-functional) was fixed by making the type extractor walk the entire JSON-LD tree recursively. The gated schema types (Menu, ReserveAction, OrderAction) are nested properties within the Restaurant node — not separate `@graph` entries — so top-level-only extraction missed them entirely.

- **Condition-gated checks in the verify engine.** CR-G (false-FAIL on legitimate no-frills restaurants) was fixed by adding a generic `condition` field to the check dispatch loop. The condition evaluates a ground truth field via dot-notation path before firing the check. This is engine-level (not restaurant-specific) — any profile can use it. The three gated checks now correctly: PASS when present, SKIP when legitimately absent, FAIL when illegitimately absent.

- **Auto-detect over explicit business_type parameter** for `validate_jsonld`. If Restaurant `@type` is found in the JSON-LD, use restaurant requirements. Otherwise, fall through to electrician (backward compatible). No config changes needed for existing electrician callers.

**Review rounds:** 4 rounds, converged [1, 2, 1, 0]. Catches: CR-A (per-page surfaces), CR-B (test fixture collision), CR-D (recursive type extraction), CR-E (draft template orphan), CR-G (condition-gated checks). Filed as CR-078–081 in the catch register.

**Regression coverage:**
- Electrician: 5/5 pairs PASS (unchanged from Wave 2 engine baseline)
- Restaurant populated config: all 6 pages verified, all 4 schema checks PASS (base + 3 gated)
- Restaurant no-frills config: all 3 gated checks correctly SKIP
- Restaurant tampered (gated schema removed): all 3 gated checks correctly FAIL

**Reusable for future apps?:** Yes — the condition-gated check pattern and recursive type extraction are engine-level features usable by any verify profile. The `--business-type` pattern on `scaffold-client-data.py` extends to any new business type.
