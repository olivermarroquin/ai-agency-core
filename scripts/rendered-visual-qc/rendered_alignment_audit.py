#!/usr/bin/env python3
"""
rendered_alignment_audit.py — design-agnostic headless-render visual QC for HTML pages.

Catches rendered-layout defect classes that source-level review structurally cannot see,
because they only appear once the CSS is laid out by a real browser:

  A. LEFT-EDGE DRIFT — in a section whose heading is left-aligned, a body block whose
     left edge doesn't match the heading's. The recurring "intro is left, trailing
     paragraph is centered" defect.
  B. EMPTY / UNDERFILLED COLUMN — a 2-column section where one side is nearly empty.
  C. HORIZONTAL OVERFLOW — a visible block extending past the viewport edge.
  D. BROKEN IMAGE — <img> that failed to load (naturalWidth==0). Opt-in: --check-images.
  E. ELEMENT OVERLAP — two normal-flow visible elements occupying the same pixels.
     Excludes position:absolute/fixed (intentional decorative overlays).
  F. SPACING-SCALE CONSISTENCY — vertical gaps between sections don't cluster to a small
     set (<=3 clusters by default, 8px tolerance).
  G. MOBILE REFLOW — re-runs A-F at mobile viewports (380px + 768px by default).

Design-agnostic: detects structure by semantic role + computed style + geometry.
Optional design profiles (<design>.profile.json) override selectors for exotic layouts.
Zero-config default works on any reasonably-semantic HTML.

Usage:
  python3 rendered_alignment_audit.py <file-or-glob> [<more>...] \\
      [--viewport 1280x2200] [--tol 14] [--col-ratio 0.4] [--col-floor 140] \\
      [--out ./visual-qc-out] [--check-images] [--json report.json] [--quiet] \\
      [--profile profiles/ev-core30.profile.json] [--mobile-breakpoints 380,768]

Exit codes: 0 = every page PASS, 1 = one or more FAIL findings, 2 = run error.
"""
import sys, os, glob, json, argparse, re, time

