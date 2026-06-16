#!/bin/bash
# install-git-hooks.sh — Install the mandatory review gate git hooks.
#
# Installs a pre-commit hook that enforces the review gate at commit time.
# The hook calls git_hook_adapter.py, which checks staged files against the
# dirty ledger and blocks the commit if unreviewed agent-produced files exist.
#
# IDEMPOTENT: Safe to run multiple times. Overwrites only the gate's own
# hook (identified by the REVIEW_GATE_HOOK marker). Preserves any existing
# pre-commit hook by chaining (calls the original after the gate check).
#
# REVERSIBLE: Run with --uninstall to remove the gate hook and restore any
# backed-up original.
#
# Usage:
#   ./install-git-hooks.sh <repo-path> [<repo-path> ...]
#   ./install-git-hooks.sh --all          # Install in all workspace repos
#   ./install-git-hooks.sh --uninstall <repo-path> [<repo-path> ...]
#   ./install-git-hooks.sh --uninstall --all
#
# Created by [RGH-2] (2026-06-16).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER="$SCRIPT_DIR/git_hook_adapter.py"
MARKER="# REVIEW_GATE_HOOK — managed by install-git-hooks.sh (RGH-2)"

# All workspace repos (siblings under ~/workspace that have .git/)
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

find_workspace_repos() {
    local repos=()
    for dir in "$WORKSPACE_ROOT"/*/; do
        if [ -d "$dir/.git" ]; then
            repos+=("$dir")
        fi
    done
    # Also check nested repos/ directory
    if [ -d "$WORKSPACE_ROOT/repos" ]; then
        for dir in "$WORKSPACE_ROOT"/repos/*/; do
            if [ -d "$dir/.git" ]; then
                repos+=("$dir")
            fi
        done
    fi
    # Also check skills/ directory
    if [ -d "$WORKSPACE_ROOT/skills" ] && [ -d "$WORKSPACE_ROOT/skills/.git" ]; then
        repos+=("$WORKSPACE_ROOT/skills/")
    fi
    echo "${repos[@]}"
}

install_hook() {
    local repo_path="$1"
    local hooks_dir="$repo_path/.git/hooks"
    local hook_path="$hooks_dir/pre-commit"
    local backup_path="$hooks_dir/pre-commit.pre-review-gate"

    if [ ! -d "$repo_path/.git" ]; then
        echo "SKIP: $repo_path — not a git repo"
        return
    fi

    mkdir -p "$hooks_dir"

    # Check if already installed (idempotent)
    if [ -f "$hook_path" ] && grep -q "REVIEW_GATE_HOOK" "$hook_path" 2>/dev/null; then
        echo "OK (already installed): $repo_path"
        return
    fi

    # Backup existing hook if present
    if [ -f "$hook_path" ]; then
        cp "$hook_path" "$backup_path"
        echo "  Backed up existing pre-commit → $backup_path"
    fi

    # Write the hook
    cat > "$hook_path" << HOOKEOF
#!/bin/sh
$MARKER
# Mandatory review gate — Tier B enforcement (substrate-agnostic).
# Blocks commit if unreviewed agent-produced files are staged.
# Override: git commit --no-verify (operator-only, audited).
# Uninstall: $SCRIPT_DIR/install-git-hooks.sh --uninstall $repo_path

# Run the gate check
python3 "$ADAPTER" --hook pre-commit
gate_exit=\$?

if [ \$gate_exit -ne 0 ]; then
    exit \$gate_exit
fi

# Chain to original pre-commit hook if it was backed up
if [ -f "$backup_path" ]; then
    exec "$backup_path"
fi

exit 0
HOOKEOF

    chmod +x "$hook_path"
    echo "INSTALLED: $repo_path"
}

uninstall_hook() {
    local repo_path="$1"
    local hooks_dir="$repo_path/.git/hooks"
    local hook_path="$hooks_dir/pre-commit"
    local backup_path="$hooks_dir/pre-commit.pre-review-gate"

    if [ ! -d "$repo_path/.git" ]; then
        echo "SKIP: $repo_path — not a git repo"
        return
    fi

    if [ ! -f "$hook_path" ]; then
        echo "OK (no hook): $repo_path"
        return
    fi

    # Only remove if it's our hook (identified by marker)
    if ! grep -q "REVIEW_GATE_HOOK" "$hook_path" 2>/dev/null; then
        echo "SKIP: $repo_path — pre-commit exists but is NOT the review gate hook"
        return
    fi

    rm "$hook_path"

    # Restore backup if present
    if [ -f "$backup_path" ]; then
        mv "$backup_path" "$hook_path"
        echo "UNINSTALLED + restored original: $repo_path"
    else
        echo "UNINSTALLED: $repo_path"
    fi
}

# --- Main ---

UNINSTALL=false
ALL=false
REPOS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --all)
            ALL=true
            shift
            ;;
        *)
            REPOS+=("$1")
            shift
            ;;
    esac
done

if $ALL; then
    read -ra REPOS <<< "$(find_workspace_repos)"
fi

if [ ${#REPOS[@]} -eq 0 ]; then
    echo "Usage: $0 [--uninstall] [--all | <repo-path> ...]"
    echo ""
    echo "Install the mandatory review gate pre-commit hook."
    echo ""
    echo "Options:"
    echo "  --all         Install/uninstall in all workspace repos"
    echo "  --uninstall   Remove the gate hook (restore any backup)"
    echo ""
    echo "One-command uninstall for all repos:"
    echo "  $0 --uninstall --all"
    exit 1
fi

echo ""
if $UNINSTALL; then
    echo "=== Uninstalling review gate git hooks ==="
else
    echo "=== Installing review gate git hooks ==="
fi
echo ""

for repo in "${REPOS[@]}"; do
    # Normalize path
    repo="$(cd "$repo" 2>/dev/null && pwd || echo "$repo")"
    if $UNINSTALL; then
        uninstall_hook "$repo"
    else
        install_hook "$repo"
    fi
done

echo ""
echo "Done."
if ! $UNINSTALL; then
    echo ""
    echo "One-command uninstall:"
    echo "  $SCRIPT_DIR/install-git-hooks.sh --uninstall --all"
fi
