"""Shared path derivation for the mandatory review gate scripts.

All three scripts (dirty-ledger-track, mandatory-review-gate, log-review-pass)
import this module to get WORKSPACE_ROOT and STATE_DIR.

WORKSPACE_ROOT is derived from THIS FILE's location on disk:
  _paths.py lives at repos/ai-agency-core/scripts/mandatory-review-gate/_paths.py
  dirname(__file__) = .../workspace/repos/ai-agency-core/scripts/mandatory-review-gate
  4 levels up       = .../workspace

This is deterministic regardless of cwd, CLAUDE_PROJECT_DIR, or which repo
the session is running inside. It does NOT use git rev-parse (multiple repos
under workspace root return different roots per subdir).
"""

import os

# This file: ~/workspace/repos/ai-agency-core/scripts/mandatory-review-gate/_paths.py
# dirname:   ~/workspace/repos/ai-agency-core/scripts/mandatory-review-gate
# 4x ..:     ~/workspace
WORKSPACE_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))

STATE_DIR = os.path.join(WORKSPACE_ROOT, '.claude', 'state')

# Sanity check — fail loud if the derivation is wrong
assert os.path.basename(WORKSPACE_ROOT) == 'workspace', (
    f'_paths.py WORKSPACE_ROOT derivation is wrong: got {WORKSPACE_ROOT}, '
    f'expected basename "workspace"'
)
assert STATE_DIR.endswith('/workspace/.claude/state'), (
    f'_paths.py STATE_DIR derivation is wrong: got {STATE_DIR}, '
    f'expected to end with /workspace/.claude/state'
)
