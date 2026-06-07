"""
gsc_indexing.py — Google Search Console Indexing API via Application Default
Credentials (ADC).

Extracted from publish-core-30-page.py so that any script can submit URLs to
the Indexing API without duplicating auth or HTTP logic.

Public API
----------
- load_gsc_access_token() -> (access_token, quota_project_id)
- request_gsc_indexing(live_url, config) -> status string (never raises)
- GSC_INDEXING_ENDPOINT, GSC_INDEXING_SCOPE constants

Setup (one-time per Mac):
    gcloud auth application-default login \
        --scopes=https://www.googleapis.com/auth/cloud-platform,\
                 https://www.googleapis.com/auth/indexing
    gcloud auth application-default set-quota-project <gcp-project-id>

See sop-gsc-indexing-api-setup.md for the full procedure.
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


def load_gsc_access_token() -> tuple[str, Optional[str]]:
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
        credentials, _project = google.auth.default(scopes=[GSC_INDEXING_SCOPE])
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


def verify_gsc_credentials() -> tuple[bool, str]:
    """Pre-flight check: verify GSC ADC credentials are configured and valid.

    Loads Application Default Credentials, refreshes the token, and confirms a
    quota project is set — everything needed for the Indexing API to succeed.
    Does NOT submit any URL. Cheap enough to call at run start before any
    page work begins.

    Returns (ok, message):
      (True,  "GSC credentials OK ...")  — ready to index
      (False, "... run `gcloud auth application-default login` ...")  — actionable fix

    Generic: works for any pipeline that uses GSC indexing via ADC, not just
    Core 30 or any specific client.
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

    try:
        credentials, _project = google.auth.default(scopes=[GSC_INDEXING_SCOPE])
    except Exception as e:
        return False, (
            "GSC Application Default Credentials not configured. Run:\n"
            "  gcloud auth application-default login \\\n"
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
        f"GSC credentials OK (quota project: {quota_project_id}, "
        f"token expires: {getattr(credentials, 'expiry', 'unknown')})"
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
        access_token, quota_project_id = load_gsc_access_token()
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
