#!/usr/bin/env python3
"""Git-gate processor — processes the commit queue.

This script runs OUTSIDE any gated Claude Code session (no dirty-ledger
gate-loop). It can be:
  - Run manually: python3 git-gate-processor.py [--once | --watch]
  - Run by Hermes daemon as a subprocess
  - Run as a cron job

Modes:
  --once   Process all pending requests in the queue, then exit (default).
  --watch  Watch the queue directory for new files (poll every 5s).
  --dry-run  Run all checks but don't execute commits (for testing).

The processor reads .json files from the commit queue directory, runs the
full pipeline (B1 safety checks → B2 independent sign-off → C self-healing
→ D reversibility → E audit+tripwire), and either auto-commits or escalates.

AC-10: Verified — this script runs outside any gated session. It reads/writes
to .commit-queue/ and .git-gate/audit/ — neither is inside .review-gate/state/
dirty ledgers, so no gate-loop is created.
"""

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import QUEUE_DIR, AUDIT_DIR
import engine


def load_pending_requests() -> list:
    """Load all pending commit requests from the queue directory."""
    if not os.path.isdir(QUEUE_DIR):
        return []

    requests = []
    for queue_file in sorted(glob.glob(os.path.join(QUEUE_DIR, '*.json'))):
        # Skip escalation files
        if os.path.basename(queue_file).startswith('escalation-'):
            continue
        # Skip already-processed files
        if os.path.basename(queue_file).startswith('done-'):
            continue

        try:
            with open(queue_file, 'r') as f:
                request = json.load(f)
            if request.get('status') == 'pending':
                request['_queue_file'] = queue_file
                requests.append(request)
        except (json.JSONDecodeError, OSError) as e:
            print(f'[git-gate] WARNING: Could not read {queue_file}: {e}',
                  file=sys.stderr)

    return requests


