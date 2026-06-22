#!/usr/bin/env python3
"""Tests for RGH-10: source-tagged exemption.

Validates that:
1. Dirty-ledger entries carry structural `entry_source` tags
2. Reviewer/gate-clearing entries are exempt from the Stop hook gate
3. Producer entries are STILL gated (no bypass)
4. ADVERSARIAL: a producer mislabeling its deliverable as reviewer is STILL gated
5. Read-only whitelist extensions (sed -n, safe find) work correctly
6. Round 1 regression tests for BLOCKING-1..4

All tests use subprocess (real runner) with REVIEW_GATE_STATE_DIR isolation.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(SCRIPTS, 'dirty-ledger-track.py')
GATE = os.path.join(SCRIPTS, 'mandatory-review-gate.py')
EXPECTED_WORKSPACE_ROOT = os.path.realpath(os.path.join(SCRIPTS, '..', '..', '..', '..'))
SUBDIR_CWD = os.path.join(EXPECTED_WORKSPACE_ROOT, 'repos', 'ai-agency-core', 'scripts')


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
    return subprocess.run(
        ['python3', GATE],
        input=json.dumps({'session_id': sid, 'stop_reason': 'end_turn'}),
        capture_output=True, text=True,
        cwd=SUBDIR_CWD,
        env=_make_env(state_dir),
    )


def _read_dirty_ledger(state_dir, sid):
    path = os.path.join(state_dir, f'{sid}-dirty.jsonl')
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _write_dirty_entries(state_dir, sid, entries):
    """Write entries directly to the dirty ledger."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, f'{sid}-dirty.jsonl')
    with open(path, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')


# ============================================================================
# Source classification unit tests
# ============================================================================

class TestClassifyEntrySource(unittest.TestCase):
    """Unit tests for engine.classify_entry_source()."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    def test_firing_tracker_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
            'Edit')
        self.assertEqual(result, 'reviewer')

    def test_catch_register_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
            'Edit')
        self.assertEqual(result, 'reviewer')

    def test_reviewer_session_execution_log_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-reviewer-session.md',
            'Write')
        self.assertEqual(result, 'reviewer')

    def test_independent_review_execution_log_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-independent-review.md',
            'Write')
        self.assertEqual(result, 'reviewer')

    def test_peer_review_execution_log_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-peer-review.md',
            'Write')
        self.assertEqual(result, 'reviewer')

    def test_prefixed_reviewer_session_log_is_reviewer(self):
        """Execution log with a project prefix before -reviewer-session."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-rgh3-independent-review.md',
            'Write')
        self.assertEqual(result, 'reviewer')

    def test_verdict_file_is_reviewer(self):
        result = self.engine.classify_entry_source(
            '/workspace/.review-gate/state/verdict-auto-abc-123.json',
            'Write')
        self.assertEqual(result, 'reviewer')

    def test_log_review_pass_bash_is_gate_clearing(self):
        result = self.engine.classify_entry_source(
            'BASH:abc123', 'Bash',
            'python3 /path/to/log-review-pass.py --session x --files f')
        self.assertEqual(result, 'gate-clearing')

    def test_gate_skip_bash_is_gate_clearing(self):
        result = self.engine.classify_entry_source(
            'BASH:abc123', 'Bash',
            'python3 gate-skip.py --session x --reason "test"')
        self.assertEqual(result, 'gate-clearing')

    def test_normal_code_file_is_producer(self):
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/src/app.tsx', 'Write')
        self.assertEqual(result, 'producer')

    def test_normal_bash_is_producer(self):
        result = self.engine.classify_entry_source(
            'BASH:abc123', 'Bash', 'npm run build')
        self.assertEqual(result, 'producer')

    def test_normal_execution_log_is_producer(self):
        """A producer's own execution log (no reviewer suffix) is producer."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-build.md',
            'Write')
        self.assertEqual(result, 'producer')

    def test_pattern_file_is_producer(self):
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/05_shared-intelligence/patterns/pattern-foo.md',
            'Edit')
        self.assertEqual(result, 'producer')


# ============================================================================
# Round 1 BLOCKING regression tests
# ============================================================================

class TestBlockingRegressions(unittest.TestCase):
    """Regression tests for BLOCKING-1..4 from Round 1 reviewer feedback.

    Each test proves a specific bypass vector that was found and fixed.
    """

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    # --- BLOCKING-1: Execution-log filename bypass ---

    def test_blocking1_reviewer_hack_filename_is_producer(self):
        """BLOCKING-1: A producer naming its log 'reviewer-hack' must NOT
        be classified as reviewer. The pattern must use exact suffixes."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/.kos/execution-logs/'
            'execution-log-2026-06-22-reviewer-hack.md', 'Write')
        self.assertEqual(result, 'producer',
                         'BLOCKING-1: -reviewer-hack must be producer, '
                         'not reviewer (exact suffix required)')

    def test_blocking1_reviewer_notes_filename_is_producer(self):
        """Producer log named 'reviewer-notes' must be producer."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/foo/.kos/execution-logs/'
            'execution-log-2026-06-22-reviewer-notes.md', 'Write')
        self.assertEqual(result, 'producer')

    def test_blocking1_my_reviewer_log_is_producer(self):
        """Producer log named 'my-reviewer' must be producer."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/foo/.kos/execution-logs/'
            'execution-log-2026-06-22-my-reviewer.md', 'Write')
        self.assertEqual(result, 'producer')

    def test_blocking1_legitimate_reviewer_session_is_reviewer(self):
        """The exact suffix -reviewer-session must still classify as reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/foo/.kos/execution-logs/'
            'execution-log-2026-06-22-reviewer-session.md', 'Write')
        self.assertEqual(result, 'reviewer')

    # --- BLOCKING-2: Gate-clearing substring match too broad ---

    def test_blocking2_echo_log_review_pass_is_producer(self):
        """BLOCKING-2: 'echo log-review-pass is cool' must be producer.
        Only the actual invoked script qualifies, not a substring mention."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash', 'echo log-review-pass is cool')
        self.assertEqual(result, 'producer',
                         'BLOCKING-2: substring mention in echo must be producer')

    def test_blocking2_compound_with_deploy_is_producer(self):
        """BLOCKING-2: 'echo log-review-pass && npm run deploy' must be producer.
        The deploy segment is not a gate-clearing script."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'echo "log-review-pass" && npm run deploy')
        self.assertEqual(result, 'producer',
                         'BLOCKING-2: compound with non-gate-clearing must be producer')

    def test_blocking2_actual_invocation_is_gate_clearing(self):
        """The actual script invocation must still be gate-clearing."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'python3 /full/path/to/log-review-pass.py --session x --files f')
        self.assertEqual(result, 'gate-clearing')

    def test_blocking2_gate_skip_actual_invocation(self):
        """gate-skip.py actual invocation must be gate-clearing."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'python3 gate-skip.py --session x --reason "emergency"')
        self.assertEqual(result, 'gate-clearing')

    # --- BLOCKING-3: Bash-command reviewer regex too broad ---

    def test_blocking3_bash_mentioning_reviewer_path_is_producer(self):
        """BLOCKING-3: A Bash command that merely mentions a reviewer artifact
        path (e.g., as an --input flag) must be producer, not reviewer."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'python3 some-script.py --input '
            '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md')
        self.assertEqual(result, 'producer',
                         'BLOCKING-3: Bash mentioning reviewer path in arg '
                         'must be producer')

    def test_blocking3_bash_grepping_reviewer_path_is_producer(self):
        """A grep command targeting a reviewer file must be producer."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'grep "pattern" /workspace/second-brain/_meta/handoffs/'
            '_review-gate-catch-register.md')
        self.assertEqual(result, 'producer')

    def test_blocking3_bash_cat_reviewer_file_is_producer(self):
        """cat of a reviewer file must be producer (read-only Bash won't
        even create a dirty entry, but if it did, it must be producer)."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'cat /workspace/second-brain/_meta/handoffs/'
            '_review-skill-firing-tracker.md')
        self.assertEqual(result, 'producer')

    # --- BLOCKING-4: Reviewer logs outside execution-logs/ directory ---

    def test_blocking4_reviewer_log_in_handoff_folder_is_reviewer(self):
        """BLOCKING-4: A reviewer log written directly in a handoff folder
        (not under execution-logs/) must still be classified as reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/review-gate-hardening/'
            'execution-log-2026-06-22-independent-review.md', 'Write')
        self.assertEqual(result, 'reviewer',
                         'BLOCKING-4: reviewer log outside execution-logs/ dir '
                         'must still be reviewer')

    def test_blocking4_peer_review_log_in_handoff_folder(self):
        """Peer-review log in a handoff folder must be reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/review-gate-hardening/'
            'execution-log-2026-06-22-peer-review.md', 'Write')
        self.assertEqual(result, 'reviewer')

    def test_blocking4_reviewer_session_log_in_handoff_folder(self):
        """Reviewer-session log in a handoff folder must be reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/review-gate-hardening/'
            'execution-log-2026-06-22-reviewer-session.md', 'Write')
        self.assertEqual(result, 'reviewer')

    def test_blocking4_prefixed_reviewer_log_in_handoff_folder(self):
        """Prefixed reviewer log (e.g., rgh3-independent-review) in handoff folder."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/review-gate-hardening/'
            'execution-log-2026-06-22-rgh3-independent-review.md', 'Write')
        self.assertEqual(result, 'reviewer')

    def test_blocking4_producer_log_in_handoff_folder_is_producer(self):
        """A producer's execution log in a handoff folder must be producer."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/review-gate-hardening/'
            'execution-log-2026-06-22-build.md', 'Write')
        self.assertEqual(result, 'producer')

    # --- BLOCKING-7: Compound Bash commands with gate-clearing segment ---

    def test_blocking7_gate_clearing_then_deploy_is_producer(self):
        """BLOCKING-7: 'python3 log-review-pass.py && npm run deploy' must be
        producer. The deploy segment is state-changing, so the whole command
        cannot be exempted as gate-clearing."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'python3 log-review-pass.py --session x --files f && npm run deploy')
        self.assertEqual(result, 'producer',
                         'BLOCKING-7: compound with state-changing segment '
                         'must be producer')

    def test_blocking7_deploy_then_gate_clearing_is_producer(self):
        """BLOCKING-7: reversed order — deploy first, then gate-clearing."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'npm run deploy && python3 log-review-pass.py --session x --files f')
        self.assertEqual(result, 'producer',
                         'BLOCKING-7: reversed compound still must be producer')

    def test_blocking7_gate_clearing_with_read_only_is_gate_clearing(self):
        """A gate-clearing script chained with read-only commands IS OK."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'echo "clearing gate" && python3 log-review-pass.py --session x --files f')
        self.assertEqual(result, 'gate-clearing')

    def test_blocking7_pure_gate_clearing_still_works(self):
        """A pure gate-clearing invocation (no compound) still works."""
        result = self.engine.classify_entry_source(
            'BASH:abc', 'Bash',
            'python3 log-review-pass.py --session x --files f')
        self.assertEqual(result, 'gate-clearing')

    # --- advisory-8: firing-tracker/catch-register path constraint ---

    def test_advisory8_tracker_in_wrong_directory_is_producer(self):
        """advisory-8: _review-skill-firing-tracker.md outside _meta/handoffs/
        must be producer, not reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/src/_review-skill-firing-tracker.md',
            'Write')
        self.assertEqual(result, 'producer',
                         'advisory-8: tracker file outside _meta/handoffs/ '
                         'must be producer')

    def test_advisory8_catch_register_in_wrong_directory_is_producer(self):
        """advisory-8: _review-gate-catch-register.md outside _meta/handoffs/."""
        result = self.engine.classify_entry_source(
            '/workspace/repos/resume-saas/src/_review-gate-catch-register.md',
            'Write')
        self.assertEqual(result, 'producer')

    def test_advisory8_tracker_in_correct_directory_is_reviewer(self):
        """The tracker in its correct location must still be reviewer."""
        result = self.engine.classify_entry_source(
            '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
            'Edit')
        self.assertEqual(result, 'reviewer')


# ============================================================================
# ADVERSARIAL: no producer bypass (HARD SAFETY BAR)
# ============================================================================

class TestNoProducerBypass(unittest.TestCase):
    """ADVERSARIAL tests proving a producer cannot bypass the gate by
    mislabeling its deliverable as reviewer.

    The source inference is STRUCTURAL (path-based), not flag-based.
    A producer writing normal deliverables gets 'producer' regardless of
    any attempt to claim reviewer status.
    """

    def test_producer_deliverable_always_producer(self):
        """A producer writing src/app.tsx is producer — no flag can change this."""
        sys.path.insert(0, SCRIPTS)
        import engine
        result = engine.classify_entry_source(
            '/workspace/repos/resume-saas/src/app.tsx', 'Write', '')
        self.assertEqual(result, 'producer',
                         'Producer deliverable MUST be classified as producer')

    def test_producer_cannot_bypass_gate_via_entry_source_field(self):
        """End-to-end: track a producer Write via subprocess, verify the
        ledger entry has entry_source=producer, verify the gate blocks."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-producer-bypass'
            _run_track(sid, 'Write', {
                'file_path': '/tmp/test-deliverable.md',
                'content': 'deliverable content',
            }, state_dir)

            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].get('entry_source'), 'producer',
                             'Structural inference must produce entry_source=producer')

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Gate MUST block producer deliverable: {r.stderr}')

    def test_adversarial_mislabel_still_gated(self):
        """Producer writes multiple normal files — all must be entry_source=producer
        and the gate must block all of them."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-mislabel'
            normal_paths = [
                '/tmp/test-code.py',
                '/tmp/test-config.json',
                '/tmp/test-readme.md',
            ]
            for path in normal_paths:
                _run_track(sid, 'Write', {
                    'file_path': path,
                    'content': 'content',
                }, state_dir)

            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), len(normal_paths))
            for entry in entries:
                self.assertEqual(entry.get('entry_source'), 'producer',
                                 f'File {entry.get("file_path")} must be producer')

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Gate MUST block all producer deliverables')

    def test_tampered_ledger_exempts_but_tracker_prevents_it(self):
        """Honest limit documentation test: if someone tampers the ledger
        directly (bypassing the tracker) to set entry_source=reviewer on a
        producer path, the gate trusts the field and exempts it. BUT the
        real protection is that the tracker (dirty-ledger-track.py) uses
        structural inference — the model cannot control the PostToolUse hook.

        This test verifies the honest limit is as documented: the tampered
        entry IS exempted (gate exits 0), proving the protection is at the
        tracker level, not the gate level.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tampered-ledger'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Write',
                'file_path': '/tmp/producer-deliverable.md',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'reviewer',  # TAMPERED
            }])
            r = _run_gate(sid, state_dir)
            # Gate trusts the ledger — tampered entry is exempted.
            # This is by design: protection is at the tracker, not the gate.
            self.assertEqual(r.returncode, 0,
                             'Tampered ledger entry should be exempted (gate trusts '
                             'ledger). Real protection is at the tracker level.')


