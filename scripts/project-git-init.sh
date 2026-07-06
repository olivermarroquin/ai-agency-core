#!/bin/bash
# project-git-init.sh — initialize git + private GitHub remote for one project repo.
# Promoted from the SEP-FU2 3-repo one-off (2026-07-06); collision handling added
# from the hire-relay lesson (name already existed on the account).
#
# Usage:
#   bash ~/workspace/repos/ai-agency-core/scripts/project-git-init.sh <repo-name>
#
# What it does (safe by construction):
#   - git init -b main (no-op if already a repo)
#   - initial commit stages ONLY .kos/ (reviewed vault content); all other files
#     stay untracked for later operator review — never sweeps WIP
#   - creates a PRIVATE GitHub repo under your gh-authed account and pushes
#   - NAME COLLISION: if the repo name already exists on GitHub, checks whether
#     the remote is EMPTY. Empty -> links + pushes. Non-empty -> STOPS and tells
#     you your options. NEVER force-pushes, never merges on its own.
# Skill-candidacy: fold into app-factory / init-project-vault.sh so new projects
# are born with git + remote + door card (see session-end-protocol-v2-spec item 8).

REPO="$1"
[ -z "$REPO" ] && { echo "usage: project-git-init.sh <repo-name>   (folder under ~/workspace/repos/)"; exit 1; }
DIR="$HOME/workspace/repos/$REPO"
[ -d "$DIR" ] || { echo "ERROR: $DIR not found"; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI not installed (brew install gh)"; exit 1; }
GHUSER=$(gh api user --jq .login) || { echo "ERROR: gh not authenticated (run: gh auth login)"; exit 1; }

cd "$DIR" || exit 1
echo "=== $REPO (github.com/$GHUSER) ==="

[ -d .git ] || git init -b main
rm -f .git/index.lock

if [ -d .kos ]; then
  git add .kos
  git commit -m "Initial commit: .kos project vault + _chat-status.md door card" || echo "  (nothing new to commit)"
else
  echo "  NOTE: no .kos/ folder — run init-project-vault.sh first (knowledge-os-setup skill), then re-run this."
  exit 1
fi

if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
  echo "  OK: pushed to existing origin."
  exit 0
fi

if gh repo create "$REPO" --private --source . --remote origin --push 2>/tmp/pgi-err.txt; then
  echo "  OK: private repo created + pushed."
  exit 0
fi

if grep -qi "already exists" /tmp/pgi-err.txt; then
  SIZE=$(gh api "repos/$GHUSER/$REPO" --jq .size 2>/dev/null)
  if [ "$SIZE" = "0" ]; then
    echo "  Repo name exists on GitHub but is EMPTY — linking and pushing."
    git remote add origin "https://github.com/$GHUSER/$REPO.git"
    git push -u origin main
    echo "  OK: linked to existing empty repo + pushed."
  else
    echo "  STOP: github.com/$GHUSER/$REPO already exists AND HAS CONTENT (size=$SIZE)."
    echo "  Look at it first:  gh repo view $GHUSER/$REPO --web"
    echo "  Your options (operator decision, tell your chat which):"
    echo "   a) different name:  gh repo create $REPO-kos --private --source . --remote origin --push  (run inside $DIR)"
    echo "   b) it's an old/dead repo you want replaced: rename it on GitHub first, then re-run this script"
    echo "   c) it's the SAME project's history: needs a manual merge - do not wire blindly"
  fi
else
  echo "  ERROR from gh:"; cat /tmp/pgi-err.txt
fi
