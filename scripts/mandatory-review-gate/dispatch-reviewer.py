#!/usr/bin/env python3
"""dispatch-reviewer: structural reviewer dispatch with auto-registration (RGH-17A).

Routes ALL reviewer dispatch (including operator hand-dispatch) through a
single script that:
1. Creates a pending-dispatch marker with the producer session pre-filled.
2. Prints a paste-ready prompt for the reviewer that includes the
   registration command template (reviewer fills in its own session ID
   from the first gate-block message).

This eliminates the chicken-and-egg problem where the reviewer needed to
be blocked once before it could discover its session ID to register.
The reviewer still experiences exactly ONE gate-block (to discover its
session ID), but after registration all verification Bash is exempt.

Usage (operator runs this in a terminal or CC session):
    python3 dispatch-reviewer.py --producer-session <session_id> \\
        [--run-id <chat-slug>] [--handoff <path-to-handoff.md>]

The script prints the complete reviewer prompt to stdout — the operator
copies it and pastes it into a NEW Claude Code session.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MANDATE_PATH = os.path.normpath(os.path.join(
    SCRIPT_DIR, '..', '..', '..', '..',
    'skills', 'gate-peer-reviewer', 'references',
    'independent-reviewer-mandate.md'))
REGISTER_SCRIPT = os.path.join(SCRIPT_DIR, 'register-reviewer-session.py')


def main():
    parser = argparse.ArgumentParser(
        description='Dispatch an independent reviewer with auto-registration (RGH-17A)')
    parser.add_argument('--producer-session', required=True,
                        help='Producer session ID being reviewed')
    parser.add_argument('--run-id', default=None,
                        help='Chat slug / run ID for firing-tracker')
    parser.add_argument('--handoff', default=None,
                        help='Path to the handoff being reviewed')
    args = parser.parse_args()

    # Create a pending-dispatch marker so the reviewer knows the producer session
    os.makedirs(STATE_DIR, exist_ok=True)
    dispatch_id = f'dispatch-{int(time.time() * 1000)}'
    marker_path = os.path.join(STATE_DIR, f'{dispatch_id}.json')
    marker = {
        'type': 'pending-reviewer-dispatch',
        'producer_session': args.producer_session,
        'run_id': args.run_id,
        'handoff': args.handoff,
        'dispatched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'dispatched_by': 'dispatch-reviewer.py',
    }
    with open(marker_path, 'w') as f:
        json.dump(marker, f, indent=2)

    # Build the paste-ready prompt
    register_cmd = (
        f'python3 {REGISTER_SCRIPT} '
        f'--session <YOUR_SESSION_ID> '
        f'--reviewing-session {args.producer_session}'
    )

    handoff_line = ''
    if args.handoff:
        handoff_line = f'\n\n**Handoff to review:** `{args.handoff}`'

    run_id_line = ''
    if args.run_id:
        run_id_line = f'\n**Run ID for firing-tracker:** `{args.run_id}`'

    prompt = f"""You are the INDEPENDENT ADVERSARIAL REVIEWER for producer session `{args.producer_session}`.

Read and execute the mandate at:
{MANDATE_PATH}

**STEP 0 — REGISTER YOUR SESSION (do this FIRST):**
Run any trivial Bash command (e.g. `date -u`) to trigger a gate-block message.
Your session ID appears in that message after `--session`. Then run:

```
{register_cmd}
```

Replace `<YOUR_SESSION_ID>` with the session ID from the gate-block message.
After registration, your read-only verification Bash will be exempt from the gate.

**IMPORTANT:** Do NOT use `gate-whoami.py` to discover your session ID — it returns the
most recently active session, which is usually the producer's. The gate-block message
is the ONLY authoritative source for your session ID.

**Producer session:** `{args.producer_session}`{handoff_line}{run_id_line}

Read the producer's dirty ledger to see what was touched:
```
cat {os.path.join(STATE_DIR, args.producer_session + '-dirty.jsonl')}
```

Then proceed with the mandate's review protocol (Phases A-E).
DO NOT trust any producer claim — verify everything on disk."""

    print(prompt)
    print(f'\n--- Dispatch marker written to: {marker_path} ---',
          file=sys.stderr)


if __name__ == '__main__':
    main()
