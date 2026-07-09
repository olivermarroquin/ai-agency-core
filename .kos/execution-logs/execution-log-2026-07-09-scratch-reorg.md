---
type: execution-log
status: draft
created: 2026-07-09
updated: 2026-07-09
venture: ai-agency-core
tier: capture
tags: [execution-log, ai-agency-core, infrastructure, scratch-convention]
---

## 2026-07-09 — Workspace scratch-file reorganization (`_scratch/` convention)

**What was built:** Moved ~48 transient workspace-root coordination files (relay, spawn, launch, gate-skip) into `~/workspace/_scratch/` with type subfolders (`relay/`, `spawn/`, `launch/`, `skip/`, `archive/`). Updated all consuming scripts, docs, and plumbing whitelist. Added operator relay mirror convention.

**Tier:** Capture (productize-adjacent infra, gate-touching but not a new skill).

**Decision made:** Legacy root-path plumbing whitelist patterns retained alongside new `_scratch/` patterns (dual coverage). Backward-compat fallback in `spawn-pair.sh` checks root if `_scratch/spawn/` file not found.

**Alternatives considered:** (1) Hard cutover with no fallback — rejected because ev-style-regression pair is mid-flight with root-path files; breaking their paths mid-session would cause gate blocks. (2) Symlinks from root to `_scratch/` — rejected as unnecessary complexity; the fallback approach is simpler and self-retiring.

**Why this approach:** The workspace root had accumulated 48+ transient files making `ls` noisy and Finder navigation difficult. Type subfolders give each file category a clear home. Legacy patterns retire naturally as in-flight pairs close.

**Reusable for future apps?:** Yes — the `_scratch/` convention pattern (`pattern-workspace-scratch-file-convention`) captures the general approach of organizing transient inter-process coordination files with type subfolders and backward-compat fallbacks.

### Files changed

| Repo | File | Change |
|------|------|--------|
| ai-agency-core | `scripts/spawn-pair.sh` | New paths + fallback + operator relay mirror duty |
| ai-agency-core | `scripts/pair-status.sh` | Dual-glob scan (new + legacy) |
| ai-agency-core | `scripts/mandatory-review-gate/mandatory-review-gate.py` | Skip script → `_scratch/skip/` |
| ai-agency-core | `scripts/mandatory-review-gate/test_rgh20_plumbing_exemption.py` | +9 tests for `_scratch/` paths |
| second-brain | `_meta/workspace-config/CLAUDE.md` | Relay, spawn, skip path updates + operator relay mirror subsection |
| (not in git) | `.review-gate/plumbing-whitelist.yml` | +4 new patterns, 4 legacy marked |
| (not in git) | `_scratch/_README.md` | Convention doc |

### Test results

- RGH-20 plumbing suite: **43/43 passed** (34 existing + 9 new)
- Conformance suite: 127/139 (12 pre-existing failures, unrelated)

### Migration

- 41 files moved to `_scratch/archive/`
- 16 files retained at root (ev-style-regression 7, scratch-reorg 6, default relay 2, ahmad-hero-swap 1)

### Independent review

Reviewer PASS (0 blocking, 1 advisory: file count 16 vs reported 15 — the ev-style-regression `_spawn-reviewer-assembled` was miscounted).
