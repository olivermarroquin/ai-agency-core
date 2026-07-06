---
type: execution-log
status: draft
created: 2026-07-06
updated: 2026-07-06
venture: ai-agency-core
tags: [execution-log, rendered-visual-qc, wave-1, productize]
---

## 2026-07-06 — RVQC Wave-1: Generalize Layer 1 (design-agnostic geometry engine)

**Tier:** Productize (reusable across all factories)

**What was built:** Complete rewrite of `rendered_alignment_audit.py` from EV-coupled to design-agnostic. The engine now detects structure by semantic role + computed style + geometry instead of class-name hooks (`.evp-section`, `.evp-heading-left`). Added 4 new checks (A2, E, F, G), a design-profile system, mobile viewport testing, and timestamped output namespacing.

**Files delivered:**
- `scripts/rendered-visual-qc/rendered_alignment_audit.py` (~580 lines, up from ~310)
- `scripts/rendered-visual-qc/profiles/ev-core30.profile.json` (example profile)
- `scripts/rendered-visual-qc/README.md` (fully rewritten)

### Decisions made

1. **Detection generalization:** Section = `<section>` or `[role="region"]`; fallback = parents of headings. Left-headed = `computedStyle.textAlign === 'left'/'start'`. Content wrapper follows single-visible-child chains. No class-name dependency; class hooks only via profile.
2. **Check A2 (mixed-alignment):** Added post-initial-build after operator flagged the Tysons seasonal-patterns regression. Collects substantial paragraphs (height >55px), normalizes textAlign (start→left), flags mixed alignment regardless of heading alignment. Catches the class check A structurally misses.
3. **Check E (overlap):** Normal-flow siblings only (position:static/relative). Excludes absolute/fixed per reviewer concern C1 (intentional decorative overlaps).
4. **Check F (spacing):** Measures inter-section gaps, clusters within 8px tolerance, flags >3 clusters. Requires ≥4 detected sections.
5. **Mobile breakpoints:** 380px + 768px (two viewports). All findings at all viewports count toward exit code.
6. **Profile format:** JSON in `profiles/` with sectionSelector, headingSelector, columnContainerSelector, spacingScale, thresholds, mobileBreakpoints fields. Zero-config default hardcoded.

### Alternatives considered

- **Single mobile breakpoint (380px only):** Rejected — 768px catches tablet breakpoint bugs that 380px misses.
- **Check F measuring intra-section gaps:** Rejected per reviewer C2 — measuring inter-section gaps is the architecturally correct level for "spacing scale consistency."
- **Including absolute/fixed in overlap check:** Rejected per reviewer C1 — badges, decorative overlays are intentional.

### Proof of generality (acceptance)

- **EV Core-30 (zero-config):** 3 pages audited, sections detected without `.evp-*`, B-underfilled correctly caught on Quick Reference columns, no false A-drift.
- **Non-EV "Acme SaaS" design (zero-config):** Realistic page with Georgia serif, dark nav, gradient hero, flexbox cards, grid pricing, footer. A-drift, A2-mixed-alignment, E-overlap, F-spacing, mobile C-overflow all correctly caught. Clean version passes with zero false positives.
- **Profile-based:** EV profile correctly overrides detection on `.evp-section` / `.evp-heading-left` markup.

### Selftest: 21/21 assertions green

Covers bug/clean × {semantic, non-EV, profile} × {desktop, mobile} for checks A, A2, B, C, E, F.

### Reviewer verdict

Independent reviewer (session 98d36da9) PASS. All 4 concerns (C1-C4) verified addressed. DoD checklist complete. Firing-tracker +3 rows.

### Reusable for future apps?

**Yes — this IS the reusable artifact.** Any future website build (any client, any design system, any factory) runs this tool zero-config and gets deterministic rendered visual QC. Exotic layouts add a profile JSON. This is Layer 1 of the Rendered Visual-QC program.

### What's next

- **Wave 2:** Layer 2 perceptual vision reviewer (advisory, claude-sonnet-4-6 + rubric)
- **Wave 3:** Factory-wide gate wiring (canonical DoD + review-gate registration)
