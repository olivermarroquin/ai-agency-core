"""
gsc_indexing.py — Google Search Console Indexing API auth + submission.

Supports two auth modes (tried in order):
  1. **Service account JSON key** (non-expiring, preferred) — loaded from
     tier-3 via `_load_secrets.load_gsc_service_account(config)`.
  2. **Application Default Credentials (ADC)** (user-level, expires) — falls
     back to `google.auth.default()` if no service account is configured.

The service-account path is the permanent fix for the "GSC keeps expiring"
friction class (D-row 2026-06-08). ADC is preserved as fallback.

Public API
----------
- load_gsc_access_token(config=None) -> (access_token, quota_project_id)
- verify_gsc_credentials(config=None) -> (bool, str)
- request_gsc_indexing(live_url, config) -> status string (never raises)
- GSC_INDEXING_ENDPOINT, GSC_INDEXING_SCOPE constants
"""

from __future__ import annotations

from typing import Any, Optional

try:
    import requests
except ImportError:
    import sys
    sys.stderr.write(
        "ERROR: `requests` is not installed. Run: pip install requests\n"
    )
    sys.exit(2)


GSC_INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
GSC_INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"


def _load_sa_credentials(config: dict[str, Any] | None):
    """Try to load service-account credentials from tier-3. Returns (creds, project) or (None, None)."""
    if not config:
        return None, None
    try:
        from _load_secrets import load_gsc_service_account
    except ImportError:
        return None, None
    sa_data = load_gsc_service_account(config)
    if not sa_data:
        return None, None
    try:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            sa_data, scopes=[GSC_INDEXING_SCOPE]
        )
        return creds, sa_data.get("project_id")
    except Exception:
        return None, None


def load_gsc_access_token(config: dict[str, Any] | None = None) -> tuple[str, Optional[str]]:
    """Return (access_token, quota_project_id) for the Indexing API.

    Tries service-account JSON first (non-expiring), then falls back to ADC.
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

    # 1. Service account (preferred — non-expiring)
    sa_creds, sa_project = _load_sa_credentials(config)
    if sa_creds:
        sa_creds.refresh(GoogleAuthRequest())
        return sa_creds.token, sa_project

    # 2. ADC fallback (user-level, expires)
    try:
        credentials, _project = google.auth.default(scopes=[GSC_INDEXING_SCOPE])
    except Exception as e:
        raise RuntimeError(
            "No GSC credentials found. Options:\n"
            "  1. Place service account JSON at automation/secrets/gsc-sa-<client>.json\n"
            "  2. Run: gcloud auth application-default login "
            "--scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/indexing\n"
            f"(underlying error: {e})"
        ) from e

    credentials.refresh(GoogleAuthRequest())
    quota_project_id = getattr(credentials, "quota_project_id", None)
    return credentials.token, quota_project_id


def verify_gsc_credentials(config: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Pre-flight: verify GSC credentials (service account or ADC).

    Returns (ok, message). Tries service account first, then ADC.
    """
    try:
        import google.auth  # noqa: F811
        from google.auth.transport.requests import Request as GoogleAuthRequest  # noqa: F811
    except ImportError:
        return False, (
            "google-auth is not installed. Run:\n"
            "  pip install google-auth google-auth-httplib2 "
            "google-api-python-client --break-system-packages\n"
            "Then re-run."
        )

    # 1. Try service account
    sa_creds, sa_project = _load_sa_credentials(config)
    if sa_creds:
        try:
            sa_creds.refresh(GoogleAuthRequest())
            return True, (
                f"GSC credentials OK (service account, project: {sa_project}, "
                f"non-expiring)"
            )
        except Exception as e:
            return False, f"Service account JSON found but failed to authenticate: {e}"

    # 2. Fall back to ADC
    try:
        credentials, _project = google.auth.default(scopes=[GSC_INDEXING_SCOPE])
    except Exception as e:
        return False, (
            "No GSC credentials found. Options:\n"
            "  1. Place service account JSON at automation/secrets/gsc-sa-<client>.json\n"
            "  2. Run: gcloud auth application-default login \\\n"
            "    --scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/indexing\n"
            f"(underlying error: {e})"
        )

    try:
        credentials.refresh(GoogleAuthRequest())
    except Exception as e:
        return False, (
            "GSC ADC credentials exist but failed to refresh (expired or revoked). "
            "Re-authenticate:\n"
            "  gcloud auth application-default login \\\n"
            "    --scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/indexing\n"
            f"(underlying error: {e})"
        )

    quota_project_id = getattr(credentials, "quota_project_id", None)
    if not quota_project_id:
        return False, (
            "GSC ADC credentials are valid but no quota project is set — "
            "Indexing API will 403. Run:\n"
            "  gcloud auth application-default set-quota-project <gcp-project-id>"
        )

    return True, (
        f"GSC credentials OK via ADC (quota project: {quota_project_id}, "
        f"token expires: {getattr(credentials, 'expiry', 'unknown')}). "
        f"Consider switching to a service account for non-expiring auth."
    )


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
        access_token, quota_project_id = load_gsc_access_token(config)
    except RuntimeError as e:
        return f"[error] {e}"
    except Exception as e:
        return f"[error] could not load GSC credentials: {e}"

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
            GSC_INDEXING_ENDPOINT, headers=headers, json=body, timeout=30
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
