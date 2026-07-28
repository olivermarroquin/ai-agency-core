---
type: build-log
status: active
created: 2026-07-28
updated: 2026-07-28
tags: [build-log, ai-agency-core]
---

# ai-agency-core Build Log

## Design Decisions

| # | Decision | Rationale | Date | Source |
|---|----------|-----------|------|--------|
| 1 | **CI migration sequencing: two parallel tracks, not strict phases.** Track A = CI plumbing (pytest status check in ai-agency-core) starts immediately; Track B = Phase-0 validator packaging runs alongside; tracks converge at the PR-diff validator-dispatch job. | The riskiest unknown is the GitHub Actions environment, not validator logic — front-load it; the pytest suites are CI-ready today with zero Phase-0 dependency. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
| 2 | **Validator contract (all Phase-0 validators): file-list argv input; exit 0 = pass, 1 = fail, 2 = config/usage error; machine-readable JSON verdict on stdout; JSON-only config (no PyYAML — CR-245 lesson); home directory `scripts/validators/`.** | Locked before any validator is written so CI dispatch stays uniform. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
| 3 | **Single-canonical-implementation rule: each check has exactly one implementation; existing callers re-point to it.** First application: placeholder-family-sweep is extracted once and both `publish-core-30-page.py::check_placeholder_gate` and the rgh18 diff-aware copy call the canonical validator. No third copy. | Eliminates drift between duplicate implementations and reduces maintenance surface. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
| 4 | **Registry re-pointing is part of every Phase-0 validator's DoD:** the gate-type registry entry must invoke the script and the hand-run reviewer-procedure text must be removed. | Packaging without re-pointing leaves the forgeable self-claim path alive. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
| 5 | **Ground-truth value cross-check (the one medium-effort Phase-0 build) is timeboxed and may trail Phase-1 convergence;** B1–B4 validators gate convergence, it joins CI dispatch when it lands. | De-risks the convergence milestone by not blocking on the hardest validator. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
| 6 | **Local hooks vs CI: local Stop/pre-commit hooks stay for fast in-session feedback; CI is the authoritative enforcement layer.** The PR-diff resolver will be designed for reuse by `git_hook_adapter.py` so local and CI share one dispatch. | Keeps the developer feedback loop fast while making CI the source of truth; shared dispatch avoids divergence between local and CI checks. | 2026-07-28 | Strategic Cowork — Phase-0/1 planning for GitHub PR + CI enforcement arc |
