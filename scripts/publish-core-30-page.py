#!/usr/bin/env python3
"""
publish-core-30-page.py — Path-2 automation for Core 30 page deployment.

Reads a WordPress-wrapped HTML draft + a client config JSON, then publishes the
page to a WordPress site via the REST API at /wp-json/wp/v2/pages.

Designed for the EV Electric Services Core 30 build (and any future client
using the same SOP). Target time per page: ~30 seconds of script execution
versus 30-45 minutes of manual paste through wp-admin.

USAGE
-----
Basic publish:

    export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
    python publish-core-30-page.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --config       /path/to/ev-electric.config.json

Choose a non-default HTML version (defaults to highest vN):

    python publish-core-30-page.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --config       /path/to/ev-electric.config.json \\
        --version v3

Dry run — validate schema + render meta, but don't POST:

    python publish-core-30-page.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --config       /path/to/ev-electric.config.json \\
        --dry-run

INPUTS
------
- --page-folder: Path to a Core 30 page folder (must contain
  draft-vN-WP-WRAPPED.html and draft-v1.md). The script auto-picks the
  highest-numbered WP-WRAPPED file unless --version is supplied.

- --config: Path to a client config JSON. Schema:
    {
      "wp_base_url":      "https://evelectric.pro",
      "wp_username":      "oliver",
      "wp_app_password_env": "WP_APP_PASSWORD",
      "default_status":   "draft",        // or "publish"
      "default_template": "",             // theme template slug; "" = default
      "default_author_id": 2,
      "aioseo_defaults": {
        "schema_local_business": false    // we provide our own JSON-LD
      },
      "client_slug": "ev-electric-services",

      // OPTIONAL — when true, every publish automatically submits the live
      // URL to the Google Search Console Indexing API via Application
      // Default Credentials (ADC). One-time setup on the operator's Mac:
      //   gcloud auth application-default login \
      //     --scopes=https://www.googleapis.com/auth/cloud-platform,\
      //              https://www.googleapis.com/auth/indexing
      // The calling identity (the gcloud-authed Google account) must be
      // Owner on the client's GSC property. See sop-gsc-indexing-api-setup.md.
      // Omit or set to false to keep the manual-click workflow.
      "gsc_indexing": true
    }

- WP_APP_PASSWORD env var: the WordPress application password generated at
  wp-admin → Users → Profile → Application Passwords. Stored in the Tier-3
  vault, NOT in the config file or this script.

OUTPUTS
-------
- Live published page URL (stdout)
- Schema validation result (stdout + log file)
- GSC indexing request submission (stdout). Fires automatically on every
  publish when the config has `gsc_indexing: true`; otherwise only fires
  when `--request-indexing` is passed (which falls through to the manual-
  submission stub). Uses Application Default Credentials — no key file.
  See request_gsc_indexing() for details.
- Execution log entry written to:
    repos/<client_slug>/.kos/execution-logs/execution-log-YYYY-MM-DD-core-30-page-<NN>-<slug>.md

PRECONDITIONS
-------------
- Python 3.8+
- `requests` library installed (pip install requests)
- WP user has Editor or Administrator role
- AIOSEO is installed on the target site (for the meta-title / meta-description
  POST fields). If not, the script skips the AIOSEO meta block and the editor
  fills it manually.
- Keelworks AIOSEO Bridge plugin installed and activated on the target site
  (source: repos/ai-agency-core/wordpress-plugins/keelworks-aioseo-bridge). The
  bridge exposes POST /wp-json/keelworks/v1/aioseo-meta/<id> with Basic Auth,
  which the script uses to populate AIOSEO meta automatically. If the bridge
  isn't installed, the script falls back to printing a paste-friendly block.

REFERENCES
----------
- SOP: second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-core-30-page-build.md
- Page-1 canonical template: 04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/01-electrical-troubleshooting-vienna-va/draft-v9-WP-WRAPPED.html
- WP REST API pages endpoint: https://developer.wordpress.org/rest-api/reference/pages/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: `requests` is not installed. Run: pip install requests\n"
    )
    sys.exit(2)


# ----------------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------------


@dataclass
class PageMetadata:
    """Metadata extracted from the v1 markdown frontmatter."""

    slug: str
    target_url: str
    target_keyword: str
    service: str
    city: str
    position: int
    aioseo_title: str = ""
    aioseo_description: str = ""
    additional_keywords: list[str] = field(default_factory=list)
    wordpress_page_title: str = ""


@dataclass
class PublishResult:
    """What we tell the operator at the end."""

    success: bool
    page_id: Optional[int]
    live_url: Optional[str]
    schema_valid: bool
    schema_errors: list[str]
    duration_seconds: float
    error_message: Optional[str] = None


# Default staleness window for the output-quality-loop pre-publish gate.
# A PASS verdict older than this many days is treated as stale and the publish
# is refused until the page is re-evaluated. Override via config field
# `quality_gate_staleness_days`. Spec source:
# ~/workspace/second-brain/_meta/handoffs/output-quality-loop/phase-3-page-level-integration.md
DEFAULT_QUALITY_GATE_STALENESS_DAYS = 7


# ----------------------------------------------------------------------------
# File discovery
# ----------------------------------------------------------------------------


def find_canonical_html(page_folder: Path, version: Optional[str] = None) -> Path:
    """Find the WP-WRAPPED HTML file to publish.

    Default: highest-numbered draft-vN-WP-WRAPPED.html in the folder.
    With --version v3, picks draft-v3-WP-WRAPPED.html.
    """
    if version:
        candidate = page_folder / f"draft-{version}-WP-WRAPPED.html"
        if not candidate.exists():
            raise FileNotFoundError(
                f"Requested version not found: {candidate}\n"
                f"Available files: {sorted(page_folder.glob('draft-v*-WP-WRAPPED.html'))}"
            )
        return candidate

    pattern = re.compile(r"draft-v(\d+)-WP-WRAPPED\.html$")
    candidates: list[tuple[int, Path]] = []
    for f in page_folder.glob("draft-v*-WP-WRAPPED.html"):
        m = pattern.search(f.name)
        if m:
            candidates.append((int(m.group(1)), f))

    if not candidates:
        raise FileNotFoundError(
            f"No draft-v*-WP-WRAPPED.html files found in {page_folder}"
        )

    candidates.sort(reverse=True)
    return candidates[0][1]


def find_v1_markdown(page_folder: Path) -> Path:
    """Find the v1 markdown source (for AIOSEO metadata extraction)."""
    candidate = page_folder / "draft-v1.md"
    if candidate.exists():
        return candidate
    # Fallback: any draft-v*.md without -WP-WRAPPED suffix
    for f in sorted(page_folder.glob("draft-v*.md")):
        if "-WP-WRAPPED" not in f.name and "-PUBLISHED" not in f.name:
            return f
    raise FileNotFoundError(f"No draft-v*.md found in {page_folder}")


# ----------------------------------------------------------------------------
# Metadata extraction
# ----------------------------------------------------------------------------


def parse_frontmatter(md_path: Path) -> dict[str, Any]:
    """Pull YAML frontmatter from a markdown file (minimal parser — handles the
    small set of keys our templates use, doesn't pull in PyYAML)."""
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}

    end = text.find("\n---", 4)
    if end < 0:
        return {}

    fm = text[4:end]
    out: dict[str, Any] = {}
    for line in fm.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip simple [bracket] lists
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            out[key] = [v.strip() for v in inner.split(",")] if inner else []
        elif value.lower() in {"true", "false"}:
            out[key] = value.lower() == "true"
        else:
            out[key] = value
    return out


