---
type: execution-log
status: draft
created: 2026-06-20
updated: 2026-06-20
venture: ai-agency-core
tags: [execution-log, review-engine, local-seo, tool-build, reviews]
---

## 2026-06-20 — [G2] Review-generation engine build

**What was built:** A reusable, engine-first review-request system at `scripts/review-engine/`. Three scripts (QR code generator, SMS/email template renderer, velocity tracker), three per-client JSON configs (EV Electric, S&H Contracting, acme-dental throwaway), three baseline reviews logs, an AggregateRating sync hook note for G3, and a README.

**Key components:**
- `generate-review-qr.py` — reads config, generates PNG QR code encoding the client's Google review link. Uses `qrcode` + `Pillow` in a local `.venv/`.
- `generate-review-templates.py` — renders 7 files per client: SMS template, email template, verbal ask script, positive/negative review response templates, filled example, policy guardrails card. Brand-voiced via config `sign_off_style` and `tone`.
- `review-velocity-report.py` — reads the reviews-log CSV, computes reviews/month vs the 4-8 target, shows trend (improving/steady/declining), nudges when velocity drops below minimum.

### Decisions made

| Decision | Choice | Rejected alternative | Why |
|---|---|---|---|
| FILL-guard on review_link | Scripts exit 1 with clear message when `review_link` starts with `FILL:` | Silently skip or use placeholder URL | Operator must provide the real GBP review URL; generating assets with a placeholder would produce unusable QR codes and broken links. Fail-loud prevents silent bad output. |
| Zero hardcoded client values | All client-specific data lives in per-client JSON configs; scripts read ONLY from config | Hardcode EV/S&H values with a "generalize later" TODO | Per `feedback_tools_reusable_across_project_types` — the productization bar requires proving reuse from config alone before "done." |
| Non-electrician vertical-agnostic proof | Throwaway `acme-dental.json` config (dentist in Richmond, VA) exercises all 3 scripts | Only test on EV + S&H (both electricians) | Two same-vertical configs don't prove the engine carries zero vertical-specific assumptions. The dentist config proves it. |
| No phone in GBP-bound response templates | Response templates say "reach out to us directly" instead of interpolating `{owner_phone}` | Include `{owner_phone}` in negative review response | Independent reviewer caught this (CR-045). GBP review replies are public — auto-embedding real phone numbers in public copy is a compliance risk. Phone is fine in private SMS/email (not GBP-bound). |
| `validate_config()` on every entry point | All 3 scripts validate required keys with clean `FAIL:` messages on missing keys | Let Python throw raw `KeyError` tracebacks | Independent reviewer requested this. Clean failure is part of the engine-first design — a bad config should tell the operator exactly what's wrong, not crash with a traceback. |
| CSV for reviews log (not markdown) | `logs/<slug>-reviews-log.csv` with DictReader schema | Markdown table in a `.md` file | CSV is machine-parseable by the velocity tracker without regex. Markdown tables are harder to append to and parse reliably. |
| Baseline as row zero with channel=baseline | First CSV row records the starting review count; velocity tracker excludes baseline rows from monthly calculations | Separate baseline field in config only | Having the baseline in the log itself makes the log self-contained — you can reconstruct the full history from the CSV alone without needing the config. |

### GBP-bound phone catch and fix

The independent reviewer (separate session) caught that `generate-review-templates.py` line 72 interpolated `{owner_phone}` into the negative review-response template. Since review replies are public GBP-bound copy, this would auto-embed real client phone numbers (EV 571-500-6637, S&H 703-972-5571) into public Google review replies. Fixed: replaced with "Please reach out to us directly so we can understand what happened and find a solution." The business's GBP listing carries the number via the Call button. CR-045 filed for the sub-agent rubber-stamp that missed this.

### Non-electrician proof

The `acme-dental.json` config (Dr. Jane Smith, dentist, 804-555-0199, Richmond VA, services: teeth cleaning / dental implants / cosmetic dentistry / emergency dental care / orthodontics) generated all 8 output files from config alone — QR code, 5 templates, filled example, policy guardrails. Zero electrician-specific language leaked. The filled example correctly rendered "teeth cleaning in Richmond" with "Dr. Jane, Acme Dental Care" sign-off.

### Surfaced deferral

0 of 2 real clients have generated assets. EV + S&H configs carry `FILL:` in `review_link` — operator must provide the real GBP review URLs (`g.page/r/…`). Engine + vertical-agnostic proof are done; real-client install is not.

**Reusable for future apps?:** Yes — the engine-first + per-client config pattern applies to any tool that must work across multiple clients without hardcoded values. The FILL-guard + validate_config pattern is directly reusable. See `[[pattern-tooling-config-driven-multi-client-engine]]`.
