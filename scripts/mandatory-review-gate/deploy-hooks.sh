#!/usr/bin/env bash
# deploy-hooks.sh — install review-gate hook config into a repo's .claude/settings.json
#
# Usage:
#   deploy-hooks.sh <repo-path>              # PostToolUse only (default)
#   deploy-hooks.sh --with-stop <repo-path>  # PostToolUse + Stop hook
#
# Idempotent: merges into existing settings.json without overwriting other config.
# Creates .claude/settings.json if it doesn't exist.
#
# The hook commands use absolute paths to the scripts in ai-agency-core so they
# work regardless of the target repo's location.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRACK_SCRIPT="${SCRIPT_DIR}/dirty-ledger-track.py"
GATE_SCRIPT="${SCRIPT_DIR}/mandatory-review-gate.py"

WITH_STOP=false

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-stop)
            WITH_STOP=true
            shift
            ;;
        -h|--help)
            echo "Usage: deploy-hooks.sh [--with-stop] <repo-path>"
            echo ""
            echo "Installs review-gate PostToolUse hooks into <repo-path>/.claude/settings.json."
            echo "Use --with-stop to also install the Stop hook (parked by default)."
            exit 0
            ;;
        *)
            REPO_PATH="$1"
            shift
            ;;
    esac
done

if [[ -z "${REPO_PATH:-}" ]]; then
    echo "Error: repo path required. Usage: deploy-hooks.sh [--with-stop] <repo-path>" >&2
    exit 1
fi

if [[ ! -d "$REPO_PATH" ]]; then
    echo "Error: $REPO_PATH is not a directory" >&2
    exit 1
fi

# Ensure .claude/ exists
SETTINGS_DIR="${REPO_PATH}/.claude"
SETTINGS_FILE="${SETTINGS_DIR}/settings.json"
mkdir -p "$SETTINGS_DIR"

# Build the hooks JSON
POST_TOOL_USE=$(cat <<PTEOF
[
  {
    "matcher": "Write|Edit|NotebookEdit",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${TRACK_SCRIPT}",
        "timeout": 10
      }
    ]
  },
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${TRACK_SCRIPT}",
        "timeout": 10
      }
    ]
  }
]
PTEOF
)

STOP_HOOK=$(cat <<SEOF
[
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${GATE_SCRIPT}",
        "timeout": 10
      }
    ]
  }
]
SEOF
)

# Merge into existing settings.json using python3 (available everywhere)
python3 - "$SETTINGS_FILE" "$POST_TOOL_USE" "$STOP_HOOK" "$WITH_STOP" <<'PYEOF'
import json
import sys
import os

settings_file = sys.argv[1]
ptu_json = sys.argv[2]
stop_json = sys.argv[3]
with_stop = sys.argv[4] == "true"

# Load existing or start fresh
if os.path.exists(settings_file):
    with open(settings_file, 'r') as f:
        try:
            settings = json.load(f)
        except json.JSONDecodeError:
            settings = {}
else:
    settings = {}

if not isinstance(settings, dict):
    settings = {}

# Ensure hooks key
if 'hooks' not in settings:
    settings['hooks'] = {}

# Set PostToolUse (always)
settings['hooks']['PostToolUse'] = json.loads(ptu_json)

# Set Stop (only with --with-stop)
if with_stop:
    settings['hooks']['Stop'] = json.loads(stop_json)
elif 'Stop' in settings['hooks']:
    # Don't remove existing Stop config if present — just don't add it
    pass

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

action = "PostToolUse + Stop" if with_stop else "PostToolUse only"
print(f"[deploy-hooks] Installed {action} into {settings_file}")
PYEOF
