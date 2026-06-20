#!/usr/bin/env bash
# load-secrets.sh — Source this to export all build credentials from tier-3.
#
# Usage:
#   source ~/workspace/repos/ai-agency-core/scripts/load-secrets.sh
#
# Or add to .envrc (direnv) for auto-load on workspace entry.
#
# Reads from the consolidated tier-3 vending machine:
#   ~/workspace/second-brain-tier3/automation/secrets/
#
# Never prints secret values. Exports env vars that scripts expect.
# The .key-file-first pattern in _load_secrets.py means most scripts
# DON'T need these env vars — but load-secrets.sh makes them available
# for any tool that checks $ENV first (curl, httpie, ad-hoc scripts).

set -euo pipefail

SECRETS_DIR="$HOME/workspace/second-brain-tier3/automation/secrets"

_load_key() {
    local file="$SECRETS_DIR/$1"
    if [[ -f "$file" ]]; then
        cat "$file"
    else
        echo ""
    fi
}

# --- Per-client WP app passwords (auto-discovered from key files) ---
for _wp_key_file in "$SECRETS_DIR"/wp-app-password-*.key; do
    [[ -f "$_wp_key_file" ]] || continue
    _slug=$(basename "$_wp_key_file" .key | sed 's/^wp-app-password-//')
    _env_name="WP_APP_PASSWORD_$(echo "$_slug" | tr '[:lower:]-' '[:upper:]_')"
    _val=$(cat "$_wp_key_file")
    [[ -n "$_val" ]] && export "$_env_name=$_val"
    unset _slug _env_name _val
done
unset _wp_key_file

# --- API keys ---
_anthropic=$(_load_key "anthropic-claude.key")
_perplexity=$(_load_key "perplexity-sonar.key")
_dataforseo_user=$(_load_key "dataforseo-username")
_dataforseo_pass=$(_load_key "dataforseo-password")
_openai=$(_load_key "openai.key")
_gemini=$(_load_key "gemini.key")
_maps=$(_load_key "google-maps-embed.key")

[[ -n "$_anthropic" ]]      && export ANTHROPIC_API_KEY="$_anthropic"
[[ -n "$_perplexity" ]]     && export PERPLEXITY_API_KEY="$_perplexity"
[[ -n "$_dataforseo_user" ]] && export DATAFORSEO_USERNAME="$_dataforseo_user"
[[ -n "$_dataforseo_pass" ]] && export DATAFORSEO_PASSWORD="$_dataforseo_pass"
[[ -n "$_openai" ]]         && export OPENAI_API_KEY="$_openai"
[[ -n "$_gemini" ]]         && export GEMINI_API_KEY="$_gemini"
[[ -n "$_maps" ]]           && export GOOGLE_MAPS_EMBED_API_KEY="$_maps"

# --- GSC: service account JSON paths (auto-discovered) ---
for _gsc_file in "$SECRETS_DIR"/gsc-sa-*.json; do
    [[ -f "$_gsc_file" ]] || continue
    _slug=$(basename "$_gsc_file" .json | sed 's/^gsc-sa-//')
    _env_name="GSC_SA_$(echo "$_slug" | tr '[:lower:]-' '[:upper:]_')"
    export "$_env_name=$_gsc_file"
    unset _slug _env_name
done
unset _gsc_file

# Clean up temp vars
unset _anthropic _perplexity _dataforseo_user _dataforseo_pass
unset _openai _gemini _maps

echo "✓ Secrets loaded from tier-3 (values not printed)"
