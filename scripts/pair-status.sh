#!/bin/bash
# pair-status.sh — one command answers: "which pairs/chats can I close RIGHT NOW?"
# Read-only. Scans relay files for FINAL signals + handoff statuses + close declarations.
# Usage:  bash ~/workspace/repos/ai-agency-core/scripts/pair-status.sh

W="$HOME/workspace"
SB="$W/second-brain"
echo "==================== PAIR / SESSION STATUS ===================="

# 1) Relay pairs: FINAL signal present in each direction?
#    Scan both _scratch/relay/ (new convention) and workspace root (legacy in-flight pairs).
FOUND_PAIR=0
for P in "$W"/_scratch/relay/_relay-producer*.md "$W"/_relay-producer*.md; do
  [ -f "$P" ] || continue
  FOUND_PAIR=1
  SLUG=$(basename "$P" .md | sed 's/_relay-producer-\{0,1\}//'); [ -z "$SLUG" ] && SLUG="(default)"
  R="${P/producer/reviewer}"
  PF="no"; RF="no"
  head -3 "$P" 2>/dev/null | grep -qi "FINAL" && PF="YES"
  [ -f "$R" ] && head -3 "$R" 2>/dev/null | grep -qi "FINAL" && RF="YES"
  if [ "$PF" = "YES" ] && [ "$RF" = "YES" ]; then
    echo "PAIR $SLUG: CLOSABLE — both FINALs on disk. Close both tabs."
  else
    echo "PAIR $SLUG: NOT closable — producer FINAL: $PF | reviewer FINAL: $RF"
  fi
done
[ "$FOUND_PAIR" = "0" ] && echo "(no relay files on disk — no pairs mid-flight by relay evidence)"

echo "---------------------------------------------------------------"
# 2) Handoffs still ACTIVE (sessions that have NOT completed their close):
echo "Handoffs still status: active (their sessions are NOT closable):"
grep -rl "^status: active" "$SB/_meta/handoffs" --include="*.md" 2>/dev/null | grep -v "_active-chats-tracker" | while read -r F; do
  echo "  - $(basename "$F")"
done
ACT=$(grep -rl "^status: active" "$SB/_meta/handoffs" --include="*.md" 2>/dev/null | grep -vc "_active-chats-tracker")
[ "$ACT" = "0" ] && echo "  (none — every handoff-spawned session has closed or not yet opened)"

echo "---------------------------------------------------------------"
# 3) Scratch that indicates pending operator action:
#    Scan both _scratch/skip/ (new convention) and workspace root (legacy).
for S in "$W"/_scratch/skip/.gate-skip*.sh "$W"/.gate-skip*.sh; do
  [ -f "$S" ] && echo "PENDING SKIP not yet run: bash ${S/$HOME/~} && rm ${S/$HOME/~}"
done
for C in "$W"/second-brain/.commit.sh "$W"/repos/*/.commit.sh "$W"/ai-factory/.commit.sh "$W"/skills/.commit.sh; do
  [ -f "$C" ] && echo "PENDING COMMIT not yet run: bash ${C/$HOME/~}"
done
echo "==============================================================="