# ---------------------------------------------------------------------------
# CHECK_JS — the browser-evaluated geometry engine (runs per viewport)
# ---------------------------------------------------------------------------
CHECK_JS = r"""
(args) => {
  const { tol, colRatio, colFloor, colMinWidth, checkImages, viewport,
          sectionSelector, headingSelector, columnContainerSelector,
          spacingClusterTol, maxSpacingClusters } = args;
  const findings = [];
  const vw = window.innerWidth;
  const cs = (el) => window.getComputedStyle(el);
  const snip = (el) => (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 90);

  function isNormalFlow(el) {
    const p = cs(el).position;
    return p === "static" || p === "relative" || p === "";
  }
  function isVisibleBlock(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const d = cs(el).display;
    return d !== "inline" && d !== "none" && d !== "contents";
  }
  // Walk single-visible-child chain to find the innermost content wrapper
  function contentWrapper(el) {
    let cur = el, depth = 0;
    while (depth < 10) {
      const vis = Array.from(cur.children).filter(c => {
        const r = c.getBoundingClientRect();
        return r.height > 0 && r.width > 0;
      });
      if (vis.length !== 1) break;
      cur = vis[0];
      depth++;
    }
    return cur;
  }

  // --- Section detection ---
  let sections;
  if (sectionSelector) {
    sections = Array.from(document.querySelectorAll(sectionSelector));
  } else {
    sections = Array.from(document.querySelectorAll("section, [role='region']"));
    if (!sections.length) {
      // Fallback: parents of headings as implicit sections
      const heads = document.querySelectorAll("h1, h2, h3, h4, h5, h6");
      const pSet = new Set();
      heads.forEach(h => {
        if (h.parentElement && h.parentElement !== document.body) pSet.add(h.parentElement);
      });
      sections = Array.from(pSet);
    }
  }
  const scope = sections.length ? sections : [document.body];

  scope.forEach((section) => {
    const wrapper = contentWrapper(section);

    // --- A. Left-edge drift in left-headed blocks ---
    const hSel = headingSelector || "h1, h2, h3, h4, h5, h6";
    const headings = Array.from(wrapper.querySelectorAll(hSel));
    headings.forEach((h) => {
      const hTA = cs(h).textAlign;
      if (hTA !== "left" && hTA !== "start") return;
      const container = h.parentElement;
      if (!container) return;
      const hLeft = h.getBoundingClientRect().left;
      Array.from(container.children).forEach((el) => {
        if (el === h) return;
        const tag = el.tagName;
        if (!["P","UL","OL","DL","BLOCKQUOTE","TABLE","DIV"].includes(tag)) return;
        const txt = snip(el);
        if (!txt) return;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;
        const delta = Math.round(r.left - hLeft);
        if (Math.abs(delta) > tol) {
          findings.push({
            check: "A-left-edge-drift", severity: "fail", viewport,
            section: snip(h),
            detail: "heading left=" + Math.round(hLeft) + "px, block left=" + Math.round(r.left) + "px (d" + delta + "px)",
            element: "<" + tag.toLowerCase() + '> "' + txt + '"'
          });
        }
      });
    });

    // --- B. Underfilled column ---
    // Include wrapper itself (contentWrapper may land ON the grid container)
    let grids;
    if (columnContainerSelector) {
      grids = Array.from(wrapper.querySelectorAll(columnContainerSelector));
    } else {
      const candidates = [wrapper, ...Array.from(wrapper.querySelectorAll("*"))];
      grids = candidates.filter((el) => {
        const d = cs(el).display;
        return (d === "grid" || d === "flex") &&
               Array.from(el.children).filter(c => c.getBoundingClientRect().height > 0).length === 2;
      });
    }
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
      const lr = cols.slice().sort((a, b) => a.x - b.x);
      if (lr[1].x < lr[0].x + lr[0].w - 20) return;  // stacked, not side-by-side
      const maxH = Math.max(...cols.map(c => c.h));
      const minC = cols.reduce((a, b) => (a.h < b.h ? a : b));
      if (minC.w < colMinWidth) return;
      if (maxH >= colFloor && minC.h < colRatio * maxH) {
        findings.push({
          check: "B-underfilled-column", severity: "warn", viewport,
          section: snip(g.closest("section,[role='region']")
            ? (g.closest("section,[role='region']").querySelector("h2,h3") || g) : g),
          detail: "column content " + Math.round(minC.h) + "px vs tallest " + Math.round(maxH) + "px (" + Math.round(100*minC.h/maxH) + "%)",
          element: 'short column: "' + minC.t + '"'
        });
      }
    });

    // --- E. Element overlap (normal-flow siblings only, per C1) ---
    const wKids = Array.from(wrapper.children)
      .filter(el => isVisibleBlock(el) && isNormalFlow(el));
    let oCount = 0;
    for (let i = 0; i < wKids.length && oCount < 15; i++) {
      const ri = wKids[i].getBoundingClientRect();
      for (let j = i + 1; j < wKids.length && oCount < 15; j++) {
        const rj = wKids[j].getBoundingClientRect();
        const ox = Math.min(ri.right, rj.right) - Math.max(ri.left, rj.left);
        const oy = Math.min(ri.bottom, rj.bottom) - Math.max(ri.top, rj.top);
        if (ox > 4 && oy > 4) {
          oCount++;
          findings.push({
            check: "E-element-overlap", severity: "warn", viewport,
            section: snip(section.querySelector("h2,h3") || section),
            detail: Math.round(ox) + "px x " + Math.round(oy) + "px overlap",
            element: "<" + wKids[i].tagName.toLowerCase() + "> overlaps <" + wKids[j].tagName.toLowerCase() + ">"
          });
        }
      }
    }
  });

  // --- C. Horizontal overflow (all elements within detected scope) ---
  const overflowEls = new Set();
  scope.forEach(s => s.querySelectorAll("*").forEach(el => overflowEls.add(el)));
  if (!overflowEls.size) document.body.querySelectorAll("*").forEach(el => overflowEls.add(el));
  let cCount = 0;
  overflowEls.forEach((el) => {
    if (cCount >= 25) return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    if (cs(el).position === "fixed") return;
    if (r.right > vw + 2 || r.left < -2) {
      const txt = snip(el);
      if (txt && txt.length > 3) {
        cCount++;
        findings.push({
          check: "C-horizontal-overflow", severity: "warn", viewport,
          section: "",
          detail: "left=" + Math.round(r.left) + " right=" + Math.round(r.right) + " (viewport " + vw + ")",
          element: "<" + el.tagName.toLowerCase() + '> "' + txt + '"'
        });
      }
    }
  });

  // --- D. Broken images (opt-in) ---
  if (checkImages) {
    Array.from(document.images).forEach((img) => {
      if (img.complete && img.naturalWidth === 0) {
        findings.push({
          check: "D-broken-image", severity: "warn", viewport,
          section: "", detail: img.currentSrc || img.src || "(no src)",
          element: '<img alt="' + (img.alt||"").slice(0,60) + '">'
        });
      }
    });
  }

  // --- F. Spacing-scale consistency (across detected sections) ---
  if (scope.length >= 4 && scope[0] !== document.body) {
    const sorted = scope.slice()
      .map(s => ({ el: s, rect: s.getBoundingClientRect() }))
      .sort((a, b) => a.rect.top - b.rect.top);
    const gaps = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const gap = Math.round(sorted[i+1].rect.top - sorted[i].rect.bottom);
      if (gap >= 0) gaps.push(gap);
    }
    if (gaps.length >= 3) {
      const sg = gaps.slice().sort((a,b) => a - b);
      const clusters = [[sg[0]]];
      for (let k = 1; k < sg.length; k++) {
        const last = clusters[clusters.length - 1];
        const center = last.reduce((a,b) => a+b, 0) / last.length;
        if (Math.abs(sg[k] - center) <= spacingClusterTol) {
          last.push(sg[k]);
        } else {
          clusters.push([sg[k]]);
        }
      }
      if (clusters.length > maxSpacingClusters) {
        const summary = clusters.map(c =>
          "~" + Math.round(c.reduce((a,b)=>a+b,0)/c.length) + "px(x" + c.length + ")"
        ).join(", ");
        findings.push({
          check: "F-spacing-inconsistency", severity: "warn", viewport,
          section: "",
          detail: clusters.length + " gap clusters (max " + maxSpacingClusters + "): " + summary,
          element: gaps.length + " gaps between " + sorted.length + " sections"
        });
      }
    }
  }

  // de-dup identical overflow spam (keep first 25 per check)
  const seen = {}, out = [];
  for (const f of findings) {
    seen[f.check] = (seen[f.check] || 0) + 1;
    if (seen[f.check] <= 25) out.push(f);
  }
  return out;
}
"""

