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

STATE_DIR is configurable via REVIEW_GATE_STATE_DIR env var (absolute path).
Default: <WORKSPACE_ROOT>/.review-gate/state/
The env var exists for two reasons:
  1. Test isolation — conformance tests set it to a temp dir
  2. Phase 2 adapters may share a neutral root
"""

import os

# This file: ~/workspace/repos/ai-agency-core/scripts/mandatory-review-gate/_paths.py
# dirname:   ~/workspace/repos/ai-agency-core/scripts/mandatory-review-gate
# 4x ..:     ~/workspace
WORKSPACE_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))

# Sanity check — fail loud if the derivation is wrong
assert os.path.basename(WORKSPACE_ROOT) == 'workspace', (
    f'_paths.py WORKSPACE_ROOT derivation is wrong: got {WORKSPACE_ROOT}, '
    f'expected basename "workspace"'
)

# State dir: env override (absolute) or default under workspace root
_env_state = os.environ.get('REVIEW_GATE_STATE_DIR', '')
if _env_state:
    STATE_DIR = os.path.realpath(_env_state)
else:
    STATE_DIR = os.path.join(WORKSPACE_ROOT, '.review-gate', 'state')

# Guard: STATE_DIR must never land outside workspace root (unless explicitly overridden for tests)
if not _env_state:
    assert STATE_DIR.startswith(WORKSPACE_ROOT), (
        f'STATE_DIR {STATE_DIR} is outside WORKSPACE_ROOT {WORKSPACE_ROOT}'
    )
