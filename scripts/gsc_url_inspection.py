"""
gsc_url_inspection.py — Google Search Console URL Inspection API client.

Inspects URLs via the urlInspection.index.inspect endpoint and produces
per-client index-status reports with coverage state, reasons, and
blocker-verification data.

Supports two auth modes (tried in order):
  1. **Service account JSON key** (non-expiring, preferred) — loaded from
     tier-3 via `_load_secrets.load_gsc_service_account(config)`.
  2. **Application Default Credentials (ADC)** (user-level, expires) — falls
     back to `google.auth.default()` if no service account is configured.

Requires the `https://www.googleapis.com/auth/webmasters` scope (full, not
readonly). See sop-gsc-indexing-api-setup.md Step 4.

Public API
----------
- inspect_url(url, site_url, config=None) -> dict
- inspect_client_urls(client_slug, urls) -> list[dict]
- pull_client_index_report(client_slug, urls=None) -> dict
- write_client_index_report(client_slug, report, output_dir=None) -> (md_path, json_path)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: `requests` is not installed. Run: pip install requests\n"
    )
    sys.exit(2)


URL_INSPECTION_SCOPE = "https://www.googleapis.com/auth/webmasters"
URL_INSPECTION_ENDPOINT = (
    "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
)

# ---------------------------------------------------------------------------
# Auth (mirrors gsc_search_analytics.py / gsc_indexing.py)
# ---------------------------------------------------------------------------

def _load_sa_credentials(config: dict[str, Any] | None):
    """Try to load service-account credentials from tier-3."""
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
            sa_data, scopes=[URL_INSPECTION_SCOPE]
        )
        return creds, sa_data.get("project_id")
    except Exception:
        return None, None


def _load_access_token(config: dict[str, Any] | None = None) -> tuple[str, Optional[str]]:
    """Return (access_token, quota_project_id) for the URL Inspection API."""
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
        credentials, _project = google.auth.default(
            scopes=[URL_INSPECTION_SCOPE]
        )
    except Exception as e:
        raise RuntimeError(
            "No GSC credentials found. Options:\n"
            "  1. Place service account JSON at automation/secrets/gsc-sa-<client>.json\n"
            "  2. Run: gcloud auth application-default login \\\n"
            "     --scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/indexing,"
            "https://www.googleapis.com/auth/webmasters\n"
            f"(underlying error: {e})"
        ) from e

    credentials.refresh(GoogleAuthRequest())
    quota_project_id = getattr(credentials, "quota_project_id", None)
    return credentials.token, quota_project_id


# ---------------------------------------------------------------------------
# Config loading (reuses gsc_search_analytics pattern)
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_client_config(client_slug: str) -> dict[str, Any]:
    """Load the client config JSON from the scripts directory."""
    config_path = SCRIPTS_DIR / f"{client_slug}.config.json"
    if not config_path.exists():
        for p in SCRIPTS_DIR.glob("*.config.json"):
            try:
                with open(p) as f:
                    candidate = json.load(f)
                if candidate.get("client_slug") == client_slug:
                    config_path = p
                    break
            except (json.JSONDecodeError, OSError):
                continue
        else:
            available = [p.name for p in SCRIPTS_DIR.glob("*.config.json")]
            raise FileNotFoundError(
                f"Client config not found for '{client_slug}'.\n"
                f"Available configs: {available}\n"
                f"Pass the config filename prefix or the client_slug value."
            )
    with open(config_path) as f:
        return json.load(f)


def _get_gsc_property(config: dict[str, Any], client_slug: str) -> str:
    """Get the GSC property URL from client config."""
    prop = config.get("gsc_property")
    if not prop:
        raise ValueError(
            f"Client config for '{client_slug}' is missing 'gsc_property' key.\n"
            f"Add it to the config JSON. Examples:\n"
            f'  "gsc_property": "sc-domain:example.com"\n'
            f'  "gsc_property": "https://example.com/"'
        )
    return prop


# ---------------------------------------------------------------------------
# Core inspection function
# ---------------------------------------------------------------------------

def inspect_url(
    url: str,
    site_url: str,
    config: dict[str, Any] | None = None,
    _token: str | None = None,
    _quota_project: str | None = None,
) -> dict:
    """Inspect a single URL via the URL Inspection API.

    Returns a dict with normalized fields:
        url, coverageState, verdict, robotsTxtState, indexingState,
        pageFetchState, lastCrawlTime, crawledAs, googleCanonical,
        userCanonical, referringUrls, sitemap
    """
    if _token is None:
        _token, _quota_project = _load_access_token(config)

    resp = requests.post(
        URL_INSPECTION_ENDPOINT,
        headers={
            "Authorization": f"Bearer {_token}",
            "Content-Type": "application/json",
            "x-goog-user-project": _quota_project or "",
        },
        json={"inspectionUrl": url, "siteUrl": site_url},
    )

    if resp.status_code != 200:
        return {
            "url": url,
            "error": f"HTTP {resp.status_code}",
            "error_detail": resp.text[:500],
        }

    data = resp.json()
    idx = data.get("inspectionResult", {}).get("indexStatusResult", {})
    rich = data.get("inspectionResult", {}).get("richResultsResult", {})

    return {
        "url": url,
        "coverageState": idx.get("coverageState", ""),
        "verdict": idx.get("verdict", ""),
        "robotsTxtState": idx.get("robotsTxtState", ""),
        "indexingState": idx.get("indexingState", ""),
        "pageFetchState": idx.get("pageFetchState", ""),
        "lastCrawlTime": idx.get("lastCrawlTime"),
        "crawledAs": idx.get("crawledAs", ""),
        "googleCanonical": idx.get("googleCanonical"),
        "userCanonical": idx.get("userCanonical"),
        "referringUrls": idx.get("referringUrls", []),
        "sitemap": idx.get("sitemap", []),
        "richResultsVerdict": rich.get("verdict"),
    }


def inspect_client_urls(
    client_slug: str,
    urls: list[str],
    rate_limit: float = 0.3,
) -> list[dict]:
    """Inspect a list of URLs for a client. Handles auth once."""
    config = _load_client_config(client_slug)
    site_url = _get_gsc_property(config, client_slug)
    token, quota_project = _load_access_token(config)

    results = []
    for i, url in enumerate(urls):
        result = inspect_url(
            url, site_url, config,
            _token=token, _quota_project=quota_project,
        )
        results.append(result)
        if i < len(urls) - 1:
            time.sleep(rate_limit)

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _classify_result(r: dict) -> str:
    """Classify a result into an actionable category."""
    if r.get("error"):
        return "error"
    if r["verdict"] == "PASS":
        return "indexed"
    cs = r["coverageState"]
    if "Crawled - currently not indexed" in cs:
        return "crawled-not-indexed"
    if "Discovered - currently not indexed" in cs:
        return "discovered-not-indexed"
    if "URL is unknown" in cs:
        return "unknown-to-google"
    if "noindex" in cs.lower():
        return "noindex"
    if "robots" in cs.lower():
        return "robots-blocked"
    if "canonical" in cs.lower():
        return "canonical-issue"
    return "other"


def pull_client_index_report(
    client_slug: str,
    urls: list[str] | None = None,
) -> dict:
    """Pull a full index-status report for a client.

    If urls is None, reads from client data file
    (data/client-<slug>.json → website_url + service pages).
    """
    config = _load_client_config(client_slug)
    site_url = _get_gsc_property(config, client_slug)

    if urls is None:
        raise ValueError(
            "urls list is required. Pass the list of URLs to inspect.\n"
            "Future: auto-discovery from sitemap or deployment status."
        )

    results = inspect_client_urls(client_slug, urls)

    # Classify
    categories = {}
    for r in results:
        cat = _classify_result(r)
        categories.setdefault(cat, []).append(r)

    return {
        "meta": {
            "client_slug": client_slug,
            "gsc_property": site_url,
            "inspected_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat() + "Z",
            "total_urls": len(urls),
        },
        "summary": {
            "indexed": len(categories.get("indexed", [])),
            "crawled_not_indexed": len(categories.get("crawled-not-indexed", [])),
            "discovered_not_indexed": len(categories.get("discovered-not-indexed", [])),
            "unknown_to_google": len(categories.get("unknown-to-google", [])),
            "noindex": len(categories.get("noindex", [])),
            "robots_blocked": len(categories.get("robots-blocked", [])),
            "canonical_issue": len(categories.get("canonical-issue", [])),
            "other": len(categories.get("other", [])),
            "error": len(categories.get("error", [])),
        },
        "categories": categories,
        "results": results,
    }


def write_client_index_report(
    client_slug: str,
    report: dict,
    output_dir: str | None = None,
) -> tuple[Path, Path]:
    """Write the report as markdown + JSON."""
    if output_dir is None:
        output_dir = SCRIPTS_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d")
    md_path = output_dir / f"index-status-{client_slug}-{date_str}.md"
    json_path = output_dir / f"index-status-{client_slug}-{date_str}.json"

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Write markdown
    meta = report["meta"]
    summary = report["summary"]
    lines = [
        f"# Index Status Report — {client_slug}",
        "",
        f"**Property:** `{meta['gsc_property']}`",
        f"**Inspected:** {meta['inspected_at'][:10]}",
        f"**Total URLs:** {meta['total_urls']}",
        "",
        "## Summary",
        "",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| Indexed | {summary['indexed']} |",
        f"| Crawled - not indexed | {summary['crawled_not_indexed']} |",
        f"| Discovered - not indexed | {summary['discovered_not_indexed']} |",
        f"| Unknown to Google | {summary['unknown_to_google']} |",
    ]
    if summary["noindex"]:
        lines.append(f"| Blocked by noindex | {summary['noindex']} |")
    if summary["robots_blocked"]:
        lines.append(f"| Blocked by robots.txt | {summary['robots_blocked']} |")
    if summary["canonical_issue"]:
        lines.append(f"| Canonical issue | {summary['canonical_issue']} |")
    if summary["error"]:
        lines.append(f"| API error | {summary['error']} |")

    # Per-category details
    for cat_name, label in [
        ("indexed", "Indexed"),
        ("crawled-not-indexed", "Crawled - not indexed"),
        ("discovered-not-indexed", "Discovered - not indexed"),
        ("unknown-to-google", "Unknown to Google"),
    ]:
        items = report["categories"].get(cat_name, [])
        if not items:
            continue
        lines.append("")
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        lines.append("| URL | Last crawl | Crawled as | Canonical (Google) | Canonical (user) | Referring |")
        lines.append("|-----|-----------|-----------|-------------------|-----------------|-----------|")
        for r in items:
            slug = r["url"].split("/")[-2] if r["url"].endswith("/") else r["url"].split("/")[-1]
            lc = (r.get("lastCrawlTime") or "—")[:10]
            ca = r.get("crawledAs") or "—"
            gc = r.get("googleCanonical") or "—"
            uc = r.get("userCanonical") or "—"
            refs = ", ".join(r.get("referringUrls", [])) or "—"
            # Truncate long fields
            if len(gc) > 40:
                gc = ".../" + gc.split("/")[-2] + "/" if "/" in gc else gc[:40]
            if len(uc) > 40:
                uc = ".../" + uc.split("/")[-2] + "/" if "/" in uc else uc[:40]
            if len(refs) > 60:
                refs = refs[:57] + "..."
            lines.append(f"| {slug} | {lc} | {ca} | {gc} | {uc} | {refs} |")

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by `gsc_url_inspection.py` on {meta['inspected_at'][:10]}*")
    lines.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))

    return md_path, json_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report_summary(report: dict) -> None:
    """Print a concise summary to stdout."""
    meta = report["meta"]
    summary = report["summary"]
    total = meta["total_urls"]

    print(f"\n--- Index Status Report ---")
    print(f"Client: {meta['client_slug']}")
    print(f"Property: {meta['gsc_property']}")
    print(f"Inspected: {meta['inspected_at'][:10]}")
    print(f"Total URLs: {total}")
    print()
    print(f"  Indexed:                  {summary['indexed']:3d} / {total}")
    print(f"  Crawled - not indexed:    {summary['crawled_not_indexed']:3d} / {total}")
    print(f"  Discovered - not indexed: {summary['discovered_not_indexed']:3d} / {total}")
    print(f"  Unknown to Google:        {summary['unknown_to_google']:3d} / {total}")
    if summary["noindex"]:
        print(f"  Blocked by noindex:       {summary['noindex']:3d} / {total}")
    if summary["robots_blocked"]:
        print(f"  Blocked by robots.txt:    {summary['robots_blocked']:3d} / {total}")
    if summary["error"]:
        print(f"  API errors:               {summary['error']:3d} / {total}")
    print()

    # Headline
    indexed_pct = summary["indexed"] / total * 100 if total else 0
    crawled_not = summary["crawled_not_indexed"]
    if crawled_not > 0:
        print(f"  ⚠  {crawled_not} page(s) were crawled by Google but NOT indexed.")
        print(f"     This suggests a quality/authority gate, not a technical blocker.")
    if summary["unknown_to_google"] > 0:
        print(f"  ⚠  {summary['unknown_to_google']} page(s) are unknown to Google.")
        print(f"     Check sitemap inclusion and internal linking.")
    print(f"\n  Index rate: {indexed_pct:.0f}%")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python gsc_url_inspection.py <client-slug> <url1> [url2] ...\n"
            "       python gsc_url_inspection.py <client-slug> --from-file <urls.txt>\n"
            "\n"
            "Inspects URLs via the GSC URL Inspection API and produces\n"
            "an index-status report (markdown + JSON).\n"
            "\n"
            "Examples:\n"
            "  python gsc_url_inspection.py ev-electric-services \\\n"
            "    https://evelectric.pro/panel-upgrade-vienna-va/ \\\n"
            "    https://evelectric.pro/ev-charger-vienna-va/\n"
            "\n"
            "  python gsc_url_inspection.py s-and-h-contracting \\\n"
            "    --from-file sh-urls.txt"
        )
        sys.exit(1)

    client_slug = sys.argv[1]

    if sys.argv[2] == "--from-file":
        urls_file = Path(sys.argv[3])
        if not urls_file.exists():
            print(f"ERROR: file not found: {urls_file}")
            sys.exit(1)
        with open(urls_file) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        urls = sys.argv[2:]

    print(f"Inspecting {len(urls)} URL(s) for {client_slug}...")
    report = pull_client_index_report(client_slug, urls)
    _print_report_summary(report)

    md_path, json_path = write_client_index_report(client_slug, report)
    print(f"\nReport written:")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")
