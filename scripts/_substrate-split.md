---
type: reference
status: shipped
created: 2026-06-19
updated: 2026-06-19
chat-id: f3-page-factory-reusability-hardening-202606190000
tags: [reference, substrate-split, website-factory, wp-vs-nextjs, pipeline-architecture]
---

# WP-vs-substrate-agnostic split

**Purpose:** Document which parts of the page-build pipeline are substrate-agnostic
(carry straight to Next.js or any other platform) vs WordPress-specific (need a
platform-specific sibling). This is the boundary the `[[website-factory]]` build reuses.

## Pipeline stages

```
Research → Briefs → Data JSONs → Page Content → Imagery → Publish/Wire
└─────────────── substrate-agnostic ──────────────┘  └── WP-specific ──┘
```

## Substrate-agnostic (carries to any platform)

These stages produce platform-independent artifacts (markdown, JSON, HTML fragments,
image files). They work unchanged for Next.js, static sites, or any future platform.

### Research layer
| Component | Artifact | Notes |
|-----------|----------|-------|
| `service-seo-research` skill | Per-service research briefs (markdown) | Profile-driven. No platform dependency. |
| `city-base-research` skill | Per-city research briefs (markdown) | Profile-driven. No platform dependency. |
| `intersection-research` skill | Service×city intersection briefs (markdown) | Profile-driven. No platform dependency. |
| `client-fact-research` skill | Client fact briefs (markdown) | Profile-driven. No platform dependency. |
| `competitor-deep-research` skill | Competitor analysis briefs (markdown) | Profile-driven. No platform dependency. |
| `content-coverage-audit` skill | Utilization/coverage/expansion reports | Engine + profiles. No platform dependency. |
| `competitor-architecture-diff` skill | Expansion backlog from competitor diffs | Engine + profiles. No platform dependency. |

### Data layer
| Component | Artifact | Notes |
|-----------|----------|-------|
| `scaffold-client-data.py` | `data/client-<slug>.json` + config example | Client-level facts. Platform-independent JSON. |
| `scaffold-city-data.py` | `data/cities/<city>.json` | City-level facts. Platform-independent JSON. |
| `scaffold-service-data.py` | `data/services/<service>.json` | Service-level facts. Platform-independent JSON. |
| JSON schemas (`schemas/`) | 7 validation schemas | Validate data files. Platform-independent. |
| `facts-completeness-gate.py` | Completeness report | Profile-driven data validation. |
| `hardcode-scanner.py` | Content-default scan | Profile-driven content verification. |

### Content generation layer
| Component | Artifact | Notes |
|-----------|----------|-------|
| `scaffold-core-30-page.py` | `draft-v1.md` + `draft-v1-WP-WRAPPED.html` | Generates page content from data. The `.md` is platform-independent. The `-WP-WRAPPED.html` is a WP-specific wrapper — see below. |
| `bulk-scaffold-pages.py` | Batch of above | Orchestrates multi-page scaffold. Platform-independent orchestration. |
| `insert-internal-links.py` | Internal link wiring | Works on HTML content. Platform-independent (links are relative paths). |
| `audit-published-links.py` | Link integrity report | Platform-independent verification. |
| `verify-artifact.py` | Pre-publish verification | Engine + profiles. Platform-independent. |

### Imagery layer
| Component | Artifact | Notes |
|-----------|----------|-------|
| `generate-imagery-prompts.py` | Higgsfield prompts (text) | Platform-independent. |
| `generate-and-distribute-heroes.py` | Hero images (PNG files) | Platform-independent. Output = image files on disk. |
| `choose-image-variant.py` | Vision-rubric auto-selection | Platform-independent. Scores image files. |
| `organize-image-downloads.py` | Sorted image files | Platform-independent file organization. |
| `ingest-real-photos.py` | Photo library ingestion | Platform-independent. |
| `optimize-image.py` | Optimized images | Platform-independent (resize, compress). |

### Maps / GSC
| Component | Artifact | Notes |
|-----------|----------|-------|
| `generate-maps-iframe.py` | Google Maps embed HTML | Generates an `<iframe>` snippet. The HTML is universal; embed target is Google Maps API (platform-independent). |
| `submit-gsc-indexing.py` | GSC indexing requests | Platform-independent (talks to Google API, not WP). |
| `gsc_indexing.py` | GSC library module | Platform-independent. |

