#!/usr/bin/env python3
"""
GSC surface diagnostic — answers "what search surface are these impressions coming from,
and which queries actually belong to which page?"

Built 2026-08-16 for the EV-Electric Wave 6 rebuild decision. The standing
gsc-performance-pull skill groups only by `query` and by `page`, separately — so it can
tell you the homepage has 9,582 impressions and that `electrician` sits at position 1.2,
but it cannot tell you whether those are the same impressions, or what surface they are on.

That gap matters: a query at position < 3 with a CTR near 0% is not behaving like a normal
blue-link result, and whether its ranking is content-driven changes whether a page rebuild
is risky.

This script adds four things the skill lacks:
  1. searchAppearance breakdown        — which SERP features the site appears in
  2. searchType breakdown              — web vs image vs video (image search explains
                                         "position 1, no clicks" more often than anything else)
  3. query x page                      — definitive attribution, the biggest gap
  4. per-page query lists + a CTR-plausibility flag on every suspicious query

Usage:
    cd ~/workspace/repos/ai-agency-core/scripts
    python3 gsc_surface_diagnostic.py ev-electric-services 90

Writes markdown + JSON to the client's reports/ directory.

Auth is inherited from the existing engine (service-account first, ADC fallback).
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import warnings

import requests

warnings.filterwarnings("ignore", message=".*without a quota project.*")

from gsc_search_analytics import (  # noqa: E402  — engine provides auth + config
    _get_gsc_property,
    _load_access_token,
    _load_client_config,
)

ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{prop}/searchAnalytics/query"

QUOTA_PROJECT_FALLBACK = "keelworks-seo-automation"

AUTH_FIX = """
────────────────────────────────────────────────────────────────────────────
GSC auth is not usable. A fresh `application-default login` WIPES the quota
project, and Search Console rejects ADC without one. Run BOTH commands (the
exact, proven-working scope list from sop-gsc-indexing-api-setup.md Step 4 —
use the full `webmasters` scope, not `webmasters.readonly`, so this one ADC
login also covers indexing/URL-inspection scripts on this account):

  gcloud auth application-default login \\
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/indexing,https://www.googleapis.com/auth/webmasters

  gcloud auth application-default set-quota-project {project}

