#!/usr/bin/env python3
"""Query x page cross-tab for the EV-Electric emergency query cluster, 2026-08-31.
Read-only. Reuses gsc_search_analytics.query_search_analytics — no new auth path.
"""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from gsc_search_analytics import _load_client_config, _get_gsc_property, query_search_analytics

SLUG = "ev-electric-services"
START = "2026-07-31"
END = "2026-08-28"  # same window as reports/gsc-performance-2026-08-31.md — comparable, not a new baseline
TARGET_QUERIES = {
    "emergency electrician near me",
    "emergency electrician",
    "emergency electrician fairfax city",
}

config = _load_client_config(SLUG)
prop = _get_gsc_property(config, SLUG)

rows = query_search_analytics(
    property_url=prop,
    start_date=START,
    end_date=END,
    dimensions=["query", "page"],
    row_limit=25000,
    config=config,
)

hits = [r for r in rows if r["keys"][0] in TARGET_QUERIES]
hits.sort(key=lambda r: (r["keys"][0], -r["impressions"]))

out_path = SCRIPTS.parents[2] / f"second-brain/04_projects/clients/_active/{SLUG}/reports/gsc-query-page-emergency-2026-08-31.json"
out_path.write_text(json.dumps({
    "metadata": {"client_slug": SLUG, "gsc_property": prop, "window": {"start": START, "end": END},
                 "dimensions": ["query", "page"], "pulled_at": "2026-08-31",
                 "note": "filtered to the three emergency-cluster queries only"},
    "rows": hits,
}, indent=2), encoding="utf-8")

print(f"{len(hits)} query×page rows for the emergency cluster\n")
for r in hits:
    q, page = r["keys"]
    print(f"  {q!r:45s} {page:60s} clicks={r['clicks']:<3} impr={r['impressions']:<4} pos={r['position']:.1f}")
print(f"\n-> {out_path}")
