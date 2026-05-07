---
type: vault-config
created: <% tp.date.now("YYYY-MM-DD") %>
tags: [vault-config, agent-context]
---

# Vault config — agent context boundary

This file tells any AI agent (Claude Code, Cowork, ECS workers, future agents) what this vault is and what scope they should operate in.

## Project name
[name]

## Project goal
One sentence.

## Active milestone
What we're working toward right now.

## Active constraints
- 
- 

## Out of scope (do NOT do)
- 

## Authoritative files inside this vault
- `specs/` — source of truth for what is being built
- `scopes/` — current implementation boundary
- Most recent file in `execution-logs/` — current state

## Authoritative files outside this vault
The agent is allowed to read these from second-brain:
- `05_shared-intelligence/patterns/`
- `05_shared-intelligence/blueprints/`
- `05_shared-intelligence/tools/`
- `01_ai-operating-system/master-system-map.md`

## Files the agent must NOT touch
- Anything in `_private/`
- Anything outside this project's `.kos/` and the allowlist above

## Promotion contract
Every meaningful execution must produce at least one note in `lessons/` that can be promoted to `05_shared-intelligence/lessons/`.

A project task is NOT complete until promotable knowledge has been extracted.
