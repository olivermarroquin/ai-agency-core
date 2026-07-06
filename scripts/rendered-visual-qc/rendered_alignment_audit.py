#!/usr/bin/env python3
"""
rendered_alignment_audit.py — headless-render visual QC for built HTML pages.

Catches the rendered-layout defect classes that source-level review (grep / word
similarity) structurally cannot see, because they only appear once the CSS is laid
out by a real browser:

  A. LEFT-EDGE DRIFT (the recurring EV Wave-2/3 bug) — in a section whose heading is
     left-aligned, a body block (p / ul / ol) that does NOT share the heading's left
     edge. This is the "intro + bullets are left-aligned but the trailing paragraph
     is centered/indented" defect. Root cause is usually a generic `margin: 0 auto`
     rule catching paragraphs that a more specific left rule missed.
  B. EMPTY / UNDERFILLED COLUMN — a multi-column (grid/flex) section where one
     column's rendered height is a small fraction of its tallest sibling (the
     "Six signs / Quick Reference" underfilled left column).
  C. HORIZONTAL OVERFLOW — a visible block extending past the viewport edge.
  D. BROKEN IMAGE — <img> that failed to load (naturalWidth==0). Network-dependent;
     only runs with --check-images.

Client- and page-agnostic. Renders locally (no network needed for A/B/C). Works on
WordPress-fragment HTML (the `<!-- wp:html -->` drafts) or full HTML documents.

Usage:
  python3 rendered_alignment_audit.py <file-or-glob> [<more>...] \
      [--viewport 1280x2200] [--tol 14] [--col-ratio 0.4] [--col-floor 140] \
      [--out ./visual-qc-out] [--check-images] [--json report.json] [--quiet]

Exit codes: 0 = every page PASS, 1 = one or more FAIL findings, 2 = run error.
"""
import sys, os, glob, json, argparse, re

CHECK_JS = r"""
(args) => {
  const tol = args.tol, colRatio = args.colRatio, colFloor = args.colFloor,
        colMinWidth = args.colMinWidth, checkImages = args.checkImages, vw = window.innerWidth;
  const findings = [];
  const snip = (el) => (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 90);
  const cs = (el) => window.getComputedStyle(el);
  const sections = Array.from(document.querySelectorAll("section, .evp-section"));
  const scope = sections.length ? sections : [document.body];

  scope.forEach((section, si) => {
    // --- A. left-edge drift within left-headed blocks ---
    const headings = Array.from(section.querySelectorAll("h2, h3"));
    headings.forEach((h) => {
      const hIsLeft = cs(h).textAlign === "left" ||
                      h.classList.contains("evp-heading-left");
      if (!hIsLeft) return;                         // centered sections are intentional
      const container = h.parentElement;
      if (!container) return;
      const hLeft = h.getBoundingClientRect().left;
      Array.from(container.children).forEach((el) => {
        if (!["P", "UL", "OL"].includes(el.tagName)) return;
        const txt = snip(el);
        if (!txt) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        const delta = Math.round(r.left - hLeft);
        if (Math.abs(delta) > tol) {
          findings.push({
            check: "A-left-edge-drift", severity: "fail",
            section: snip(h),
            detail: `heading left=${Math.round(hLeft)}px, block left=${Math.round(r.left)}px (Δ${delta}px)`,
            element: `<${el.tagName.toLowerCase()}> "${txt}"`
          });
        }
      });
    });

    // --- B. empty / underfilled column ---
    // Measure each column's CONTENT extent (top of first child to bottom of last),
    // not the column box: grid/flex default `align-items:stretch` forces column
    // boxes to equal height, which would hide an underfilled column.
    // Only TRUE 2-column split layouts (main text + sidebar). Card/tile grids
    // (3+ items) legitimately vary in height and must NOT be flagged — that was
    // the false-positive flood. A genuine "empty column" is a 2-track section
    // where one side is nearly empty.
    const grids = Array.from(section.querySelectorAll("*")).filter((el) => {
      const d = cs(el).display;
      return (d === "grid" || d === "flex") &&
             Array.from(el.children).filter(c => c.getBoundingClientRect().height > 0).length === 2;
    });
    grids.forEach((g) => {
      const cols = Array.from(g.children).map((c) => {
        const kids = Array.from(c.children)
          .map(k => k.getBoundingClientRect()).filter(r => r.height > 0);
        const rect = c.getBoundingClientRect();
        let h = rect.height;
        if (kids.length) h = Math.max(...kids.map(r => r.bottom)) - Math.min(...kids.map(r => r.top));
        return { el: c, h, w: rect.width, x: rect.left, t: snip(c) };
      }).filter(c => c.t.length > 0);
      if (cols.length !== 2) return;
      // must be SIDE-BY-SIDE columns, not a vertical stack (title-above-body cards
      // share a left edge and would otherwise be misread as an empty column).
      const lr = cols.slice().sort((a, b) => a.x - b.x);
      if (lr[1].x < lr[0].x + lr[0].w - 20) return;
      const maxH = Math.max(...cols.map(c => c.h));
      const minC = cols.reduce((a, b) => (a.h < b.h ? a : b));
      if (minC.w < colMinWidth) return;   // narrow = icon/decoration, not a content column
      if (maxH >= colFloor && minC.h < colRatio * maxH) {
        findings.push({
          check: "B-underfilled-column", severity: "warn",
          section: snip(g.closest("section") ? g.closest("section").querySelector("h2,h3") || g : g),
          detail: `column content ${Math.round(minC.h)}px vs tallest ${Math.round(maxH)}px (${Math.round(100*minC.h/maxH)}%)`,
          element: `short column: "${minC.t}"`
        });
      }
    });
  });

  // --- C. horizontal overflow (document-wide, visible blocks) ---
  Array.from(document.querySelectorAll("section *")).forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (cs(el).position === "fixed") return;
    if (r.right > vw + 2 || r.left < -2) {
      const txt = snip(el);
      if (txt) findings.push({
        check: "C-horizontal-overflow", severity: "warn",
        section: "", detail: `left=${Math.round(r.left)} right=${Math.round(r.right)} (viewport ${vw})`,
        element: `<${el.tagName.toLowerCase()}> "${txt}"`
      });
    }
  });

  // --- D. broken images (opt-in) ---
  if (checkImages) {
    Array.from(document.images).forEach((img) => {
      if (img.complete && img.naturalWidth === 0) {
        findings.push({
          check: "D-broken-image", severity: "warn",
          section: "", detail: img.currentSrc || img.src || "(no src)",
          element: `<img alt="${(img.alt||"").slice(0,60)}">`
        });
      }
    });
  }

  // de-dup identical overflow spam (keep first 8 per check)
  const seen = {}, out = [];
  for (const f of findings) {
    seen[f.check] = (seen[f.check] || 0) + 1;
    if (seen[f.check] <= 25) out.push(f);
  }
  return out;
}
"""

