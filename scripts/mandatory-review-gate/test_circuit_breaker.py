#!/usr/bin/env python3
"""Regression tests for the stop-hook circuit breaker (RGH-CB).

Tests:
1. Circuit breaker fires on Nth consecutive identical block (N=3).
2. Counter resets when new entries are added between firings.
3. Counter resets when entries are cleared between firings.
4. verify-artifact.py is classified as read-only (no dirty entry).
5. --skip-http / --dry-run / --json flags classify python3 as read-only.

TEST ISOLATION: Every test creates a fresh temp dir via REVIEW_GATE_STATE_DIR.
No test reads or writes live .review-gate/state/.

Usage:
    python3 test_circuit_breaker.py        # from any directory
    python3 test_circuit_breaker.py -v     # verbose output
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(SCRIPTS, 'dirty-ledger-track.py')
GATE = os.path.join(SCRIPTS, 'mandatory-review-gate.py')

# Workspace root derived the same way _paths.py does it
EXPECTED_WORKSPACE_ROOT = os.path.realpath(
    os.path.join(SCRIPTS, '..', '..', '..', '..'))
SUBDIR_CWD = os.path.join(
    EXPECTED_WORKSPACE_ROOT, 'repos', 'ai-agency-core', 'scripts')


def _make_env(state_dir, source=None):
    """Build a subprocess env dict with REVIEW_GATE_STATE_DIR set."""
    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = state_dir
    env['REVIEW_GATE_EVENT_LOG'] = os.path.join(
        state_dir, '_event-log-test.md')
    if source is not None:
        env['REVIEW_GATE_SOURCE'] = source
    elif 'REVIEW_GATE_SOURCE' in env:
        del env['REVIEW_GATE_SOURCE']
    return env


def _run_track(sid, tool_name, tool_input, state_dir, source=None):
    """Run dirty-ledger-track.py with the given inputs."""
    return subprocess.run(
        ['python3', TRACK],
        input=json.dumps({
            'session_id': sid, 'tool_name': tool_name,
            'tool_input': tool_input, 'tool_result': {'text': 'ok'},
        }),
        capture_output=True, text=True,
        cwd=SUBDIR_CWD,
        env=_make_env(state_dir, source=source),
    )


def _run_gate(sid, state_dir):
    """Run mandatory-review-gate.py (the stop hook)."""
    return subprocess.run(
        ['python3', GATE],
        input=json.dumps({'session_id': sid, 'stop_reason': 'end_turn'}),
        capture_output=True, text=True,
        cwd=SUBDIR_CWD,
        env=_make_env(state_dir),
    )


# Content >5 lines so classify_tier returns 'full' (not fast-path auto-cleared)
_FULL_TIER_CONTENT = '\n'.join([f'line {i}' for i in range(10)]) + '\n'


def _create_dirty_entry(sid, state_dir, file_path):
    """Create a dirty entry by tracking a Write to the given file_path.

    Uses >5 lines of content so the entry is classified as full-tier,
    preventing fast-path auto-clear from short-circuiting the gate.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        f.write(_FULL_TIER_CONTENT)
    _run_track(sid, 'Write', {
        'file_path': file_path,
        'content': _FULL_TIER_CONTENT,
    }, state_dir)


