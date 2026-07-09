#!/bin/bash
# spawn-pair.sh — open a producer + independent peer-reviewer Claude Code pair
# in TWO TABS of one Terminal window, prompts pre-delivered.
# Manual v0 of FLEET-1 (launcher); superseded when fleet-orchestration ships.
#
# Usage (three modes):
#   bash .../spawn-pair.sh                    # default files: _spawn-producer.md / _spawn-reviewer.md
#   bash .../spawn-pair.sh <task-slug>        # slug mode:     _spawn-producer-<slug>.md / _spawn-reviewer-<slug>.md
#   bash .../spawn-pair.sh <prod.md> <rev.md> # explicit file paths
#
# CONCURRENCY: many chats can stage pairs at once — each writes its own
# _spawn-*-<slug>.md files; you run one line per pair, any order, no waiting.
#
# First run: approve macOS prompts (Terminal -> System Events / Terminal) and
# System Settings > Privacy & Security > Accessibility > Terminal ON.

SCRATCH="$HOME/workspace/_scratch"
mkdir -p "$SCRATCH"/{relay,spawn,launch,skip,archive}

ARG1="$1"
if [ -n "$ARG1" ] && [ ! -f "$ARG1" ] && [[ "$ARG1" != */* ]]; then
  SLUG="$ARG1"
  PROD="$SCRATCH/spawn/_spawn-producer-$SLUG.md"
  REV="$SCRATCH/spawn/_spawn-reviewer-$SLUG.md"
  # Backward-compat fallback: check workspace root if not found at new path
  [ -f "$PROD" ] || PROD="$HOME/workspace/_spawn-producer-$SLUG.md"
  [ -f "$REV" ]  || REV="$HOME/workspace/_spawn-reviewer-$SLUG.md"
else
  PROD="${1:-$SCRATCH/spawn/_spawn-producer.md}"
  REV="${2:-$SCRATCH/spawn/_spawn-reviewer.md}"
  # Backward-compat fallback for default mode
  [ -f "$PROD" ] || PROD="${1:-$HOME/workspace/_spawn-producer.md}"
  [ -f "$REV" ]  || REV="${2:-$HOME/workspace/_spawn-reviewer.md}"
  SLUG="default"
fi

[ -f "$PROD" ] || { echo "ERROR: producer prompt not found: $PROD"; exit 1; }
[ -f "$REV" ]  || { echo "ERROR: reviewer prompt not found: $REV"; exit 1; }

# Per-pair relay filenames (suffixed unless default, per the relay convention).
if [ "$SLUG" = "default" ]; then
  RELAY_P="~/workspace/_scratch/relay/_relay-producer.md"; RELAY_R="~/workspace/_scratch/relay/_relay-reviewer.md"
else
  RELAY_P="~/workspace/_scratch/relay/_relay-producer-$SLUG.md"; RELAY_R="~/workspace/_scratch/relay/_relay-reviewer-$SLUG.md"
fi

# 1) Reviewer gets a relay-aware standby preamble prepended automatically.
ASSEMBLED="$SCRATCH/spawn/_spawn-reviewer-assembled-$SLUG.md"
cat > "$ASSEMBLED" <<EOF
STANDBY CONTEXT: the paired producer chat is being spawned in parallel RIGHT NOW — it has produced no outputs or deliverables yet. Do your Opening/setup reading, then stand by. The working exchange follows the Producer<->Reviewer Relay Convention in CLAUDE.md, using THIS PAIR'S relay files: the producer writes full updates to $RELAY_P and the operator will tell you to read it; you verify everything ON DISK (never from producer claims), then write your full reply to $RELAY_R and print to the operator EXACTLY this two-line form (label line, then the read-command ALONE on its own bare line — no bullet, no prefix — so the operator can triple-click it):

Tell producer:
read $RELAY_R

Same bare-line rule for ANY command you hand the operator (skip runners etc.): the command sits alone on its own line, nothing else on that line. AND never as the FIRST line of a message — the terminal glues a bullet marker to first lines; always put one short text line above any command.

THREE STANDING DUTIES (no operator interpretation required):
1. GATE-CLEARING IS YOUR JOB: when the producer's relay contains a Stop-hook block listing DELIVERABLE files you have verified, YOU run the log-review-pass.py command from that block (your session ID as --reviewer-session, a real verdict JSON as --verdict-file) to clear the producer's gate. Plumbing-only blocks: tell the operator a skip is OK. The operator never decides this — you do, and you say so in your relay reply.
2. FINAL MESSAGE SIGNAL: when your review is fully complete (verdict issued, bookkeeping rows landed, nothing further needed), title your last relay message "FINAL — review complete, no further messages" and tell the operator this chat can be closed. Expect the producer to do the same; the pair is over when BOTH finals have appeared.
3. OPERATOR RELAY MIRROR: every time you print operator-facing output (runner commands, commit scripts, gate-block dumps, skip requests, final summaries), also append it verbatim to ~/workspace/_scratch/relay/_relay-operator-$SLUG.md. The strategic/Cowork chat reads this file to vet skips and commit scripts — the operator never copies terminal output between sessions. Scratch, never committed.

Your spawn prompt follows.

---

EOF
cat "$REV" >> "$ASSEMBLED"

# 2) Quote-free launcher helpers, suffixed per pair (no clobbering between pairs).
PL="$SCRATCH/launch/.pair-launch-producer-$SLUG.sh"
RL="$SCRATCH/launch/.pair-launch-reviewer-$SLUG.sh"

cat > "$PL" <<EOF
#!/bin/bash
cd \$HOME/workspace
exec claude --dangerously-skip-permissions "\$(cat '$PROD')"
EOF

cat > "$RL" <<EOF
#!/bin/bash
cd \$HOME/workspace
exec claude --dangerously-skip-permissions "\$(cat '$ASSEMBLED')"
EOF

chmod +x "$PL" "$RL"

# 3) One window, two tabs. AppleScript strings contain no double quotes.
osascript <<OSA
tell application "Terminal"
	activate
	do script "bash '$PL'"
end tell
delay 1.2
tell application "System Events" to tell process "Terminal" to keystroke "t" using command down
delay 0.8
tell application "Terminal"
	do script "bash '$RL'" in front window
end tell
OSA

if [ $? -ne 0 ]; then
  echo ""
  echo "AppleScript failed (permission denied mid-run?). Manual fallback — one per tab:"
  echo "  bash $PL"
  echo "  bash $RL"
  exit 1
fi

echo ""
echo "Spawned pair [$SLUG]:"
echo "  Tab 1 = PRODUCER   ($PROD)"
echo "  Tab 2 = REVIEWER   ($ASSEMBLED)"
echo "Relay: $RELAY_P <-> $RELAY_R"