# ---------------------------------------------------------------------------
# Selftest fixtures
# ---------------------------------------------------------------------------

# Bug fixture: triggers A (drift), B (underfilled col), E (overlap), F (spacing)
# Also has a wide element that triggers C at mobile but not desktop
SELFTEST_BUG = """\
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:sans-serif}
.inner{max-width:1200px;margin:0 auto;padding:2rem}
.inner>h2{text-align:left}
.inner>h2+p{text-align:left;max-width:820px;margin-left:0;margin-right:0}
.inner>p{max-width:900px;margin-left:auto;margin-right:auto}  /* BUG: centers trailing p */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
</style></head><body>

<section style="margin-bottom:10px"><div class="inner">
  <h2>Section One — left-headed</h2>
  <p>Intro paragraph shares the heading's left edge correctly.</p>
  <ul><li>bullet one</li><li>bullet two</li></ul>
  <p>Trailing paragraph is CENTERED by the generic rule — drifts right of heading (check A).</p>
</div></section>

<section style="margin-bottom:60px"><div class="inner"><div class="two-col">
  <div><h3>Short col</h3></div>
  <div><h3>Tall col</h3><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p><p>line</p></div>
</div></div></section>

<section style="margin-bottom:15px"><div class="inner">
  <h2>Overlap section</h2>
  <div style="padding:20px;background:#eee;margin-bottom:-40px">Block one with large negative margin</div>
  <div style="padding:20px;background:#ddd">Block two — overlaps block one (check E)</div>
</div></section>

<section style="margin-bottom:90px"><div class="inner">
  <h2>Filler section D</h2>
  <p>Content for spacing measurement.</p>
</div></section>

<section style="margin-bottom:25px"><div class="inner">
  <h2>Filler section E</h2>
  <p>Content for spacing measurement.</p>
</div></section>

<section><div class="inner">
  <h2>Mobile overflow section</h2>
  <div style="width:500px;background:#eee;padding:1rem">This 500px-wide box fits at 1280px but overflows at 380px (check C at mobile / check G).</div>
</div></section>

</body></html>"""

# Clean fixture: passes all checks A-F at desktop and mobile
SELFTEST_CLEAN = """\
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:sans-serif}
.inner{max-width:1200px;margin:0 auto;padding:2rem}
.inner>h2{text-align:left}
.inner>h2~p,.inner>h2~ul{text-align:left;max-width:820px;margin-left:0;margin-right:0}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
section{margin-bottom:48px}
</style></head><body>

<section><div class="inner">
  <h2>Section One — left-headed</h2>
  <p>Intro paragraph shares the heading's left edge.</p>
  <ul><li>bullet one</li><li>bullet two</li></ul>
  <p>Trailing paragraph also shares the heading's left edge — no drift.</p>
</div></section>

<section><div class="inner"><div class="two-col">
  <div><h3>Balanced A</h3><p>line</p><p>line</p><p>line</p></div>
  <div><h3>Balanced B</h3><p>line</p><p>line</p><p>line</p></div>
</div></div></section>

<section><div class="inner">
  <h2>Section Three</h2>
  <div style="padding:20px;background:#eee;margin-bottom:8px">Block one — no overlap</div>
  <div style="padding:20px;background:#ddd">Block two — proper spacing</div>
</div></section>

<section><div class="inner">
  <h2>Section Four</h2>
  <p>Content with consistent 48px inter-section spacing.</p>
</div></section>

<section><div class="inner">
  <h2>Mobile-safe section</h2>
  <div style="max-width:100%;background:#eee;padding:1rem">Responsive box — fits any viewport.</div>
</div></section>

</body></html>"""