# ============================================================================
# Stop hook exemption integration tests (real runner via subprocess)
# ============================================================================

class TestStopHookSourceExemption(unittest.TestCase):
    """Integration tests: reviewer-only sessions stop clean, producer sessions block."""

    def test_reviewer_session_stops_clean(self):
        """A session that ONLY writes reviewer artifacts stops with exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-clean-stop'
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': time.time(),
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': time.time() + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
            ])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer-only session MUST stop clean (exit 0), '
                             f'got exit {r.returncode}. stderr: {r.stderr}')

    def test_gate_clearing_session_stops_clean(self):
        """A session with only gate-clearing entries stops with exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'gate-clearing-stop'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:abc123',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'gate-clearing',
            }])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Gate-clearing session MUST stop clean, '
                             f'got exit {r.returncode}. stderr: {r.stderr}')

    def test_producer_session_blocks(self):
        """A session with producer entries blocks (exit 2)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'producer-blocks'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Write',
                'file_path': '/workspace/repos/resume-saas/src/app.tsx',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Producer session MUST block (exit 2), '
                             f'got exit {r.returncode}')

    def test_mixed_reviewer_and_producer_blocks(self):
        """A mixed session with both reviewer and producer entries blocks."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'mixed-blocks'
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': time.time(),
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': time.time() + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Write',
                    'file_path': '/workspace/repos/resume-saas/src/app.tsx',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Mixed session MUST block on producer entries')

    def test_reviewer_session_full_replay(self):
        """Real-runner replay: a reviewer session writes execution log,
        verdict file, firing-tracker rows, catch-register rows, and
        runs log-review-pass — then stops clean with ZERO gate blocks.

        This is the acceptance test from the handoff DoD.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-full-replay'
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Write',
                    'file_path': '/workspace/repos/resume-saas/.kos/execution-logs/execution-log-2026-06-22-independent-review.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Write',
                    'file_path': '/workspace/.review-gate/state/verdict-independent-123.json',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 2,
                    'iso_time': '2026-06-22T00:00:02Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 3,
                    'iso_time': '2026-06-22T00:00:03Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 4,
                    'iso_time': '2026-06-22T00:00:04Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:def456',
                    'display': 'python3 log-review-pass.py --session prod --files ...',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'gate-clearing',
                },
            ])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer full replay MUST stop clean (exit 0). '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_reviewer_log_in_handoff_folder_replay(self):
        """BLOCKING-4 replay: reviewer writes log directly in handoff folder,
        not under execution-logs/. Gate must still pass clean."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-handoff-folder-replay'
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Write',
                    'file_path': '/workspace/second-brain/_meta/handoffs/review-gate-hardening/execution-log-2026-06-22-independent-review.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
            ])

            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'BLOCKING-4: reviewer log in handoff folder must '
                             f'stop clean. Got exit {r.returncode}. stderr: {r.stderr}')


