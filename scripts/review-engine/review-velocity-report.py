#!/usr/bin/env python3
"""Review velocity tracker — reads reviews-log CSV, computes reviews/month, flags low velocity.

Usage:
    python3 review-velocity-report.py --config configs/<client-slug>.json [--log logs/<slug>-reviews-log.csv]

The reviews log CSV schema:
    date,customer_first_name,city,service,channel,requested,landed,stars

Row zero (baseline) uses channel=baseline, requested=no, landed=yes to seed the starting count.

Reports:
  - Total reviews (baseline + landed)
  - Reviews per month (rolling, excluding baseline)
  - Current velocity vs target (4-8/month)
  - Trend (improving / steady / declining)
  - Nudge if velocity < min target
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, date


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return json.load(f)


def validate_config(config: dict, config_path: str) -> None:
    required = ["client_slug", "business_name", "review_baseline", "velocity_target_per_month"]
    missing = [k for k in required if k not in config]
    if missing:
        print(f"FAIL: config {config_path} missing required keys: {missing}", file=sys.stderr)
        sys.exit(1)

    baseline = config["review_baseline"]
    baseline_required = ["count", "rating", "captured_date", "source"]
    baseline_missing = [k for k in baseline_required if k not in baseline]
    if baseline_missing:
        print(f"FAIL: config {config_path} review_baseline missing required keys: {baseline_missing}", file=sys.stderr)
        sys.exit(1)

    target = config["velocity_target_per_month"]
    target_required = ["min", "max"]
    target_missing = [k for k in target_required if k not in target]
    if target_missing:
        print(f"FAIL: config {config_path} velocity_target_per_month missing required keys: {target_missing}", file=sys.stderr)
        sys.exit(1)


def load_log(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        print(f"FAIL: reviews log not found at {log_path}", file=sys.stderr)
        sys.exit(1)
    with open(log_path) as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_date(date_str: str) -> date:
    return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()


def compute_velocity(rows: list[dict], config: dict) -> None:
    business = config["business_name"]
    target_min = config["velocity_target_per_month"]["min"]
    target_max = config["velocity_target_per_month"]["max"]
    baseline = config["review_baseline"]

    # Separate baseline from actual reviews
    actual_reviews = [r for r in rows if r.get("channel", "").strip() != "baseline"]
    landed_reviews = [r for r in actual_reviews if r.get("landed", "").strip().lower() == "yes"]
    requested_reviews = [r for r in actual_reviews if r.get("requested", "").strip().lower() == "yes"]

    print(f"{'=' * 60}")
    print(f"REVIEW VELOCITY REPORT — {business}")
    print(f"{'=' * 60}")
    print()

    # Baseline
    print(f"Baseline ({baseline['captured_date']}): {baseline['count']} reviews, {baseline['rating']}★")
    print(f"  Source: {baseline['source']}")
    print()

    # Totals
    total_landed = baseline["count"] + len(landed_reviews)
    print(f"Total reviews (baseline + landed): {total_landed}")
    print(f"New reviews landed (post-baseline): {len(landed_reviews)}")
    print(f"Requests sent (post-baseline): {len(requested_reviews)}")
    if requested_reviews:
        conversion = len(landed_reviews) / len(requested_reviews) * 100
        print(f"Conversion rate: {conversion:.0f}%")
    print()

    # Monthly breakdown
    if not landed_reviews:
        print("No post-baseline reviews yet. Start the ask sequence!")
        print(f"Target: {target_min}-{target_max} reviews/month.")
        return

    by_month = defaultdict(list)
    for r in landed_reviews:
        try:
            d = parse_date(r["date"])
            key = f"{d.year}-{d.month:02d}"
            by_month[key].append(r)
        except (ValueError, KeyError):
            pass

    if by_month:
        months_sorted = sorted(by_month.keys())
        print("Monthly breakdown:")
        counts = []
        for m in months_sorted:
            count = len(by_month[m])
            counts.append(count)
            status = ""
            if count < target_min:
                status = " ⚠ BELOW TARGET"
            elif count > target_max:
                status = " 🎯 ABOVE TARGET"
            else:
                status = " ✓ on target"
            print(f"  {m}: {count} reviews{status}")
        print()

        # Velocity
        avg = sum(counts) / len(counts)
        print(f"Average velocity: {avg:.1f} reviews/month")
        print(f"Target range: {target_min}-{target_max}/month")

        if avg < target_min:
            print(f"\n⚠ VELOCITY BELOW TARGET — tighten the ask discipline.")
            print(f"  The verbal ask at job completion + same-day SMS doubles conversion.")
            print(f"  Are all techs/owners asking on every completed job?")
        elif avg > target_max:
            print(f"\n🎯 Velocity above target — excellent! Maintain the routine.")
        else:
            print(f"\n✓ Velocity on target. Keep the routine consistent.")

        # Trend (last 2 months)
        if len(counts) >= 2:
            recent = counts[-1]
            prior = counts[-2]
            if recent > prior:
                print(f"Trend: ↑ improving ({prior} → {recent})")
            elif recent < prior:
                print(f"Trend: ↓ declining ({prior} → {recent})")
            else:
                print(f"Trend: → steady ({prior} → {recent})")

    print()
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Review velocity report from reviews log")
    parser.add_argument("--config", required=True, help="Path to client config JSON")
    parser.add_argument("--log", default=None, help="Path to reviews log CSV (default: logs/<slug>-reviews-log.csv)")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config, args.config)
    slug = config["client_slug"]
    log_path = args.log or os.path.join(os.path.dirname(__file__), "logs", f"{slug}-reviews-log.csv")

    rows = load_log(log_path)
    compute_velocity(rows, config)


if __name__ == "__main__":
    main()
