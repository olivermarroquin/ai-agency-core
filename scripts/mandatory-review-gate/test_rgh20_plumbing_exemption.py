#!/usr/bin/env python3
"""Test suite for RGH-20 / CR-219: Reviewer-plumbing gate exemption.

Covers acceptance criteria AC-1 through AC-7:
  AC-1: Replay of 5 real blocks from 2026-07-05/06 → zero false blocks
  AC-2: Adversarial suite — deliverable writes disguised via each whitelist
        pattern → all still block
  AC-3: Unregistered (non-reviewer) sessions behave exactly as today
  AC-4: Productize DoD B1-B6 (engine/config split verified structurally)
  AC-5: Block message ≤8 lines, correct decision line for both cases
  AC-6: Reviewer can execute log-review-pass purely from block file
  AC-7: RVQC sequence replay — skip-script write creates no dirty entry,
        second/third turn-ends print one-line reminder

Test isolation: every test uses a fresh temp dir via REVIEW_GATE_STATE_DIR.
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
LOG = os.path.join(SCRIPTS, 'log-review-pass.py')
WORKSPACE_ROOT = os.path.realpath(os.path.join(SCRIPTS, '..', '..', '..', '..'))
SUBDIR_CWD = os.path.join(WORKSPACE_ROOT, 'repos', 'ai-agency-core', 'scripts')

# Import engine directly for unit-level tests
sys.path.insert(0, SCRIPTS)
import engine


def _make_env(state_dir, source=None):
    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = state_dir
    env['REVIEW_GATE_EVENT_LOG'] = os.path.join(state_dir, '_event-log-test.md')
    if source is not None:
        env['REVIEW_GATE_SOURCE'] = source
    elif 'REVIEW_GATE_SOURCE' in env:
        del env['REVIEW_GATE_SOURCE']
    return env


def _run_track(sid, tool_name, tool_input, state_dir, source=None):
    return subprocess.run(
        [sys.executable, TRACK],
        input=json.dumps({
            'session_id': sid, 'tool_name': tool_name,
            'tool_input': tool_input, 'tool_result': {'text': 'ok'},
        }),
        capture_output=True, text=True,
        cwd=SUBDIR_CWD,
        env=_make_env(state_dir, source=source),
    )


def _run_gate(sid, state_dir):
    return subprocess.run(
        [sys.executable, GATE],
        input=json.dumps({'session_id': sid, 'stop_reason': 'end_turn'}),
        capture_output=True, text=True,
        cwd=SUBDIR_CWD,
        env=_make_env(state_dir),
    )


def _register_reviewer(state_dir, reviewer_sid, producer_sid):
    """Create a reviewer marker file."""
    os.makedirs(state_dir, exist_ok=True)
    marker = {
        'role': 'reviewer',
        'reviewing_session': producer_sid,
        'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'registered_by': 'test',
    }
    with open(os.path.join(state_dir, f'{reviewer_sid}-role.json'), 'w') as f:
        json.dump(marker, f)


def _read_dirty_ledger(state_dir, sid):
    path = os.path.join(state_dir, f'{sid}-dirty.jsonl')
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, 'r') as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def _write_plumbing_config(state_dir):
    """Write the plumbing whitelist config for testing."""
    config_dir = os.path.join(state_dir, '..', '.review-gate')
    os.makedirs(config_dir, exist_ok=True)
    # We use the live config from workspace root — engine loads from WORKSPACE_ROOT
    # Tests that need isolated config should mock load_plumbing_whitelist.


class TestPlumbingExemptionUnit(unittest.TestCase):
    """Unit tests for engine.is_plumbing_exempt()."""

    def setUp(self):
        engine._reset_plumbing_cache()

    def tearDown(self):
        engine._reset_plumbing_cache()

    def test_relay_file_exempt(self):
        """Relay files at workspace root match the plumbing pattern."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_relay-producer-rgh-cr219.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('CR-219', result)
        self.assertIn('relay-file', result)

    def test_gate_skip_script_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/.gate-skip-abc123.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('gate-skip-script', result)

    def test_spawn_file_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_spawn-producer-rgh-cr219.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('spawn-file', result)

    def test_pair_launch_script_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/.pair-launch-rgh-cr219.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('pair-launch-script', result)

    def test_execution_log_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/some-repo/.kos/execution-logs/'
            f'execution-log-2026-07-06-review.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('execution-log', result)

    def test_commit_script_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/.commit.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('commit-script', result)

    def test_firing_xlsx_bash_exempt(self):
        result = engine.is_plumbing_exempt(
            'BASH:abc123', 'Bash',
            'python3 second-brain/_meta/scripts/build-review-firing-xlsx.py',
            WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('firing-xlsx-regen', result)

    def test_normal_deliverable_not_exempt(self):
        """A normal deliverable file is NOT plumbing-exempt."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/scripts/engine.py',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result)

    def test_no_config_returns_none(self):
        """With no config file, nothing is exempt."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_relay-test.md',
            'Write', '', '/nonexistent/workspace')
        self.assertIsNone(result)


