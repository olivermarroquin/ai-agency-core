"""Resolve the WordPress application password without manual env-var export.

Why this module exists
----------------------
The canonical secret store is Oliver's tier-3 air-gapped vault on local disk
at `~/workspace/second-brain-tier3/`. Before this helper existed, every
script that needed `WP_APP_PASSWORD` required running `export WP_APP_PASSWORD=...`
in the shell first — which (1) is friction-y across multiple sessions and
(2) leaves the password sitting in `~/.zsh_history` in plaintext.

This helper reads the password directly from the tier-3 markdown file, so the
password never has to land in a shell or env var. The env-var path is preserved
as a higher-priority lookup so CI runs or explicit overrides (`export
WP_APP_PASSWORD=...`) still work without modification.

Lookup hierarchy
----------------
1. `$WP_APP_PASSWORD` (or whatever env var name `config["wp_app_password_env"]`
   specifies). If set, return it.
2. The tier-3 markdown file at `config["wp_app_password_tier3_file"]`
   (default: `~/workspace/second-brain-tier3/personal/business-keelworks.md`).
   Look for the bullet entry named by `config["wp_app_password_tier3_identifier"]`
   (default: `core-30-publish-script`).
3. Raise `RuntimeError` with an actionable message.

Expected tier-3 markdown shape
------------------------------
The tier-3 file holds the password in a markdown bullet under the WordPress
Application Passwords section, matching Oliver's convention as of 2026-05-26:

    #### 8. WordPress Application Passwords

    - **core-30-publish-script** — `<password value here>`
      - WP User: `oliver`
      - Generated: 2026-05-24
      - Env var: `WP_APP_PASSWORD`

The parser matches `- **<identifier>** — `<value>`` and extracts the value
between the first pair of backticks after the em-dash. Both the U+2014 em-dash
and an ASCII hyphen are tolerated.

Security notes
--------------
* This helper never logs or prints the password value.
* The tier-3 file already lives in plaintext on disk — reading from it here
  doesn't change the security model, it just removes the redundant copy that
  would otherwise sit in shell history and env-var space.
* If the env var is set, the tier-3 file is never opened or read (so the helper
  works fine in environments where the tier-3 vault isn't mounted, e.g. CI).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_TIER3_FILE = "~/workspace/second-brain-tier3/personal/business-keelworks.md"
DEFAULT_WP_IDENTIFIER = "core-30-publish-script"
DEFAULT_GOOGLE_MAPS_IDENTIFIER = "google-maps-embed-api"
# Kept for backwards compatibility with any direct callers that reference it.
DEFAULT_IDENTIFIER = DEFAULT_WP_IDENTIFIER


def _parse_password_from_markdown(text: str, identifier: str) -> str | None:
    """Extract a password from a markdown bullet of the form:
        - **<identifier>** — `<value>`

    Both the typographic em-dash (U+2014) and the ASCII hyphen are accepted.
    Surrounding whitespace is tolerated. Returns the first match or None.
    """
    pattern = (
        r"-\s*\*\*"
        + re.escape(identifier)
        + r"\*\*\s*[—\-]\s*`([^`]+)`"
    )
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _load_from_env_or_tier3(
    *,
    env_key: str,
    tier3_path: Path,
    identifier: str,
    credential_label: str,
) -> str:
    """Generic credential resolver used by every per-credential wrapper.

    Hierarchy: env var first, then tier-3 markdown, then raise. The wrapper
    functions below (`load_wp_app_password`, `load_google_maps_api_key`, …)
    each plug in their own defaults and call this.

    `credential_label` only appears in error messages — kept human-readable
    so the operator knows exactly which credential is missing.
    """
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value

    if not tier3_path.exists():
        raise RuntimeError(
            f"{credential_label} not found.\n"
            f"  - ${env_key} is not set in environment.\n"
            f"  - Tier-3 fallback path does not exist: {tier3_path}\n"
            f"Either export ${env_key} or create/move the tier-3 file."
        )

    try:
        text = tier3_path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(
            f"Could not read tier-3 file {tier3_path}: {e}"
        ) from e

    value = _parse_password_from_markdown(text, identifier)
    if not value:
        raise RuntimeError(
            f"{credential_label} not found.\n"
            f"  - ${env_key} is not set in environment.\n"
            f"  - Tier-3 file exists ({tier3_path}) but no entry found for "
            f"identifier '{identifier}'.\n"
            f"Expected a line like: - **{identifier}** — `<value>`"
        )
    return value


def load_wp_app_password(config: dict[str, Any]) -> str:
    """Resolve the WordPress application password.

    Reads `config["wp_app_password_env"]` (default `WP_APP_PASSWORD`) from the
    env first, then falls back to the tier-3 markdown file.

    Tier-3 file resolution precedence:
      1. explicit `config["wp_app_password_tier3_file"]`
      2. derived per-client path from `config["client_slug"]` — the standard
         home `~/workspace/second-brain-tier3/clients/<slug>/credentials.md`
      3. legacy hardcoded default (`personal/business-keelworks.md`)
    The identifier defaults to `config["wp_app_password_tier3_identifier"]`
    (default `core-30-publish-script`). This means every client "just works"
    with its own `clients/<slug>/credentials.md` + the default identifier — no
    per-client config edit required.

    Never logs or prints the value. Raises RuntimeError if neither lookup yields.
    """
    tier3_file = config.get("wp_app_password_tier3_file")
    if not tier3_file:
        slug = config.get("client_slug")
        tier3_file = (
            f"~/workspace/second-brain-tier3/clients/{slug}/credentials.md"
            if slug
            else DEFAULT_TIER3_FILE
        )
    return _load_from_env_or_tier3(
        env_key=config.get("wp_app_password_env", "WP_APP_PASSWORD"),
        tier3_path=Path(tier3_file).expanduser(),
        identifier=config.get(
            "wp_app_password_tier3_identifier", DEFAULT_WP_IDENTIFIER
        ),
        credential_label="WP application password",
    )


def load_google_maps_api_key(config: dict[str, Any]) -> str:
    """Resolve the Google Maps Embed API key.

    Reads `config["api_key_env"]` (default `GOOGLE_MAPS_EMBED_API_KEY`) from
    the env first, falls back to tier-3 entry
    `config["google_maps_api_key_tier3_identifier"]`
    (default `google-maps-embed-api`) in the markdown file at
    `config["google_maps_api_key_tier3_file"]` (default tier-3 business-keelworks.md).

    The function reuses the maps config's existing `api_key_env` key for the
    env var name so no config-file edits are needed when adopting this.
    """
    return _load_from_env_or_tier3(
        env_key=config.get("api_key_env", "GOOGLE_MAPS_EMBED_API_KEY"),
        tier3_path=Path(
            config.get("google_maps_api_key_tier3_file", DEFAULT_TIER3_FILE)
        ).expanduser(),
        identifier=config.get(
            "google_maps_api_key_tier3_identifier", DEFAULT_GOOGLE_MAPS_IDENTIFIER
        ),
        credential_label="Google Maps Embed API key",
    )