def mark_processed(queue_file: str, result: dict):
    """Mark a queue file as processed by renaming it."""
    try:
        with open(queue_file, 'r') as f:
            request = json.load(f)
        request['status'] = result.get('outcome', 'processed')
        request['processed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        request['result'] = result

        # Rename to done-<original>
        dirname = os.path.dirname(queue_file)
        basename = os.path.basename(queue_file)
        done_file = os.path.join(dirname, f'done-{basename}')
        with open(done_file, 'w') as f:
            json.dump(request, f, indent=2)
        os.remove(queue_file)
    except (json.JSONDecodeError, OSError) as e:
        print(f'[git-gate] WARNING: Could not mark {queue_file} as processed: {e}',
              file=sys.stderr)


def process_queue(dry_run: bool = False) -> list:
    """Process all pending requests. Returns list of results."""
    requests = load_pending_requests()
    if not requests:
        print('[git-gate] No pending requests in queue.')
        return []

    print(f'[git-gate] Processing {len(requests)} pending request(s)...')
    results = []

    for request in requests:
        queue_file = request.pop('_queue_file', '')
        run_id = request.get('run_id', 'unknown')
        repo = request.get('repo', 'unknown')
        files = request.get('files_relative', [])

        print(f'\n[git-gate] Processing: {run_id}')
        print(f'  Repo: {repo}')
        print(f'  Files: {", ".join(files[:5])}{"..." if len(files) > 5 else ""}')
        print(f'  Tier: {request.get("tier", "?")}')
        print(f'  Push: {request.get("push", False)}')

        if dry_run:
            # Run checks but don't commit
            check_results = engine.run_safety_checks(request)
            signoff = engine.verify_independent_signoff(request)
            check_results.append(signoff)
            reversibility = engine.classify_reversibility(request)

            failures = [c for c in check_results if not c.passed]
            result = {
                'run_id': run_id,
                'repo': repo,
                'outcome': 'dry-run',
                'checks': [c.to_dict() for c in check_results],
                'reversibility': reversibility,
                'would_commit': len(failures) == 0,
                'failures': [c.to_dict() for c in failures],
            }

            print(f'  [DRY RUN] {len(check_results)} checks: '
                  f'{len(check_results) - len(failures)} PASS, '
                  f'{len(failures)} FAIL')
            print(f'  [DRY RUN] Reversibility: {reversibility}')
            print(f'  [DRY RUN] Would commit: {result["would_commit"]}')

            if failures:
                for f in failures:
                    print(f'    FAIL: {f["name"]} — {f["detail"]}')
        else:
            result = engine.process_commit_request(request)

            outcome = result.get('outcome', '?')
            detail = result.get('detail', '')
            print(f'  Outcome: {outcome}')
            if detail:
                print(f'  Detail: {detail}')
            if result.get('commit_hash'):
                print(f'  Commit: {result["commit_hash"]}')
            if result.get('tripwire_alert'):
                print(f'  ⚠️  TRIPWIRE ALERT: {result["tripwire_alert"]["alerts"]}')
            if result.get('escalation'):
                blocking = result['escalation'].get('blocking_checks', [])
                print(f'  Escalated: {len(blocking)} blocking check(s)')
                for b in blocking:
                    print(f'    - {b["name"]}: {b["detail"]}')

        if queue_file:
            mark_processed(queue_file, result)

        results.append(result)

    # Summary
    print(f'\n[git-gate] Summary: {len(results)} request(s) processed')
    for r in results:
        print(f'  {r.get("run_id", "?")}: {r.get("outcome", "?")}')

    return results


def watch_queue(dry_run: bool = False, interval: int = 5):
    """Watch the queue directory for new requests (polling)."""
    print(f'[git-gate] Watching {QUEUE_DIR} for new requests '
          f'(poll every {interval}s)...')
    print(f'[git-gate] Dry-run: {dry_run}')
    print(f'[git-gate] Press Ctrl+C to stop.\n')

    while True:
        try:
            results = process_queue(dry_run=dry_run)
            if results:
                print()  # blank line between batches
            time.sleep(interval)
        except KeyboardInterrupt:
            print('\n[git-gate] Stopped.')
            break


def main():
    parser = argparse.ArgumentParser(
        description='Git-gate processor — processes the commit queue.')
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--once', action='store_true', default=True,
                            help='Process all pending requests, then exit (default)')
    mode_group.add_argument('--watch', action='store_true',
                            help='Watch the queue for new requests')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run checks but do not execute commits')
    parser.add_argument('--interval', type=int, default=5,
                        help='Poll interval in seconds for --watch mode')
    parser.add_argument('--clear-tripwire', action='store_true',
                        help='Clear the tripwire pause (operator action)')
    parser.add_argument('--status', action='store_true',
                        help='Show current git-gate status')
    args = parser.parse_args()

    if args.clear_tripwire:
        engine.clear_tripwire_pause()
        print('[git-gate] Tripwire pause cleared.')
        return

    if args.status:
        paused = engine.is_auto_land_paused()
        pending = load_pending_requests()
        print(f'[git-gate] Status:')
        print(f'  Queue dir: {QUEUE_DIR}')
        print(f'  Audit dir: {AUDIT_DIR}')
        print(f'  Pending requests: {len(pending)}')
        print(f'  Auto-land paused: {paused}')
        if paused:
            tripwire_file = os.path.join(engine.TRIPWIRE_DIR, 'pause.json')
            try:
                with open(tripwire_file, 'r') as f:
                    alert = json.load(f)
                print(f'  Tripwire alerts: {alert.get("alerts", [])}')
            except (json.JSONDecodeError, OSError):
                pass
        return

    if args.watch:
        watch_queue(dry_run=args.dry_run, interval=args.interval)
    else:
        results = process_queue(dry_run=args.dry_run)
        # Exit with non-zero if any request was rejected or escalated
        if any(r.get('outcome') in ('rejected', 'escalated') for r in results):
            sys.exit(1)


if __name__ == '__main__':
    main()
