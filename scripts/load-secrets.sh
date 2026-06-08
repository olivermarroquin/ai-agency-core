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

# --- Per-client WP app passwords (no collision) ---
_wp_ev=$(_load_key "wp-app-password-ev-electric-services.key")
_wp_sh=$(_load_key "wp-app-password-s-and-h-contracting.key")
[[ -n "$_wp_ev" ]] && export WP_APP_PASSWORD_EV_ELECTRIC_SERVICES="$_wp_ev"
[[ -n "$_wp_sh" ]] && export WP_APP_PASSWORD_S_AND_H_CONTRACTING="$_wp_sh"

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

# --- GSC: service account JSON paths (not values — just paths for tools that check env) ---
_gsc_ev="$SECRETS_DIR/gsc-sa-ev-electric-services.json"
_gsc_sh="$SECRETS_DIR/gsc-sa-s-and-h-contracting.json"
[[ -f "$_gsc_ev" ]] && export GSC_SA_EV_ELECTRIC="$_gsc_ev"
[[ -f "$_gsc_sh" ]] && export GSC_SA_S_AND_H="$_gsc_sh"

# Clean up temp vars
unset _wp_ev _wp_sh _anthropic _perplexity _dataforseo_user _dataforseo_pass
unset _openai _gemini _maps _gsc_ev _gsc_sh

echo "✓ Secrets loaded from tier-3 (values not printed)"