# Non-EV design: a completely different design system (semantic HTML, flexbox, no .evp-*)
# Bug version: has drift + overlap. Proves zero-config generality.
SELFTEST_NONEV_BUG = """\
<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
body{margin:0;font-family:'Georgia',serif;color:#222;background:#fafafa}
nav{background:#1a1a2e;color:#fff;padding:1rem 2rem;display:flex;justify-content:space-between}
nav a{color:#e94560;text-decoration:none;margin-left:1.5rem}
.hero{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:4rem 2rem;text-align:center}
.hero h1{font-size:2.5rem;margin-bottom:1rem}
.container{max-width:960px;margin:0 auto;padding:2rem}
.features{display:flex;gap:2rem;flex-wrap:wrap}
.feature-card{flex:1;min-width:280px;background:#fff;border-radius:8px;padding:1.5rem;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.team{display:flex;gap:2rem}
.team-member{flex:1;text-align:center}
.pricing{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
/* BUG: misaligned body text in left-headed section */
section .container h2{text-align:left}
section .container h2+p{text-align:left;margin-left:0}
section .container p.secondary{max-width:600px;margin-left:auto;margin-right:auto} /* drifts right */
</style></head><body>

<nav><span>Acme SaaS</span><div><a href="#">Features</a><a href="#">Pricing</a><a href="#">Team</a></div></nav>

<section class="hero"><h1>Build faster with Acme</h1><p>The modern platform for teams.</p></section>

<section style="margin-bottom:12px"><div class="container">
  <h2>Why Acme</h2>
  <p>Our platform gives you everything you need to ship products quickly.</p>
  <p class="secondary">This paragraph drifts right due to the auto-margin bug — just like the EV defect but on a totally different design (check A).</p>
</div></section>

<section style="margin-bottom:70px"><div class="container">
  <h2>Features</h2>
  <div class="features">
    <div class="feature-card"><h3>Analytics</h3><p>Real-time dashboards and custom reports for your entire team.</p></div>
    <div class="feature-card"><h3>Integrations</h3><p>Connect with 200+ tools your team already uses daily.</p></div>
    <div class="feature-card"><h3>Security</h3><p>SOC 2 compliant with end-to-end encryption and audit logs.</p></div>
  </div>
</div></section>

<section style="margin-bottom:15px"><div class="container">
  <h2>Our Team</h2>
  <div class="team">
    <div class="team-member">
      <img src="placeholder-alice.jpg" alt="Alice — CEO" style="width:120px;height:120px;border-radius:50%;background:#ddd">
      <h3>Alice</h3><p>CEO and Co-founder</p>
    </div>
    <div class="team-member">
      <img src="placeholder-bob.jpg" alt="Bob — CTO" style="width:120px;height:120px;border-radius:50%;background:#ddd">
      <h3>Bob</h3><p>CTO and Co-founder</p>
    </div>
  </div>
</div></section>

<section style="margin-bottom:85px"><div class="container">
  <h2>Overlap demo</h2>
  <div style="padding:1.5rem;background:#fff;border-radius:8px;margin-bottom:-35px;box-shadow:0 2px 8px rgba(0,0,0,.1)">Card one — large negative margin</div>
  <div style="padding:1.5rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)">Card two — overlaps card one (check E)</div>
</div></section>

<section style="margin-bottom:30px"><div class="container">
  <h2>Pricing</h2>
  <div class="pricing">
    <div style="background:#fff;border-radius:8px;padding:2rem;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.1)">
      <h3>Starter</h3><p style="font-size:2rem;font-weight:bold">$29/mo</p><p>For small teams getting started.</p>
    </div>
    <div style="background:#fff;border-radius:8px;padding:2rem;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.1)">
      <h3>Pro</h3><p style="font-size:2rem;font-weight:bold">$99/mo</p><p>For growing teams that need more power and integrations.</p>
    </div>
  </div>
</div></section>

<section><div class="container">
  <h2>Get Started</h2>
  <p>Sign up today and start building with Acme.</p>
  <div style="width:500px;background:#1a1a2e;color:#fff;padding:1rem;border-radius:8px;margin-top:1rem">
    This 500px call-to-action overflows on mobile (check C at 380px).
  </div>
</div></section>

<footer style="background:#1a1a2e;color:#aaa;padding:2rem;text-align:center;margin-top:3rem">
  <p>2026 Acme SaaS Inc. All rights reserved.</p>
</footer>

</body></html>"""