SELFTEST_BUG = """<style>
.evp-section-inner{max-width:1200px;margin:0 auto;padding:2rem}
.evp-section-inner>h2.evp-heading-left{text-align:left}
.evp-section-inner>h2.evp-heading-left+p{text-align:left;max-width:820px;margin-left:0;margin-right:0}
.evp-section-inner>p{max-width:900px;margin-left:auto;margin-right:auto}   /* BUG: centers trailing p */
.evp-2col{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
</style>
<section><div class="evp-section-inner">
  <h2 class="evp-heading-left">Left-headed section</h2>
  <p>Intro paragraph is left-aligned and shares the heading left edge.</p>
  <ul><li>bullet one</li><li>bullet two</li></ul>
  <p>Trailing paragraph is CENTERED by the generic rule and drifts right of the heading.</p>
</div></section>
<section><div class="evp-section-inner"><div class="evp-2col">
  <div><h3>Short col</h3></div>
  <div><h3>Tall col</h3><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p></div>
</div></div></section>"""

SELFTEST_CLEAN = """<style>
.evp-section-inner{max-width:1200px;margin:0 auto;padding:2rem}
.evp-section-inner>h2.evp-heading-left{text-align:left}
.evp-section-inner>h2.evp-heading-left~p,.evp-section-inner>h2.evp-heading-left~ul{text-align:left;max-width:820px;margin-left:0;margin-right:0}
.evp-2col{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
</style>
<section><div class="evp-section-inner">
  <h2 class="evp-heading-left">Left-headed section</h2>
  <p>Intro paragraph is left-aligned and shares the heading left edge.</p>
  <ul><li>bullet one</li><li>bullet two</li></ul>
  <p>Trailing paragraph is also left-aligned and shares the same left edge.</p>
</div></section>
<section><div class="evp-section-inner"><div class="evp-2col">
  <div><h3>Balanced A</h3><p>line</p><p>line</p><p>line</p></div>
  <div><h3>Balanced B</h3><p>line</p><p>line</p><p>line</p></div>
</div></div></section>"""

