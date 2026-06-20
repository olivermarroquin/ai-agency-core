---
type: cross-link
created: 2026-06-20
tags: [review-engine, schema, aggregate-rating, sync-hook]
---

# AggregateRating sync hook — review-engine → schema markup

**Owner workstream:** [G3] Schema verify + AggregateRating sync (EV + S&H)
**SOP:** `[[sop-local-schema-markup]]`
**Source of truth for review count:** Google Business Profile (live) or per-client reviews log baseline + landed count

## The contract

The `AggregateRating` in each client's LocalBusiness schema markup MUST track the **real, growing** review count and rating:

```json
{
  "@type": "AggregateRating",
  "ratingValue": "<current average from GBP>",
  "reviewCount": "<current total from GBP>",
  "bestRating": "5"
}
```

## Why this matters

- A stale `reviewCount` (e.g., hardcoded "75" while the real count grows to 90) is **inaccurate structured data** — Google can demote or remove the rich result.
- The review-generation engine is designed to increase velocity (4-8 new/month). The schema must keep pace.

## How to sync

**Option A (manual, v1):** At each schema update pass, pull the current count from GBP and update the markup.

**Option B (automated, future):** When [G6] GBP API access lands, the API can pull the live count and the schema build pipeline can read it directly.

## Per-client current baselines (as of 2026-06-20)

| Client | Count | Rating | Source |
|---|---|---|---|
| EV Electric Services | ~75 | 5.0 | Operator-reported, G1 pass 2026-06-19 |
| S&H Contracting | 69 | 5.0 | Operator-reported, G1 pass 2026-06-19 |

These will grow as the review engine operates. Schema must be updated accordingly.

## See also

- `[[sop-review-generation-engine]]` (this engine)
- `[[sop-local-schema-markup]]` (G3 — the consumer of this hook)
- `[[handoff-2026-06-11-gbp-api-automation-toolbuild]]` (G6 — future automated sync)
