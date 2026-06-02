#!/usr/bin/env bash
# append_event_log.sh — Append a single row to the vault event log.
#
# Usage:
#   append_event_log.sh <surface> <pass_or_iter> <chat_id> <summary>
#
# Arguments:
#   surface       — Which file/area changed (e.g., _active-chats-tracker, tier-3 automation)
#   pass_or_iter  — Pass number or "n/a" if not a tracker pass
#   chat_id       — Chat ID per convention: <project-slug>-<topic>-<YYYYMMDDHHMM>
#   summary       — One-line description of the significant edit
#
# Appends a pipe-delimited Markdown table row with UTC timestamp.
# Uses >> redirect-append to avoid merge collisions across parallel sessions.

set -euo pipefail

if [ $# -ne 4 ]; then
  echo "Usage: append_event_log.sh <surface> <pass_or_iter> <chat_id> <summary>" >&2
  echo "Error: expected 4 arguments, got $#" >&2
  exit 1
fi

SURFACE="$1"
PASS_OR_ITER="$2"
CHAT_ID="$3"
SUMMARY="$4"

# Auto-detect workspace root: Mac terminal vs Cowork sandbox
if [ -d "$HOME/workspace/second-brain/_meta" ]; then
  EVENT_LOG="$HOME/workspace/second-brain/_meta/_event-log.md"
elif [ -d "$HOME/mnt/workspace/second-brain/_meta" ]; then
  EVENT_LOG="$HOME/mnt/workspace/second-brain/_meta/_event-log.md"
else
  echo "Error: cannot locate second-brain/_meta/ in ~/workspace/ or ~/mnt/workspace/" >&2
  exit 1
fi

if [ ! -f "$EVENT_LOG" ]; then
  echo "Error: event log not found at $EVENT_LOG" >&2
  exit 1
fi

printf '| %s | %s | %s | %s | %s |\n' \
  "$(date -u +"%F %T")" \
  "$SURFACE" \
  "$PASS_OR_ITER" \
  "$CHAT_ID" \
  "$SUMMARY" \
  >> "$EVENT_LOG"
