# verify-render-integrity.py

One-command tool to detect and fix render-path CSS corruption on WordPress pages.

## What it catches

WordPress's `wpautop` filter injects `</p><p>` tokens into inline `<style>` blocks when page content lacks `<!-- wp:html -->` Gutenberg block markers. The CSS parser's error recovery then skips rules following each injection — destroying hero gradients, grid layouts, callout backgrounds, and other visual structure.

**This defect is invisible to stored-content checks.** The raw content in the WP database can be byte-identical to canonical drafts while the rendered output is broken. Only render-time verification catches it.

**Discovered:** 2026-07-09, EV Electric Wave-1 incident (#3). Five pages had broken CSS rendering for 6 days (2026-07-03 to 2026-07-09) because a prior script (`ev-fu1-swap-review-shortcodes.py`) read `.get("rendered")` instead of `.get("raw")` and wrote back — stripping the `<!-- wp:html -->` markers from stored content. See `repos/ev-electric-services/.kos/execution-logs/execution-log-2026-07-09-wave1-style-block-regression.md` for full incident details.

## Usage

### Verify all pages for a client (read-only)

```bash
python verify-render-integrity.py --client ev-electric-services
```

### Verify specific pages

```bash
python verify-render-integrity.py --client ev-electric-services --page-ids 6098 6167 6177
```

### Fix pages (wrap in `<!-- wp:html -->` markers)

```bash
python verify-render-integrity.py --client ev-electric-services --fix
```

### Dry-run fix

```bash
python verify-render-integrity.py --client ev-electric-services --fix --dry-run
```

## Checks performed (per page)

| Check | What it asserts | What failure means |
|-------|----------------|-------------------|
| `wp_html_markers` | Raw content starts with `<!-- wp:html -->` | wpautop will corrupt any inline `<style>` block |
| `p_clean` | 0 `<p>` tags inside any rendered `<style>` block | wpautop is actively corrupting CSS right now |
| `length_match` | Rendered style-block length = raw style-block length | Render pipeline is inflating CSS (likely `<p>` injection) |
| `braces_balanced` | `{` count = `}` count in rendered CSS | CSS is structurally broken |

Pages without `<style>` blocks are reported as SKIP (not Core-30 pages).

## Fix behavior

With `--fix`, the tool wraps pages in `<!-- wp:html -->...<!-- /wp:html -->` markers — the same fix applied in the 2026-07-09 incident. It then re-verifies the page.

The tool **only fixes pages failing the marker check**. Pages failing for other reasons (e.g., braces already unbalanced in stored content) are reported for human triage — the tool does not attempt to fix content-level CSS damage.

## When to run

1. **After any REST API batch edit** (shortcode conversions, hero swaps, schema updates, hours changes). Any script that reads and writes page content can strip `<!-- wp:html -->` markers if it reads `.get("rendered")` instead of `.get("raw")`.
2. **Periodically as drift detection** — run monthly or before any client visual audit.
3. **As a CI gate** — exit code is non-zero on any non-fixable failure or error.

## Exit codes

- `0` — all pages pass (or all failures were fixable and `--fix` was used)
- `1` — at least one non-fixable failure or error

## Prerequisites

- Client config at `scripts/data/client-<slug>.json` with `website_url_no_slash` and `client_slug`
- WP application password via tier-3 vending machine, env var, or tier-3 markdown (same as `publish-core-30-page.py`)

## Related

- `publish-core-30-page.py` Guard 4 (wp:html wrapper verify-after-write) + Guard 5 (render-time CSS integrity check)
- `pattern-wordpress-bulk-hero-alt-swap.md` validation item 5 (content-integrity invariant)
- CR-223 in `_review-gate-catch-register.md`
