#!/usr/bin/env python3
"""Regression tests for RGH-8: reviewer-independence (CR-045) + fail-closed (CR-049).

4 cases from the handoff DoD:
1. --reviewer-type independent with --reviewer-session == --session → rejected (exit 1)
2. --reviewer-type independent with distinct --reviewer-session + existing run-id rows → accepted (exit 0)
3. log-review-pass.py invoked from a non-root cwd → tracker still found (path-anchoring works)
4. Independent PASS with --run-id that has NO rows in tracker → rejected (exit 1)

Updated 2026-07-25 (gate-harden-w1): use valid UUID v4 session IDs + role
markers + reviewer dirty-ledger activity (required by CR-165 + RGH-15 hardened).
"""

import json
import os
import subprocess
import sys
import tempfile
import time

# The script under test
SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'log-review-pass.py'
)
SCRIPT = os.path.realpath(SCRIPT)

# Workspace root (from _paths.py logic: 4 levels up from the script dir)
WORKSPACE_ROOT = os.path.realpath(os.path.join(
    os.path.dirname(SCRIPT), '..', '..', '..', '..'))

FIRING_TRACKER = os.path.join(
    WORKSPACE_ROOT, 'second-brain', '_meta', 'handoffs',
    '_review-skill-firing-tracker.md')

# Valid UUID v4 values for test sessions (CR-165 requires UUID v4 format)
PRODUCER_SESSION = '11111111-1111-4111-a111-111111111111'
REVIEWER_SESSION = '22222222-2222-4222-a222-222222222222'


def _make_verdict_file(tmpdir, reviewer_type='independent', mandate_version='v1.2'):
    """Create a minimal valid independent verdict file."""
    verdict = {
        'verdict': 'PASS',
        'checks_run': [
            {'name': 'ground-truth-cross-check', 'result': 'PASS'},
            {'name': 'placeholder-sweep', 'result': 'PASS'},
        ],
        'catches': [],
        'cost_usd': 0.0,
        'reviewer_type': reviewer_type,
        'mandate_version': mandate_version,
    }
    path = os.path.join(tmpdir, 'verdict.json')
    with open(path, 'w') as f:
        json.dump(verdict, f)
    return path


def _make_dummy_file(tmpdir, name='artifact.md'):
    """Create a dummy file to pass as --files."""
    path = os.path.join(tmpdir, name)
    with open(path, 'w') as f:
        f.write('# dummy artifact\n')
    return path


def _register_reviewer(state_dir, reviewer_id, producer_id,
                        operator_dispatched=False):
    """Create a reviewer role marker in state_dir."""
    os.makedirs(state_dir, exist_ok=True)
    marker = {
        'role': 'reviewer',
        'reviewing_session': producer_id,
        'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'registered_by': 'test',
    }
    if operator_dispatched:
        marker['operator_dispatched'] = True
    marker_path = os.path.join(state_dir, f'{reviewer_id}-role.json')
    with open(marker_path, 'w') as f:
        json.dump(marker, f)


def _create_reviewer_activity(state_dir, reviewer_id):
    """Create a dummy dirty-ledger entry for the reviewer session.

    RGH-15 hardened: inert reviewers (no dirty-ledger activity) are DENIED
    unless operator-dispatched. This creates activity to prove the reviewer
    actually ran verification commands.
    """
    os.makedirs(state_dir, exist_ok=True)
    ledger_path = os.path.join(state_dir, f'{reviewer_id}-dirty.jsonl')
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': 'Bash',
        'file_path': 'BASH:abc123',
        'tier': 'full',
        'source': 'claude-code',
        'entry_source': 'reviewer',
        'display': 'grep -r pattern .',
    }
    with open(ledger_path, 'a') as f:
        f.write(json.dumps(entry) + '\n')


def _run_script(args, cwd=None, env_override=None):
    """Run log-review-pass.py with the given args. Returns (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True, cwd=cwd, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_1_same_session_rejected():
    """CR-045: --reviewer-session == --session → rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verdict_path = _make_verdict_file(tmpdir)
        artifact = _make_dummy_file(tmpdir)
        state_dir = os.path.join(tmpdir, 'state')
        os.makedirs(state_dir)

        rc, stdout, stderr = _run_script([
            '--session', PRODUCER_SESSION,
            '--files', artifact,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-independent',
            '--verdict-file', verdict_path,
            '--reviewer-type', 'independent',
            '--reviewer-session', PRODUCER_SESSION,  # SAME as --session
            '--run-id', 'test-run-id',
        ], env_override={'REVIEW_GATE_STATE_DIR': state_dir})

        assert rc != 0, f'Expected non-zero exit, got {rc}. stderr: {stderr}'
        assert 'REJECTED' in stderr, f'Expected REJECTED in stderr: {stderr}'
        assert 'reviewer-session equals' in stderr or 'CR-165' in stderr, (
            f'Expected same-session rejection message: {stderr}')
        # Verify no marker was written
        reviewed = os.path.join(state_dir, f'{PRODUCER_SESSION}-reviewed.jsonl')
        assert not os.path.exists(reviewed), \
            'Review marker should NOT be written on rejection'