# Non-EV clean: same design system, all bugs fixed
SELFTEST_NONEV_CLEAN = """\
<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
body{margin:0;font-family:'Georgia',serif;color:#222;background:#fafafa}
nav{background:#1a1a2e;color:#fff;padding:1rem 2rem;display:flex;justify-content:space-between}
nav a{color:#e94560;text-decoration:none;margin-left:1.5rem}
.hero{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:4rem 2rem;text-align:center}
.hero h1{font-size:2.5rem;margin-bottom:1rem}
.container{max-width:960px;margin:0 auto;padding:2rem}
.features{display:flex;gap:2rem;flex-wrap:wrap}
.feature-card{flex:1;min-width:280px;background:#fff;border-radius:8px;padding:1.5rem;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.team{display:flex;gap:2rem}
.team-member{flex:1;text-align:center}
.pricing{display:grid;grid-template-columns:1fr 1fr;gap:2rem}
section .container h2{text-align:left}
section .container p{text-align:left;margin-left:0;margin-right:0}
section{margin-bottom:48px}
</style></head><body>

<nav><span>Acme SaaS</span><div><a href="#">Features</a><a href="#">Pricing</a><a href="#">Team</a></div></nav>

<section class="hero"><h1>Build faster with Acme</h1><p>The modern platform for teams.</p></section>

<section><div class="container">
  <h2>Why Acme</h2>
  <p>Our platform gives you everything you need to ship products quickly.</p>
  <p>This paragraph shares the heading's left edge — no drift.</p>
</div></section>

<section><div class="container">
  <h2>Features</h2>
  <div class="features">
    <div class="feature-card"><h3>Analytics</h3><p>Real-time dashboards and custom reports for your entire team.</p></div>
    <div class="feature-card"><h3>Integrations</h3><p>Connect with 200+ tools your team already uses daily.</p></div>
    <div class="feature-card"><h3>Security</h3><p>SOC 2 compliant with end-to-end encryption and audit logs.</p></div>
  </div>
</div></section>

<section><div class="container">
  <h2>Our Team</h2>
  <div class="team">
    <div class="team-member">
      <img alt="Alice — CEO" style="width:120px;height:120px;border-radius:50%;background:#ddd">
      <h3>Alice</h3><p>CEO and Co-founder</p>
    </div>
    <div class="team-member">
      <img alt="Bob — CTO" style="width:120px;height:120px;border-radius:50%;background:#ddd">
      <h3>Bob</h3><p>CTO and Co-founder</p>
    </div>
  </div>
</div></section>

<section><div class="container">
  <h2>No overlap here</h2>
  <div style="padding:1.5rem;background:#fff;border-radius:8px;margin-bottom:1rem;box-shadow:0 2px 8px rgba(0,0,0,.1)">Card one — proper spacing</div>
  <div style="padding:1.5rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)">Card two — no overlap</div>
</div></section>

<section><div class="container">
  <h2>Pricing</h2>
  <div class="pricing">
    <div style="background:#fff;border-radius:8px;padding:2rem;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.1)">
      <h3>Starter</h3><p style="font-size:2rem;font-weight:bold">$29/mo</p><p>For small teams.</p>
    </div>
    <div style="background:#fff;border-radius:8px;padding:2rem;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.1)">
      <h3>Pro</h3><p style="font-size:2rem;font-weight:bold">$99/mo</p><p>For growing teams.</p>
    </div>
  </div>
</div></section>

<section><div class="container">
  <h2>Get Started</h2>
  <p>Sign up today and start building with Acme.</p>
  <div style="max-width:100%;background:#1a1a2e;color:#fff;padding:1rem;border-radius:8px;margin-top:1rem">
    Responsive CTA — fits any viewport.
  </div>
</div></section>

<footer style="background:#1a1a2e;color:#aaa;padding:2rem;text-align:center;margin-top:3rem">
  <p>2026 Acme SaaS Inc. All rights reserved.</p>
</footer>

</body></html>"""