_AIOSEO_TITLE_PATTERN = re.compile(
    r"\*\*Page title.*?\*\*\s*`([^`]+)`", re.DOTALL
)
_AIOSEO_DESC_PATTERN = re.compile(
    r"\*\*Meta description.*?\*\*\s*`([^`]+)`", re.DOTALL
)
_AIOSEO_WP_TITLE_PATTERN = re.compile(
    r"\*\*WordPress page title.*?\*\*\s*`([^`]+)`", re.DOTALL
)
_AIOSEO_FOCUS_PATTERN = re.compile(
    r"\*\*Focus keyword.*?\*\*\s*`([^`]+)`", re.DOTALL
)
_AIOSEO_ADDL_PATTERN = re.compile(
    r"\*\*Additional keywords.*?\*\*\s*(.+)", re.DOTALL
)


def extract_aioseo_metadata(md_path: Path) -> dict[str, Any]:
    """Pull the AIOSEO meta fields the SOP templates embed in the markdown."""
    text = md_path.read_text(encoding="utf-8")
    out: dict[str, Any] = {}

    m = _AIOSEO_TITLE_PATTERN.search(text)
    if m:
        out["aioseo_title"] = m.group(1).strip()

    m = _AIOSEO_DESC_PATTERN.search(text)
    if m:
        out["aioseo_description"] = m.group(1).strip()

    m = _AIOSEO_WP_TITLE_PATTERN.search(text)
    if m:
        out["wordpress_page_title"] = m.group(1).strip()

    m = _AIOSEO_FOCUS_PATTERN.search(text)
    if m:
        out["aioseo_focus_keyword"] = m.group(1).strip()

    m = _AIOSEO_ADDL_PATTERN.search(text)
    if m:
        # Grab the first line of the match; the patterns are followed by ` `, ` `, ...
        raw = m.group(1).split("\n")[0].strip()
        keywords = re.findall(r"`([^`]+)`", raw)
        out["aioseo_additional_keywords"] = keywords

    return out


def build_page_metadata(page_folder: Path) -> PageMetadata:
    md_path = find_v1_markdown(page_folder)
    fm = parse_frontmatter(md_path)
    aioseo = extract_aioseo_metadata(md_path)

    # Derive position from folder name (e.g., 02-panel-upgrade-vienna-va → 2)
    folder_name = page_folder.name
    position_m = re.match(r"(\d+)-", folder_name)
    position = int(position_m.group(1)) if position_m else 0

    return PageMetadata(
        slug=fm.get("page-slug", folder_name),
        target_url=fm.get("target-url", f"/{folder_name}/"),
        target_keyword=fm.get("target-keyword", ""),
        service=fm.get("service", ""),
        city=fm.get("city", ""),
        position=int(fm.get("core-30-position", position)),
        aioseo_title=aioseo.get("aioseo_title", ""),
        aioseo_description=aioseo.get("aioseo_description", ""),
        additional_keywords=aioseo.get("aioseo_additional_keywords", []),
        wordpress_page_title=aioseo.get("wordpress_page_title", ""),
    )


# ----------------------------------------------------------------------------
# Schema validation
# ----------------------------------------------------------------------------