class TestScratchDirExemptions(unittest.TestCase):
    """New _scratch/ convention paths are plumbing-exempt."""

    def setUp(self):
        engine._reset_plumbing_cache()

    def tearDown(self):
        engine._reset_plumbing_cache()

    def test_scratch_relay_exempt(self):
        """Relay files in _scratch/relay/ match the plumbing pattern."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/relay/_relay-producer-task.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('relay-file', result)

    def test_scratch_relay_reviewer_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/relay/_relay-reviewer-task.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('relay-file', result)

    def test_scratch_relay_default_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/relay/_relay-producer.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('relay-file', result)

    def test_scratch_gate_skip_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/skip/.gate-skip-abc123.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('gate-skip-script', result)

    def test_scratch_gate_skip_default_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/skip/.gate-skip.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('gate-skip-script', result)

    def test_scratch_pair_launch_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/launch/.pair-launch-producer-task.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('pair-launch-script', result)

    def test_scratch_spawn_producer_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/spawn/_spawn-producer-task.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('spawn-file', result)

    def test_scratch_spawn_reviewer_assembled_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/spawn/_spawn-reviewer-assembled-task.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result)
        self.assertIn('spawn-file', result)

    def test_scratch_readme_not_exempt(self):
        """The _scratch/_README.md is NOT a coordination file — not exempt."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/_scratch/_README.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result)