def run_selftest(a):
    os.makedirs(a.out, exist_ok=True)
    bug = os.path.join(a.out, "_selftest-bug.html")
    clean = os.path.join(a.out, "_selftest-clean.html")
    open(bug, "w").write(SELFTEST_BUG)
    open(clean, "w").write(SELFTEST_CLEAN)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[selftest] playwright not importable: {e}"); return 2
    def audit(fp):
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 1280, "height": 2200})
            pg.set_content(load_html(fp), wait_until="domcontentloaded", timeout=15000)
            pg.wait_for_timeout(300)
            f = pg.evaluate(CHECK_JS, {"tol": a.tol, "colRatio": a.col_ratio,
                                       "colFloor": a.col_floor, "colMinWidth": a.col_min_width,
                                       "checkImages": False})
            b.close(); return f
    bugf, cleanf = audit(bug), audit(clean)
    bug_drift = any(f["check"] == "A-left-edge-drift" for f in bugf)
    bug_col = any(f["check"] == "B-underfilled-column" for f in bugf)
    clean_drift = any(f["check"] == "A-left-edge-drift" for f in cleanf)
    ok = bug_drift and bug_col and not clean_drift
    print(f"[selftest] bug fixture: A-drift={'caught' if bug_drift else 'MISSED'}, "
          f"B-underfilled={'caught' if bug_col else 'MISSED'}")
    print(f"[selftest] clean fixture: A-drift={'false-positive!' if clean_drift else 'clean'}")
    print(f"[selftest] {'PASS — auditor works' if ok else 'FAIL — auditor logic broken'}")
    return 0 if ok else 1


def expand(paths):
    out = []
    for p in paths:
        m = glob.glob(p, recursive=True)
        out.extend(m if m else [p])
    return sorted(set(out))

def load_html(fp):
    with open(fp, encoding="utf-8", errors="ignore") as fh:
        txt = fh.read()
    if "<html" not in txt.lower():
        txt = ('<!doctype html><html><head><meta charset="utf-8">'
               '<style>body{margin:0}</style></head><body>' + txt + "</body></html>")
    return txt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--selftest", action="store_true",
                    help="render built-in bug/clean fixtures and assert the checks fire correctly")
    ap.add_argument("--viewport", default="1280x2200")
    ap.add_argument("--tol", type=int, default=14, help="left-edge px tolerance")
    ap.add_argument("--col-ratio", type=float, default=0.4, dest="col_ratio")
    ap.add_argument("--col-floor", type=int, default=140, dest="col_floor")
    ap.add_argument("--col-min-width", type=int, default=140, dest="col_min_width",
                    help="min px width for a grid cell to count as a content column (skips icons)")
    ap.add_argument("--out", default="./visual-qc-out")
    ap.add_argument("--check-images", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return run_selftest(a)
    if not a.paths:
        ap.error("provide HTML paths/globs, or use --selftest")

    files = [f for f in expand(a.paths) if f.lower().endswith((".html", ".htm"))]
    if not files:
        print("No HTML files matched.", file=sys.stderr); return 2
    vw, vh = (int(x) for x in a.viewport.lower().split("x"))
    os.makedirs(a.out, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[error] playwright not importable: {e}", file=sys.stderr); return 2

    report, total_fail = [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": vw, "height": vh},
                                device_scale_factor=1)
        for fp in files:
            name = os.path.basename(os.path.dirname(fp)) or os.path.basename(fp)
            entry = {"file": fp, "name": name, "findings": [], "error": None}
            try:
                page.set_content(load_html(fp), wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(500)  # settle layout/fonts
                findings = page.evaluate(CHECK_JS, {
                    "tol": a.tol, "colRatio": a.col_ratio, "colFloor": a.col_floor,
                    "colMinWidth": a.col_min_width, "checkImages": a.check_images})
                shot = os.path.join(a.out, name + ".png")
                page.screenshot(path=shot, full_page=True)
                entry["findings"] = findings
                entry["screenshot"] = shot
            except Exception as e:
                entry["error"] = str(e)
            report.append(entry)

            fails = [f for f in entry["findings"] if f["severity"] == "fail"]
            warns = [f for f in entry["findings"] if f["severity"] == "warn"]
            if fails or entry["error"]:
                total_fail += 1
            if not a.quiet:
                status = "ERROR" if entry["error"] else ("FAIL" if fails else ("WARN" if warns else "PASS"))
                print(f"\n{'█' if (fails or entry['error']) else '·'} {name} … {status}")
                if entry["error"]:
                    print(f"    run-error: {entry['error']}")
                for f in entry["findings"]:
                    tag = "FAIL" if f["severity"] == "fail" else "warn"
                    sec = f' | section: "{f["section"]}"' if f.get("section") else ""
                    print(f"    [{tag}] {f['check']}{sec}\n        {f['detail']}\n        {f['element']}")
        browser.close()

    n = len(report)
    npass = sum(1 for r in report if not r["error"] and not any(f["severity"]=="fail" for f in r["findings"]))
    print(f"\n{'='*66}\nSUMMARY: {n} page(s) — {npass} clean, {total_fail} with FAIL/error. "
          f"screenshots → {a.out}/")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"pages": report, "viewport": a.viewport, "tol": a.tol}, fh, indent=2)
        print(f"JSON report → {a.json}")
    return 1 if total_fail else 0

if __name__ == "__main__":
    sys.exit(main())