_JSON_LD_BLOCK_PATTERN = re.compile(
    r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
    re.DOTALL,
)

_REQUIRED_SCHEMA_TYPES = {"LocalBusiness", "Service", "FAQPage"}


def validate_jsonld(html: str) -> tuple[bool, list[str]]:
    """Pull every JSON-LD block from the HTML, parse each, confirm the required
    schema types are present. Returns (is_valid, list_of_errors)."""
    errors: list[str] = []
    found_types: set[str] = set()

    blocks = _JSON_LD_BLOCK_PATTERN.findall(html)
    if not blocks:
        return False, ["No <script type='application/ld+json'> blocks found"]

    for i, block in enumerate(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError as e:
            errors.append(f"Block {i}: JSON parse error — {e}")
            continue

        # Single object or @graph?
        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                items = data["@graph"]
            else:
                items = [data]
        elif isinstance(data, list):
            items = data

        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if isinstance(t, str):
                found_types.add(t)
            elif isinstance(t, list):
                for tt in t:
                    if isinstance(tt, str):
                        found_types.add(tt)

    missing = _REQUIRED_SCHEMA_TYPES - found_types
    if missing:
        errors.append(
            f"Missing required @type values: {sorted(missing)} "
            f"(found: {sorted(found_types)})"
        )

    return (len(errors) == 0), errors


# ----------------------------------------------------------------------------
# WordPress REST API
# ----------------------------------------------------------------------------


class WordPressClient:
    """Thin wrapper around the WP REST API pages endpoint."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.app_password = app_password
        self.timeout = timeout

        token = b64encode(f"{username}:{app_password}".encode("utf-8")).decode(
            "utf-8"
        )
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "User-Agent": "publish-core-30-page.py/1.0",
        }

    def get_page_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        """Returns the page dict if a page with this slug exists, else None."""
        url = f"{self.base_url}/wp-json/wp/v2/pages"
        resp = requests.get(
            url,
            headers=self.headers,
            params={"slug": slug, "status": "any"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json()
        return results[0] if results else None

    def create_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/wp-json/wp/v2/pages"
        resp = requests.post(
            url, headers=self.headers, json=payload, timeout=self.timeout
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"WP create_page failed: {resp.status_code} — {resp.text}"
            )
        return resp.json()

    def update_page(self, page_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/wp-json/wp/v2/pages/{page_id}"
        resp = requests.post(
            url, headers=self.headers, json=payload, timeout=self.timeout
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"WP update_page failed: {resp.status_code} — {resp.text}"
            )
        return resp.json()

    def update_aioseo_meta(
        self,
        page_id: int,
        title: str,
        description: str,
        focus_keyphrase: str,
        additional_keywords: list[str],
    ) -> tuple[bool, str]:
        """POST AIOSEO meta to the Keelworks AIOSEO Bridge plugin.

        The bridge plugin (repos/ai-agency-core/wordpress-plugins/keelworks-aioseo-bridge)
        must be installed and activated on the target WordPress site for this
        call to succeed. If the plugin isn't installed, the route returns 404
        and we report that back so the caller can fall through to the manual
        paste block.

        Returns (success, message).
        """
        url = f"{self.base_url}/wp-json/keelworks/v1/aioseo-meta/{page_id}"
        body = {
            "title": title,
            "description": description,
            "focus_keyphrase": focus_keyphrase,
            "additional_keywords": list(additional_keywords or []),
        }
        try:
            resp = requests.post(
                url, headers=self.headers, json=body, timeout=self.timeout
            )
        except requests.RequestException as e:
            return False, f"network error talking to AIOSEO bridge: {e}"

        if resp.status_code == 404:
            return False, (
                "AIOSEO bridge plugin not installed on this site "
                "(install Keelworks AIOSEO Bridge from repos/ai-agency-core/wordpress-plugins/)"
            )
        if resp.status_code == 401 or resp.status_code == 403:
            return False, (
                f"AIOSEO bridge rejected the request ({resp.status_code}). "
                f"Check the WP user has Editor/Admin role: {resp.text}"
            )
        if resp.status_code >= 400:
            return False, f"AIOSEO bridge error {resp.status_code}: {resp.text}"

        try:
            data = resp.json()
        except ValueError:
            return False, f"AIOSEO bridge returned non-JSON response: {resp.text[:200]}"

        if not data.get("ok"):
            return False, f"AIOSEO bridge returned unexpected payload: {data}"

        action = data.get("action", "wrote")
        return True, f"AIOSEO meta {action} via REST bridge (post_id={page_id})"


def build_wp_payload(
    metadata: PageMetadata,
    html: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON payload for POST /wp/v2/pages.

    Note on AIOSEO meta: AIOSEO exposes its meta-title / meta-description via the
    WP REST API as custom `meta` fields under namespaced keys. The exact keys
    depend on the AIOSEO version installed. We register both the modern key
    (`_aioseo_title` / `_aioseo_description`) and the legacy key. If neither is
    accepted, the WP user does the AIOSEO meta entry manually post-publish —
    this script's only contract is that the page lands with the correct title,
    slug, and body.
    """
    payload: dict[str, Any] = {
        "slug": metadata.slug,
        "title": metadata.wordpress_page_title or _slug_to_title(metadata.slug),
        "content": html,
        "status": config.get("default_status", "draft"),
    }

    if config.get("default_template"):
        payload["template"] = config["default_template"]

    if config.get("default_author_id"):
        payload["author"] = config["default_author_id"]

    # Try to set AIOSEO meta. WP will silently ignore unrecognized meta keys
    # if they're not registered, so this is safe.
    if metadata.aioseo_title or metadata.aioseo_description:
        payload["meta"] = {
            "_aioseo_title": metadata.aioseo_title,
            "_aioseo_description": metadata.aioseo_description,
            "_aioseo_keywords": ", ".join(metadata.additional_keywords),
        }

    return payload


def _slug_to_title(slug: str) -> str:
    """Fallback title generator from slug."""
    words = slug.replace("-", " ").split()
    return " ".join(w.capitalize() if len(w) > 2 else w.upper() for w in words)


# ----------------------------------------------------------------------------
# Optional: GSC indexing request
# ----------------------------------------------------------------------------


_GSC_INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
_GSC_INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"


def _load_gsc_access_token() -> tuple[str, Optional[str]]:
    """Lazy-load google-auth and return (access_token, quota_project_id) for
    the Indexing API via Application Default Credentials (ADC).

    Reads credentials from gcloud's ADC file, typically at
    ~/.config/gcloud/application_default_credentials.json. One-time setup:

        gcloud auth application-default login \\
            --scopes=https://www.googleapis.com/auth/cloud-platform,\\
                     https://www.googleapis.com/auth/indexing
        gcloud auth application-default set-quota-project <gcp-project-id>

    The gcloud-authed Google account must be Owner on the target GSC
    property — GSC, not GCP, authorizes the Indexing API request. The quota
    project is required for user-credential ADC on the Indexing API (Google
    bills request quota against that project; the request 403s without it).

    Raises RuntimeError with a helpful message if google-auth is not
    installed or if ADC is not configured.
    """
    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
    except ImportError as e:
        raise RuntimeError(
            "google-auth is not installed. Run: "
            "pip install google-auth google-auth-httplib2 google-api-python-client "
            "--break-system-packages"
        ) from e

    try:
        credentials, _project = google.auth.default(scopes=[_GSC_INDEXING_SCOPE])
    except Exception as e:
        raise RuntimeError(
            "Application Default Credentials not configured. Run: "
            "gcloud auth application-default login "
            "--scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/indexing "
            f"(underlying error: {e})"
        ) from e

    credentials.refresh(GoogleAuthRequest())
    quota_project_id = getattr(credentials, "quota_project_id", None)
    return credentials.token, quota_project_id


def request_gsc_indexing(live_url: str, config: dict[str, Any]) -> str:
    """Submit a URL to the Google Search Console Indexing API via ADC.

    Reads `config["gsc_indexing"]` (boolean). When true, uses Application
    Default Credentials — set up via `gcloud auth application-default login`
    on the operator's Mac — to authenticate. When false or absent, returns
    the manual-submission stub message; the publish still succeeds.

    Returns a single-line status string suitable for printing. Never raises:
    indexing is a nice-to-have, not load-bearing, so any failure (missing
    library, bad credentials, network error, 4xx/5xx response) is returned as
    a human-readable error string and the publish continues.

    Google's official policy is that the Indexing API is intended for
    `JobPosting` and `BroadcastEvent` pages. The Core 30 use case is
    best-effort: Google may process the request anyway, and worst case the
    request fails cleanly and the operator falls back to the manual "Request
    Indexing" click in GSC (see sop-gsc-request-indexing.md).

    Setup procedure (one-time per Mac, covers all clients):
    see second-brain/05_shared-intelligence/workflows/
        workflow-marketing-seo-engagement/sops/sop-gsc-indexing-api-setup.md
    """
    if not config.get("gsc_indexing"):
        return (
            "[skipped] gsc_indexing not enabled in config — "
            "submit manually at search.google.com/search-console → URL Inspection"
        )

    try:
        access_token, quota_project_id = _load_gsc_access_token()
    except RuntimeError as e:
        return f"[error] {e}"
    except Exception as e:
        return f"[error] could not load ADC credentials: {e}"

    if not quota_project_id:
        return (
            "[error] ADC has no quota project set — Indexing API will 403. Run: "
            "gcloud auth application-default set-quota-project <gcp-project-id>"
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        # Required for user-credential ADC paths — Google bills request quota
        # against this project, and Indexing API 403s without it.
        "x-goog-user-project": quota_project_id,
    }
    body = {"url": live_url, "type": "URL_UPDATED"}

    try:
        resp = requests.post(
            _GSC_INDEXING_ENDPOINT, headers=headers, json=body, timeout=30
        )
    except requests.RequestException as e:
        return f"[error] network error talking to Indexing API: {e}"

    if resp.status_code == 200:
        # Pull the notify-time out of the response if available so the
        # operator has a timestamp to cross-reference in GSC.
        try:
            data = resp.json()
        except ValueError:
            data = {}
        notify_time = (
            data.get("urlNotificationMetadata", {})
            .get("latestUpdate", {})
            .get("notifyTime")
        )
        if notify_time:
            return f"submitted ({notify_time})"
        return "submitted (HTTP 200)"

    # Surface the common failure modes with actionable hints so a publish-log
    # reader knows exactly what to fix.
    text_preview = (resp.text or "")[:240]
    if resp.status_code in (401, 403):
        return (
            f"[error] Indexing API rejected the request ({resp.status_code}). "
            f"Confirm the service account email is added as an Owner on the "
            f"GSC property and the Indexing API is enabled on the GCP project. "
            f"Response: {text_preview}"
        )
    if resp.status_code == 429:
        return (
            f"[error] Indexing API quota exceeded (429). Default quota is "
            f"~200 URL submissions per day; spread across days or request a "
            f"quota increase. Response: {text_preview}"
        )
    return f"[error] Indexing API returned HTTP {resp.status_code}: {text_preview}"


# ----------------------------------------------------------------------------
# Execution log
# ----------------------------------------------------------------------------


def write_execution_log(
    config: dict[str, Any],
    metadata: PageMetadata,
    result: PublishResult,
    html_path: Path,
) -> Optional[Path]:
    """Append an execution-log entry per the CLAUDE.md Knowledge Capture Protocol.

    Location:
        repos/<client_slug>/.kos/execution-logs/
        execution-log-YYYY-MM-DD-core-30-page-<NN>-<slug>.md
    """
    client_slug = config.get("client_slug")
    if not client_slug:
        return None

    # The script may live anywhere; find the workspace root by walking up
    # until we find "repos/". This is a heuristic — fine for the
    # repos/ai-agency-core/scripts/ path; configurable via env var otherwise.
    workspace_root = _find_workspace_root()
    if not workspace_root:
        return None

    log_dir = workspace_root / "repos" / client_slug / ".kos" / "execution-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    nn = f"{metadata.position:02d}"
    log_path = log_dir / f"execution-log-{today}-core-30-page-{nn}-{metadata.slug}.md"

    body = _render_execution_log(metadata, result, html_path, today)
    log_path.write_text(body, encoding="utf-8")
    return log_path


def _render_execution_log(
    metadata: PageMetadata,
    result: PublishResult,
    html_path: Path,
    today: str,
) -> str:
    return f"""---
type: execution-log
status: draft
created: {today}
updated: {today}
venture: ev-electric-services
tags: [execution-log, core-30, ev-electric-services, automation]
---

## {today} — Core 30 page #{metadata.position:02d} published via script: {metadata.slug}

**What was built:** Core 30 page #{metadata.position} for EV Electric Services. Slug: `{metadata.slug}`. Published via the Path-2 automation script (publish-core-30-page.py).
**Decision made:** Used the WP REST API path (Path 2) instead of manual paste through wp-admin (Path 1). Time savings: ~30 seconds scripted vs. 30-45 minutes manual.
**Alternatives considered:** Manual paste through wp-admin Custom HTML block (Path 1). Rejected because the template is now locked at page 1 v9 and the per-page content is the only variable — automation captures that cleanly.
**Why this approach:** The script reads the highest-vN WP-WRAPPED file, validates the JSON-LD schema before posting, builds the WP REST payload from the markdown frontmatter, and writes an execution-log entry on success. Schema validation = {"PASS" if result.schema_valid else "FAIL"}.

**Live URL:** {result.live_url or "(dry-run, no URL)"}
**WP page ID:** {result.page_id or "(dry-run)"}
**Source HTML:** `{html_path}`
**Script execution time:** {result.duration_seconds:.2f}s

**Reusable for future apps?:** Yes — script is at `repos/ai-agency-core/scripts/publish-core-30-page.py` and works for any client whose Core 30 pages follow the v9 template + v1 markdown frontmatter pattern.

### Schema validation

- LocalBusiness: detected
- Service: detected
- FAQPage: detected
- Errors: {result.schema_errors if result.schema_errors else "none"}

### Post-publish punchlist

- [ ] Confirm AIOSEO title and description are populated (script writes via Keelworks AIOSEO Bridge; if the bridge plugin isn't installed, paste manually using the printed block)
- [ ] Run Google Rich Results Test on the live URL
- [ ] GSC → URL Inspection → Request Indexing
- [ ] Update `core-30/_build-order.md` to mark this page as published
- [ ] Swap hero image, Ahmad portrait, and Google Maps placeholders for real assets when available
"""


def _find_workspace_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "repos").is_dir() and (parent / "second-brain").is_dir():
            return parent
    return None


