---
type: project-readme
project-name: ai-agency-core
status: active
created: 2026-05-06
client: false
sensitivity: standard
tags: [project, vault-root]
---

# Project: ai-agency-core

## Purpose
What this project produces and for whom.

## Status
Current phase. What's blocked, what's next.

## Sensitivity tier
- [ ] Standard (Tier 1) — lives in second-brain via symlink
- [ ] NDA / confidential (Tier 2) — `_private/`, excluded from search and sync
- [ ] Sensitive / regulated (Tier 3) — separate air-gapped vault

## Vault contents
- `specs/` — what we're building
- `scopes/` — implementation boundaries
- `execution-logs/` — what actually happened
- `lessons/` — extracted learnings (promote to shared-intelligence)
- `.vault-config.md` — agent-facing context boundary

## What an agent reading this vault needs to know
- Project goal: 
- Current milestone: 
- Active constraints: 
- Out of scope: 

## Promotion log
- 2026-05-06 — project initialized

## Links to shared-intelligence used by this project

Manually-curated links (add by hand as you adopt patterns / tools / lessons):

- [[ ]]
- [[ ]]

### Suggested by VIS extractions

Source notes flagged with this project in their `relevant-projects:` frontmatter. Review periodically; if a source's content is genuinely useful, promote a link to it (or to one of its derived artifacts) into the manual list above.

*(v1 scope: surfaces source notes only. Tools, tactics, and patterns derived from a relevant source are reachable via that source's "Extracted artifacts" section. See `phase-3-plus-queue.md` item #2 for the v2 upgrade criteria.)*

```dataview
TABLE WITHOUT ID
  file.link AS "Suggested",
  type AS "Type",
  ingested AS "Ingested",
  tier AS "Tier"
FROM "00_inbox" OR "03_domains" OR "05_shared-intelligence"
WHERE contains(relevant-projects, this.project-name)
  AND !contains(this.file.outlinks, file.link)
SORT ingested DESC
LIMIT 10
```

## Lessons promoted from this project
- [[ ]]
