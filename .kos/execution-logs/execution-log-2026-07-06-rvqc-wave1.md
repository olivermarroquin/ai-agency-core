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

### Productization-Readiness DoD (B1-B6)

**B1 — Repeatable steps:** `python3 rendered_alignment_audit.py <files> [--profile <path>] [--out <dir>]` is the single command. `--selftest` verifies the engine works. README documents every flag. A new operator runs `--selftest`, then `rendered_alignment_audit.py *.html` — no tribal knowledge required.

**B2 — Engine/config split:** The engine (`rendered_alignment_audit.py`) is fully generic — zero class-name or client coupling. Configuration lives in two layers: (1) CLI flags for thresholds (`--tol`, `--col-ratio`, `--mobile-breakpoints`, `--spacing-cluster-tol`, `--max-spacing-clusters`), (2) design profiles (`profiles/*.profile.json`) for selector overrides. The engine never imports client-specific code.

**B3 — Config schema:** Profile JSON schema (documented in README):
```json
{
  "sectionSelector": "string | null",
  "headingSelector": "string | null",
  "columnContainerSelector": "string | null",
  "spacingScale": "[number] | null",
  "thresholds": { "tol": "int", "colRatio": "float", "colFloor": "int" },
  "mobileBreakpoints": "[int] | null"
}
```
All fields optional — zero-config default works without any profile. `ev-core30.profile.json` ships as the example. Schema is enforced by `load_profile()` which reads only known keys.

**B4 — 2nd-instance verdict:** PASS. The non-EV "Acme SaaS" design system (Georgia serif, dark nav, gradient hero, flexbox feature cards, 2-col pricing grid, footer) was audited zero-config with no profile. The engine correctly detected sections, flagged A-drift, A2-mixed-alignment, E-overlap, F-spacing, and mobile C-overflow — identical check coverage to the EV design. The clean variant produced zero false positives. This is a genuinely different design system (different font family, layout patterns, color scheme, component structure), not a reskinned EV template.

**B5 — Safety/quality rules:**
- Exit-code contract: 0 = pass, 1 = FAIL findings, 2 = run error. Any pipeline gates on exit code.
- Severity tiering: `fail` (blocks publish) vs `warn` (advisory). Only check A (left-edge drift) is fail-severity; A2/B/C/D/E/F are warn. This prevents false-positive blocks from heuristic checks while guaranteeing the proven drift check hard-blocks.
- De-dup cap: max 25 findings per check type (prevents report spam on badly broken pages).
- Overlap exclusion: position:absolute/fixed elements excluded from check E (prevents false positives on intentional decorative overlays).
- Substantial-paragraph threshold: A2 only fires on paragraphs >55px height (prevents false positives on short captions or lead-ins).
- `--selftest` is the regression gate: 21 assertions across 5 fixtures, 3 design systems, 2 viewports. Any check regression fails the selftest.

**B6 — Skill-candidacy verdict:** NOT YET a registered skill. The tool is a standalone script in `repos/ai-agency-core/scripts/`. It becomes a registered skill after Wave 3 (factory-wide gate wiring) when it's integrated into the review-gate as an auto-invoked check. At that point it should be registered in `skills/` with a proper skill manifest. For now it's a callable tool, not a skill.

### Drift sweep

- **Handoff status:** `handoff-2026-07-05-rvqc-program.md` flipped `ready` → `wave-1-done` + `updated: 2026-07-06`. Verified on disk.
- **Event-log row:** Appended, verified present at tail of `_event-log.md`.
- **Spec file:** `spec-rendered-visual-qc-program.md` status still `draft` — correct, the spec covers all 3 waves; it flips to `active`/`consumed` when the full program ships (Wave 3).
- **Selftest output files:** `visual-qc-out/` and `proof-ev-zeroconfig/` and `proof-nonev-zeroconfig/` are local scratch (gitignored area or uncommitted). Not committed — correct, these are transient test artifacts.
- **Relay files:** `~/workspace/_relay-producer-rvqc.md` and `~/workspace/_relay-reviewer-rvqc.md` are scratch at workspace root (no repo). Not committed — correct per convention.
- **Orphaned state:** No dangling handoff-tracker references found. No YAML parse issues in touched files.
- **Active-chats-tracker:** Not updated by this session (tracker passes are operator/orchestrator responsibility, not producer). No stale row to clean.

### What's next

- **Wave 2:** Layer 2 perceptual vision reviewer (advisory, claude-sonnet-4-6 + rubric)
- **Wave 3:** Factory-wide gate wiring (canonical DoD + review-gate registration)
