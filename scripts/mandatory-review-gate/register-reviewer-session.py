#!/usr/bin/env python3
"""Register a session as a reviewer session (RGH-11).

Called by the reviewer-orchestrator dispatch or operator to mark a session
as reviewing another session's work. The marker file enables session-level
exemption in the gate: reviewer inspection Bash (without write indicators)
and known reviewer working-doc entries are exempt from requiring independent
review. Deliverable-class Write/Edit ALWAYS gates regardless of session role.

SAFETY:
- Self-registration rejected: session == reviewing_session → exit 1.
- The marker grants SCOPED exemption only (Bash without write indicators).
- A producer forging a marker gains nothing: its Write/Edit deliverables
  still gate, and state-changing Bash (with write indicators) still gates.

Usage:
  python3 register-reviewer-session.py \\
    --session <reviewer-session-id> \\
    --reviewing-session <producer-session-id>

Creates: .review-gate/state/<session>-role.json
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR


def main():
    parser = argparse.ArgumentParser(
        description='Register a session as a reviewer session (RGH-11).')
    parser.add_argument('--session', required=True,
                        help='This reviewer session ID')
    parser.add_argument('--reviewing-session', required=True,
                        help='The producer session ID being reviewed')
    args = parser.parse_args()

    # Self-review rejected (structural — same protection as RGH-8)
    if args.session == args.reviewing_session:
        print(f'REJECTED: --session and --reviewing-session must differ '
              f'(both = {args.session}). A session cannot review itself.',
              file=sys.stderr)
        sys.exit(1)

    if not args.session.strip() or not args.reviewing_session.strip():
        print('REJECTED: --session and --reviewing-session must be non-empty.',
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(STATE_DIR, exist_ok=True)
    marker_path = os.path.join(STATE_DIR, f'{args.session}-role.json')

    marker = {
        'role': 'reviewer',
        'reviewing_session': args.reviewing_session,
        'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'registered_by': 'register-reviewer-session.py',
    }

    with open(marker_path, 'w') as f:
        json.dump(marker, f, indent=2)

    print(json.dumps({
        'status': 'registered',
        'session': args.session,
        'reviewing_session': args.reviewing_session,
        'marker_path': marker_path,
    }))


if __name__ == '__main__':
    main()
