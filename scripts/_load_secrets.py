"""Resolve credentials from the consolidated tier-3 secrets store.

Lookup hierarchy (per credential)
---------------------------------
1. **Vending-machine `.key` file** in `~/workspace/second-brain-tier3/automation/secrets/`
   (or per-client variant). Plain-text value, no quotes, no newline. This is
   the primary source after the 2026-06-08 credential consolidation.
2. **Environment variable** (e.g. `$WP_APP_PASSWORD_EV`). If set, used. This
   lets CI runs or explicit overrides work without the tier-3 vault.
3. **Tier-3 markdown fallback** (e.g. `clients/<slug>/credentials.md`).
   Parses the ``- **identifier** — `value``` convention. Non-destructive
   backwards compatibility — the markdown files remain untouched.
4. Raise `RuntimeError` with an actionable message.

Security notes
--------------
* Never logs or prints credential values.
* Tier-3 files are local-disk-only, never committed, never synced to cloud.
* The `.key` pattern matches the existing vending-machine discipline used by
  `perplexity_sonar.py`, `claude_query.py`, `choose-image-variant.py`, etc.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

TIER3_SECRETS_DIR = Path.home() / "workspace" / "second-brain-tier3" / "automation" / "secrets"
DEFAULT_TIER3_FILE = "~/workspace/second-brain-tier3/personal/business-keelworks.md"
DEFAULT_WP_IDENTIFIER = "core-30-publish-script"
DEFAULT_GOOGLE_MAPS_IDENTIFIER = "google-maps-embed-api"
DEFAULT_IDENTIFIER = DEFAULT_WP_IDENTIFIER


def _read_key_file(path: Path) -> str | None:
    """Read a plain-text `.key` file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value if value else None
    except OSError:
        return None


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


def _resolve_credential(
    *,
    key_file: Path | None = None,
    env_key: str,
    tier3_md_path: Path | None = None,
    tier3_md_identifier: str = "",
    credential_label: str,
) -> str:
    """Consolidated credential resolver. Four-level hierarchy:

    1. .key file (vending machine) — primary
    2. env var — CI / explicit override
    3. tier-3 markdown — backwards-compat fallback
    4. raise RuntimeError
    """
    # 1. Vending-machine .key file
    if key_file:
        value = _read_key_file(key_file)
        if value:
            return value

    # 2. Environment variable
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value

    # 3. Tier-3 markdown fallback
    if tier3_md_path and tier3_md_path.exists() and tier3_md_identifier:
        try:
            text = tier3_md_path.read_text(encoding="utf-8")
            value = _parse_password_from_markdown(text, tier3_md_identifier)
            if value:
                return value
        except OSError:
            pass

    # 4. Raise with actionable message
    sources_tried = []
    if key_file:
        sources_tried.append(f"  - .key file: {key_file} (not found or empty)")
    sources_tried.append(f"  - ${env_key} env var (not set)")
    if tier3_md_path:
        sources_tried.append(f"  - tier-3 markdown: {tier3_md_path} (identifier: '{tier3_md_identifier}')")
    raise RuntimeError(
        f"{credential_label} not found. Tried:\n" + "\n".join(sources_tried)
    )


def load_wp_app_password(config: dict[str, Any]) -> str:
    """Resolve the WordPress application password for a specific client.

    Per-client keying: each client has its own .key file and env var to prevent
    collision when interleaving builds across clients.

    Hierarchy:
      1. .key file: automation/secrets/wp-app-password-<client_slug>.key
      2. env var: WP_APP_PASSWORD_<SLUG> (e.g. WP_APP_PASSWORD_EV_ELECTRIC)
         Falls back to generic WP_APP_PASSWORD for backwards compat.
      3. tier-3 markdown: clients/<slug>/credentials.md
      4. raise
    """
    slug = config.get("client_slug", "")
    slug_upper = slug.replace("-", "_").upper()

    # Per-client .key file
    key_file = TIER3_SECRETS_DIR / f"wp-app-password-{slug}.key"

    # Per-client env var (falls back to generic)
    env_key = f"WP_APP_PASSWORD_{slug_upper}" if slug_upper else "WP_APP_PASSWORD"
    env_value = os.environ.get(env_key) or os.environ.get("WP_APP_PASSWORD")
    if env_value:
        os.environ[env_key] = env_value  # normalize for downstream

    # Tier-3 markdown fallback
    tier3_file = config.get("wp_app_password_tier3_file")
    if not tier3_file and slug:
        tier3_file = f"~/workspace/second-brain-tier3/clients/{slug}/credentials.md"
    tier3_md_path = Path(tier3_file).expanduser() if tier3_file else None

    return _resolve_credential(
        key_file=key_file,
        env_key=env_key,
        tier3_md_path=tier3_md_path,
        tier3_md_identifier=config.get("wp_app_password_tier3_identifier", DEFAULT_WP_IDENTIFIER),
        credential_label=f"WP application password ({slug or 'unknown client'})",
    )


def load_google_maps_api_key(config: dict[str, Any]) -> str:
    """Resolve the Google Maps Embed API key.

    Hierarchy:
      1. .key file: automation/secrets/google-maps-embed.key
      2. env var: GOOGLE_MAPS_EMBED_API_KEY
      3. tier-3 markdown: business-keelworks.md
      4. raise
    """
    return _resolve_credential(
        key_file=TIER3_SECRETS_DIR / "google-maps-embed.key",
        env_key=config.get("api_key_env", "GOOGLE_MAPS_EMBED_API_KEY"),
        tier3_md_path=Path(
            config.get("google_maps_api_key_tier3_file", DEFAULT_TIER3_FILE)
        ).expanduser(),
        tier3_md_identifier=config.get(
            "google_maps_api_key_tier3_identifier", DEFAULT_GOOGLE_MAPS_IDENTIFIER
        ),
        credential_label="Google Maps Embed API key",
    )


def load_gsc_service_account(config: dict[str, Any]) -> dict | None:
    """Load GSC service account credentials from a JSON key file.

    Hierarchy:
      1. config["gsc_service_account_path"] (explicit per-client path)
      2. automation/secrets/gsc-sa-<client_slug>.json
      3. Return None (caller falls back to ADC or skips)

    Returns the parsed JSON dict, or None if no service account is configured.
    """
    # Explicit config path
    explicit = config.get("gsc_service_account_path")
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    # Convention-based path
    slug = config.get("client_slug", "")
    if slug:
        path = TIER3_SECRETS_DIR / f"gsc-sa-{slug}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    return None
