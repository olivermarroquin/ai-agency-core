# rendered-visual-qc — rendered alignment auditor

Deterministic **rendered** visual QC for built HTML pages. Catches the layout defect
classes that source-level review (grep / word-similarity) structurally cannot see,
because they only appear once a real browser lays out the CSS. Built after the EV
Wave-2/3 builds shipped the same misalignment three runs in a row and only a human
eyeball caught it each time (CR-207, LU-Q6/Q8, LU-B5).

## What it checks (per page, per section)

| Check | Severity | What it catches |
|---|---|---|
| **A — left-edge drift** | FAIL | In a section whose heading is left-aligned, a body block (`p`/`ul`/`ol`) whose left edge doesn't match the heading's. This is the recurring "intro + bullets are left, but the trailing paragraph is centered/indented" bug (a generic `margin:0 auto` rule catching paragraphs a specific left rule missed). |
| **B — underfilled column** | warn | A multi-column (grid/flex) section where one column's rendered height is < `--col-ratio` of its tallest sibling (the "Six signs / Quick Reference" empty-left-column). |
| **C — horizontal overflow** | warn | A visible block extending past the viewport edge. |
| **D — broken image** | warn | `<img>` that failed to load (`naturalWidth==0`). Network-dependent — only with `--check-images`. |

Client- and page-agnostic. Renders locally; **no network needed** for A/B/C (so it
works on the vault's WP-fragment drafts without touching the live server).

## Setup (host-side — needs a browser)

```bash
pip install playwright --break-system-packages
python3 -m playwright install chromium-headless-shell     # ~110MB, one time
```

> Note: this must run where a browser can be installed — the host machine / Claude
> Code, not the Cowork sandbox (its 45s command cap can't finish the browser
> download). That's fine: builds publish host-side, so the QC runs in the same place.

## Verify the tool works (5 seconds)

```bash
python3 rendered_alignment_audit.py --selftest
# renders a known-bug fixture + a known-clean fixture and asserts:
#   bug   → A-drift caught + B-underfilled caught
#   clean → no A-drift (no false positive)
# prints "PASS — auditor works"
```

## Run it on built pages

```bash
python3 rendered_alignment_audit.py \
  ~/workspace/second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/*/draft-v*-WP-WRAPPED.html \
  --out ./visual-qc-out --json ./visual-qc-out/report.json
```

- Exit code **0** = every page clean; **1** = at least one FAIL; **2** = run error.
  Use the exit code to gate a build ("no publish while exit != 0").
- Writes a full-page screenshot per page to `--out` and a machine-readable
  `--json` report (per-page, per-finding).

### Tuning

| Flag | Default | Meaning |
|---|---|---|
| `--viewport WxH` | `1280x2200` | render size |
| `--tol N` | `14` | left-edge px tolerance for check A |
| `--col-ratio F` | `0.4` | column-height ratio that trips check B |
| `--col-floor N` | `140` | ignore B on sections shorter than this |
| `--check-images` | off | enable check D (needs network for live images) |

## Where this fits

This is the mechanized form of **criterion G** (rendered visual QC) in
`second-brain/_meta/handoffs/client-growth-sprint/build-acceptance-criteria-ev-wave1.md`.
The build-producer runs it before declaring a page done; the independent reviewer
re-runs it. A non-zero exit blocks publish. See LU-Q8 in
`second-brain/05_shared-intelligence/_levelup-register-differentiation-pipeline.md`.
