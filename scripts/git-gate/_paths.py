"""Shared path derivation for the git-gate scripts.

All git-gate scripts import this module to get WORKSPACE_ROOT, STATE_DIR,
QUEUE_DIR, and AUDIT_DIR.

WORKSPACE_ROOT is derived from THIS FILE's location on disk:
  _paths.py lives at repos/ai-agency-core/scripts/git-gate/_paths.py
  dirname(__file__) = .../workspace/repos/ai-agency-core/scripts/git-gate
  4 levels up       = .../workspace

STATE_DIR reuses the review-gate state dir (verdict files live there).
QUEUE_DIR is where commit requests are enqueued.
AUDIT_DIR is the git-gate audit trail.
"""

import os

# This file: ~/workspace/repos/ai-agency-core/scripts/git-gate/_paths.py
# dirname:   ~/workspace/repos/ai-agency-core/scripts/git-gate
# 4x ..:     ~/workspace
WORKSPACE_ROOT = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))

assert os.path.basename(WORKSPACE_ROOT) == 'workspace', (
    f'_paths.py WORKSPACE_ROOT derivation is wrong: got {WORKSPACE_ROOT}, '
    f'expected basename "workspace"'
)

# Commit queue directory
_env_queue = os.environ.get('GIT_GATE_QUEUE_DIR', '')
if _env_queue:
    QUEUE_DIR = os.path.realpath(_env_queue)
else:
    QUEUE_DIR = os.path.join(WORKSPACE_ROOT, '.commit-queue')

# Review-gate state dir (where verdict files + dirty ledgers live)
_env_state = os.environ.get('REVIEW_GATE_STATE_DIR', '')
if _env_state:
    REVIEW_GATE_STATE_DIR = os.path.realpath(_env_state)
else:
    REVIEW_GATE_STATE_DIR = os.path.join(WORKSPACE_ROOT, '.review-gate', 'state')

# Audit trail directory
_env_audit = os.environ.get('GIT_GATE_AUDIT_DIR', '')
if _env_audit:
    AUDIT_DIR = os.path.realpath(_env_audit)
else:
    AUDIT_DIR = os.path.join(WORKSPACE_ROOT, '.git-gate', 'audit')

# Tripwire state directory
TRIPWIRE_DIR = os.path.join(WORKSPACE_ROOT, '.git-gate', 'tripwire')