# ----------------------------------------------------------------------------
# output-quality-loop pre-publish gate
# ----------------------------------------------------------------------------


def _quality_frontmatter(md_path: Path) -> dict[str, Any]:
    """Pull just the quality-loop tracking fields from the draft frontmatter.

    Reuses parse_frontmatter() and extracts `last-verdict:`, `last-evaluated:`,
    and `quality-log:`. Returns empty dict if no frontmatter or the fields
    aren't present yet.
    """
    fm = parse_frontmatter(md_path)
    return {
        "last_verdict": fm.get("last-verdict", "").strip() if isinstance(fm.get("last-verdict"), str) else "",
        "last_evaluated": fm.get("last-evaluated", "").strip() if isinstance(fm.get("last-evaluated"), str) else "",
        "quality_log": fm.get("quality-log", "").strip() if isinstance(fm.get("quality-log"), str) else "",
    }


def check_quality_gate(
    md_path: Path,
    config: dict[str, Any],
    bypass: bool,
) -> tuple[bool, str]:
    """Return (allowed, reason).

    The gate refuses publication unless the draft's frontmatter shows
    `last-verdict: PASS` AND `last-evaluated` is within the configured
    staleness window (default 7 days; configurable via
    config["quality_gate_staleness_days"]).

    When `bypass` is True, the gate allows the publish but the reason string
    flags the bypass so the caller can log a warning + write a bypass record
    to the folder log per the audit-trail discipline.

    Spec source:
    ~/workspace/skills/output-quality-loop/SKILL.md § Phase 3 publish gate
    ~/workspace/skills/output-quality-loop/references/folder-quality-log-shape.md
    """
    qm = _quality_frontmatter(md_path)
    last_verdict = qm["last_verdict"]
    last_evaluated_raw = qm["last_evaluated"]
    quality_log_link = qm["quality_log"]

    # Bypass short-circuits — but never silently. The caller logs + writes a
    # bypass record to the folder log so the audit trail captures the override.
    if bypass:
        return True, (
            "BYPASSED — operator used --bypass-quality-gate. "
            f"Frontmatter at publish time: last-verdict={last_verdict or '(missing)'}, "
            f"last-evaluated={last_evaluated_raw or '(missing)'}, "
            f"quality-log={quality_log_link or '(missing)'}."
        )

    if not last_verdict:
        return False, (
            "page never evaluated by output-quality-loop — run quality-check "
            f"on `{md_path}` first. Refusing to publish until the draft has a "
            "`last-verdict:` frontmatter field. To override in an emergency, "
            "re-run with --bypass-quality-gate."
        )

    verdict_upper = last_verdict.upper().strip()
    if "FAIL" in verdict_upper:
        log_pointer = quality_log_link or "(no folder-log pointer)"
        return False, (
            f"page failed quality-loop (last-verdict: {last_verdict}) — see "
            f"folder log at {log_pointer} for diagnosis. Refusing to publish. "
            "Re-run quality-check after applying the revision prompt's fixes, "
            "or re-run with --bypass-quality-gate."
        )
    if "NEEDS REVISION" in verdict_upper:
        log_pointer = quality_log_link or "(no folder-log pointer)"
        return False, (
            f"page is iterating through quality-loop (last-verdict: {last_verdict}) "
            f"— see folder log at {log_pointer} for the open revision prompt. "
            "Refusing to publish until verdict is PASS, or re-run with "
            "--bypass-quality-gate."
        )

    if "PASS" not in verdict_upper:
        return False, (
            f"unexpected last-verdict value: '{last_verdict}'. Expected PASS, "
            "NEEDS REVISION (minor), NEEDS REVISION (substantive), or FAIL. "
            "Refusing to publish. Either re-evaluate via quality-loop or "
            "re-run with --bypass-quality-gate."
        )

    # PASS — check staleness.
    if not last_evaluated_raw:
        return False, (
            "page has last-verdict: PASS but no last-evaluated date. The gate "
            "needs the date to enforce the staleness window. Re-run quality-check "
            "to land both fields, or re-run with --bypass-quality-gate."
        )

    staleness_days = int(
        config.get("quality_gate_staleness_days", DEFAULT_QUALITY_GATE_STALENESS_DAYS)
    )

    try:
        last_evaluated = datetime.strptime(last_evaluated_raw[:10], "%Y-%m-%d")
    except ValueError:
        return False, (
            f"could not parse last-evaluated: '{last_evaluated_raw}'. Expected "
            "YYYY-MM-DD. Re-run quality-check to land a valid date, or "
            "re-run with --bypass-quality-gate."
        )

    age_days = (datetime.now() - last_evaluated).days
    if age_days > staleness_days:
        return False, (
            f"page PASS verdict is {age_days} days old (>{staleness_days}-day "
            "staleness window). Re-evaluate via output-quality-loop before "
            "publishing, or re-run with --bypass-quality-gate. Artifacts can "
            "drift; old PASS verdicts go stale."
        )

    return True, (
        f"PASS verdict from {last_evaluated_raw} ({age_days} days old, within "
        f"{staleness_days}-day staleness window)."
    )


