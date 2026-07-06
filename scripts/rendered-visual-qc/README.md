# rendered-visual-qc — design-agnostic rendered alignment auditor

Deterministic **rendered** visual QC for built HTML pages. Catches the layout defect
classes that source-level review (grep / word-similarity) structurally cannot see,
because they only appear once a real browser lays out the CSS. Built after the EV
Wave-2/3 builds shipped the same misalignment three runs in a row and only a human
eyeball caught it each time (CR-207, LU-Q6/Q8, LU-B5).

**Design-agnostic:** detects structure by semantic role + computed style + geometry —
no class-name coupling. Works on any reasonably-semantic HTML out of the box (zero-config).
Optional design profiles override selectors for exotic layouts.

## What it checks (per page, per viewport)

| Check | Severity | What it catches |
|---|---|---|
| **A — left-edge drift** | FAIL | In a section whose heading is left-aligned, a body block (`p`/`ul`/`ol`/`div`) whose left edge doesn't match the heading's. The recurring "intro + bullets are left, but the trailing paragraph is centered/indented" bug. |
| **A2 — mixed alignment** | warn | Within any section (including centered-headed ones that check A skips), substantial body paragraphs (height >55px) don't all share one `text-align`. Catches the EV Tysons "seasonal patterns" bug: a centered section where one paragraph is left-aligned among centered siblings. Normalizes `start`→`left`, `end`→`right` before comparing. |
| **B — underfilled column** | warn | A 2-column (grid/flex) section where one column's rendered height is < `--col-ratio` of its tallest sibling. |
| **C — horizontal overflow** | warn | A visible block extending past the viewport edge. |
| **D — broken image** | warn | `<img>` that failed to load (`naturalWidth==0`). Network-dependent — only with `--check-images`. |
| **E — element overlap** | warn | Two normal-flow visible elements (both `position:static/relative`) whose bounding rects overlap by >4px in both axes. Excludes `position:absolute/fixed` (intentional decorative overlays). |
| **F — spacing-scale inconsistency** | warn | Vertical gaps between sections cluster into >3 distinct values (8px tolerance per cluster). Pages should follow a consistent spacing scale, not random gaps. Requires ≥4 detected sections. |
| **G — mobile reflow** | (re-runs A–F) | Re-runs all checks (A, A2, B–F) at mobile viewports (380px + 768px by default). A finding at any viewport counts toward the exit code. Screenshots captured per viewport. |

## How detection works (design-agnostic)

- **Section:** `<section>` or `[role="region"]`; fallback: parents of heading elements.
- **Left-headed section:** heading with `computedStyle.textAlign === 'left'` or `'start'`.
- **Column:** grid/flex container with exactly 2 visible children side-by-side.
- **Content wrapper:** follows single-visible-child chains (e.g., `<section><div class="inner">...`) to find the actual content container.

No `.evp-section`, `.evp-heading-left`, or any class-name dependency. Those can be
used via an optional design profile for layouts that need them.

## Setup (host-side — needs a browser)

```bash
pip install playwright --break-system-packages
python3 -m playwright install chromium-headless-shell     # ~110MB, one time
```

> Note: this must run where a browser can be installed — the host machine / Claude
> Code, not the Cowork sandbox (its 45s command cap can't finish the browser
> download).

## Verify the tool works

```bash
python3 rendered_alignment_audit.py --selftest
# Tests 5 fixtures (bug, clean, non-EV bug, non-EV clean, profile) across
# desktop + mobile viewports. Asserts:
#   bug    → A-drift, A2-mixed-alignment, B-underfilled, E-overlap, F-spacing caught; C-overflow at mobile
#   clean  → no false positives at any viewport (including A2)
#   nonev  → same checks fire on a completely different design system (generality proof)
#   profile → EV profile overrides detection on .evp-* class-based HTML (A + A2)
# Prints "PASS — all checks correct" (21 assertions)
```

## Run it on built pages

```bash
python3 rendered_alignment_audit.py \
  ~/workspace/second-brain/.../core-30/*/draft-v*-WP-WRAPPED.html \
  --out ./visual-qc-out --json ./visual-qc-out/report.json
```

- Exit code **0** = every page clean; **1** = at least one FAIL; **2** = run error.
- Screenshots per page per viewport (desktop + mobile) in the output directory.
- Output directory is **auto-timestamped** by default (`visual-qc-out-YYYYMMDD-HHMMSS/`)
  to prevent collision between parallel runs. Override with explicit `--out <dir>`.
- Machine-readable `--json` report (per-page, per-viewport, per-finding).

## Design profiles

For layouts that use non-semantic selectors (e.g., `.evp-section` instead of
`<section>`), create a profile JSON:

```json
{
  "sectionSelector": ".evp-section",
  "headingSelector": "h2.evp-heading-left, h3.evp-heading-left",
  "columnContainerSelector": null,
  "spacingScale": [32, 48, 64],
  "thresholds": { "tol": 14, "colRatio": 0.4 },
  "mobileBreakpoints": [380, 768]
}
```

```bash
python3 rendered_alignment_audit.py page.html --profile profiles/ev-core30.profile.json
```

Profiles live in `profiles/`. The zero-config default (no `--profile`) works on any
semantic HTML. Profiles are opt-in overrides for exotic layouts only.

### Tuning

| Flag | Default | Meaning |
|---|---|---|
| `--viewport WxH` | `1280x2200` | Desktop render size |
| `--tol N` | `14` | Left-edge px tolerance for check A |
| `--col-ratio F` | `0.4` | Column-height ratio that trips check B |
| `--col-floor N` | `140` | Ignore B on sections shorter than this |
| `--col-min-width N` | `140` | Min px width for a grid cell to count as a content column |
| `--check-images` | off | Enable check D (needs network for live images) |
| `--mobile-breakpoints` | `380,768` | Comma-separated mobile viewport widths for check G |
| `--spacing-cluster-tol` | `8` | Px tolerance for spacing-gap clustering (check F) |
| `--max-spacing-clusters` | `3` | Max distinct spacing clusters before flagging (check F) |
| `--profile PATH` | (none) | Design profile JSON for selector/threshold overrides |

## Where this fits

This is the mechanized form of **criterion G** (rendered visual QC) in
`second-brain/_meta/handoffs/client-growth-sprint/build-acceptance-criteria-ev-wave1.md`.
The build-producer runs it before declaring a page done; the independent reviewer
re-runs it. A non-zero exit blocks publish. See LU-Q8 in
`second-brain/05_shared-intelligence/_levelup-register-differentiation-pipeline.md`.

**Layer 1** of the Rendered Visual-QC program (deterministic geometry engine).
Layer 2 (perceptual vision reviewer) adds aesthetic judgment on top.
See `second-brain/_meta/handoffs/rendered-visual-qc/spec-rendered-visual-qc-program.md`.
