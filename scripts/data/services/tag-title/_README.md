# Tag-title service data files (bilingual) — Latin United Multiservices

First BILINGUAL service-file set in the factory. Schema per file:
`slug`, `name` + `name_es`, `same_day` (bool), `summary`/`summary_es`, `what_it_is`/`_es`,
`documents_checklist[]` (each {en, es, note?}), `process_steps[]` ({title_en/es, body_en/es}),
`fees_note`/`_es` (NEVER hard prices for MVA fees — they change; state "MVA fees + our service fee, total quoted before we start"),
`common_problems[]` ({problem_en/es, fix_en/es}), `faqs[]` ({q_en/es, a_en/es}), `cta_hook`/`_es`,
`schema_service_type`, `_facts_verified` (source + date).

FACT DISCIPLINE: figures sourced 2026-07-27 from MVTA's Apr-2026 snapshot of MVA info pages
(excise 6.5%, notarized bill of sale 3-condition rule, 90-day inspection certificate, VR-181/VR-197/VR-129,
salvage = MD State Police inspection). RE-VERIFY on mva.maryland.gov at page-authoring time — flag = `_facts_verified`.
Placement note: kept in services/tag-title/ subdir (electrician files live flat in services/) — wire path in scaffolder run.
