#!/usr/bin/env python3
"""One-off host-side runner — EV Electric GSC health pull (2026-07-10).

Pulls, using existing ADC credentials on this machine:
  1. Standard 28-day performance report (pull_client_report) -> reports/
  2. Daily clicks/impressions series, last 120 days (dimensions=["date"])
  3. Daily series split brand vs non-brand is derived later from (1)/(2).

Writes:
  second-brain/04_projects/clients/_active/ev-electric-services/reports/
    gsc-performance-2026-07-10.{md,json}   (via write_client_report)
    gsc-daily-series-2026-07-10.json       (raw by-date rows)

Run from anywhere:  python3 ~/workspace/repos/ai-agency-core/scripts/pull-ev-gsc-health-2026-07-10.py
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from gsc_search_analytics import (  # noqa: E402
    _load_client_config,
    _get_gsc_property,
    query_search_analytics,
    pull_client_report,
    write_client_report,
)

# Slug-parameterized 2026-07-11 (client-health-run workflow): pass any client slug.
#   python3 pull-ev-gsc-health-2026-07-10.py [client-slug]   (default: ev-electric-services)
SLUG = sys.argv[1] if len(sys.argv) > 1 else "ev-electric-services"
REPORTS_DIR = (
    SCRIPTS.parents[2]
    / f"second-brain/04_projects/clients/_active/{SLUG}/reports"
)


def main() -> None:
    config = _load_client_config(SLUG)
    prop = _get_gsc_property(config, SLUG)

    # --- 1. Standard 28-day report ---
    print("1/2 Standard 28-day report...")
    report = pull_client_report(SLUG, 28)
    md_path, json_path = write_client_report(SLUG, report)
    s = report["summary"]
    print(f"   clicks={s['total_clicks']} impressions={s['total_impressions']}")
    print(f"   -> {md_path}\n   -> {json_path}")

    # --- 2. Daily series, 120 days ---
    print("2/2 Daily series (120 days, by date)...")
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=120)
    rows = query_search_analytics(
        property_url=prop,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        dimensions=["date"],
        row_limit=200,
        config=config,
    )
    out = {
        "metadata": {
            "client_slug": SLUG,
            "gsc_property": prop,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "dimensions": ["date"],
            "pulled_at": date.today().isoformat(),
        },
        "rows": rows,
    }
    daily_path = REPORTS_DIR / f"gsc-daily-series-{date.today().isoformat()}.json"
    daily_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    total_clicks = sum(r.get("clicks", 0) for r in rows)
    total_impr = sum(r.get("impressions", 0) for r in rows)
    print(f"   {len(rows)} days, clicks={total_clicks}, impressions={total_impr}")
    print(f"   -> {daily_path}")
    print("\nDONE — tell the Cowork chat the pull is complete.")


if __name__ == "__main__":
    main()