# Profile fixture: uses .evp-* classes, only detected via profile
SELFTEST_PROFILE = """\
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:sans-serif}
.evp-section-inner{max-width:1200px;margin:0 auto;padding:2rem}
.evp-section-inner>h2.evp-heading-left{text-align:left}
.evp-section-inner>h2.evp-heading-left+p{text-align:left;max-width:820px;margin-left:0}
.evp-section-inner>p{max-width:900px;margin-left:auto;margin-right:auto}
</style></head><body>

<div class="evp-section"><div class="evp-section-inner">
  <h2 class="evp-heading-left">EV-styled section</h2>
  <p>Intro paragraph is left-aligned.</p>
  <p>Trailing paragraph CENTERED by generic rule — drifts (check A via profile).</p>
</div></div>

</body></html>"""


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------
def load_profile(path):
    """Load a design profile JSON. Returns dict with selector/threshold overrides."""
    with open(path) as f:
        p = json.load(f)
    return {
        "sectionSelector": p.get("sectionSelector"),
        "headingSelector": p.get("headingSelector"),
        "columnContainerSelector": p.get("columnContainerSelector"),
        "spacingScale": p.get("spacingScale"),
        "thresholds": p.get("thresholds", {}),
        "mobileBreakpoints": p.get("mobileBreakpoints"),
    }


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def run_selftest(a):
    os.makedirs(a.out, exist_ok=True)
    fixtures = {
        "_selftest-bug.html": SELFTEST_BUG,
        "_selftest-clean.html": SELFTEST_CLEAN,
        "_selftest-nonev-bug.html": SELFTEST_NONEV_BUG,
        "_selftest-nonev-clean.html": SELFTEST_NONEV_CLEAN,
        "_selftest-profile.html": SELFTEST_PROFILE,
    }
    for name, html in fixtures.items():
        with open(os.path.join(a.out, name), "w") as f:
            f.write(html)

    # Write the EV profile for profile testing
    profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
    os.makedirs(profile_dir, exist_ok=True)
    ev_profile_path = os.path.join(profile_dir, "ev-core30.profile.json")
    if not os.path.exists(ev_profile_path):
        with open(ev_profile_path, "w") as f:
            json.dump({
                "sectionSelector": ".evp-section",
                "headingSelector": "h2.evp-heading-left, h3.evp-heading-left",
                "columnContainerSelector": None,
                "spacingScale": [32, 48, 64],
                "thresholds": {"tol": 14, "colRatio": 0.4},
                "mobileBreakpoints": [380, 768]
            }, f, indent=2)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[selftest] playwright not importable: {e}")
        return 2

    def make_args(profile=None):
        base = {"tol": a.tol, "colRatio": a.col_ratio, "colFloor": a.col_floor,
                "colMinWidth": a.col_min_width, "checkImages": False,
                "sectionSelector": None, "headingSelector": None,
                "columnContainerSelector": None,
                "spacingClusterTol": 8, "maxSpacingClusters": 3}
        if profile:
            base["sectionSelector"] = profile.get("sectionSelector")
            base["headingSelector"] = profile.get("headingSelector")
            base["columnContainerSelector"] = profile.get("columnContainerSelector")
            thr = profile.get("thresholds", {})
            if "tol" in thr: base["tol"] = thr["tol"]
            if "colRatio" in thr: base["colRatio"] = thr["colRatio"]
        return base

    def audit(pw, fp, viewport_w=1280, viewport_h=2200, profile=None):
        b = pw.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": viewport_w, "height": viewport_h})
        html = load_html(fp)
        pg.set_content(html, wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(300)
        args = make_args(profile)
        args["viewport"] = f"{'desktop' if viewport_w > 800 else f'mobile-{viewport_w}'}"
        findings = pg.evaluate(CHECK_JS, args)
        b.close()
        return findings

    ok = True
    with sync_playwright() as pw:
        # 1. Bug fixture at desktop
        bf = os.path.join(a.out, "_selftest-bug.html")
        bugf = audit(pw, bf)
        bug_drift = any(f["check"] == "A-left-edge-drift" for f in bugf)
        bug_col = any(f["check"] == "B-underfilled-column" for f in bugf)
        bug_overlap = any(f["check"] == "E-element-overlap" for f in bugf)
        bug_spacing = any(f["check"] == "F-spacing-inconsistency" for f in bugf)

        # 2. Bug fixture at mobile (380px) for check G/C
        bugf_mobile = audit(pw, bf, viewport_w=380, viewport_h=2200)
        bug_mobile_overflow = any(f["check"] == "C-horizontal-overflow" for f in bugf_mobile)

        # 3. Clean fixture at desktop
        cf = os.path.join(a.out, "_selftest-clean.html")
        cleanf = audit(pw, cf)
        clean_drift = any(f["check"] == "A-left-edge-drift" for f in cleanf)
        clean_overlap = any(f["check"] == "E-element-overlap" for f in cleanf)
        clean_spacing = any(f["check"] == "F-spacing-inconsistency" for f in cleanf)

        # 4. Clean fixture at mobile (380px)
        cleanf_mobile = audit(pw, cf, viewport_w=380, viewport_h=2200)
        clean_mobile_overflow = any(f["check"] == "C-horizontal-overflow" for f in cleanf_mobile)

        # 5. Non-EV bug fixture at desktop (zero-config generality proof)
        nef = os.path.join(a.out, "_selftest-nonev-bug.html")
        nevf = audit(pw, nef)
        nonev_drift = any(f["check"] == "A-left-edge-drift" for f in nevf)
        nonev_overlap = any(f["check"] == "E-element-overlap" for f in nevf)
        nonev_spacing = any(f["check"] == "F-spacing-inconsistency" for f in nevf)

        # 6. Non-EV bug at mobile
        nevf_mobile = audit(pw, nef, viewport_w=380, viewport_h=2200)
        nonev_mobile_overflow = any(f["check"] == "C-horizontal-overflow" for f in nevf_mobile)

        # 7. Non-EV clean fixture
        ncf = os.path.join(a.out, "_selftest-nonev-clean.html")
        ncf_results = audit(pw, ncf)
        nonev_clean_drift = any(f["check"] == "A-left-edge-drift" for f in ncf_results)
        nonev_clean_overlap = any(f["check"] == "E-element-overlap" for f in ncf_results)

        # 8. Profile fixture: without profile (div.evp-section not a <section>)
        pf = os.path.join(a.out, "_selftest-profile.html")
        # With profile (should detect via .evp-section selector)
        ev_profile = load_profile(ev_profile_path)
        prof_withprof = audit(pw, pf, profile=ev_profile)
        prof_drift_with = any(f["check"] == "A-left-edge-drift" for f in prof_withprof)

    # --- Report ---
    checks = [
        ("bug A-drift", bug_drift, True),
        ("bug B-underfilled", bug_col, True),
        ("bug E-overlap", bug_overlap, True),
        ("bug F-spacing", bug_spacing, True),
        ("bug mobile C-overflow (G)", bug_mobile_overflow, True),
        ("clean A-drift (false-pos?)", clean_drift, False),
        ("clean E-overlap (false-pos?)", clean_overlap, False),
        ("clean F-spacing (false-pos?)", clean_spacing, False),
        ("clean mobile C-overflow (false-pos?)", clean_mobile_overflow, False),
        ("nonev-bug A-drift (generality)", nonev_drift, True),
        ("nonev-bug E-overlap (generality)", nonev_overlap, True),
        ("nonev-bug F-spacing (generality)", nonev_spacing, True),
        ("nonev-bug mobile C-overflow (generality)", nonev_mobile_overflow, True),
        ("nonev-clean A-drift (false-pos?)", nonev_clean_drift, False),
        ("nonev-clean E-overlap (false-pos?)", nonev_clean_overlap, False),
        ("profile A-drift (with profile)", prof_drift_with, True),
    ]

    for label, got, expected in checks:
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            ok = False
        verb = "caught" if got else "clean"
        print(f"[selftest] {label}: {verb} ... {status}")

    print(f"\n[selftest] {'PASS — all checks correct' if ok else 'FAIL — see above'}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def make_out_dir(base):
    """Create a timestamped output directory to avoid collisions."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = f"{base}-{ts}"
    os.makedirs(out, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Design-agnostic rendered visual QC for HTML pages.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--selftest", action="store_true",
                    help="Run built-in bug/clean/non-EV/profile fixtures and assert checks fire correctly")
    ap.add_argument("--viewport", default="1280x2200")
    ap.add_argument("--tol", type=int, default=14, help="Left-edge px tolerance (check A)")
    ap.add_argument("--col-ratio", type=float, default=0.4, dest="col_ratio")
    ap.add_argument("--col-floor", type=int, default=140, dest="col_floor")
    ap.add_argument("--col-min-width", type=int, default=140, dest="col_min_width",
                    help="Min px width for a grid cell to count as a content column")
    ap.add_argument("--out", default="./visual-qc-out")
    ap.add_argument("--check-images", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--profile", default="",
                    help="Path to a <design>.profile.json for selector/threshold overrides")
    ap.add_argument("--mobile-breakpoints", default="380,768", dest="mobile_breakpoints",
                    help="Comma-separated mobile viewport widths for check G (default: 380,768)")
    ap.add_argument("--spacing-cluster-tol", type=int, default=8, dest="spacing_cluster_tol",
                    help="Px tolerance for spacing-gap clustering (check F, default: 8)")
    ap.add_argument("--max-spacing-clusters", type=int, default=3, dest="max_spacing_clusters",
                    help="Max distinct spacing clusters before flagging (check F, default: 3)")
    a = ap.parse_args()

    if a.selftest:
        return run_selftest(a)
    if not a.paths:
        ap.error("Provide HTML paths/globs, or use --selftest")

    files = [f for f in expand(a.paths) if f.lower().endswith((".html", ".htm"))]
    if not files:
        print("No HTML files matched.", file=sys.stderr)
        return 2

    vw, vh = (int(x) for x in a.viewport.lower().split("x"))
    mobile_bps = [int(x.strip()) for x in a.mobile_breakpoints.split(",") if x.strip()]

    # Load profile if specified
    profile = None
    if a.profile:
        try:
            profile = load_profile(a.profile)
        except Exception as e:
            print(f"[error] failed to load profile {a.profile}: {e}", file=sys.stderr)
            return 2

    # Determine output dir (timestamped by default, unless explicit --out given)
    if a.out == "./visual-qc-out":
        out_dir = make_out_dir(a.out)
    else:
        out_dir = a.out
        os.makedirs(out_dir, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[error] playwright not importable: {e}", file=sys.stderr)
        return 2

    # Build JS args template
    def js_args(viewport_label):
        base = {
            "tol": a.tol, "colRatio": a.col_ratio, "colFloor": a.col_floor,
            "colMinWidth": a.col_min_width, "checkImages": a.check_images,
            "viewport": viewport_label,
            "sectionSelector": None, "headingSelector": None,
            "columnContainerSelector": None,
            "spacingClusterTol": a.spacing_cluster_tol,
            "maxSpacingClusters": a.max_spacing_clusters,
        }
        if profile:
            base["sectionSelector"] = profile.get("sectionSelector")
            base["headingSelector"] = profile.get("headingSelector")
            base["columnContainerSelector"] = profile.get("columnContainerSelector")
            thr = profile.get("thresholds", {})
            if "tol" in thr: base["tol"] = thr["tol"]
            if "colRatio" in thr: base["colRatio"] = thr["colRatio"]
            if "colFloor" in thr: base["colFloor"] = thr["colFloor"]
        return base

    report, total_fail = [], 0
    viewports = [("desktop", vw, vh)] + [(f"mobile-{bp}", bp, vh) for bp in mobile_bps]

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])

        for fp in files:
            name = os.path.basename(os.path.dirname(fp)) or os.path.splitext(os.path.basename(fp))[0]
            entry = {"file": fp, "name": name, "viewports": {}, "error": None}
            html = load_html(fp)
            all_findings = []

            try:
                for vp_label, vp_w, vp_h in viewports:
                    page = browser.new_page(
                        viewport={"width": vp_w, "height": vp_h},
                        device_scale_factor=1)
                    page.set_content(html, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(500)

                    findings = page.evaluate(CHECK_JS, js_args(vp_label))

                    shot_name = f"{name}-{vp_label}.png"
                    shot_path = os.path.join(out_dir, shot_name)
                    page.screenshot(path=shot_path, full_page=True)
                    page.close()

                    entry["viewports"][vp_label] = {
                        "findings": findings,
                        "screenshot": shot_path,
                        "viewport_width": vp_w,
                    }
                    all_findings.extend(findings)

            except Exception as e:
                entry["error"] = str(e)

            # Flatten findings for backward compat
            entry["findings"] = all_findings
            report.append(entry)

            fails = [f for f in all_findings if f["severity"] == "fail"]
            warns = [f for f in all_findings if f["severity"] == "warn"]
            if fails or entry["error"]:
                total_fail += 1

            if not a.quiet:
                status = "ERROR" if entry["error"] else (
                    "FAIL" if fails else ("WARN" if warns else "PASS"))
                print(f"\n{'█' if (fails or entry['error']) else '·'} {name} ... {status}")
                if entry["error"]:
                    print(f"    run-error: {entry['error']}")
                for f in all_findings:
                    tag = "FAIL" if f["severity"] == "fail" else "warn"
                    sec = f' | section: "{f["section"]}"' if f.get("section") else ""
                    vp = f" [{f.get('viewport','')}]" if f.get("viewport") else ""
                    print(f"    [{tag}]{vp} {f['check']}{sec}")
                    print(f"        {f['detail']}")
                    print(f"        {f['element']}")

        browser.close()

    n = len(report)
    npass = sum(1 for r in report
                if not r["error"] and not any(f["severity"] == "fail" for f in r["findings"]))
    print(f"\n{'='*66}")
    print(f"SUMMARY: {n} page(s) — {npass} clean, {total_fail} with FAIL/error.")
    print(f"Viewports: desktop ({vw}px) + mobile ({', '.join(str(b) for b in mobile_bps)}px)")
    print(f"Screenshots -> {out_dir}/")

    if a.json:
        json_path = a.json
        with open(json_path, "w") as fh:
            json.dump({
                "pages": report,
                "viewport": a.viewport,
                "mobile_breakpoints": mobile_bps,
                "tol": a.tol,
                "profile": a.profile or None,
            }, fh, indent=2)
        print(f"JSON report -> {json_path}")

    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
