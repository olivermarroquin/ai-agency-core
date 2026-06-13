# Website-Factory Data Model Schemas

JSON Schema definitions for the 5 (+1 overlay) data objects that power the website-factory pipeline. Everything renders from these. Locked by [WF-7] (2026-06-12).

## Schema files

| Schema | File | Data location | Authored or derived |
|---|---|---|---|
| Client configuration | `client.schema.json` | `data/client-<slug>.json` | Authored (one per client) |
| City (shared facts) | `city.schema.json` | `data/cities/<city-slug>.json` | Authored (research-heavy) |
| City overlay (per-client) | `city-overlay.schema.json` | `data/cities/<client-slug>/<city-slug>.json` | Derived/authored (HQ-relative) |
| Service | `service.schema.json` | `data/services/<slug>.json` or `data/services/<client-slug>/<slug>.json` | Authored |
| Service x City facts | `service-city-facts.schema.json` | `data/service_city_facts/<service>--<city>.json` | Authored (per-cell research) |
| Price layer | `price-layer.schema.json` | `data/price_layer.json` | Authored (single source of truth) |
| Link graph | `link-graph.schema.json` | `data/link_graph.json` | **Derived** (generated at build time) |

## Shared-vs-client city split (DA5 design)

City data is split into two layers per the [DA5] design absorbed into [WF-7]:

- **Shared city file** (`data/cities/<city-slug>.json`) — facts true regardless of client: county, state, ZIP codes, utilities, neighborhoods, housing patterns, population.
- **Per-client overlay** (`data/cities/<client-slug>/<city-slug>.json`) — fields that vary by which client serves the city: HQ distance/dispatch, no-trip-charge zone, map-iframe title.

**Missing overlay = SC-2 fail-loud.** The scaffolder/renderer merges shared + overlay for the active client. If no overlay exists for the build's client, the build fails loudly (SC-2). It never falls back to another client's values.

**HQ-relative fields are derived, not copied.** Distance/dispatch phrases should be computed from the client's `geo.latitude`/`geo.longitude` + the city's coordinates. See [DA5] implementation handoff for auto-derivation details.

## Validation

```bash
python3 repos/ai-agency-core/schemas/validate_data.py
```

Validates every JSON file in `data/` against the appropriate schema. Reports per-file pass/fail with specific error paths.

## Companion doc

Plain-language spec: `second-brain/_meta/handoffs/website-factory/data-model-spec-v1.md`

## Related

- [[blueprint-website-factory]] section 2
- [[decision-2026-06-11-website-factory-program-locked-decisions]]
- [[handoff-2026-06-07-da5-city-data-shared-vs-client-split]] (implementation)
- [[handoff-2026-06-11-wf9-city-research-fanout]] (consumer)
- [[handoff-2026-06-11-wf10-tier-generators]] (consumer)
