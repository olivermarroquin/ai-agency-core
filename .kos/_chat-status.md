---
type: project-status
project: ai-agency-core
updated: 2026-07-08
current-focus: 'Shared foundation repo — scripts, schemas, gate tooling; receives work from every other sprint rather than having its own dedicated chats'
in-flight-count: 0
ready-to-spawn-count: 0
queued-count: 0
open-decisions: []
blockers: []
last-closed: 2026-07-08
last-closed-summary: '[RGH-20] Reviewer-plumbing gate exemption — plumbing whitelist + compact block message + dedupe; 34 new tests'
metrics:
  chats-closed-past-7d: 5
  chats-closed-past-30d: 21
  artifacts-produced-past-7d: 12
spawn-recommendations: []
---

# Status digest — ai-agency-core

Machine-readable digest of this project's chat-coordination state. The Phase 2 master-tracker-aggregator parses the frontmatter above and rolls it up into a generated section of the master tracker.

## Notes

- ai-agency-core has no dedicated project chats — it is the shared foundation repo (scripts, schemas, gate tooling) that accumulates work from every other project's runs.
- `in-flight-count: 0` means zero chats scoped TO ai-agency-core; many in-flight chats (EV-DIFF, SH-DIFF, SEP-FU2) write INTO this repo as a side effect.
- `last-closed` reflects the most recent chat that wrote deliverables into this repo ([RGH-20] 2026-07-08, plumbing exemption).
- `metrics.chats-closed-past-7d: 5` counts chats since 2026-07-01 that touched ai-agency-core files: [RGH-20], [SH-IDX], [SH-G1], [RGH-18/19-CAL-2], [client-schema-sync]. Approximate.
- 20 execution logs exist in `.kos/execution-logs/` (most recent: 2026-07-08).