def test_2_distinct_session_accepted():
    """Distinct --reviewer-session + existing run-id rows → accepted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verdict_path = _make_verdict_file(tmpdir)
        artifact = _make_dummy_file(tmpdir)
        state_dir = os.path.join(tmpdir, 'state')
        os.makedirs(state_dir)

        # Register the reviewer and create activity (RGH-15 requires it)
        _register_reviewer(state_dir, REVIEWER_SESSION, PRODUCER_SESSION)
        _create_reviewer_activity(state_dir, REVIEWER_SESSION)

        # The firing tracker must exist AND contain the run-id.
        assert os.path.isfile(FIRING_TRACKER), (
            f'Firing tracker must exist at {FIRING_TRACKER} for this test')

        with open(FIRING_TRACKER, 'r') as f:
            content = f.read()
        import re
        run_ids = re.findall(r'[a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{12}', content)
        assert run_ids, 'No run IDs found in firing tracker — cannot test acceptance'
        real_run_id = run_ids[0]

        rc, stdout, stderr = _run_script([
            '--session', PRODUCER_SESSION,
            '--files', artifact,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-independent',
            '--verdict-file', verdict_path,
            '--reviewer-type', 'independent',
            '--reviewer-session', REVIEWER_SESSION,
            '--run-id', real_run_id,
        ], env_override={'REVIEW_GATE_STATE_DIR': state_dir})

        assert rc == 0, f'Expected exit 0, got {rc}. stderr: {stderr}'
        assert 'Logged PASS' in stdout, f'Expected success message: {stdout}'
        reviewed = os.path.join(state_dir, f'{PRODUCER_SESSION}-reviewed.jsonl')
        assert os.path.exists(reviewed), \
            'Review marker should be written on acceptance'


def test_3_non_root_cwd_tracker_found():
    """CR-049: invoked from a non-root cwd → tracker still found (path-anchoring)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verdict_path = _make_verdict_file(tmpdir)
        artifact = _make_dummy_file(tmpdir)
        state_dir = os.path.join(tmpdir, 'state')
        os.makedirs(state_dir)

        # Register reviewer + activity
        _register_reviewer(state_dir, REVIEWER_SESSION, PRODUCER_SESSION)
        _create_reviewer_activity(state_dir, REVIEWER_SESSION)

        nested_cwd = os.path.join(tmpdir, 'some', 'deep', 'nested', 'dir')
        os.makedirs(nested_cwd)

        assert os.path.isfile(FIRING_TRACKER), (
            f'Firing tracker must exist at {FIRING_TRACKER}')

        with open(FIRING_TRACKER, 'r') as f:
            content = f.read()
        import re
        run_ids = re.findall(r'[a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{12}', content)
        assert run_ids, 'No run IDs found in firing tracker'
        real_run_id = run_ids[0]

        rc, stdout, stderr = _run_script([
            '--session', PRODUCER_SESSION,
            '--files', artifact,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-independent',
            '--verdict-file', verdict_path,
            '--reviewer-type', 'independent',
            '--reviewer-session', REVIEWER_SESSION,
            '--run-id', real_run_id,
        ], cwd=nested_cwd, env_override={'REVIEW_GATE_STATE_DIR': state_dir})

        assert rc == 0, (
            f'Expected exit 0 (tracker found from non-root cwd), got {rc}. '
            f'stderr: {stderr}')
        assert 'Logged PASS' in stdout, f'Expected success message: {stdout}'


def test_4_missing_run_id_rows_rejected():
    """Fail-closed: independent PASS with --run-id that has NO rows → rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verdict_path = _make_verdict_file(tmpdir)
        artifact = _make_dummy_file(tmpdir)
        state_dir = os.path.join(tmpdir, 'state')
        os.makedirs(state_dir)

        # Register reviewer + activity
        _register_reviewer(state_dir, REVIEWER_SESSION, PRODUCER_SESSION)
        _create_reviewer_activity(state_dir, REVIEWER_SESSION)

        assert os.path.isfile(FIRING_TRACKER), (
            f'Firing tracker must exist at {FIRING_TRACKER}')

        fake_run_id = 'nonexistent-fake-runid-999999999999'

        rc, stdout, stderr = _run_script([
            '--session', PRODUCER_SESSION,
            '--files', artifact,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-independent',
            '--verdict-file', verdict_path,
            '--reviewer-type', 'independent',
            '--reviewer-session', REVIEWER_SESSION,
            '--run-id', fake_run_id,
        ], env_override={'REVIEW_GATE_STATE_DIR': state_dir})

        assert rc != 0, \
            f'Expected non-zero exit (fail-closed), got {rc}. stderr: {stderr}'
        assert 'REJECTED' in stderr, f'Expected REJECTED in stderr: {stderr}'
        assert 'No firing-tracker rows found' in stderr, (
            f'Expected "No firing-tracker rows found" in stderr: {stderr}')
        reviewed = os.path.join(state_dir, f'{PRODUCER_SESSION}-reviewed.jsonl')
        assert not os.path.exists(reviewed), \
            'Review marker should NOT be written on rejection'


if __name__ == '__main__':
    print('RGH-8 Regression Tests')
    print('=' * 60)

    tests = [
        ('Test 1 — CR-045: same-session reviewer rejected',
         test_1_same_session_rejected),
        ('Test 2 — distinct-session reviewer accepted',
         test_2_distinct_session_accepted),
        ('Test 3 — CR-049: non-root cwd path-anchoring',
         test_3_non_root_cwd_tracker_found),
        ('Test 4 — fail-closed: missing run-id rows',
         test_4_missing_run_id_rows_rejected),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f'\n{name}:')
        try:
            fn()
            passed += 1
            print('  PASS')
        except AssertionError as e:
            print(f'  FAIL: {e}')
            failed += 1
        except Exception as e:
            print(f'  ERROR: {type(e).__name__}: {e}')
            failed += 1

    print(f'\n{"=" * 60}')
    print(f'Results: {passed} passed, {failed} failed out of {len(tests)}')
    sys.exit(1 if failed else 0)