class TestHardNonExemptions(unittest.TestCase):
    """AC-2: Adversarial — deliverable writes disguised via plumbing patterns."""

    def setUp(self):
        engine._reset_plumbing_cache()

    def tearDown(self):
        engine._reset_plumbing_cache()

    def test_projects_dir_never_exempt(self):
        """A file in 04_projects/ is NEVER exempt even if basename matches."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/second-brain/04_projects/clients/_active/'
            f'ev-electric/_relay-smuggle.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result, '04_projects/ must be hard non-exempt')

    def test_shared_intelligence_never_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/second-brain/05_shared-intelligence/patterns/'
            f'execution-log-2026-07-06-smuggle.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result, '05_shared-intelligence/ must be hard non-exempt')

    def test_skills_never_exempt(self):
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/skills/gate-peer-reviewer/'
            f'execution-log-2026-07-06-smuggle.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result, 'skills/ must be hard non-exempt')

    def test_repos_deliverable_never_exempt(self):
        """A script in repos/ (not .kos/execution-logs/) is NOT exempt."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/scripts/smuggle.py',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result, 'repos/ deliverable must not be exempt')

    def test_repos_execution_log_outside_kos_NOT_exempt(self):
        """An execution-log-named file in repos/ but NOT under .kos/execution-logs/
        must NOT be exempt. This is the adversarial vector: a producer names
        a deliverable execution-log-YYYY-MM-DD-smuggle.md and places it in
        repos/<name>/scripts/ to bypass the gate."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/scripts/'
            f'execution-log-2026-07-06-smuggle.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result,
                          'execution-log in repos/ outside .kos/ must NOT be exempt')

    def test_repos_kos_execution_logs_IS_exempt(self):
        """The .kos/execution-logs/ carve-out works inside repos/."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/.kos/execution-logs/'
            f'execution-log-2026-07-06-review.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result, '.kos/execution-logs/ should be exempt')

    def test_repos_commit_sh_IS_exempt(self):
        """The .commit.sh carve-out works inside repos/."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/resume-saas/.commit.sh',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNotNone(result, '.commit.sh should be exempt')

    def test_deliverable_with_relay_in_name_not_exempt(self):
        """A file in repos/ named like a relay is NOT exempt."""
        result = engine.is_plumbing_exempt(
            f'{WORKSPACE_ROOT}/repos/ai-agency-core/_relay-attack.md',
            'Write', '', WORKSPACE_ROOT)
        self.assertIsNone(result, 'repos/ relay-named file must not be exempt')


class TestUnregisteredSessionsUnchanged(unittest.TestCase):
    """AC-3: Unregistered (non-reviewer) sessions behave exactly as today."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rgh20-ac3-')
        self.SID = 'producer-session-ac3'
        engine._reset_plumbing_cache()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        engine._reset_plumbing_cache()

    def test_producer_relay_write_creates_dirty_entry(self):
        """A producer session writing a relay file still creates a dirty entry
        (plumbing suppression in dirty-ledger-track applies to ALL sessions,
        but check_gate only applies plumbing exemption for reviewer sessions).

        UPDATED for RGH-20 Part C: plumbing-pattern writes are suppressed at
        the dirty-ledger-track level for ALL sessions. This is safe because
        relay/skip/spawn files are never deliverables regardless of session role.
        """
        r = _run_track(self.SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/_relay-producer.md',
            'content': 'test message',
        }, self.state_dir)
        # Part C: plumbing writes are suppressed at track level
        entries = _read_dirty_ledger(self.state_dir, self.SID)
        self.assertEqual(len(entries), 0,
                         'Plumbing writes should be suppressed for all sessions')
        self.assertIn('Plumbing-exempt', r.stdout)

    def test_producer_deliverable_still_blocks(self):
        """A producer session writing a real deliverable is still blocked."""
        test_file = os.path.join(self.state_dir, 'deliverable.py')
        with open(test_file, 'w') as f:
            f.write('# real code')
        _run_track(self.SID, 'Write', {
            'file_path': test_file,
            'content': '# real code\n# multi-line\n# content\n# here\n# six\n# lines',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2, 'Producer deliverable must still block')


class TestReplayRealBlocks(unittest.TestCase):
    """AC-1: Replay of the 5 real CR-219 blocks → zero false blocks.

    The 5 blocks from 2026-07-05/06:
    1. SEP-FU3 producer: _relay-producer.md write
    2. SEP-FU3 producer: .gate-skip-sep-fu3.sh write
    3. QC reviewer: execution-log write + read-only Bash (git log, find, awk)
    4. QC reviewer: _relay-reviewer.md write
    5. SEP-FU2 reviewer: build-review-firing-xlsx.py invocation
    """

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rgh20-ac1-')
        self.REVIEWER_SID = 'reviewer-ac1'
        self.PRODUCER_SID = 'producer-ac1'
        _register_reviewer(self.state_dir, self.REVIEWER_SID, self.PRODUCER_SID)
        engine._reset_plumbing_cache()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        engine._reset_plumbing_cache()

    def test_block1_relay_write_suppressed(self):
        """SEP-FU3 producer: _relay-producer.md — suppressed at track level."""
        r = _run_track(self.REVIEWER_SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/_relay-producer-sep-fu3.md',
            'content': 'relay message',
        }, self.state_dir)
        entries = _read_dirty_ledger(self.state_dir, self.REVIEWER_SID)
        self.assertEqual(len(entries), 0, 'Relay write should be suppressed')
        r = _run_gate(self.REVIEWER_SID, self.state_dir)
        self.assertEqual(r.returncode, 0, 'Should not block on suppressed entry')

    def test_block2_gate_skip_write_suppressed(self):
        """SEP-FU3 producer: .gate-skip.sh — suppressed at track level."""
        _run_track(self.REVIEWER_SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/.gate-skip-sep-fu3.sh',
            'content': '#!/bin/bash\npython3 gate-skip.py ...',
        }, self.state_dir)
        entries = _read_dirty_ledger(self.state_dir, self.REVIEWER_SID)
        self.assertEqual(len(entries), 0)
        r = _run_gate(self.REVIEWER_SID, self.state_dir)
        self.assertEqual(r.returncode, 0)

    def test_block3_reviewer_exec_log_exempt(self):
        """QC reviewer: execution-log write — exempt via plumbing whitelist."""
        exec_log = os.path.join(
            self.state_dir,
            'execution-log-2026-07-06-qc-reviewer.md')
        with open(exec_log, 'w') as f:
            f.write('# Review log\n')
        _run_track(self.REVIEWER_SID, 'Write', {
            'file_path': exec_log,
            'content': '# Review log\n## Findings\n- None',
        }, self.state_dir)
        # The exec log IS tracked (not at workspace root), but exempt
        # in check_gate for reviewer sessions
        r = _run_gate(self.REVIEWER_SID, self.state_dir)
        # Should not block — entry_source is 'reviewer' for reviewer exec logs
        # OR plumbing-exempt
        self.assertEqual(r.returncode, 0,
                         'Reviewer exec log should not block')

    def test_block4_reviewer_relay_suppressed(self):
        """QC reviewer: _relay-reviewer.md — suppressed at track level."""
        _run_track(self.REVIEWER_SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/_relay-reviewer-qc.md',
            'content': 'reviewer findings',
        }, self.state_dir)
        entries = _read_dirty_ledger(self.state_dir, self.REVIEWER_SID)
        self.assertEqual(len(entries), 0)
        r = _run_gate(self.REVIEWER_SID, self.state_dir)
        self.assertEqual(r.returncode, 0)

    def test_block5_firing_xlsx_bash_exempt(self):
        """SEP-FU2 reviewer: build-review-firing-xlsx.py — suppressed."""
        _run_track(self.REVIEWER_SID, 'Bash', {
            'command': 'python3 second-brain/_meta/scripts/build-review-firing-xlsx.py',
        }, self.state_dir)
        entries = _read_dirty_ledger(self.state_dir, self.REVIEWER_SID)
        self.assertEqual(len(entries), 0,
                         'Firing xlsx Bash should be suppressed')
        r = _run_gate(self.REVIEWER_SID, self.state_dir)
        self.assertEqual(r.returncode, 0)