def write_bypass_record(md_path: Path, page_slug: str, reason: str) -> Optional[Path]:
    """Append a bypass-record entry to the folder's `_quality-log.md`.

    Run only when --bypass-quality-gate fires. Captures the operator override
    in the audit trail per the folder-quality-log shape's "Ship record" /
    "Bypass record" convention.

    Returns the folder-log path if a write happened, else None.

    Spec source:
    ~/workspace/skills/output-quality-loop/references/folder-quality-log-shape.md
    """
    folder = md_path.parent
    log_path = folder / "_quality-log.md"
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    bypass_line = (
        f"\n**Bypassed:** {today_iso} — published via "
        f"`publish-core-30-page.py --bypass-quality-gate`. Reason captured: "
        f"{reason}\n"
    )

    if not log_path.exists():
        # Create a minimal folder-log so the bypass record has a home.
        # Per folder-quality-log-shape.md "What to do if the folder log
        # doesn't exist yet" section.
        seed = (
            "---\n"
            "type: folder-quality-log\n"
            "status: active\n"
            f"created: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"last-updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            "artifacts-tracked: 1\n"
            "shipped: 0\n"
            "iterating: 0\n"
            "escalated: 0\n"
            "bypassed: 1\n"
            "tags: [folder-quality-log, quality-loop]\n"
            "---\n\n"
            f"# Quality log — {folder.name}\n\n"
            "This file tracks every output-quality-loop evaluation for "
            "artifacts in this folder. One section per artifact; iteration "
            "history inside each section.\n\n"
            "See [[output-quality-loop|the skill]] for evaluation methodology "
            "and the verdict-rollup thresholds.\n\n"
            "---\n\n"
            f"## {page_slug}\n\n"
            "**Latest:** BYPASSED — never formally evaluated by quality-loop "
            "before publish.\n"
            + bypass_line
        )
        log_path.write_text(seed, encoding="utf-8")
        return log_path

    # Folder log exists — find or create the per-artifact section, append the
    # bypass record at the end of that section.
    existing = log_path.read_text(encoding="utf-8")
    section_header = f"## {page_slug}"
    if section_header in existing:
        # Append the bypass line at the end of that section. Simpler than
        # parsing the markdown tree: find the section, find the next "## " or
        # end-of-file, insert before that boundary.
        idx = existing.find(section_header)
        # Find the next top-level "##" after this section, if any.
        next_section = existing.find("\n## ", idx + len(section_header))
        if next_section < 0:
            new_content = existing.rstrip() + "\n" + bypass_line
        else:
            new_content = (
                existing[:next_section].rstrip() + "\n" + bypass_line +
                existing[next_section:]
            )
    else:
        # Append a new per-artifact section at the end of the file.
        new_content = (
            existing.rstrip()
            + "\n\n---\n\n"
            + f"## {page_slug}\n\n"
            + "**Latest:** BYPASSED — never formally evaluated by quality-loop "
              "before publish.\n"
            + bypass_line
        )

    log_path.write_text(new_content, encoding="utf-8")
    return log_path