class TestCircuitBreaker(unittest.TestCase):
    """Test the circuit breaker in mandatory-review-gate.py.

    NOTE: The independent reviewer dispatch (RGH-5) auto-writes a BLOCKING
    reviewed marker on first block, which clears the entry from the unreviewed
    set. To test the circuit breaker in isolation, we must remove the
    dispatch-written reviewed entries between firings so the entries remain
    unreviewed and the circuit breaker can accumulate consecutive blocks.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rg-cb-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        os.makedirs(self.state_dir, exist_ok=True)
        self.sid = f'test-cb-{int(time.time() * 1000)}'
        # Create a test file that will be tracked as dirty
        self.test_file = os.path.join(self.tmpdir, 'test-artifact.md')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _clear_dispatch_artifacts(self):
        """Remove reviewed entries and verdict files written by the
        independent dispatch so the dirty entry stays unreviewed."""
        import glob as glob_mod
        for f in glob_mod.glob(os.path.join(self.state_dir, f'{self.sid}-reviewed.jsonl')):
            os.remove(f)
        for f in glob_mod.glob(os.path.join(self.state_dir, 'verdict-*.json')):
            os.remove(f)

    def test_circuit_breaker_fires_on_3rd_consecutive_block(self):
        """DoD #3, #4: auto-skip on 3 consecutive identical blocks with loud warning."""
        _create_dirty_entry(self.sid, self.state_dir, self.test_file)

        # First firing — should block (exit 2)
        r1 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r1.returncode, 2, f'1st firing should block. stderr: {r1.stderr}')
        self._clear_dispatch_artifacts()

        # Second firing — should block (exit 2)
        r2 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r2.returncode, 2, f'2nd firing should block. stderr: {r2.stderr}')
        self._clear_dispatch_artifacts()

        # Third firing — circuit breaker fires, should allow (exit 0)
        r3 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r3.returncode, 0,
                         f'3rd firing should auto-skip (circuit breaker). '
                         f'stderr: {r3.stderr}')

        # Verify LOUD warning in stderr
        self.assertIn('CIRCUIT BREAKER', r3.stderr,
                       'Circuit breaker warning must be loud')
        self.assertIn('auto-skip', r3.stderr.lower(),
                       'Warning must mention auto-skip')

    def test_circuit_breaker_history_file_created(self):
        """DoD #2: stop-hook-history.jsonl is written (append-only, CR-011)."""
        _create_dirty_entry(self.sid, self.state_dir, self.test_file)
        _run_gate(self.sid, self.state_dir)

        history_path = os.path.join(
            self.state_dir, f'{self.sid}-stop-hook-history.jsonl')
        self.assertTrue(os.path.isfile(history_path),
                        f'History file should exist at {history_path}')

        with open(history_path, 'r') as f:
            rows = [json.loads(l) for l in f if l.strip()]
        self.assertGreater(len(rows), 0, 'History should have at least one row')
        self.assertIn('fingerprint', rows[0],
                       'Each row must have a fingerprint (anti-poisoning)')
        self.assertIn('timestamp', rows[0])

    def test_metrics_logged_on_circuit_breaker(self):
        """DoD #5: metrics.jsonl has circuit-breaker-triggered entry."""
        _create_dirty_entry(self.sid, self.state_dir, self.test_file)

        # Fire 3 times to trigger circuit breaker, clearing dispatch
        # artifacts between firings so entries stay unreviewed.
        _run_gate(self.sid, self.state_dir)
        self._clear_dispatch_artifacts()
        _run_gate(self.sid, self.state_dir)
        self._clear_dispatch_artifacts()
        _run_gate(self.sid, self.state_dir)

        metrics_path = os.path.join(self.state_dir, 'metrics.jsonl')
        self.assertTrue(os.path.isfile(metrics_path),
                        'metrics.jsonl should exist')

        with open(metrics_path, 'r') as f:
            lines = [json.loads(l) for l in f if l.strip()]

        cb_entries = [e for e in lines
                      if e.get('event') == 'circuit-breaker-triggered'
                      or e.get('outcome') == 'circuit-breaker-triggered']
        self.assertTrue(len(cb_entries) >= 1,
                        f'Expected circuit-breaker-triggered in metrics. '
                        f'Got: {[e.get("outcome") for e in lines]}')

    def test_counter_resets_on_new_entry(self):
        """DoD #8: adding a new entry between blocks resets the counter."""
        _create_dirty_entry(self.sid, self.state_dir, self.test_file)

        # Fire twice — blocked both times, counter at 2
        r1 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r1.returncode, 2)
        self._clear_dispatch_artifacts()
        r2 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r2.returncode, 2)
        self._clear_dispatch_artifacts()

        # Add a NEW entry — this changes the entry set
        new_file = os.path.join(self.tmpdir, 'new-artifact.md')
        _create_dirty_entry(self.sid, self.state_dir, new_file)

        # 3rd firing on a DIFFERENT set — counter resets, should block (not skip)
        r3 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r3.returncode, 2,
                         f'3rd firing on different set should block, not skip. '
                         f'stderr: {r3.stderr}')
        self.assertNotIn('CIRCUIT BREAKER', r3.stderr,
                          'Circuit breaker should NOT fire when entries changed')

    def test_counter_resets_after_circuit_breaker_fires(self):
        """After circuit breaker fires, counter resets — next block starts fresh."""
        _create_dirty_entry(self.sid, self.state_dir, self.test_file)

        # Trigger circuit breaker (3 firings)
        _run_gate(self.sid, self.state_dir)
        _run_gate(self.sid, self.state_dir)
        r3 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r3.returncode, 0)  # circuit breaker fired

        # Add a new dirty entry (the old ones were skip-cleared)
        new_file = os.path.join(self.tmpdir, 'post-cb-artifact.md')
        _create_dirty_entry(self.sid, self.state_dir, new_file)

        # Next firing should block normally, not immediately skip
        r4 = _run_gate(self.sid, self.state_dir)
        self.assertEqual(r4.returncode, 2,
                         'After CB reset + new entry, should block normally')


