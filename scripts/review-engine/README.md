# Review-generation engine

Reusable, engine-first review-request system for local service businesses. Produces QR codes, SMS/email templates, and velocity tracking from per-client config — zero hardcoded client values.

## Quick start

```bash
# Activate the venv
source .venv/bin/activate

# Generate QR code
python3 generate-review-qr.py --config configs/<client-slug>.json

# Generate SMS/email templates
python3 generate-review-templates.py --config configs/<client-slug>.json

# Run velocity report
python3 review-velocity-report.py --config configs/<client-slug>.json
```

## Per-client config

Each client gets a JSON file in `configs/`. The engine reads ONLY from config — no hardcoded values.

Required fields: `client_slug`, `business_name`, `owner_name`, `owner_first_name`, `owner_phone`, `review_link`, `brand_voice`, `primary_services`, `review_baseline`, `velocity_target_per_month`, `policy`.

The `review_link` must be the real GBP write-review URL (`g.page/r/…`). A `FILL:` prefix causes all scripts to fail loudly.

## Reviews log

CSV at `logs/<client-slug>-reviews-log.csv`. Schema:

```
date,customer_first_name,city,service,channel,requested,landed,stars
```

Row zero is the baseline (channel=baseline). Add rows as review requests go out.

## Policy guardrails

Encoded in config and enforced in templates:
- No incentives for reviews
- No review-gating (ask every customer)
- Real customers only

## See also

- `AGGREGATERATING-SYNC-HOOK.md` — schema markup must track growing review count
- `sop-review-generation-engine` in second-brain
