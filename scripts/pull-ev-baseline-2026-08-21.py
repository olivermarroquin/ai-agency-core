#!/usr/bin/env python3
"""Full-depth GSC pull for the EV Electric pre-Wave-6 baseline (2026-08-21).

Why this exists
---------------
`gsc_search_analytics.py` pulls 5000 rows per dimension but its JSON companion
stores only the top 30. The baseline needs the top 200 queries and the full page
list, so this script calls the same engine and dumps `raw_by_query` /
`raw_by_page` in full, plus three dimensions the engine does not write at all
(date, device, searchType) and a query x page join for page ownership.

It does NOT modify the engine and does NOT overwrite any existing report.

Usage
-----
    cd ~/workspace/repos/ai-agency-core/scripts
    python3 pull-ev-baseline-2026-08-21.py --preflight   # auth check only, no writes
    python3 pull-ev-baseline-2026-08-21.py               # the real pull

Output
------
    second-brain/04_projects/clients/_active/ev-electric-services/reports/
      gsc-baseline-raw-2026-08-21.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from gsc_search_analytics import (
        _load_client_config,
        _get_gsc_property,
        query_search_analytics,
        pull_client_report,
    )
except ImportError as e:
    sys.exit(
        f"Could not import gsc_search_analytics: {e}\n"
        "Run this from ~/workspace/repos/ai-agency-core/scripts"
    )

# searchType is NOT a Search Analytics dimension, it is a request-level "type"
# parameter. gsc_surface_diagnostic._query knows how to send it.
try:
    from gsc_surface_diagnostic import _query as _typed_query
except ImportError:
    _typed_query = None

CLIENT = "ev-electric-services"
OUT_DIR = (
    Path.home() / "workspace" / "second-brain" / "04_projects" / "clients"
    / "_active" / CLIENT / "reports"
)
STAMP = "2026-08-21"
OUT = OUT_DIR / f"gsc-baseline-raw-{STAMP}.json"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def main() -> None:
    preflight = "--preflight" in sys.argv

    config = _load_client_config(CLIENT)
    prop = _get_gsc_property(config, CLIENT)
    end = date.today() - timedelta(days=3)   # GSC lags ~3 days
    print(f"\nProperty : {prop}")
    print(f"Today    : {date.today().isoformat()}")
    print(f"Data end : {end.isoformat()}  (GSC 3-day lag)\n")

    print("Preflight: single-row probe to confirm auth ...")
    probe = query_search_analytics(
        property_url=prop,
        start_date=(end - timedelta(days=7)).isoformat(),
        end_date=end.isoformat(),
        dimensions=["date"],
        row_limit=1,
        config=config,
    )
    if not probe:
        sys.exit(
            "Probe returned zero rows. Auth may be fine but the property is empty,\n"
            "or the property string is wrong. Check gsc_property in the config."
        )
    log(f"OK. Probe row: {probe[0]}")

    if preflight:
        print("\nPreflight only. Nothing written. Re-run without --preflight.\n")
        return

    if OUT.exists() and "--force" not in sys.argv:
        sys.exit(
            f"\nRefusing to overwrite an existing artifact:\n  {OUT}\n"
            "Move it aside, or re-run with --force.\n"
        )

    out: dict = {
        "client": CLIENT,
        "property": prop,
        "pulled_at": date.today().isoformat(),
        "gsc_data_end": end.isoformat(),
        "note": (
            "Full-depth pull for the pre-Wave-6 baseline. raw_by_query and raw_by_page are "
            "COMPLETE row sets, not top-30 slices. Dimensions do not reconcile with each "
            "other by design: date is authoritative for site totals, page for per-URL, "
            "query for relative query comparison, query x page for ownership only. All rows except by_search_type_90d are web search only, which is the GSC API default type."
        ),
        "windows": {},
    }

    # 1 + 2. Engine pulls at 28d and 90d, keeping the raw rows the companion drops.
    for days in (28, 90):
        print(f"Pulling {days}-day window (query + page dimensions) ...")
        rep = pull_client_report(CLIENT, window_days=days)
        md = rep["metadata"]
        out["windows"][f"{days}d"] = {
            "window": md["window"],
            "summary_by_query_dimension": rep["summary"],
            "total_query_rows": md["total_query_rows"],
            "total_page_rows": md["total_page_rows"],
            "raw_by_query": rep["raw_by_query"],
            "raw_by_page": rep["raw_by_page"],
            "striking_distance_queries": rep["striking_distance_queries"],
            "ctr_outliers_queries": [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in rep["ctr_outliers_queries"]
            ],
        }
        log(f"{md['total_query_rows']} queries, {md['total_page_rows']} pages, "
            f"window {md['window']['start']} to {md['window']['end']}")

    # 3. Date dimension, 16 months, for authoritative site totals and full trend.
    print("Pulling daily series (date dimension, 480 days) ...")
    daily = query_search_analytics(
        property_url=prop,
        start_date=(end - timedelta(days=480)).isoformat(),
        end_date=end.isoformat(),
        dimensions=["date"],
        row_limit=1000,
        config=config,
    )
    out["daily_series"] = daily
    log(f"{len(daily)} days with data, "
        f"{daily[0]['keys'][0] if daily else '-'} to {daily[-1]['keys'][0] if daily else '-'}")

    # 4. Query x page, 90d, for page ownership only.
    print("Pulling query x page (90d, ownership join) ...")
    qxp = query_search_analytics(
        property_url=prop,
        start_date=(end - timedelta(days=90)).isoformat(),
        end_date=end.isoformat(),
        dimensions=["query", "page"],
        row_limit=25000,
        config=config,
    )
    out["query_x_page_90d"] = qxp
    log(f"{len(qxp)} query x page rows")

    owner: dict[str, Counter] = defaultdict(Counter)
    for r in qxp:
        owner[r["keys"][0]][r["keys"][1]] += r.get("impressions", 0)
    out["query_owner_90d"] = {q: c.most_common(1)[0][0] for q, c in owner.items()}

    # Core pulls are done. Write NOW, so no later failure can discard them.
    def flush() -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    flush()
    log(f"core pulls saved to {OUT.name}")

    # 5. Optional extras. Each is wrapped: a failure here degrades the file, never loses it.
    print("Pulling device dimension (90d) ...")
    try:
        out["by_device_90d"] = query_search_analytics(
            property_url=prop,
            start_date=(end - timedelta(days=90)).isoformat(),
            end_date=end.isoformat(),
            dimensions=["device"],
            row_limit=100,
            config=config,
        )
        flush()
        log("OK")
    except Exception as e:
        out["by_device_90d"] = {"error": str(e)[:300]}
        flush()
        log(f"SKIPPED: {e}")

    # 6. Search type totals. Not a dimension: sent as body "type".
    print("Pulling search type totals (web / image / video, 90d) ...")
    if _typed_query is None:
        out["by_search_type_90d"] = {"error": "gsc_surface_diagnostic._query unavailable"}
        log("SKIPPED: could not import the typed query helper")
    else:
        st: dict = {}
        for t in ("web", "image", "video"):
            try:
                rows = _typed_query(
                    prop, config,
                    (end - timedelta(days=90)).isoformat(),
                    end.isoformat(),
                    None, None, t, 1,
                )
                r = rows[0] if rows else {}
                st[t] = {
                    "impressions": r.get("impressions", 0),
                    "clicks": r.get("clicks", 0),
                    "ctr": r.get("ctr", 0),
                    "position": r.get("position", 0),
                }
                log(f"{t}: {st[t]['impressions']:,} impressions, {st[t]['clicks']} clicks")
            except Exception as e:
                st[t] = {"error": str(e)[:300]}
                log(f"{t}: SKIPPED, {e}")
        out["by_search_type_90d"] = st
    flush()

    # Headline readout, by-date dimension only.
    def window(n: int) -> str:
        sel = daily[-n:]
        c = sum(r.get("clicks", 0) for r in sel)
        i = sum(r.get("impressions", 0) for r in sel)
        p = sum(r.get("position", 0) * r.get("impressions", 0) for r in sel) / i if i else 0
        return (f"{sel[0]['keys'][0]} to {sel[-1]['keys'][0]}  "
                f"impr {i:,}  clicks {c}  CTR {100*c/i:.2f}%  pos {p:.1f}")

    print("\n" + "=" * 68)
    print(f"WROTE  {OUT}")
    print("=" * 68)
    print("\nSite totals, DATE dimension (the authoritative one):")
    print(f"  28d : {window(28)}")
    print(f"  90d : {window(90)}")
    q90 = out["windows"]["90d"]["summary_by_query_dimension"]
    print(f"\nSame 90d by QUERY dimension (undercounts, contrast only):")
    print(f"  impr {q90['total_impressions']:,}  clicks {q90['total_clicks']}  "
          f"pos {q90['weighted_average_position']}")
    pages = sorted(out["windows"]["90d"]["raw_by_page"],
                   key=lambda r: -r.get("impressions", 0))
    if pages:
        tot_i = sum(r.get("impressions", 0) for r in pages)
        tot_c = sum(r.get("clicks", 0) for r in pages)
        h = pages[0]
        print(f"\nTop page (PAGE dimension): {h['keys'][0]}")
        print(f"  impr {h['impressions']:,} ({100*h['impressions']/tot_i:.0f}% of page-dim impressions)  "
              f"clicks {h['clicks']} ({100*h['clicks']/tot_c:.0f}%)  "
              f"CTR {100*h.get('ctr',0):.2f}%  pos {h['position']:.1f}")
    print(f"\nDepth check: {out['windows']['90d']['total_query_rows']} queries and "
          f"{out['windows']['90d']['total_page_rows']} pages captured IN FULL.")
    print("\nPaste this readout back into the Cowork chat, or just say it is done.\n")


if __name__ == "__main__":
    main()