class TestBlockMessageFormat(unittest.TestCase):
    """AC-5: Block message ≤8 lines, correct decision line for both cases."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rgh20-ac5-')
        self.SID = 'producer-ac5'
        engine._reset_plumbing_cache()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        engine._reset_plumbing_cache()

    def test_deliverable_block_message_compact(self):
        """Block message for deliverables is ≤8 lines with correct decision."""
        test_file = os.path.join(self.state_dir, 'deliverable.py')
        with open(test_file, 'w') as f:
            f.write('# code\n' * 10)
        _run_track(self.SID, 'Write', {
            'file_path': test_file,
            'content': '# code\n' * 10,
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        msg = r.stderr.strip()
        lines = [l for l in msg.splitlines() if l.strip()]
        self.assertLessEqual(len(lines), 8,
                             f'Block message must be ≤8 lines, got {len(lines)}:\n{msg}')
        self.assertIn('REVIEW GATE BLOCKED', msg)
        self.assertIn('deliverables', msg.lower())
        self.assertIn('Relay to your reviewer', msg)
        self.assertIn('Details + ready-made commands:', msg)

    def test_block_file_created_with_commands(self):
        """Block file contains the full detail + ready-made commands."""
        test_file = os.path.join(self.state_dir, 'deliverable.py')
        with open(test_file, 'w') as f:
            f.write('# code\n' * 10)
        _run_track(self.SID, 'Write', {
            'file_path': test_file,
            'content': '# code\n' * 10,
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        # Extract block file path from message
        for line in r.stderr.splitlines():
            if 'Details + ready-made commands:' in line:
                block_path = line.split(':', 1)[1].strip()
                # Handle "Details + ready-made commands: /path/to/file"
                if 'Details + ready-made commands:' in line:
                    block_path = line.split('Details + ready-made commands:')[1].strip()
                self.assertTrue(os.path.isfile(block_path),
                                f'Block file should exist at {block_path}')
                with open(block_path, 'r') as f:
                    content = f.read()
                self.assertIn('log-review-pass.py', content)
                self.assertIn(self.SID, content)
                break
        else:
            self.fail('Block file path not found in message')


class TestBlockFileSufficient(unittest.TestCase):
    """AC-6: Reviewer can execute log-review-pass purely from block file."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rgh20-ac6-')
        self.SID = 'producer-ac6'
        engine._reset_plumbing_cache()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        engine._reset_plumbing_cache()

    def test_block_file_contains_session_and_files(self):
        """Block file has session ID and file paths for log-review-pass."""
        test_file = os.path.join(self.state_dir, 'deliverable.md')
        with open(test_file, 'w') as f:
            f.write('# test\n' * 10)
        _run_track(self.SID, 'Write', {
            'file_path': test_file,
            'content': '# test\n' * 10,
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        # Find and read block file
        for line in r.stderr.splitlines():
            if 'Details + ready-made commands:' in line:
                block_path = line.split('Details + ready-made commands:')[1].strip()
                with open(block_path, 'r') as f:
                    content = f.read()
                # Must contain the session ID
                self.assertIn(self.SID, content)
                # Must contain log-review-pass.py command
                self.assertIn('log-review-pass.py', content)
                # Must contain the file path
                self.assertIn(os.path.realpath(test_file), content)
                break


class TestRepeatBlockDedupe(unittest.TestCase):
    """AC-7: Repeat-block dedupe — same unreviewed set → one-line reminder."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rgh20-ac7-')
        self.SID = 'producer-ac7'
        engine._reset_plumbing_cache()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        engine._reset_plumbing_cache()

    def test_first_block_is_full_message(self):
        """First block prints the full compact message."""
        import engine
        test_file = os.path.join(self.state_dir, 'deliverable.py')
        with open(test_file, 'w') as f:
            f.write('# Real code\n' * 10)
        engine.append_dirty_entry(self.state_dir, self.SID, {
            'timestamp': time.time(), 'tool': 'Write',
            'file_path': os.path.realpath(test_file), 'tier': 'full',
            'source': 'claude-code', 'entry_source': 'producer',
        })
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        self.assertIn('REVIEW GATE BLOCKED', r.stderr)

    def test_dedupe_mechanism_directly(self):
        """The dedupe mechanism prints a one-line reminder when the same
        unreviewed set blocks consecutively.

        Tested at the Python level: import the adapter's internal functions
        and verify the history-based dedupe fires correctly. End-to-end
        subprocess testing is impractical because the independent dispatch
        writes BLOCKING verdicts that clear the gate on subsequent calls
        (by design — BLOCKING = findings surfaced). The dedupe fires in
        production when the dispatch fails or when turns end rapidly.
        """
        # Import the adapter's internal functions
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'mrg', os.path.join(SCRIPTS, 'mandatory-review-gate.py'))
        mrg = importlib.util.module_from_spec(spec)
        # Redirect STATE_DIR to our temp dir
        import _paths
        orig_state_dir = _paths.STATE_DIR
        _paths.STATE_DIR = self.state_dir
        try:
            spec.loader.exec_module(mrg)
        finally:
            _paths.STATE_DIR = orig_state_dir

        # Simulate: compute fingerprint for a set of entry keys
        entry_keys = ['BASH:abc123', '/tmp/deliverable.py']

        # First block: append to history, should NOT dedupe
        fp = mrg._entry_fingerprint(entry_keys)
        mrg._append_history(self.SID, entry_keys)
        count, last_fp = mrg._read_history_tail(self.SID)
        self.assertEqual(count, 1)
        self.assertEqual(last_fp, fp)
        # last_count=1 < 2 → no dedupe (first block shows full message)

        # Second block: append again, same fingerprint → SHOULD dedupe
        mrg._append_history(self.SID, entry_keys)
        count2, last_fp2 = mrg._read_history_tail(self.SID)
        self.assertEqual(count2, 2)
        self.assertEqual(last_fp2, fp)
        # last_count=2 >= 2 → dedupe fires (one-line reminder)

        # Different set resets: different keys → count back to 1
        different_keys = ['BASH:xyz789']
        mrg._append_history(self.SID, different_keys)
        count3, last_fp3 = mrg._read_history_tail(self.SID)
        self.assertEqual(count3, 1)
        self.assertNotEqual(last_fp3, fp)

    def test_skip_script_write_no_dirty_entry(self):
        """Part C: writing .gate-skip*.sh creates no dirty entry."""
        _run_track(self.SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/.gate-skip-rvqc.sh',
            'content': '#!/bin/bash\npython3 gate-skip.py --session abc',
        }, self.state_dir)
        entries = _read_dirty_ledger(self.state_dir, self.SID)
        self.assertEqual(len(entries), 0,
                         'Skip-script write must not create dirty entry')

    def test_rvqc_sequence_no_growth(self):
        """Full RVQC sequence: write deliverable → block → write skip → check.
        Skip-script write must not grow the unreviewed set."""
        import engine
        test_file = os.path.join(self.state_dir, 'deliverable.md')
        content = '# Real code with no placeholders\n' * 10
        with open(test_file, 'w') as f:
            f.write(content)
        # Step 1: track a real deliverable directly
        engine.append_dirty_entry(self.state_dir, self.SID, {
            'timestamp': time.time(),
            'tool': 'Write',
            'file_path': os.path.realpath(test_file),
            'tier': 'full',
            'source': 'claude-code',
            'entry_source': 'producer',
        })
        entries_before = _read_dirty_ledger(self.state_dir, self.SID)
        self.assertEqual(len(entries_before), 1)
        # Step 2: first block
        r1 = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r1.returncode, 2)
        self.assertIn('REVIEW GATE BLOCKED', r1.stderr)
        # Step 3: write skip script (should NOT create dirty entry)
        _run_track(self.SID, 'Write', {
            'file_path': f'{WORKSPACE_ROOT}/.gate-skip-rvqc.sh',
            'content': '#!/bin/bash\npython3 gate-skip.py',
        }, self.state_dir)
        entries_after = _read_dirty_ledger(self.state_dir, self.SID)
        self.assertEqual(len(entries_before), len(entries_after),
                         'Skip-script write must not grow dirty ledger')


class TestConfigDriven(unittest.TestCase):
    """AC-4 structural: engine/config split — patterns live in config file."""

    def test_config_file_exists(self):
        """The plumbing whitelist config file exists at the expected path."""
        config_path = os.path.join(WORKSPACE_ROOT, '.review-gate',
                                   'plumbing-whitelist.json')
        self.assertTrue(os.path.isfile(config_path),
                        f'Config file must exist at {config_path}')

    def test_config_has_required_sections(self):
        """Config has plumbing_patterns and hard_non_exempt sections."""
        config = engine.load_plumbing_whitelist(WORKSPACE_ROOT)
        engine._reset_plumbing_cache()
        self.assertIn('plumbing_patterns', config)
        self.assertIn('hard_non_exempt', config)
        self.assertGreater(len(config['plumbing_patterns']), 0)
        self.assertGreater(len(config['hard_non_exempt']), 0)

    def test_patterns_not_hardcoded_in_engine(self):
        """Engine does NOT hardcode plumbing patterns — reads from config."""
        with open(os.path.join(SCRIPTS, 'engine.py'), 'r') as f:
            source = f.read()
        # The engine should NOT contain the literal plumbing path patterns
        self.assertNotIn("'_relay-'", source,
                         'Plumbing patterns must not be hardcoded in engine.py')
        self.assertNotIn("'.gate-skip'", source,
                         'Plumbing patterns must not be hardcoded in engine.py')


if __name__ == '__main__':
    unittest.main()