class TestReadOnlyClassification(unittest.TestCase):
    """Test the broadened read-only classification (RGH-CB task 2)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rg-ro-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        os.makedirs(self.state_dir, exist_ok=True)
        self.sid = f'test-ro-{int(time.time() * 1000)}'

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_verify_artifact_is_read_only(self):
        """DoD #6: verify-artifact.py does NOT create a dirty entry."""
        cmd = 'python3 /some/path/verify-artifact.py --profile test --skip-http'
        r = _run_track(self.sid, 'Bash', {'command': cmd}, self.state_dir)

        ledger_path = os.path.join(
            self.state_dir, f'{self.sid}-dirty.jsonl')
        if os.path.exists(ledger_path):
            with open(ledger_path, 'r') as f:
                entries = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(entries), 0,
                             f'verify-artifact.py should be read-only, '
                             f'but created entries: {entries}')

    def test_flags_do_not_bypass_gate_cr012(self):
        """CR-012: --skip-http/--dry-run/--json must NOT exempt arbitrary scripts."""
        for flag in ('--skip-http', '--dry-run', '--json'):
            sid = f'{self.sid}-{flag}'
            cmd = f'python3 /some/script.py {flag}'
            _run_track(sid, 'Bash', {'command': cmd}, self.state_dir)

            ledger_path = os.path.join(
                self.state_dir, f'{sid}-dirty.jsonl')
            self.assertTrue(os.path.exists(ledger_path),
                            f'python3 with {flag} should be tracked (CR-012)')
            with open(ledger_path, 'r') as f:
                entries = [json.loads(l) for l in f if l.strip()]
            self.assertGreater(len(entries), 0,
                               f'python3 with {flag} must create a dirty entry')

    def test_python_without_read_only_flag_still_tracked(self):
        """python3 without read-only flags should still create a dirty entry."""
        cmd = 'python3 /some/script.py --output /tmp/result.json'
        _run_track(self.sid, 'Bash', {'command': cmd}, self.state_dir)

        ledger_path = os.path.join(
            self.state_dir, f'{self.sid}-dirty.jsonl')
        self.assertTrue(os.path.exists(ledger_path),
                        'python3 without read-only flags should be tracked')
        with open(ledger_path, 'r') as f:
            entries = [json.loads(l) for l in f if l.strip()]
        self.assertGreater(len(entries), 0,
                           'python3 without read-only flags should create entry')


if __name__ == '__main__':
    unittest.main()
