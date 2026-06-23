"""
gsc_search_analytics.py — Google Search Console Search Analytics pull.

Supports two auth modes (tried in order):
  1. **Service account JSON key** (non-expiring, preferred) — loaded from
     tier-3 via `_load_secrets.load_gsc_service_account(config)`.
  2. **Application Default Credentials (ADC)** (user-level, expires) — falls
     back to `google.auth.default()` if no service account is configured.

Mirrors the auth pattern in gsc_indexing.py but uses the
webmasters.readonly scope for read-only Search Analytics access.

Public API
----------
- query_search_analytics(property_url, start_date, end_date, dimensions, row_limit) -> list[dict]
- pull_client_report(client_slug, window_days=28) -> dict
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: `requests` is not installed. Run: pip install requests\n"
    )
    sys.exit(2)

GSC_SEARCH_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_SEARCH_ANALYTICS_ENDPOINT = (
    "https://searchconsole.googleapis.com/webmasters/v3"
    "/sites/{property_url}/searchAnalytics/query"
)

# ---------------------------------------------------------------------------
# Auth (mirrors gsc_indexing.py)
# ---------------------------------------------------------------------------

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
            sa_data, scopes=[GSC_SEARCH_ANALYTICS_SCOPE]
        )
        return creds, sa_data.get("project_id")
    except Exception:
        return None, None


def _load_access_token(config: dict[str, Any] | None = None) -> tuple[str, Optional[str]]:
    """Return (access_token, quota_project_id) for the Search Analytics API."""
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
            scopes=[GSC_SEARCH_ANALYTICS_SCOPE]
        )
    except Exception as e:
        raise RuntimeError(
            "No GSC credentials found. Options:\n"
            "  1. Place service account JSON at automation/secrets/gsc-sa-<client>.json\n"
            "  2. Run: gcloud auth application-default login \\\n"
            "     --scopes=https://www.googleapis.com/auth/cloud-platform,"
            "https://www.googleapis.com/auth/webmasters.readonly\n"
            f"(underlying error: {e})"
        ) from e

    credentials.refresh(GoogleAuthRequest())
    quota_project_id = getattr(credentials, "quota_project_id", None)
    return credentials.token, quota_project_id


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_client_config(client_slug: str) -> dict[str, Any]:
    """Load the client config JSON from the scripts directory.

    Accepts either the config filename prefix (e.g. 'ev-electric') or the
    full client_slug from inside the config (e.g. 'ev-electric-services').
    Tries exact match first, then scans all *.config.json for a matching
    client_slug field.
    """
    # Direct match: <slug>.config.json
    config_path = SCRIPTS_DIR / f"{client_slug}.config.json"
    if not config_path.exists():
        # Fallback: scan for a config whose client_slug matches
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


# GSC property URL mapping — loaded from client config's "gsc_property" key.
# Format examples:
#   Domain property:     "sc-domain:evelectric.pro"
#   URL-prefix property: "https://shcontractingunlimited.com/"

def _get_gsc_property(config: dict[str, Any], client_slug: str) -> str:
    """Get the GSC property URL from client config."""
    prop = config.get("gsc_property")
    if not prop:
        raise ValueError(
            f"Client config for '{client_slug}' is missing 'gsc_property' key.\n"
            f"Add it to the config JSON. Examples:\n"
            f'  "gsc_property": "sc-domain:example.com"       (domain property)\n'
            f'  "gsc_property": "https://example.com/"         (URL-prefix property)'
        )
    return prop


# ---------------------------------------------------------------------------
# Core query function
# ---------------------------------------------------------------------------

def query_search_analytics(
    property_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    row_limit: int = 1000,
    config: dict[str, Any] | None = None,
) -> list[dict]:
    """Query the GSC Search Analytics API.

    Args:
        property_url: GSC property (e.g. "sc-domain:evelectric.pro" or
                      "https://shcontractingunlimited.com/").
        start_date: YYYY-MM-DD start (inclusive).
        end_date: YYYY-MM-DD end (inclusive).
        dimensions: List of dimensions — any of ['query', 'page', 'date',
                    'country', 'device', 'searchAppearance'].
        row_limit: Max rows (API max = 25000).
        config: Client config dict (for auth). None = ADC-only.

    Returns:
        List of row dicts, each with 'keys' (list matching dimensions) and
        'clicks', 'impressions', 'ctr', 'position'.
    """
    if dimensions is None:
        dimensions = ["query"]

    access_token, quota_project_id = _load_access_token(config)

    url = GSC_SEARCH_ANALYTICS_ENDPOINT.format(
        property_url=requests.utils.quote(property_url, safe="")
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if quota_project_id:
        headers["x-goog-user-project"] = quota_project_id

    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": min(row_limit, 25000),
    }

    resp = requests.post(url, headers=headers, json=body, timeout=60)

    if resp.status_code == 401:
        raise RuntimeError(
            f"GSC Search Analytics 401 — credentials expired or invalid.\n"
            f"Re-authenticate: gcloud auth application-default login \\\n"
            f"  --scopes=https://www.googleapis.com/auth/cloud-platform,"
            f"https://www.googleapis.com/auth/webmasters.readonly\n"
            f"Response: {resp.text[:300]}"
        )
    if resp.status_code == 403:
        raise RuntimeError(
            f"GSC Search Analytics 403 — access denied.\n"
            f"Confirm Oliver has access to property '{property_url}' and "
            f"quota project is set (x-goog-user-project: {quota_project_id}).\n"
            f"Response: {resp.text[:300]}"
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"GSC Search Analytics HTTP {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    return data.get("rows", [])


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _top_by_metric(rows: list[dict], metric: str, n: int = 20) -> list[dict]:
    """Return top N rows sorted descending by a metric."""
    return sorted(rows, key=lambda r: r.get(metric, 0), reverse=True)[:n]


def _striking_distance(rows: list[dict], min_pos: float = 5.0, max_pos: float = 20.0) -> list[dict]:
    """Return rows where average position is between min_pos and max_pos (inclusive).

    These are the "almost ranking" queries — close enough to push onto page 1
    with targeted content or optimization.
    """
    return sorted(
        [r for r in rows if min_pos <= r.get("position", 0) <= max_pos],
        key=lambda r: r.get("impressions", 0),
        reverse=True,
    )


def _ctr_outliers(rows: list[dict], min_impressions: int = 10) -> list[dict]:
    """Return rows with unusually low CTR relative to their position.

    A page ranking in positions 1-5 should have decent CTR. If it doesn't,
    it's a title/description optimization opportunity.
    """
    outliers = []
    # Expected CTR by position range (rough benchmarks)
    expected_ctr = {
        (1, 1): 0.20,
        (2, 2): 0.10,
        (3, 3): 0.07,
        (4, 5): 0.04,
        (6, 10): 0.02,
    }
    for row in rows:
        if row.get("impressions", 0) < min_impressions:
            continue
        pos = row.get("position", 0)
        ctr = row.get("ctr", 0)
        for (lo, hi), threshold in expected_ctr.items():
            if lo <= pos <= hi and ctr < threshold * 0.5:
                outliers.append({
                    **row,
                    "_expected_ctr_min": threshold,
                    "_ctr_ratio": round(ctr / threshold, 3) if threshold else 0,
                })
                break
    return sorted(outliers, key=lambda r: r.get("impressions", 0), reverse=True)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def pull_client_report(client_slug: str, window_days: int = 28) -> dict:
    """Pull a full GSC performance report for a client.

    Reads the client config from <client_slug>.config.json, queries
    Search Analytics by query and by page, and computes:
      - Top queries by impressions
      - Top pages by impressions
      - Striking-distance queries (position 5-20)
      - CTR outliers
      - Summary stats

    Returns a dict with all analysis results + metadata.
    """
    config = _load_client_config(client_slug)
    gsc_property = _get_gsc_property(config, client_slug)

    end = date.today() - timedelta(days=3)  # GSC data lags ~3 days
    start = end - timedelta(days=window_days)
    start_str = start.isoformat()
    end_str = end.isoformat()

    # Pull by-query
    by_query = query_search_analytics(
        property_url=gsc_property,
        start_date=start_str,
        end_date=end_str,
        dimensions=["query"],
        row_limit=5000,
        config=config,
    )

    # Pull by-page
    by_page = query_search_analytics(
        property_url=gsc_property,
        start_date=start_str,
        end_date=end_str,
        dimensions=["page"],
        row_limit=5000,
        config=config,
    )

    # Compute analysis
    top_queries = _top_by_metric(by_query, "impressions", n=30)
    top_pages = _top_by_metric(by_page, "impressions", n=30)
    striking = _striking_distance(by_query)
    ctr_outliers_queries = _ctr_outliers(by_query)
    ctr_outliers_pages = _ctr_outliers(by_page)

    # Summary stats
    total_clicks = sum(r.get("clicks", 0) for r in by_query)
    total_impressions = sum(r.get("impressions", 0) for r in by_query)
    avg_ctr = total_clicks / total_impressions if total_impressions else 0
    avg_position = (
        sum(r.get("position", 0) * r.get("impressions", 0) for r in by_query)
        / total_impressions
        if total_impressions
        else 0
    )

    report = {
        "metadata": {
            "client_slug": client_slug,
            "gsc_property": gsc_property,
            "window": {"start": start_str, "end": end_str, "days": window_days},
            "pulled_at": date.today().isoformat(),
            "total_query_rows": len(by_query),
            "total_page_rows": len(by_page),
        },
        "summary": {
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "average_ctr": round(avg_ctr, 4),
            "weighted_average_position": round(avg_position, 1),
            "thin_data": total_impressions < 100,
        },
        "top_queries_by_impressions": top_queries,
        "top_pages_by_impressions": top_pages,
        "striking_distance_queries": striking,
        "ctr_outliers_queries": ctr_outliers_queries,
        "ctr_outliers_pages": ctr_outliers_pages,
        "raw_by_query": by_query,
        "raw_by_page": by_page,
    }

    return report


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

VAULT_CLIENTS_DIR = Path.home() / "workspace" / "second-brain" / "04_projects" / "clients" / "_active"


def _format_row_md(row: dict, dimensions_label: str = "query") -> str:
    """Format a single analytics row as a markdown table row."""
    keys = row.get("keys", ["?"])
    key_display = keys[0] if len(keys) == 1 else " | ".join(keys)
    clicks = row.get("clicks", 0)
    impressions = row.get("impressions", 0)
    ctr = row.get("ctr", 0)
    position = row.get("position", 0)
    return f"| {key_display} | {clicks} | {impressions} | {ctr:.1%} | {position:.1f} |"


def _build_markdown_report(report: dict) -> str:
    """Build the human-readable markdown report."""
    meta = report["metadata"]
    summary = report["summary"]
    lines = []

    lines.append("---")
    lines.append("type: gsc-performance-report")
    lines.append(f"client: {meta['client_slug']}")
    lines.append(f"property: \"{meta['gsc_property']}\"")
    lines.append(f"window-start: {meta['window']['start']}")
    lines.append(f"window-end: {meta['window']['end']}")
    lines.append(f"pulled: {meta['pulled_at']}")
    lines.append(f"status: current")
    lines.append(f"tags: [gsc, performance, {meta['client_slug']}]")
    lines.append("---")
    lines.append("")
    lines.append(f"# GSC Performance Report — {meta['client_slug']}")
    lines.append("")
    lines.append(f"**Property:** `{meta['gsc_property']}`")
    lines.append(f"**Window:** {meta['window']['start']} to {meta['window']['end']} ({meta['window']['days']} days)")
    lines.append(f"**Pulled:** {meta['pulled_at']}")
    lines.append("")

    # Plain-language summary
    lines.append("## Summary")
    lines.append("")
    if summary["thin_data"]:
        lines.append(
            f"> **Thin data warning:** This property has only {summary['total_impressions']} "
            f"total impressions in the window. Pages are likely new (~15 days old). "
            f"Lean on impression trends, not click volume — clicks will come as "
            f"pages age and Google settles rankings."
        )
        lines.append("")

    lines.append(f"- **Total clicks:** {summary['total_clicks']}")
    lines.append(f"- **Total impressions:** {summary['total_impressions']}")
    lines.append(f"- **Average CTR:** {summary['average_ctr']:.1%}")
    lines.append(f"- **Weighted avg position:** {summary['weighted_average_position']:.1f}")
    lines.append(f"- **Unique queries:** {meta['total_query_rows']}")
    lines.append(f"- **Unique pages:** {meta['total_page_rows']}")
    lines.append("")

    if summary["total_impressions"] == 0:
        lines.append(
            "No impression data in this window. The property may be too new, "
            "or pages may not yet be indexed. Check indexation status first."
        )
        return "\n".join(lines)

    # Top queries
    lines.append("## Top Queries by Impressions")
    lines.append("")
    lines.append("| Query | Clicks | Impressions | CTR | Avg Position |")
    lines.append("|-------|--------|-------------|-----|--------------|")
    for row in report["top_queries_by_impressions"][:20]:
        lines.append(_format_row_md(row, "query"))
    lines.append("")

    # Top pages
    lines.append("## Top Pages by Impressions")
    lines.append("")
    lines.append("| Page | Clicks | Impressions | CTR | Avg Position |")
    lines.append("|------|--------|-------------|-----|--------------|")
    for row in report["top_pages_by_impressions"][:20]:
        lines.append(_format_row_md(row, "page"))
    lines.append("")

    # Striking distance
    striking = report["striking_distance_queries"]
    lines.append("## Striking Distance (Position 5–20)")
    lines.append("")
    lines.append(
        "These queries are close to page 1. Targeted optimization — better "
        "title tags, richer content, internal links — can push them up."
    )
    lines.append("")
    if striking:
        lines.append("| Query | Clicks | Impressions | CTR | Avg Position |")
        lines.append("|-------|--------|-------------|-----|--------------|")
        for row in striking[:30]:
            lines.append(_format_row_md(row, "query"))
    else:
        lines.append("No queries in the 5–20 position range yet.")
    lines.append("")

    # CTR outliers
    ctr_out_q = report["ctr_outliers_queries"]
    if ctr_out_q:
        lines.append("## CTR Outliers (Queries)")
        lines.append("")
        lines.append(
            "These queries rank well but have unusually low CTR — "
            "title/meta description improvements could capture more clicks."
        )
        lines.append("")
        lines.append("| Query | Clicks | Impressions | CTR | Avg Position | Expected CTR Floor |")
        lines.append("|-------|--------|-------------|-----|--------------|-------------------|")
        for row in ctr_out_q[:15]:
            exp = row.get("_expected_ctr_min", 0)
            lines.append(
                f"{_format_row_md(row, 'query')} {exp:.0%} |"
            )
        lines.append("")

    ctr_out_p = report["ctr_outliers_pages"]
    if ctr_out_p:
        lines.append("## CTR Outliers (Pages)")
        lines.append("")
        lines.append("| Page | Clicks | Impressions | CTR | Avg Position | Expected CTR Floor |")
        lines.append("|------|--------|-------------|-----|--------------|-------------------|")
        for row in ctr_out_p[:15]:
            exp = row.get("_expected_ctr_min", 0)
            lines.append(
                f"{_format_row_md(row, 'page')} {exp:.0%} |"
            )
        lines.append("")

    return "\n".join(lines)


def _build_json_companion(report: dict) -> dict:
    """Build the machine-readable JSON companion for downstream consumption (A2)."""
    return {
        "metadata": report["metadata"],
        "summary": report["summary"],
        "top_queries_by_impressions": report["top_queries_by_impressions"],
        "top_pages_by_impressions": report["top_pages_by_impressions"],
        "striking_distance_queries": report["striking_distance_queries"],
        "ctr_outliers_queries": [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in report["ctr_outliers_queries"]
        ],
        "ctr_outliers_pages": [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in report["ctr_outliers_pages"]
        ],
    }


def write_client_report(client_slug: str, report: dict) -> tuple[Path, Path]:
    """Write the markdown report + JSON companion to the client's reports folder.

    Returns (md_path, json_path).
    """
    client_dir = VAULT_CLIENTS_DIR / client_slug / "reports"
    client_dir.mkdir(parents=True, exist_ok=True)

    report_date = report["metadata"]["pulled_at"]
    md_path = client_dir / f"gsc-performance-{report_date}.md"
    json_path = client_dir / f"gsc-performance-{report_date}.json"

    md_content = _build_markdown_report(report)
    md_path.write_text(md_content, encoding="utf-8")

    json_content = _build_json_companion(report)
    json_path.write_text(
        json.dumps(json_content, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return md_path, json_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: python gsc_search_analytics.py <client_slug> [window_days]"""
    if len(sys.argv) < 2:
        print("Usage: python gsc_search_analytics.py <client_slug> [window_days]")
        print("  client_slug: e.g. 'ev-electric-services' or 's-and-h-contracting'")
        print("  window_days: lookback window (default: 28)")
        sys.exit(1)

    client_slug = sys.argv[1]
    window_days = int(sys.argv[2]) if len(sys.argv) > 2 else 28

    print(f"Pulling GSC Search Analytics for {client_slug} ({window_days}-day window)...")

    report = pull_client_report(client_slug, window_days)

    meta = report["metadata"]
    summary = report["summary"]
    print(f"\n--- Report Summary ---")
    print(f"Property: {meta['gsc_property']}")
    print(f"Window: {meta['window']['start']} to {meta['window']['end']}")
    print(f"Total clicks: {summary['total_clicks']}")
    print(f"Total impressions: {summary['total_impressions']}")
    print(f"Average CTR: {summary['average_ctr']:.1%}")
    print(f"Weighted avg position: {summary['weighted_average_position']:.1f}")
    print(f"Queries: {meta['total_query_rows']}, Pages: {meta['total_page_rows']}")
    print(f"Striking-distance queries: {len(report['striking_distance_queries'])}")

    if summary["thin_data"]:
        print(
            f"\n⚠️  Thin data: {summary['total_impressions']} impressions. "
            f"Pages are likely new. Lean on impression trends, not clicks."
        )

    md_path, json_path = write_client_report(client_slug, report)
    print(f"\nReports written:")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")


if __name__ == "__main__":
    main()
