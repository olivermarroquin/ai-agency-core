#!/usr/bin/env python3
"""Fail-open block-test canary for the review gate.

Writes a synthetic dirty entry and confirms the Stop hook blocks (exit 2).
If the hook returns exit 0 instead, the gate has silently broken (D-11).

RGH-1b item 6: complements the Cowork gate-health-canary weekly audit
(config drift / metrics freshness / skip rows / verdict spot-checks).
This is the in-gate complement — a real block test.

Usage:
    python3 gate-canary.py                     # run canary test
    python3 gate-canary.py --json              # JSON output
    python3 gate-canary.py --state-dir <dir>   # custom state dir

Exit codes:
    0 — canary PASSED (gate correctly blocked)
    1 — canary FAILED (gate did NOT block — D-11 fail-open detected)
    2 — canary ERROR (could not run the test)
"""

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(SCRIPTS, 'dirty-ledger-track.py')
GATE = os.path.join(SCRIPTS, 'mandatory-review-gate.py')


def run_canary(state_dir: str = None) -> dict:
    """Run the block-test canary.

    Creates an isolated state dir, writes a synthetic dirty entry,
    runs the Stop hook, and checks for exit 2.

    Returns a dict with: passed (bool), exit_code (int), details (str).
    """
    # Use a temp dir to avoid polluting real state
    canary_dir = state_dir or tempfile.mkdtemp(prefix='rg-canary-')
    canary_sid = f'canary-{int(time.time())}'

    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = canary_dir

    # Step 1: Write a synthetic dirty entry via the tracker
    track_input = json.dumps({
        'session_id': canary_sid,
        'tool_name': 'Write',
        'tool_input': {
            'file_path': os.path.join(canary_dir, 'canary-artifact.md'),
            'content': '# Canary\nThis is a test artifact.\nLine 3\nLine 4\n'
                       'Line 5\nLine 6\nLine 7\n',
        },
        'tool_result': {'text': 'ok'},
    })

    # Also create the actual file so fast-path checks can read it
    artifact_path = os.path.join(canary_dir, 'canary-artifact.md')
    os.makedirs(canary_dir, exist_ok=True)
    with open(artifact_path, 'w') as f:
        f.write('# Canary\nThis is a test artifact.\nLine 3\nLine 4\n'
                'Line 5\nLine 6\nLine 7\n')

    try:
        track_result = subprocess.run(
            [sys.executable, TRACK],
            input=track_input,
            capture_output=True, text=True,
            env=env, timeout=10,
        )
    except Exception as e:
        return {
            'passed': None,
            'exit_code': -1,
            'details': f'Tracker failed: {e}',
            'phase': 'track',
        }

    # Verify dirty entry was written
    dirty_path = os.path.join(canary_dir, f'{canary_sid}-dirty.jsonl')
    if not os.path.isfile(dirty_path):
        return {
            'passed': None,
            'exit_code': -1,
            'details': 'Tracker did not create dirty ledger',
            'phase': 'track-verify',
        }

    # Step 2: Run the Stop hook — should block (exit 2)
    gate_input = json.dumps({
        'session_id': canary_sid,
        'stop_reason': 'end_turn',
    })

    try:
        gate_result = subprocess.run(
            [sys.executable, GATE],
            input=gate_input,
            capture_output=True, text=True,
            env=env, timeout=30,
        )
    except Exception as e:
        return {
            'passed': None,
            'exit_code': -1,
            'details': f'Stop hook failed to run: {e}',
            'phase': 'gate',
        }

    if gate_result.returncode == 2:
        return {
            'passed': True,
            'exit_code': gate_result.returncode,
            'details': 'Gate correctly blocked (exit 2)',
            'phase': 'complete',
        }
    else:
        return {
            'passed': False,
            'exit_code': gate_result.returncode,
            'details': (f'FAIL-OPEN DETECTED (D-11): gate returned exit '
                        f'{gate_result.returncode} instead of 2. '
                        f'stdout: {gate_result.stdout[:200]}  '
                        f'stderr: {gate_result.stderr[:200]}'),
            'phase': 'complete',
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Fail-open block-test canary for the review gate')
    parser.add_argument('--state-dir', default=None,
                        help='Custom state dir (default: temp dir)')
    parser.add_argument('--json', action='store_true', dest='as_json',
                        help='JSON output')
    args = parser.parse_args()

    result = run_canary(state_dir=args.state_dir)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        status = ('PASSED' if result['passed']
                  else 'FAILED' if result['passed'] is False
                  else 'ERROR')
        print(f'Gate canary: {status}')
        print(f'  {result["details"]}')

    if result['passed'] is True:
        sys.exit(0)
    elif result['passed'] is False:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == '__main__':
    main()