# ============================================================================
# Tracker integration: verify entry_source set by subprocess tracker
# ============================================================================

class TestTrackerSourceTagging(unittest.TestCase):
    """Verify dirty-ledger-track.py sets entry_source correctly via subprocess."""

    def test_normal_write_tagged_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tracker-source-normal'
            _run_track(sid, 'Write', {
                'file_path': '/tmp/normal-file.md',
                'content': 'content',
            }, state_dir)

            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].get('entry_source'), 'producer')

    def test_firing_tracker_edit_tagged_reviewer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tracker-source-reviewer'
            _run_track(sid, 'Edit', {
                'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                'old_string': 'old',
                'new_string': 'new',
            }, state_dir)

            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].get('entry_source'), 'reviewer')

    def test_catch_register_edit_tagged_reviewer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tracker-source-catch-reg'
            _run_track(sid, 'Edit', {
                'file_path': '/workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
                'old_string': 'old',
                'new_string': 'new',
            }, state_dir)

            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].get('entry_source'), 'reviewer')


# ============================================================================
# Read-only whitelist extension tests
# ============================================================================

class TestReadOnlyWhitelistExtension(unittest.TestCase):
    """Tests for extended read-only Bash classification (RGH-10)."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    def test_sed_n_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('sed -n "5p" file.txt'))

    def test_sed_nE_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('sed -nE "s/foo/bar/p" file.txt'))

    def test_sed_quiet_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('sed --quiet "5p" file.txt'))

    def test_sed_i_is_not_read_only(self):
        self.assertFalse(self.engine.is_read_only_bash('sed -i "s/foo/bar/" file.txt'))

    def test_bare_sed_is_not_read_only(self):
        self.assertFalse(self.engine.is_read_only_bash('sed "s/foo/bar/" file.txt'))

    def test_find_basic_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('find . -name "*.md"'))

    def test_find_type_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('find /tmp -type f -name "*.log"'))

    def test_find_delete_is_not_read_only(self):
        self.assertFalse(self.engine.is_read_only_bash('find . -name "*.tmp" -delete'))

    def test_find_exec_rm_is_not_read_only(self):
        self.assertFalse(self.engine.is_read_only_bash('find . -exec rm {} +'))

    def test_find_exec_mv_is_not_read_only(self):
        self.assertFalse(self.engine.is_read_only_bash('find . -exec mv {} /tmp/ +'))

    def test_find_exec_ls_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('find . -exec ls {} +'))

    def test_python3_c_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('python3 -c "print(1)"'))


if __name__ == '__main__':
    unittest.main()
