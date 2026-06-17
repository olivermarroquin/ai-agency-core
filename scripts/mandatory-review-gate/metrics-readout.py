#!/usr/bin/env python3
"""Analyze review-gate metrics.jsonl for wall-clock overhead.

Reports p50/p95/max wall-clock times, broken down by outcome
(approve/block/auto-clear/exempt). Flags any invocation exceeding
a configurable threshold (default 5000ms).

RGH-1b item 5: built 2026-06-17. Numeric tuning deferred until the
06-19 staged-week wrap provides real production data.

Usage:
    python3 metrics-readout.py                    # default state dir
    python3 metrics-readout.py --state-dir <dir>  # custom state dir
    python3 metrics-readout.py --threshold 3000   # custom slow threshold (ms)
    python3 metrics-readout.py --json             # JSON output
    python3 metrics-readout.py --since 2026-06-12 # filter by date
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR


def load_metrics(state_dir: str, since: str = None) -> list:
    """Load metrics.jsonl entries, optionally filtering by date."""
    path = os.path.join(state_dir, 'metrics.jsonl')
    if not os.path.isfile(path):
        return []

    entries = []
    since_ts = None
    if since:
        try:
            since_ts = datetime.strptime(since, '%Y-%m-%d').timestamp()
        except ValueError:
            print(f'Warning: invalid --since date "{since}", ignoring filter',
                  file=sys.stderr)

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts and entry.get('timestamp', 0) < since_ts:
                continue
            entries.append(entry)

    return entries


def percentile(values: list, p: float) -> float:
    """Compute the p-th percentile (0-100) of a sorted list."""
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(values):
        return values[f]
    return values[f] + (k - f) * (values[c] - values[f])


def analyze(entries: list, threshold_ms: float = 5000.0) -> dict:
    """Compute statistics from metrics entries."""
    if not entries:
        return {'total': 0, 'by_outcome': {}, 'slow': [], 'overall': {}}

    by_outcome = {}
    all_wall = []
    slow = []

    for e in entries:
        outcome = e.get('outcome', 'unknown')
        wall = e.get('wall_ms', 0.0)
        all_wall.append(wall)

        if outcome not in by_outcome:
            by_outcome[outcome] = []
        by_outcome[outcome].append(wall)

        if wall > threshold_ms:
            slow.append({
                'session_id': e.get('session_id', '?'),
                'outcome': outcome,
                'wall_ms': round(wall, 2),
                'iso_time': e.get('iso_time', '?'),
                'tier': e.get('tier', '?'),
            })

    result = {
        'total': len(entries),
        'threshold_ms': threshold_ms,
        'overall': {
            'p50': round(percentile(all_wall, 50), 2),
            'p95': round(percentile(all_wall, 95), 2),
            'max': round(max(all_wall), 2),
            'min': round(min(all_wall), 2),
            'mean': round(sum(all_wall) / len(all_wall), 2),
        },
        'by_outcome': {},
        'slow_count': len(slow),
        'slow': slow[:10],  # cap at 10 worst
    }

    for outcome, walls in sorted(by_outcome.items()):
        result['by_outcome'][outcome] = {
            'count': len(walls),
            'p50': round(percentile(walls, 50), 2),
            'p95': round(percentile(walls, 95), 2),
            'max': round(max(walls), 2),
        }

    return result


def format_human(stats: dict) -> str:
    """Format stats for human-readable output."""
    lines = []
    lines.append(f'Review Gate Metrics — {stats["total"]} invocations')
    lines.append(f'Slow threshold: {stats["threshold_ms"]}ms')
    lines.append('')

    ov = stats.get('overall', {})
    if ov:
        lines.append(f'Overall:  p50={ov["p50"]}ms  p95={ov["p95"]}ms  '
                      f'max={ov["max"]}ms  min={ov["min"]}ms  mean={ov["mean"]}ms')
        lines.append('')

    lines.append('By outcome:')
    for outcome, s in stats.get('by_outcome', {}).items():
        lines.append(f'  {outcome:20s}  n={s["count"]:4d}  '
                      f'p50={s["p50"]:8.2f}ms  p95={s["p95"]:8.2f}ms  '
                      f'max={s["max"]:8.2f}ms')

    slow_count = stats.get('slow_count', 0)
    if slow_count > 0:
        lines.append('')
        lines.append(f'⚠ {slow_count} invocation(s) exceeded threshold:')
        for s in stats.get('slow', []):
            lines.append(f'  {s["iso_time"]}  {s["outcome"]:15s}  '
                          f'{s["wall_ms"]}ms  tier={s["tier"]}  '
                          f'session={s["session_id"][:16]}...')
    else:
        lines.append('')
        lines.append('No invocations exceeded threshold.')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Analyze review-gate metrics for wall-clock overhead')
    parser.add_argument('--state-dir', default=STATE_DIR,
                        help='Path to state directory')
    parser.add_argument('--threshold', type=float, default=5000.0,
                        help='Slow-invocation threshold in ms (default: 5000)')
    parser.add_argument('--json', action='store_true', dest='as_json',
                        help='Output as JSON')
    parser.add_argument('--since', default=None,
                        help='Only include entries after this date (YYYY-MM-DD)')
    args = parser.parse_args()

    entries = load_metrics(args.state_dir, since=args.since)
    stats = analyze(entries, threshold_ms=args.threshold)

    if args.as_json:
        print(json.dumps(stats, indent=2))
    else:
        print(format_human(stats))


if __name__ == '__main__':
    main()