The second is the one people skip. Then re-run this script.
────────────────────────────────────────────────────────────────────────────
"""
REPORTS = Path.home() / "workspace" / "second-brain" / "04_projects" / "clients" / "_active"

# A query ranking this well should not convert this badly. Anything tripping both is not a
# normal organic blue-link result — find out what surface it is before treating it as an
# asset worth protecting.
SUSPICIOUS_MAX_POSITION = 3.0
SUSPICIOUS_MAX_CTR = 0.01


class GscApiError(RuntimeError):
    """Raised so a systemic auth failure aborts loudly instead of writing empty reports."""

    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body[:400]}")
        self.status = status
        self.body = body

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_quota_project(self) -> bool:
        return "quota project" in self.body.lower() or "quota_project" in self.body.lower()


def _query(
    prop: str,
    cfg: dict[str, Any],
    start: str,
    end: str,
    dimensions: list[str] | None = None,
    filters: list[dict] | None = None,
    search_type: str = "web",
    row_limit: int = 5000,
) -> list[dict]:
    """One Search Analytics call. Returns [] on a 4xx rather than raising, so a single
    unsupported combination cannot abort the whole diagnostic."""
    token, quota = _load_access_token(cfg)
    quota = quota or cfg.get("gsc_quota_project") or QUOTA_PROJECT_FALLBACK
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if quota:
        headers["x-goog-user-project"] = quota

    body: dict[str, Any] = {
        "startDate": start,
        "endDate": end,
        "rowLimit": row_limit,
        "type": search_type,
    }
    if dimensions:
        body["dimensions"] = dimensions
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    r = requests.post(
        ENDPOINT.format(prop=requests.utils.quote(prop, safe="")),
        headers=headers,
        json=body,
        timeout=90,
    )
    if r.status_code >= 400:
        short = " ".join(r.text.split())[:150]
        print(f"   ! {r.status_code} dims={dimensions} type={search_type}: {short}")
        raise GscApiError(r.status_code, r.text)
    return r.json().get("rows", [])


def _safe(fn, *a, **kw):
    """Run a query, tolerating a single unsupported dimension combination but never an auth failure."""
    try:
        return fn(*a, **kw)
    except GscApiError as e:
        if e.is_auth:
            raise
        return []


def preflight(prop: str, cfg: dict) -> None:
    """One trivial call. Fail here, with instructions, rather than 20 lines deep."""
    try:
        _query(prop, cfg, "2026-01-01", "2026-01-02", None, row_limit=1)
    except GscApiError as e:
        if e.is_auth:
            print(AUTH_FIX.format(project=cfg.get("gsc_quota_project") or QUOTA_PROJECT_FALLBACK))
            if e.is_quota_project:
                print("The error names the quota project specifically — the second command is the fix.\n")
            raise SystemExit(2)
        raise


def _fmt(rows: list[dict], n: int = 25, label: str = "key") -> str:
    if not rows:
        return "_No rows returned._\n"
    out = [f"| {label} | Impressions | Clicks | CTR | Position |", "|---|---:|---:|---:|---:|"]
    for row in sorted(rows, key=lambda x: -x["impressions"])[:n]:
        k = " · ".join(row.get("keys", ["—"]))
        out.append(
            f"| {k} | {row['impressions']:,} | {row['clicks']} | "
            f"{row['ctr'] * 100:.1f}% | {row['position']:.1f} |"
        )
    return "\n".join(out) + "\n"


def run(client_slug: str, window_days: int = 90) -> dict:
    cfg = _load_client_config(client_slug)
    prop = _get_gsc_property(cfg, client_slug)
    end = date.today() - timedelta(days=3)          # GSC lags ~2-3 days
    start = end - timedelta(days=window_days)
    s, e = start.isoformat(), end.isoformat()
    print(f"Property {prop}   window {s} → {e}")
    print("0/5  auth preflight …")
    preflight(prop, cfg)

    result: dict[str, Any] = {
        "client": client_slug,
        "property": prop,
        "window": {"start": s, "end": e, "days": window_days},
    }

    # 1 — searchAppearance. Must be grouped ALONE; the API rejects it alongside other dimensions.
    print("1/5  searchAppearance …")
    result["search_appearance"] = _safe(_query, prop, cfg, s, e, ["searchAppearance"])

    # 1b — for each appearance type found, the queries behind it.
    result["appearance_queries"] = {}
    for row in result["search_appearance"]:
        appearance = row["keys"][0]
        print(f"      ↳ queries for {appearance} …")
        result["appearance_queries"][appearance] = _safe(
            _query, prop, cfg, s, e, ["query"],
            filters=[{"dimension": "searchAppearance", "operator": "equals",
                      "expression": appearance}],
            row_limit=100,
        )

    # 2 — searchType. Image search is the most common explanation for "position 1, no clicks".
    print("2/5  searchType (web / image / video) …")
    result["by_search_type"] = {}
    for t in ("web", "image", "video"):
        rows = _safe(_query, prop, cfg, s, e, None, search_type=t)
        totals = rows[0] if rows else {}
        result["by_search_type"][t] = {
            "impressions": totals.get("impressions", 0),
            "clicks": totals.get("clicks", 0),
            "ctr": totals.get("ctr", 0),
            "position": totals.get("position", 0),
        }
        result["by_search_type"][t]["top_queries"] = _safe(
            _query, prop, cfg, s, e, ["query"], search_type=t, row_limit=50
        )

    # 3 — query x page. The attribution the standing report cannot produce.
    print("3/5  query x page …")
    qp = _safe(_query, prop, cfg, s, e, ["query", "page"], row_limit=25000)
    result["query_x_page"] = qp

    # 4 — per-page rollup, so "which queries does the homepage actually own" is answerable.
    print("4/5  per-page rollup …")
    by_page: dict[str, list[dict]] = {}
    for row in qp:
        by_page.setdefault(row["keys"][1], []).append(
            {"query": row["keys"][0], **{k: row[k] for k in ("clicks", "impressions", "ctr", "position")}}
        )
    result["by_page"] = {
        page: sorted(rows, key=lambda x: -x["impressions"])[:50]
        for page, rows in sorted(by_page.items(), key=lambda kv: -sum(r["impressions"] for r in kv[1]))
    }

    # 5 — device split on the suspicious set.
    print("5/5  device split …")
    result["by_device"] = _safe(_query, prop, cfg, s, e, ["device"])

    # CTR-plausibility flag
    flagged = [
        {"query": r["keys"][0], "page": r["keys"][1], **{k: r[k] for k in ("clicks", "impressions", "ctr", "position")}}
        for r in qp
        if r["position"] < SUSPICIOUS_MAX_POSITION
        and r["ctr"] < SUSPICIOUS_MAX_CTR
        and r["impressions"] >= 50
    ]
    result["implausible_ctr"] = sorted(flagged, key=lambda x: -x["impressions"])
    return result


def write(client_slug: str, result: dict) -> tuple[Path, Path]:
    outdir = REPORTS / client_slug / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    jpath = outdir / f"gsc-surface-diagnostic-{stamp}.json"
    mpath = outdir / f"gsc-surface-diagnostic-{stamp}.md"
    jpath.write_text(json.dumps(result, indent=2), encoding="utf-8")

    w = result["window"]
    md = [
        "---", "type: report", f"client: {client_slug}", f"created: {stamp}",
        "tags: [gsc, search-appearance, attribution, diagnostic]", "---", "",
        "# GSC surface diagnostic", "",
        f"Property `{result['property']}` · {w['start']} → {w['end']} ({w['days']} days)", "",
        "Answers what the standing performance report cannot: which SERP surface the impressions",
        "come from, and which queries belong to which page.", "",
        "## 1 — Search appearance", "",
        "What SERP features the site appears in. An appearance type other than plain web results",
        "explains a high average position paired with a near-zero CTR.", "",
        _fmt(result["search_appearance"], label="Appearance"), "",
        "## 2 — Search type", "",
        "| Type | Impressions | Clicks | CTR | Position |", "|---|---:|---:|---:|---:|",
    ]
    for t, v in result["by_search_type"].items():
        md.append(f"| {t} | {v['impressions']:,} | {v['clicks']} | {v['ctr'] * 100:.1f}% | {v['position']:.1f} |")
    md += [
        "", "**If a large share of impressions are `image`, that alone explains a position near 1",
        "with almost no clicks — and those rankings are not driven by body copy.**", "",
        "## 3 — Implausible CTR (position < 3, CTR < 1%, 50+ impressions)", "",
        "These are not behaving like normal blue-link results. Identify the surface before treating",
        "any of them as a ranking asset a page rebuild could endanger.", "",
    ]
    md.append(
        _fmt([{"keys": [f"{r['query']}  →  {r['page']}"], **r} for r in result["implausible_ctr"]],
             n=40, label="Query → Page")
    )
    md += ["", "## 4 — Queries by page", ""]
    for page, rows in list(result["by_page"].items())[:12]:
        tot = sum(r["impressions"] for r in rows)
        md += [f"### {page}", "", f"_{tot:,} impressions across the queries below._", "",
               _fmt([{"keys": [r["query"]], **r} for r in rows], n=20, label="Query"), ""]
    md += ["## 5 — Device", "", _fmt(result["by_device"], label="Device")]
    mpath.write_text("\n".join(md), encoding="utf-8")
    return mpath, jpath


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "ev-electric-services"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    res = run(slug, days)

    if not res.get("query_x_page") and not res.get("search_appearance"):
        print("\n✗ Every query returned zero rows. Not writing a report — an empty file that "
              "looks like a result is worse than no file.")
        print("  If auth passed preflight, check the property and the date window.")
        raise SystemExit(1)

    m, j = write(slug, res)
    print(f"\n✅ {m}\n✅ {j}")
    print(f"\nAppearance types: {[r['keys'][0] for r in res['search_appearance']] or 'none returned'}")
    print(f"Implausible-CTR queries: {len(res['implausible_ctr'])}")
    for r in res["implausible_ctr"][:8]:
        print(f"   {r['query'][:42]:42s} {r['impressions']:>5} impr  pos {r['position']:.1f}  CTR {r['ctr']*100:.1f}%  → {r['page']}")