### Quality / review
| Component | Notes |
|-----------|-------|
| `gate-peer-reviewer` skill | Platform-independent review engine. |
| `output-quality-loop` skill | Platform-independent quality evaluation. |
| `mandatory-review-gate/` (18 scripts) | Platform-independent enforcement infrastructure. |

## WordPress-specific (needs a platform sibling for Next.js)

These components talk directly to WordPress REST API, WP plugins, or WP-specific
conventions. Each needs a Next.js counterpart for the website-factory.

### Publish layer
| Component | WP dependency | Next.js equivalent needed |
|-----------|---------------|--------------------------|
| `publish-core-30-page.py` | WP REST API (`/wp-json/wp/v2/pages`), creates/updates pages, manages slugs, parent pages, status | **`deploy-to-nextjs.py`** — write page content to the Next.js project's file system (MDX/JSON), trigger build |
| `publish-core-30-page.py` (SEO meta) | Keelworks AIOSEO Bridge plugin (`/wp-json/keelworks/v1/aioseo-meta/`) OR Keelworks Yoast Bridge (`/wp-json/keelworks/v1/yoast-meta/`) | **Next.js `<head>` metadata** — set via `metadata` export in page component or `generateMetadata()` |
| `publish-core-30-page.py` (page options) | Keelworks bridge (`/wp-json/keelworks/v1/page-options/`) — theme hide-title, featured image | **Layout config** — controlled via frontmatter/props in Next.js page component |
| `publish-core-30-page.py` (cache purge) | Keelworks LiteSpeed Bridge (`/wp-json/keelworks/v1/litespeed-purge`) | **ISR revalidation** — `revalidatePath()` / `revalidateTag()` or Vercel deploy hook |

### Image hosting
| Component | WP dependency | Next.js equivalent needed |
|-----------|---------------|--------------------------|
| `upload-image-to-wp.py` | WP REST API media upload (`/wp-json/wp/v2/media`) | **`deploy-images-to-static.py`** — copy to `/public/images/` or upload to CDN (Vercel Blob, Cloudflare R2) |
| `refresh-cached-image.py` | WP media library cache management | **CDN cache invalidation** — platform-specific (Vercel purge, Cloudflare purge) |
| `wire-page-images.py` | Reads WP media URLs from config | **Static path mapping** — map to `/images/<slug>/...` paths |
| `wire-images-into-html.py` | Reads WP media URLs from config | Same as above |

### WP plugins
| Plugin | Function | Next.js equivalent |
|--------|----------|-------------------|
| `keelworks-aioseo-bridge` | REST endpoint for AIOSEO meta | N/A — Next.js handles SEO via `metadata` API |
| `keelworks-yoast-bridge` | REST endpoint for Yoast SEO meta | N/A — same as above |
| `keelworks-litespeed-bridge` | REST endpoint for cache purge | N/A — Vercel handles caching natively |

### Content wrapper
| Component | WP dependency | Next.js equivalent |
|-----------|---------------|-------------------|
| `scaffold-core-30-page.py` `-WP-WRAPPED.html` output | Elementor/wp:html Custom HTML block wrappers, WP-specific CSS classes | **React component** — the page template becomes a `.tsx` component consuming the same data JSON; the `draft-v1.md` content is universal |

## Migration strategy for Next.js website-factory

1. **Research → Data → Content**: Carry over unchanged. The entire research and scaffolding pipeline produces platform-independent artifacts.

2. **Imagery**: Carry over unchanged. Image generation and selection is platform-independent. Only the final hosting destination changes (WP media → static/CDN).

3. **Publish**: Build a `nextjs_adapter.py` (or equivalent) that:
   - Writes page content to the Next.js project's page directory
   - Sets metadata via the framework's metadata API
   - Deploys images to the static hosting path
   - Triggers ISR revalidation instead of cache purge

4. **WP plugins**: Not needed. Next.js handles SEO metadata and caching natively.

5. **Content template**: Convert the WP-WRAPPED HTML template to a React component. The data substitution variables (`{client_name}`, `{city_name}`, etc.) map to component props sourced from the same `client-<slug>.json` and service/city JSON files.

## Key insight

**~85% of the pipeline is substrate-agnostic.** The WP-specific surface is thin:
one publish script, two image-hosting scripts, three bridge plugins, and the HTML
wrapper format. The Next.js build reuses everything upstream of the publish step
and replaces only the final-mile delivery.
