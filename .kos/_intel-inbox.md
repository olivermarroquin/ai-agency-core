---
type: intel-inbox
status: active
created: 2026-05-27
updated: 2026-05-27
project: ai-agency-core
archetypes: [infrastructure-tooling, ai-tooling, personal-system]
tags: [intel-inbox, ai-agency-core]
---

# Intel inbox — AI Agency Core

> Artifacts from the vault (tactics, patterns, tools, syntheses, opportunities) that may apply to this project. Triaged on the project's review cadence — typically monthly for clients, ad-hoc for personal projects.

## What this file is

The routing pipeline (manual today, [[intel-routing-skill-spec|intel-routing skill]] later) populates the view below via Dataview. It surfaces anything in `03_domains/` or `05_shared-intelligence/` (plus opportunities in `00_inbox/decisions-pending/`) that either names this project in its `applies-to-projects` frontmatter, or shares at least one archetype with this project's declared archetypes.

The query filters out items routed in the last 14 days (controllable in the query below). What appears is "new since you last looked."

Schema for the five fields powering this view: see [[conventions#Project applicability fields]].

## Triage protocol

For each item in the table below:

- **Keep** — relevant, leave for now, will revisit on next triage
- **Apply** — write a bridge note (`bridge-<artifact-slug>-to-<project-slug>.md`) and add the action to this project's `_punchlist.md`; then update the artifact's `routed-at:` to today
- **Defer** — relevant later, not now; set the artifact's `applicability-confidence:` lower or add a deferral note
- **Not applicable** — remove this project's slug from `applies-to-projects:` on the artifact (or sharpen `applies-to-archetypes:`)

After triaging, refresh this view. Items you actioned (and updated `routed-at:` on) drop off until the lookback window resets.

## The query

```dataview
TABLE
  type AS "Kind",
  applicability-confidence AS "Confidence",
  applies-to-archetypes AS "Via archetypes",
  routed-at AS "Last routed",
  file.mtime AS "Updated"
FROM "03_domains" OR "05_shared-intelligence" OR "00_inbox/decisions-pending"
WHERE contains(applies-to-projects, this.project)
   OR any(applies-to-archetypes, (a) => contains(this.archetypes, a))
WHERE !routed-at OR routed-at < date(today) - dur(14 days)
SORT applicability-confidence DESC, file.mtime DESC
```

The query reads `this.project` (this file's `project:` frontmatter) and `this.archetypes` (this file's `archetypes:` frontmatter). Keep both in sync with the project README — that's the operator's responsibility until the `intel-routing` skill ships a validator.

## Bridge notes spun up from this inbox

> Every "Apply" triage produces a bridge note linking source artifact to project action. Aggregated here for audit.

```dataview
LIST
FROM ""
WHERE type = "bridge-note" AND project = this.project
SORT file.ctime DESC
```

## Last triage

**Date:** <not yet triaged>
**Items kept:** —
**Items applied:** —
**Items deferred:** —
**Items marked not-applicable:** —

Update this section after each triage pass. The dates here are the operator-readable record; `routed-at:` on individual artifacts is what the query reads.

## How to instantiate this template for a new project

1. Copy to the project folder root: `04_projects/<area>/<project-slug>/_intel-inbox.md`
2. Replace `<project-slug>` in frontmatter `project:` and `tags:`
3. Replace `<Project name>` in the title
4. Copy the `archetypes:` list from the project's README frontmatter into this file's frontmatter
5. Leave the rest unchanged — the Dataview queries use `this.project` and `this.archetypes`, so they self-customize from the frontmatter

See also: [[conventions#Project applicability fields]], [[intel-opportunities-inbox]], [[intel-routing-skill-spec]].