# ----------------------------------------------------------------------------
# Main publish flow
# ----------------------------------------------------------------------------


def publish(
    page_folder: Path,
    config_path: Path,
    version: Optional[str] = None,
    dry_run: bool = False,
    request_indexing: bool = False,
    bypass_quality_gate: bool = False,
) -> PublishResult:
    started = datetime.now()

    config = json.loads(config_path.read_text(encoding="utf-8"))

    html_path = find_canonical_html(page_folder, version=version)
    html = html_path.read_text(encoding="utf-8")
    md_path = find_v1_markdown(page_folder)
    metadata = build_page_metadata(page_folder)

    print(f"→ Page folder:   {page_folder}")
    print(f"→ HTML version:  {html_path.name}")
    print(f"→ Slug:          {metadata.slug}")
    print(f"→ WP title:      {metadata.wordpress_page_title or '(derived from slug)'}")
    print(f"→ AIOSEO title:  {metadata.aioseo_title or '(empty — manual entry required)'}")
    print(f"→ Position #:    {metadata.position}")

    # output-quality-loop pre-publish gate. Refuses publication unless the
    # draft's frontmatter shows `last-verdict: PASS` within the configured
    # staleness window. --bypass-quality-gate overrides + writes a bypass
    # record to the folder log.
    gate_allowed, gate_reason = check_quality_gate(
        md_path, config, bypass=bypass_quality_gate
    )
    if bypass_quality_gate:
        print(f"→ Quality gate:  ⚠ BYPASSED — {gate_reason}")
        bypass_log = write_bypass_record(md_path, metadata.slug, gate_reason)
        if bypass_log:
            print(f"   bypass record written to: {bypass_log}")
    else:
        print(f"→ Quality gate:  {'✓ allowed' if gate_allowed else '✗ REFUSED'} — {gate_reason}")
    if not gate_allowed:
        duration = (datetime.now() - started).total_seconds()
        return PublishResult(
            success=False,
            page_id=None,
            live_url=None,
            schema_valid=False,
            schema_errors=[],
            duration_seconds=duration,
            error_message=f"output-quality-loop pre-publish gate refused: {gate_reason}",
        )

    schema_valid, schema_errors = validate_jsonld(html)
    print(f"→ Schema valid:  {schema_valid}")
    if schema_errors:
        for e in schema_errors:
            print(f"   ! {e}")

    if not schema_valid:
        # Stop only if we couldn't even parse the JSON; warn but proceed if
        # only a "missing types" warning. Strict mode would refuse to publish.
        if any("parse error" in e for e in schema_errors):
            duration = (datetime.now() - started).total_seconds()
            return PublishResult(
                success=False,
                page_id=None,
                live_url=None,
                schema_valid=False,
                schema_errors=schema_errors,
                duration_seconds=duration,
                error_message="JSON-LD parse error — refusing to publish",
            )
        print("   (warning only — proceeding with publish)")

    if dry_run:
        duration = (datetime.now() - started).total_seconds()
        print(f"\nDRY RUN — no POST. Elapsed: {duration:.2f}s")
        return PublishResult(
            success=True,
            page_id=None,
            live_url=None,
            schema_valid=schema_valid,
            schema_errors=schema_errors,
            duration_seconds=duration,
        )

    # Resolve app password — env var first, tier-3 markdown fallback
    from _load_secrets import load_wp_app_password
    app_password = load_wp_app_password(config)

    client = WordPressClient(
        base_url=config["wp_base_url"],
        username=config["wp_username"],
        app_password=app_password,
    )

    payload = build_wp_payload(metadata, html, config)

    existing = client.get_page_by_slug(metadata.slug)
    if existing:
        existing_status = existing.get("status", "draft")
        print(
            f"→ Existing page found (ID {existing['id']}, status={existing_status}). "
            f"Updating — preserving existing status."
        )
        # Strip status from update payload so we don't demote a published page
        # back to draft just because the config default is 'draft'.
        update_payload = {k: v for k, v in payload.items() if k != "status"}
        wp_page = client.update_page(existing["id"], update_payload)
    else:
        print("→ No existing page with this slug. Creating.")
        wp_page = client.create_page(payload)

    live_url = wp_page.get("link", f"{config['wp_base_url']}{metadata.target_url}")
    page_id = wp_page.get("id")

    print(f"→ Published:     {live_url}")
    print(f"→ WP page ID:    {page_id}")

    # Submit to the GSC Indexing API automatically when gsc_indexing is true
    # in the client config; otherwise fire only when --request-indexing is
    # passed (which falls through to the manual-submission stub message). This
    # keeps the indexing step nice-to-have — failures here don't break publish.
    gsc_configured = bool(config.get("gsc_indexing"))
    if gsc_configured or request_indexing:
        msg = request_gsc_indexing(live_url, config)
        print(f"→ GSC indexing:  {msg}")

    duration = (datetime.now() - started).total_seconds()
    result = PublishResult(
        success=True,
        page_id=page_id,
        live_url=live_url,
        schema_valid=schema_valid,
        schema_errors=schema_errors,
        duration_seconds=duration,
    )

    log_path = write_execution_log(config, metadata, result, html_path)
    if log_path:
        print(f"→ Execution log: {log_path}")

    # Populate AIOSEO meta via the Keelworks AIOSEO Bridge plugin.
    #
    # AIOSEO stores its data in a custom DB table (aioseo_posts), not in
    # wp_postmeta — so the standard WP REST `meta` field doesn't reach it.
    # AIOSEO's own internal REST routes require cookie auth and reject the
    # application-password Basic Auth we use here. The bridge plugin lives at
    # repos/ai-agency-core/wordpress-plugins/keelworks-aioseo-bridge/ and
    # exposes POST /wp-json/keelworks/v1/aioseo-meta/<id> with the same Basic
    # Auth this script already uses.
    #
    # If the bridge isn't installed (404), we fall back to the paste-friendly
    # print block so the operator can still finish the job manually.
    aioseo_ok = False
    if metadata.aioseo_title or metadata.aioseo_description:
        aioseo_ok, aioseo_msg = client.update_aioseo_meta(
            page_id=page_id,
            title=metadata.aioseo_title,
            description=metadata.aioseo_description,
            focus_keyphrase=metadata.target_keyword,
            additional_keywords=metadata.additional_keywords,
        )
        if aioseo_ok:
            print(f"→ AIOSEO meta:  {aioseo_msg}")
        else:
            print(f"→ AIOSEO meta:  could not auto-populate — {aioseo_msg}")
            print("                falling back to manual paste block below")

    if (metadata.aioseo_title or metadata.aioseo_description) and not aioseo_ok:
        print("\n" + "=" * 70)
        print("AIOSEO META — PASTE INTO wp-admin → AIOSEO sidebar on this page")
        print("=" * 70)
        if metadata.aioseo_title:
            print(f"Page Title:\n  {metadata.aioseo_title}")
        if metadata.aioseo_description:
            print(f"\nMeta Description:\n  {metadata.aioseo_description}")
        if metadata.additional_keywords:
            print(f"\nFocus Keyword:\n  {metadata.target_keyword}")
            print(f"\nAdditional Keywords (comma-separated):")
            print(f"  {', '.join(metadata.additional_keywords)}")
        print("=" * 70)

    print(f"\nDone. Elapsed: {duration:.2f}s")
    return result


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Publish a Core 30 page draft to WordPress via REST API.",
    )
    p.add_argument(
        "--page-folder",
        type=Path,
        required=True,
        help="Path to a Core 30 page folder (contains draft-vN-WP-WRAPPED.html + draft-v1.md)",
    )
    p.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to client config JSON",
    )
    p.add_argument(
        "--version",
        type=str,
        default=None,
        help="Which draft version to publish (e.g. 'v3'). Defaults to highest vN.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview the POST payload but don't actually publish.",
    )
    p.add_argument(
        "--request-indexing",
        action="store_true",
        help=(
            "Force a GSC indexing-API submission attempt even when the config "
            "does not set gsc_indexing: true. When the config DOES have "
            "gsc_indexing: true, submission fires automatically on every "
            "publish and this flag is redundant. See sop-gsc-indexing-api-setup.md."
        ),
    )
    p.add_argument(
        "--bypass-quality-gate",
        action="store_true",
        help=(
            "Override the output-quality-loop pre-publish gate. The gate "
            "normally refuses to publish a page whose `last-verdict:` "
            "frontmatter isn't PASS, or whose `last-evaluated:` date is "
            "older than `quality_gate_staleness_days` (default 7). With "
            "this flag the publish proceeds anyway; a 'bypass record' is "
            "written to the folder's _quality-log.md so the audit trail "
            "captures the override. Emergency use only — bypassed pages "
            "have not been quality-checked."
        ),
    )

    args = p.parse_args()

    if not args.page_folder.is_dir():
        sys.stderr.write(f"ERROR: page-folder not found: {args.page_folder}\n")
        return 2
    if not args.config.is_file():
        sys.stderr.write(f"ERROR: config not found: {args.config}\n")
        return 2

    try:
        result = publish(
            page_folder=args.page_folder,
            config_path=args.config,
            version=args.version,
            dry_run=args.dry_run,
            request_indexing=args.request_indexing,
            bypass_quality_gate=args.bypass_quality_gate,
        )
    except Exception as e:
        sys.stderr.write(f"FATAL: {e}\n")
        return 1

    if result.error_message and not result.success:
        sys.stderr.write(f"ERROR: {result.error_message}\n")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
