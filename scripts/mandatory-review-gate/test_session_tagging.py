#!/usr/bin/env python3
"""Tests for RGH-11: session-level reviewer tagging.

Validates that:
1. classify_session_role() detects reviewer sessions structurally (marker file)
2. is_bash_entry_write_safe() correctly distinguishes write-safe from write-unsafe
3. check_gate() exempts reviewer-session Bash WITHOUT write indicators
4. check_gate() STILL gates deliverable Write/Edit in reviewer sessions
5. ADVERSARIAL: a producer cannot bypass the gate by forging a reviewer marker
6. Real-runner replay of the 2026-06-22 session-26201e56 scenario

All integration tests use subprocess (real runner) with REVIEW_GATE_STATE_DIR
isolation — no mocks, per the CR-004 lesson.
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
REGISTER = os.path.join(SCRIPTS, 'register-reviewer-session.py')
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


def _run_register(session, reviewing_session, state_dir):
    return subprocess.run(
        ['python3', REGISTER,
         '--session', session,
         '--reviewing-session', reviewing_session],
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


def _write_session_marker(state_dir, session_id, role='reviewer',
                          reviewing_session='producer-session-abc'):
    """Write a session role marker file directly."""
    os.makedirs(state_dir, exist_ok=True)
    marker_path = os.path.join(state_dir, f'{session_id}-role.json')
    marker = {
        'role': role,
        'reviewing_session': reviewing_session,
        'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'registered_by': 'register-reviewer-session.py',
    }
    with open(marker_path, 'w') as f:
        json.dump(marker, f)
    return marker_path


# ============================================================================
# classify_session_role() unit tests
# ============================================================================

class TestClassifySessionRole(unittest.TestCase):
    """Unit tests for engine.classify_session_role()."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    def test_no_marker_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.engine.classify_session_role(state_dir, 'no-marker-session')
            self.assertEqual(result, 'producer')

    def test_valid_marker_returns_reviewer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            _write_session_marker(state_dir, 'reviewer-sid',
                                  reviewing_session='producer-sid')
            result = self.engine.classify_session_role(state_dir, 'reviewer-sid')
            self.assertEqual(result, 'reviewer')

    def test_self_review_returns_producer(self):
        """Session marker with reviewing_session == session_id → producer."""
        with tempfile.TemporaryDirectory() as state_dir:
            _write_session_marker(state_dir, 'self-review-sid',
                                  reviewing_session='self-review-sid')
            result = self.engine.classify_session_role(state_dir, 'self-review-sid')
            self.assertEqual(result, 'producer',
                             'Self-review marker must be rejected (fail-closed)')

    def test_missing_reviewing_session_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'bad-marker-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'reviewer'}, f)  # no reviewing_session
            result = self.engine.classify_session_role(state_dir, 'bad-marker-sid')
            self.assertEqual(result, 'producer')

    def test_wrong_role_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'wrong-role-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'producer', 'reviewing_session': 'other'}, f)
            result = self.engine.classify_session_role(state_dir, 'wrong-role-sid')
            self.assertEqual(result, 'producer')

    def test_corrupt_json_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'corrupt-sid-role.json')
            with open(marker_path, 'w') as f:
                f.write('not valid json {{{')
            result = self.engine.classify_session_role(state_dir, 'corrupt-sid')
            self.assertEqual(result, 'producer')

    def test_empty_reviewing_session_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'empty-rev-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'reviewer', 'reviewing_session': ''}, f)
            result = self.engine.classify_session_role(state_dir, 'empty-rev-sid')
            self.assertEqual(result, 'producer')

    def test_non_dict_marker_returns_producer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'list-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump(['reviewer', 'other'], f)
            result = self.engine.classify_session_role(state_dir, 'list-sid')
            self.assertEqual(result, 'producer')

    def test_whitespace_reviewing_session_returns_producer(self):
        """MAJOR-1 fix: whitespace-only reviewing_session must be rejected."""
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'ws-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'reviewer', 'reviewing_session': '   '}, f)
            result = self.engine.classify_session_role(state_dir, 'ws-sid')
            self.assertEqual(result, 'producer',
                             'Whitespace-only reviewing_session must be rejected')

    def test_integer_reviewing_session_returns_producer(self):
        """MAJOR-1 fix: non-string reviewing_session must be rejected."""
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'int-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'reviewer', 'reviewing_session': 99999}, f)
            result = self.engine.classify_session_role(state_dir, 'int-sid')
            self.assertEqual(result, 'producer',
                             'Integer reviewing_session must be rejected')

    def test_list_reviewing_session_returns_producer(self):
        """MAJOR-1 fix: list reviewing_session must be rejected."""
        with tempfile.TemporaryDirectory() as state_dir:
            os.makedirs(state_dir, exist_ok=True)
            marker_path = os.path.join(state_dir, 'arr-sid-role.json')
            with open(marker_path, 'w') as f:
                json.dump({'role': 'reviewer', 'reviewing_session': ['x']}, f)
            result = self.engine.classify_session_role(state_dir, 'arr-sid')
            self.assertEqual(result, 'producer',
                             'List reviewing_session must be rejected')


# ============================================================================
# is_bash_entry_write_safe() unit tests
# ============================================================================

class TestIsBashEntryWriteSafe(unittest.TestCase):
    """Unit tests for engine.is_bash_entry_write_safe()."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    # --- Write-safe (should return True — safe for reviewer exemption) ---

    def test_python3_engine_run_checks(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 engine.py run-fast-path-checks /path/to/file'))

    def test_python3_test_script(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 test_session_tagging.py'))

    def test_python3_c_print(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 -c "import json; print(json.dumps({}))"'))

    def test_grep_command(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'grep -r "pattern" /workspace/'))

    def test_npm_run_test(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'npm run test'))

    def test_pytest_verbose(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 -m pytest -v tests/'))

    def test_compound_read_commands(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 engine.py run-fast-path-checks f1 && echo "done"'))

    def test_git_status(self):
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'git status && git diff'))

    def test_unknown_script_no_write_signals(self):
        """Unknown python script with no write indicators → write-safe."""
        self.assertTrue(self.engine.is_bash_entry_write_safe(
            'python3 some-checker.py --verbose'))

    # --- NOT write-safe (should return False — still gated) ---

    def test_empty_string_fail_closed(self):
        """Empty command → fail-closed (not write-safe). minor-2 requirement."""
        self.assertFalse(self.engine.is_bash_entry_write_safe(''))

    def test_none_fail_closed(self):
        """None command → fail-closed."""
        self.assertFalse(self.engine.is_bash_entry_write_safe(None))

    def test_stdout_redirect(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'echo "content" > deliverable.py'))

    def test_append_redirect(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'echo "more" >> deliverable.py'))

    def test_tee_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'echo "data" | tee output.txt'))

    def test_mkdir_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'mkdir -p /tmp/new-dir'))

    def test_rm_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'rm -rf /tmp/old-dir'))

    def test_mv_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'mv old.py new.py'))

    def test_cp_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'cp template.py deliverable.py'))

    def test_touch_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'touch new-file.txt'))

    def test_cat_heredoc_redirect(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            "cat > deliverable.py <<'EOF'"))

    def test_python3_c_open_write(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            """python3 -c "open('src/app.py','w').write('code')" """))

    def test_python3_c_file_write(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            """python3 -c "f=open('x','a'); f.write('y')" """))

    def test_python3_c_shutil(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            """python3 -c "import shutil; shutil.copy('a','b')" """))

    def test_find_delete(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'find . -name "*.tmp" -delete'))

    def test_find_exec_rm(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'find . -exec rm {} +'))

    def test_compound_with_write_segment(self):
        """Compound command where ONE segment writes → not write-safe."""
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'python3 engine.py run-fast-path-checks && echo "x" > out.txt'))

    def test_chmod_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'chmod +x script.sh'))

    def test_ln_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'ln -s target link'))

    def test_dd_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'dd if=/dev/zero of=file bs=1M count=1'))

    def test_patch_command(self):
        self.assertFalse(self.engine.is_bash_entry_write_safe(
            'patch -p1 < fix.patch'))


# ============================================================================
# register-reviewer-session.py integration tests
# ============================================================================

class TestRegisterReviewerSession(unittest.TestCase):
    """Integration tests for register-reviewer-session.py."""

    def test_valid_registration(self):
        with tempfile.TemporaryDirectory() as state_dir:
            r = _run_register('reviewer-sid', 'producer-sid', state_dir)
            self.assertEqual(r.returncode, 0, f'Should succeed: {r.stderr}')
            marker_path = os.path.join(state_dir, 'reviewer-sid-role.json')
            self.assertTrue(os.path.isfile(marker_path))
            with open(marker_path) as f:
                marker = json.load(f)
            self.assertEqual(marker['role'], 'reviewer')
            self.assertEqual(marker['reviewing_session'], 'producer-sid')

    def test_self_registration_rejected(self):
        with tempfile.TemporaryDirectory() as state_dir:
            r = _run_register('same-sid', 'same-sid', state_dir)
            self.assertEqual(r.returncode, 1,
                             'Self-registration must be rejected (exit 1)')
            marker_path = os.path.join(state_dir, 'same-sid-role.json')
            self.assertFalse(os.path.isfile(marker_path),
                             'No marker file should be created on rejection')


# ============================================================================
# ADVERSARIAL: no producer bypass (HARD SAFETY BAR)
# ============================================================================

class TestNoProducerBypass(unittest.TestCase):
    """ADVERSARIAL tests proving a producer cannot bypass the gate by
    masquerading as a reviewer session.

    The session-level exemption is SCOPED: only Bash entries without
    write indicators are exempt. Write/Edit deliverables ALWAYS gate.
    """

    def test_producer_with_reviewer_marker_deliverable_write_still_blocked(self):
        """A producer with a valid reviewer marker writing a deliverable
        via Write tool → gate blocks (exit 2). The marker only exempts
        write-safe Bash, not Write/Edit deliverables."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-marker-write'
            # Create valid reviewer marker
            _write_session_marker(state_dir, sid, reviewing_session='other-sid')
            # Write a deliverable via Write tool
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
                             f'Deliverable Write MUST block even with reviewer '
                             f'marker. Got exit {r.returncode}. stderr: {r.stderr}')

    def test_producer_with_reviewer_marker_deliverable_edit_still_blocked(self):
        """Same as above but via Edit tool."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-marker-edit'
            _write_session_marker(state_dir, sid, reviewing_session='other-sid')
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Edit',
                'file_path': '/workspace/repos/resume-saas/src/utils.ts',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Deliverable Edit MUST block even with reviewer marker')

    def test_producer_forges_marker_bash_writes_deliverable_still_blocked(self):
        """BLOCKING-1 test: producer forges marker via .review-gate/ Bash
        (no dirty entry for the forgery), then writes deliverable via
        state-changing Bash (echo > file). The Bash entry has write
        indicators → NOT exempt → gate blocks.

        This is the exact bypass vector from the Round 1 review."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-forge-bash'
            # Step 1: producer forges marker (via .review-gate/ path — excluded
            # from dirty ledger by dirty-ledger-track.py line 70)
            _write_session_marker(state_dir, sid, reviewing_session='victim-sid')
            # Step 2: producer writes deliverable via Bash redirect
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:deadbeef1234',
                'display': 'echo "malicious code" > src/app.py',
                'bash_cmd': 'echo "malicious code" > src/app.py',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'State-changing Bash MUST block even with forged '
                             f'reviewer marker (write indicator: redirect). '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_producer_forges_marker_python_write_still_blocked(self):
        """Producer forges marker, writes deliverable via python3 -c with
        open(...,'w') → Bash entry has write indicator → NOT exempt."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-forge-python'
            _write_session_marker(state_dir, sid, reviewing_session='victim-sid')
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:cafe1234',
                'display': "python3 -c \"open('src/app.py','w').write('code')",
                'bash_cmd': "python3 -c \"open('src/app.py','w').write('code')\"",
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Python inline write MUST block even with forged marker')

    def test_producer_forges_marker_tee_still_blocked(self):
        """Producer forges marker, writes via tee → still blocked."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-forge-tee'
            _write_session_marker(state_dir, sid, reviewing_session='victim-sid')
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:beef1234',
                'display': 'echo "content" | tee deliverable.py',
                'bash_cmd': 'echo "content" | tee deliverable.py',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'tee write MUST block even with forged marker')

    def test_producer_self_registers_rejected(self):
        """Producer calls register-reviewer-session.py with session==reviewing_session
        → rejected (exit 1), no marker created."""
        with tempfile.TemporaryDirectory() as state_dir:
            r = _run_register('producer-sid', 'producer-sid', state_dir)
            self.assertEqual(r.returncode, 1,
                             'Self-registration MUST be rejected')

    def test_producer_no_marker_all_bash_blocked(self):
        """Producer without a marker: even write-safe Bash is blocked
        (session role = producer → no session-level exemption)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-no-marker'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:abcd1234',
                'display': 'python3 engine.py run-fast-path-checks f',
                'bash_cmd': 'python3 engine.py run-fast-path-checks f',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Producer without marker: Bash entries MUST block')

    def test_producer_mixed_deliverable_and_bash_still_blocked(self):
        """Producer with forged marker writes deliverable + runs Bash.
        The Bash may be exempted but the Write deliverable blocks."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'adversarial-mixed'
            _write_session_marker(state_dir, sid, reviewing_session='other-sid')
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:safe1234',
                    'display': 'python3 engine.py run-fast-path-checks f',
                    'bash_cmd': 'python3 engine.py run-fast-path-checks f',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
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
                             'Mixed: Write deliverable MUST block even with '
                             'reviewer marker and exempted Bash')


# ============================================================================
# Reviewer session acceptance tests
# ============================================================================

class TestReviewerSessionAcceptance(unittest.TestCase):
    """Acceptance tests: confirmed reviewer sessions stop clean for
    inspection Bash + working docs, but block on deliverable writes."""

    def test_reviewer_session_write_safe_bash_stops_clean(self):
        """Reviewer session with only write-safe Bash entries → exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-bash-clean'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': time.time(),
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:abc123',
                    'display': 'python3 engine.py run-fast-path-checks f',
                    'bash_cmd': 'python3 engine.py run-fast-path-checks /path/to/file',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': time.time() + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:def456',
                    'display': 'python3 test_source_tagging.py',
                    'bash_cmd': 'python3 test_source_tagging.py',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer session with write-safe Bash MUST stop '
                             f'clean (exit 0). Got exit {r.returncode}. '
                             f'stderr: {r.stderr}')

    def test_reviewer_session_mixed_bash_and_working_docs_stops_clean(self):
        """Reviewer session: write-safe Bash + reviewer working-doc writes → exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-mixed-clean'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:abc123',
                    'display': 'python3 engine.py run-fast-path-checks f',
                    'bash_cmd': 'python3 engine.py run-fast-path-checks /path/to/file',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
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
                {
                    'timestamp': now + 2,
                    'iso_time': '2026-06-22T00:00:02Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:ghi789',
                    'display': 'python3 log-review-pass.py --session prod --files f',
                    'bash_cmd': 'python3 log-review-pass.py --session prod-sid --files f',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'gate-clearing',
                },
            ])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer session with Bash + working docs + '
                             f'gate-clearing MUST stop clean. Got exit '
                             f'{r.returncode}. stderr: {r.stderr}')

    def test_reviewer_session_deliverable_write_still_blocks(self):
        """Reviewer session that (wrongly) writes a deliverable → blocked."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-deliverable-block'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:abc123',
                    'display': 'python3 engine.py run-fast-path-checks f',
                    'bash_cmd': 'python3 engine.py run-fast-path-checks /path/to/file',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-22T00:00:01Z',
                    'tool': 'Write',
                    'file_path': '/workspace/repos/resume-saas/src/new-feature.tsx',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Reviewer session writing deliverable MUST block. '
                             f'Got exit {r.returncode}')

    def test_reviewer_session_state_changing_bash_still_blocks(self):
        """Reviewer session with state-changing Bash (write indicators) → blocked."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-stateful-bash-block'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:abc123',
                'display': 'echo "data" > /tmp/output.txt',
                'bash_cmd': 'echo "data" > /tmp/output.txt',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'State-changing Bash in reviewer session MUST block. '
                             f'Got exit {r.returncode}')

    def test_reviewer_session_pre_rgh11_ledger_entries_fail_closed(self):
        """Pre-RGH-11 ledger entries without bash_cmd field → fail-closed
        (is_bash_entry_write_safe gets empty string from fallback → False)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'reviewer-pre-rgh11'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-22T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:old123',
                'display': 'python3 engine.py run-fast-path-checks f',
                # NO bash_cmd field — pre-RGH-11 entry
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            # Fall back to display field — 80-char truncated but still has
            # the command. Should be write-safe since it's just engine.py.
            # The fail-closed behavior applies to EMPTY display, not to
            # pre-RGH-11 entries that have display.
            self.assertEqual(r.returncode, 0,
                             f'Pre-RGH-11 entry with display field should use '
                             f'display as fallback. Got exit {r.returncode}')


# ============================================================================
# Real-runner replay: 2026-06-22 session-26201e56 scenario (RGH11-4)
# ============================================================================

class TestRealRunnerReplay(unittest.TestCase):
    """Replay the exact 2026-06-22 orchestrator-Phase-2 reviewer scenario.

    The separate-session independent reviewer (session 26201e56) hit the
    gate on 2 read-only engine.py inspection calls + reviewer working docs.
    With RGH-11: no block, no skip.
    """

    def test_replay_26201e56_reviewer_session(self):
        """Full replay: reviewer session with read-only engine.py Bash +
        python3 -c inspection + reviewer working docs + gate-clearing.
        Must stop clean (exit 0) with zero gate blocks."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'replay-26201e56'
            producer_sid = 'revorch-phase2-producer'
            _write_session_marker(state_dir, sid,
                                  reviewing_session=producer_sid)
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                # The two read-only engine.py inspection calls that blocked
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T16:10:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:engine1',
                    'display': 'python3 engine.py run-fast-path-checks /works',
                    'bash_cmd': ('python3 /Users/olivermarroquin/workspace/repos/'
                                 'ai-agency-core/scripts/mandatory-review-gate/'
                                 'engine.py run-fast-path-checks /workspace/skills/'
                                 'reviewer-orchestrator/SKILL.md'),
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-22T16:10:01Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:engine2',
                    'display': 'python3 engine.py run-fast-path-checks /works',
                    'bash_cmd': ('python3 /Users/olivermarroquin/workspace/repos/'
                                 'ai-agency-core/scripts/mandatory-review-gate/'
                                 'engine.py run-fast-path-checks /workspace/skills/'
                                 'reviewer-orchestrator/references/dispatch-contract.md'),
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                # Reviewer working-doc writes (already exempt by RGH-10)
                {
                    'timestamp': now + 2,
                    'iso_time': '2026-06-22T16:15:00Z',
                    'tool': 'Write',
                    'file_path': '/workspace/second-brain/_meta/handoffs/review-gate-hardening/execution-log-2026-06-22-independent-review.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 3,
                    'iso_time': '2026-06-22T16:20:00Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                {
                    'timestamp': now + 4,
                    'iso_time': '2026-06-22T16:20:01Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
                # Gate-clearing call (already exempt by RGH-10)
                {
                    'timestamp': now + 5,
                    'iso_time': '2026-06-22T16:25:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:gateclear1',
                    'display': 'python3 log-review-pass.py --session revorch',
                    'bash_cmd': ('python3 log-review-pass.py --session '
                                 f'{producer_sid} --files f --verdict PASS '
                                 '--tier full --gate-id G-independent '
                                 '--verdict-file v.json --reviewer-type independent '
                                 f'--reviewer-session {sid}'),
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'gate-clearing',
                },
            ])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Replay of 2026-06-22 session-26201e56: reviewer '
                             f'session MUST stop clean (exit 0) with zero gate '
                             f'blocks. Got exit {r.returncode}. '
                             f'stderr: {r.stderr}')

    def test_replay_without_marker_would_block(self):
        """Same scenario WITHOUT the reviewer marker → blocked (exit 2).
        Proves the marker is what enables the exemption."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'replay-no-marker'
            now = time.time()
            _write_dirty_entries(state_dir, sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-22T16:10:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:engine1',
                    'display': 'python3 engine.py run-fast-path-checks f',
                    'bash_cmd': 'python3 engine.py run-fast-path-checks /path',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Same scenario WITHOUT marker MUST block (exit 2). '
                             f'Got exit {r.returncode}')


# ============================================================================
# bash_cmd field in dirty-ledger-track.py integration test
# ============================================================================

class TestTrackerBashCmdField(unittest.TestCase):
    """Verify dirty-ledger-track.py stores bash_cmd field for RGH-11."""

    def test_bash_entry_has_bash_cmd_field(self):
        """Non-read-only Bash command creates entry with full bash_cmd."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tracker-bash-cmd'
            cmd = 'python3 some-script.py --check /path/to/file'
            _run_track(sid, 'Bash', {'command': cmd}, state_dir)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].get('bash_cmd'), cmd,
                             'Dirty entry must include full bash_cmd field')
            # display should be truncated version
            self.assertIn('display', entries[0])

    def test_write_entry_has_no_bash_cmd_field(self):
        """Write tool entries should NOT have bash_cmd field."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'tracker-no-bash-cmd'
            _run_track(sid, 'Write', {
                'file_path': '/tmp/test-file.md',
                'content': 'content',
            }, state_dir)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1)
            self.assertNotIn('bash_cmd', entries[0],
                             'Write entries should not have bash_cmd field')


# ============================================================================
# RGH12-8: Git plumbing is non-dirty (chats can commit)
# ============================================================================

class TestGitPlumbingNonDirty(unittest.TestCase):
    """RGH12-8: git add/commit/push/stash/fetch/pull and rm -f .git/index.lock
    are classified as read-only (no dirty-ledger entry). Destructive git
    mutations (reset --hard, clean, checkout --, restore) still create entries.

    Safety: whitelisting git plumbing does NOT let unreviewed deliverables
    escape — a producer's deliverable is its Write/Edit (still dirty), and
    the Tier-B pre-commit hook independently guards commit content."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    # --- Non-dirty (should return True from is_read_only_bash) ---

    def test_git_add_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git add engine.py test.py'))

    def test_git_commit_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash(
            'git commit -m "feat: add feature"'))

    def test_git_push_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git push'))

    def test_git_push_with_remote(self):
        self.assertTrue(self.engine.is_read_only_bash('git push origin main'))

    def test_git_stash_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git stash'))

    def test_git_fetch_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git fetch origin'))

    def test_git_pull_is_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git pull'))

    def test_git_status_still_read_only(self):
        """Regression: existing READ_ONLY_GIT_SUBCMDS unchanged."""
        self.assertTrue(self.engine.is_read_only_bash('git status'))

    def test_git_diff_still_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git diff'))

    def test_git_log_still_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git log --oneline -5'))

    def test_git_show_still_read_only(self):
        self.assertTrue(self.engine.is_read_only_bash('git show HEAD'))

    def test_compound_git_add_commit_push(self):
        """The exact commit pattern from CLAUDE.md."""
        self.assertTrue(self.engine.is_read_only_bash(
            'git add engine.py test.py && git commit -m "fix" && git push'))

    def test_rm_f_git_index_lock(self):
        self.assertTrue(self.engine.is_read_only_bash(
            'rm -f /workspace/repos/ai-agency-core/.git/index.lock'))

    def test_rm_f_git_lock_then_git_add_commit_push(self):
        """The exact pattern from CLAUDE.md commit blocks."""
        self.assertTrue(self.engine.is_read_only_bash(
            'rm -f .git/index.lock && git add file.py && '
            'git commit -m "msg" && git push'))

    def test_cd_then_git_commit(self):
        """cd + git commit compound."""
        self.assertTrue(self.engine.is_read_only_bash(
            'cd ~/workspace/repos/ai-agency-core && git add f && git commit -m "x"'))

    # --- STILL dirty (destructive — should return False) ---

    def test_git_reset_hard_still_dirty(self):
        self.assertFalse(self.engine.is_read_only_bash('git reset --hard'))

    def test_git_reset_hard_origin(self):
        self.assertFalse(self.engine.is_read_only_bash(
            'git reset --hard origin/main'))

    def test_git_clean_still_dirty(self):
        self.assertFalse(self.engine.is_read_only_bash('git clean -fd'))

    def test_git_checkout_path_still_dirty(self):
        self.assertFalse(self.engine.is_read_only_bash(
            'git checkout -- src/app.py'))

    def test_git_restore_still_dirty(self):
        self.assertFalse(self.engine.is_read_only_bash(
            'git restore src/app.py'))

    def test_rm_without_f_flag_still_dirty(self):
        """rm without -f is still dirty (general rm)."""
        self.assertFalse(self.engine.is_read_only_bash(
            'rm /workspace/.git/index.lock'))

    def test_rm_f_non_lock_file_still_dirty(self):
        """rm -f of a non-.git/index.lock file is still dirty."""
        self.assertFalse(self.engine.is_read_only_bash(
            'rm -f /workspace/src/app.py'))

    def test_rm_f_git_lock_plus_other_file_still_dirty(self):
        """rm -f targeting lock + another file → still dirty."""
        self.assertFalse(self.engine.is_read_only_bash(
            'rm -f .git/index.lock src/app.py'))


class TestGitPlumbingIntegration(unittest.TestCase):
    """Integration: git add && commit && push creates NO dirty entry,
    session stops clean. git reset --hard creates a dirty entry."""

    def test_git_commit_push_no_dirty_entry(self):
        """git add && git commit && git push → no dirty-ledger entry."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'git-commit-session'
            cmd = 'cd ~/workspace/repos/ai-agency-core && rm -f .git/index.lock && git add engine.py && git commit -m "msg" && git push'
            r = _run_track(sid, 'Bash', {'command': cmd}, state_dir)
            self.assertEqual(r.returncode, 0)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 0,
                             f'Git commit+push should create NO dirty entry. '
                             f'Got {len(entries)}: {entries}')

    def test_git_commit_push_session_stops_clean(self):
        """Session that ONLY ran git add/commit/push → exit 0 (no block)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'git-only-session'
            # Track git plumbing — should create no entries
            cmd = 'git add engine.py && git commit -m "feat" && git push'
            _run_track(sid, 'Bash', {'command': cmd}, state_dir)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 0, 'Git plumbing should be non-dirty')
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Git-only session MUST stop clean. '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_git_reset_hard_creates_dirty_entry(self):
        """git reset --hard → dirty-ledger entry created."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'git-reset-session'
            cmd = 'git reset --hard origin/main'
            _run_track(sid, 'Bash', {'command': cmd}, state_dir)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1,
                             f'git reset --hard MUST create a dirty entry. '
                             f'Got {len(entries)}')

    def test_git_clean_creates_dirty_entry(self):
        """git clean -fd → dirty-ledger entry created."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'git-clean-session'
            _run_track(sid, 'Bash', {'command': 'git clean -fd'}, state_dir)
            entries = _read_dirty_ledger(state_dir, sid)
            self.assertEqual(len(entries), 1,
                             'git clean MUST create a dirty entry')


# ============================================================================
# RGH-12: auto-infer reviewer role from gate-clearing records
# ============================================================================

def _write_reviewed_entry(state_dir, producer_session_id, reviewer_session_id):
    """Write a reviewed-ledger entry to the PRODUCER's ledger with
    reviewer_session set to the reviewer's session ID.
    This is what log-review-pass.py produces."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, f'{producer_session_id}-reviewed.jsonl')
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'file_path': '/workspace/repos/some-file.py',
        'verdict': 'PASS',
        'tier': 'full',
        'gate_id': 'G-independent',
        'evidence': 'test evidence',
        'reviewer_type': 'independent',
        'reviewer_session': reviewer_session_id,
    }
    with open(path, 'a') as f:
        f.write(json.dumps(entry) + '\n')


class TestClassifySessionRoleAutoInference(unittest.TestCase):
    """RGH12-1: classify_session_role() returns 'reviewer' when gate-clearing
    records show this session cleared a different producer's gate."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine
        # Clear the cache between tests
        self.engine._gate_clearing_signal_cache.clear()

    def test_auto_infer_reviewer_from_gate_clearing(self):
        """Session that cleared another session's gate → reviewer (no marker)."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'reviewer-auto-abc'
            producer_sid = 'producer-xyz'
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)
            result = self.engine.classify_session_role(state_dir, reviewer_sid)
            self.assertEqual(result, 'reviewer',
                             'Session that cleared another session\'s gate '
                             'must be classified as reviewer')

    def test_auto_infer_no_reviewed_files_returns_producer(self):
        """No reviewed files at all → producer."""
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.engine.classify_session_role(state_dir, 'lonely-sid')
            self.assertEqual(result, 'producer')

    def test_auto_infer_only_own_ledger_returns_producer(self):
        """Session only has entries in its OWN reviewed ledger → producer.
        A session cannot auto-infer reviewer from its own ledger."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'self-only-sid'
            # Write to own ledger (not another session's)
            path = os.path.join(state_dir, f'{sid}-reviewed.jsonl')
            entry = {
                'timestamp': time.time(),
                'file_path': '/workspace/file.py',
                'verdict': 'PASS',
                'reviewer_session': sid,
            }
            with open(path, 'w') as f:
                f.write(json.dumps(entry) + '\n')
            result = self.engine.classify_session_role(state_dir, sid)
            self.assertEqual(result, 'producer',
                             'Own ledger entries must NOT trigger auto-inference')

    def test_auto_infer_reviewer_session_mismatch_returns_producer(self):
        """Reviewed entry has a DIFFERENT reviewer_session → producer for us."""
        with tempfile.TemporaryDirectory() as state_dir:
            our_sid = 'our-session'
            producer_sid = 'producer-session'
            other_reviewer = 'other-reviewer-session'
            _write_reviewed_entry(state_dir, producer_sid, other_reviewer)
            result = self.engine.classify_session_role(state_dir, our_sid)
            self.assertEqual(result, 'producer',
                             'Entry with different reviewer_session must not '
                             'match our session')

    def test_auto_infer_caching(self):
        """Once confirmed, result is cached — no re-glob."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'cached-reviewer'
            producer_sid = 'cached-producer'
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)
            r1 = self.engine.classify_session_role(state_dir, reviewer_sid)
            self.assertEqual(r1, 'reviewer')
            # Remove the file — cached result should still return reviewer
            os.remove(os.path.join(state_dir, f'{producer_sid}-reviewed.jsonl'))
            r2 = self.engine.classify_session_role(state_dir, reviewer_sid)
            self.assertEqual(r2, 'reviewer',
                             'Cached positive result must persist')

    def test_marker_takes_precedence_over_auto_infer(self):
        """If marker exists, it should return reviewer without needing
        auto-inference (marker is checked first, faster)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'marker-plus-auto'
            _write_session_marker(state_dir, sid, reviewing_session='prod-sid')
            result = self.engine.classify_session_role(state_dir, sid)
            self.assertEqual(result, 'reviewer')


# ============================================================================
# RGH12-3: ADVERSARIAL — no producer bypass via forged gate-clearing records
# ============================================================================

class TestNoProducerBypassAutoInference(unittest.TestCase):
    """ADVERSARIAL: a producer cannot bypass the gate by forging
    gate-clearing evidence for auto-inference."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine
        self.engine._gate_clearing_signal_cache.clear()

    def test_producer_self_clear_no_auto_inference(self):
        """Producer writes reviewed entry in its OWN ledger with
        reviewer_session == self. classify_session_role skips own ledger
        → still producer."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'self-clear-forger'
            path = os.path.join(state_dir, f'{sid}-reviewed.jsonl')
            entry = {
                'timestamp': time.time(),
                'file_path': '/workspace/file.py',
                'verdict': 'PASS',
                'reviewer_session': sid,
            }
            with open(path, 'w') as f:
                f.write(json.dumps(entry) + '\n')
            result = self.engine.classify_session_role(state_dir, sid)
            self.assertEqual(result, 'producer',
                             'Self-clear in own ledger must NOT grant reviewer')

    def test_producer_forges_entry_in_another_sessions_ledger(self):
        """Producer writes a forged entry into another session's reviewed
        ledger claiming reviewer_session == self. This DOES match signal (b).

        HONEST LIMIT: if a producer can write arbitrary files into state_dir,
        it could forge this. But:
        1. The producer's dirty-ledger-track would record the Write/Bash that
           created the forged file → that write would itself be gated.
        2. The scoped exemption only covers write-safe Bash — deliverable
           Write/Edit still gates regardless of session role.
        3. In practice, .review-gate/state/ writes are tracked by the
           dirty-ledger-track PostToolUse hook.

        This test documents the honest limit — the forged entry DOES match.
        The safety comes from the scoped exemption + dirty-ledger tracking,
        not from preventing the forge itself."""
        with tempfile.TemporaryDirectory() as state_dir:
            forger_sid = 'forger-producer'
            victim_sid = 'innocent-producer'
            # Forger writes into victim's ledger
            _write_reviewed_entry(state_dir, victim_sid, forger_sid)
            result = self.engine.classify_session_role(state_dir, forger_sid)
            # The forge DOES match — honest limit documented
            self.assertEqual(result, 'reviewer',
                             'Forged entry matches (honest limit) — safety '
                             'comes from scoped exemption + dirty-ledger tracking')

    def test_forged_reviewer_deliverable_write_still_blocked(self):
        """Even if a producer forges auto-inference, deliverable Write
        still blocks (RGH12-2). The scoped exemption is the real guard."""
        with tempfile.TemporaryDirectory() as state_dir:
            forger_sid = 'forger-deliverable'
            victim_sid = 'victim-producer'
            # Forge auto-inference
            _write_reviewed_entry(state_dir, victim_sid, forger_sid)
            # Write a deliverable
            _write_dirty_entries(state_dir, forger_sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-23T00:00:00Z',
                'tool': 'Write',
                'file_path': '/workspace/repos/resume-saas/src/app.tsx',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(forger_sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'Deliverable Write MUST block even with forged '
                             'auto-inference (scoped exemption is the guard)')

    def test_forged_reviewer_state_changing_bash_still_blocked(self):
        """Even with forged auto-inference, state-changing Bash blocks."""
        with tempfile.TemporaryDirectory() as state_dir:
            forger_sid = 'forger-bash-write'
            victim_sid = 'victim-producer2'
            _write_reviewed_entry(state_dir, victim_sid, forger_sid)
            _write_dirty_entries(state_dir, forger_sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-23T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:forged1234',
                'display': 'echo "malicious" > src/app.py',
                'bash_cmd': 'echo "malicious" > src/app.py',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(forger_sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             'State-changing Bash MUST block even with forged '
                             'auto-inference')

    def test_corrupt_reviewed_ledger_no_auto_inference(self):
        """Corrupt JSONL in another session's ledger → skip, not crash."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'safe-from-corrupt'
            os.makedirs(state_dir, exist_ok=True)
            path = os.path.join(state_dir, 'other-session-reviewed.jsonl')
            with open(path, 'w') as f:
                f.write('not valid json {{{\n')
                f.write('also broken\n')
            result = self.engine.classify_session_role(state_dir, sid)
            self.assertEqual(result, 'producer',
                             'Corrupt ledger must not crash or grant reviewer')

    def test_reviewed_entry_missing_reviewer_session_no_match(self):
        """Entry without reviewer_session field → no match."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'no-field-sid'
            os.makedirs(state_dir, exist_ok=True)
            path = os.path.join(state_dir, 'other-reviewed.jsonl')
            entry = {
                'timestamp': time.time(),
                'file_path': '/workspace/file.py',
                'verdict': 'PASS',
                # no reviewer_session field
            }
            with open(path, 'w') as f:
                f.write(json.dumps(entry) + '\n')
            result = self.engine.classify_session_role(state_dir, sid)
            self.assertEqual(result, 'producer')


# ============================================================================
# RGH12-5: Real-runner replay — hand-spawned reviewer, no marker
# ============================================================================

class TestAutoInferRealRunnerReplay(unittest.TestCase):
    """Replay the 2026-06-23 BTF-W3 / MCD CR-075 scenarios.

    A hand-spawned reviewer with NO marker logs the producer's PASS, then
    runs read-only Bash — must stop cleanly (exit 0), zero registration,
    zero gate-skip."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine
        self.engine._gate_clearing_signal_cache.clear()

    def test_replay_btf_w3_reviewer_no_marker(self):
        """Full replay: reviewer (no marker) clears producer + runs
        read-only Bash (shasum, find, ls) → stops exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'replay-9e7973a8'
            producer_sid = 'replay-btf-producer'

            # Step 1: reviewer clears producer's gate (creates the evidence)
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)

            # Step 2: reviewer's dirty ledger has read-only Bash
            now = time.time()
            _write_dirty_entries(state_dir, reviewer_sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-23T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:shasum1',
                    'display': 'shasum -a 256 /workspace/repos/file.py',
                    'bash_cmd': 'shasum -a 256 /workspace/repos/file.py',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-23T00:00:01Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:find1',
                    'display': 'find /workspace/repos -name "*.py" -type f',
                    'bash_cmd': 'find /workspace/repos -name "*.py" -type f',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 2,
                    'iso_time': '2026-06-23T00:00:02Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:ls1',
                    'display': 'ls -la /workspace/repos/',
                    'bash_cmd': 'ls -la /workspace/repos/',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                # Gate-clearing entry (already exempt by RGH-10)
                {
                    'timestamp': now + 3,
                    'iso_time': '2026-06-23T00:00:03Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:gateclear',
                    'display': 'python3 log-review-pass.py --session prod',
                    'bash_cmd': (f'python3 log-review-pass.py --session '
                                 f'{producer_sid} --files f --verdict PASS '
                                 f'--tier full --gate-id G-independent '
                                 f'--verdict-file v.json --reviewer-type '
                                 f'independent --reviewer-session {reviewer_sid}'),
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'gate-clearing',
                },
            ])
            r = _run_gate(reviewer_sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'BTF-W3 replay: reviewer with NO marker that '
                             f'cleared producer MUST stop clean (exit 0). '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_replay_mcd_cr075_reviewer_no_marker(self):
        """MCD CR-075 reviewer replay: python3 -c + grep Bash → exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'replay-b8a83829'
            producer_sid = 'replay-mcd-producer'

            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)

            now = time.time()
            _write_dirty_entries(state_dir, reviewer_sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-23T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:python3c',
                    'display': 'python3 -c "import json; print(json.dumps({}))"',
                    'bash_cmd': 'python3 -c "import json; print(json.dumps({}))"',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-23T00:00:01Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:grep1',
                    'display': 'grep -r "reviewer_session" /workspace/.review-gate/',
                    'bash_cmd': 'grep -r "reviewer_session" /workspace/.review-gate/',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                # Reviewer working-doc write (exempt by RGH-10 source tag)
                {
                    'timestamp': now + 2,
                    'iso_time': '2026-06-23T00:00:02Z',
                    'tool': 'Edit',
                    'file_path': '/workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'reviewer',
                },
            ])
            r = _run_gate(reviewer_sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'MCD CR-075 replay: reviewer with NO marker '
                             f'MUST stop clean. Got exit {r.returncode}. '
                             f'stderr: {r.stderr}')

    def test_replay_no_gate_clearing_evidence_blocks(self):
        """Same Bash entries but WITHOUT the gate-clearing evidence
        → session is producer → blocks (exit 2)."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'replay-no-evidence'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-23T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:shasum1',
                'display': 'shasum -a 256 /workspace/repos/file.py',
                'bash_cmd': 'shasum -a 256 /workspace/repos/file.py',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Without gate-clearing evidence MUST block. '
                             f'Got exit {r.returncode}')


# ============================================================================
# RGH12-6: Reviewer-doc Bash write exemption
# ============================================================================

class TestIsBashReviewerDocWrite(unittest.TestCase):
    """Unit tests for engine.is_bash_reviewer_doc_write()."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine

    # --- Should return True (reviewer-doc writes only) ---

    def test_cat_append_firing_tracker(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'cat >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md'))

    def test_echo_append_catch_register(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'echo "| CR-099 |" >> /workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md'))

    def test_redirect_to_verdict_file(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'echo "{}" > /workspace/.review-gate/state/verdict-test-123.json'))

    def test_redirect_to_reviewed_jsonl(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'echo "{}" >> /workspace/.review-gate/state/abc-reviewed.jsonl'))

    def test_redirect_to_reviewer_execution_log(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'cat >> /workspace/repos/ai-agency-core/.kos/execution-logs/execution-log-2026-06-23-independent-review.md'))

    def test_redirect_to_peer_review_log(self):
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'echo "done" >> /workspace/execution-log-2026-06-23-peer-review.md'))

    def test_compound_read_plus_doc_write(self):
        """Compound: read-only + doc write → True."""
        self.assertTrue(self.engine.is_bash_reviewer_doc_write(
            'echo "row" >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md && echo "done"'))

    # --- Should return False (NOT exempt) ---

    def test_redirect_to_deliverable(self):
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "code" > /workspace/repos/resume-saas/src/app.tsx'))

    def test_compound_doc_write_plus_deploy(self):
        """B3 guard: doc write + non-doc action → NOT exempt."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "row" >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md && npm run deploy'))

    def test_compound_doc_write_plus_rm(self):
        """B3 guard: doc write + rm → NOT exempt."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "row" >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md; rm -rf /tmp/x'))

    def test_compound_doc_write_plus_non_doc_write(self):
        """B3 guard: doc write + non-doc file write → NOT exempt."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "row" >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md && echo "x" > /tmp/hack.py'))

    def test_tee_to_doc_not_exempt(self):
        """tee writes are NOT covered (tee is in _FILE_MUTATING_CMDS)."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "row" | tee -a /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md'))

    def test_empty_command(self):
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(''))

    def test_none_command(self):
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(None))

    def test_read_only_command_not_doc_write(self):
        """Pure read-only is NOT a doc write (returns False — has_doc_write
        flag is never set). Use is_bash_entry_write_safe for read-only."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'grep -r "pattern" /workspace/'))

    def test_redirect_to_non_reviewer_execution_log(self):
        """Execution log without reviewer/independent-review suffix → NOT exempt."""
        self.assertFalse(self.engine.is_bash_reviewer_doc_write(
            'echo "x" >> /workspace/execution-log-2026-06-23-normal-build.md'))


class TestReviewerDocWriteIntegration(unittest.TestCase):
    """Integration tests: verified reviewer session with doc-write Bash
    entries stops clean; compound bypass still gates."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS)
        import engine
        self.engine = engine
        self.engine._gate_clearing_signal_cache.clear()

    def test_reviewer_cat_append_firing_tracker_stops_clean(self):
        """Verified reviewer appends to firing tracker via cat >> → exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'reviewer-doc-write'
            producer_sid = 'producer-for-doc'
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)
            _write_dirty_entries(state_dir, reviewer_sid, [
                {
                    'timestamp': time.time(),
                    'iso_time': '2026-06-23T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:catappend1',
                    'display': 'cat >> _review-skill-firing-tracker.md',
                    'bash_cmd': 'cat >> /workspace/second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])
            r = _run_gate(reviewer_sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer cat >> firing-tracker MUST stop clean. '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_reviewer_doc_write_plus_read_only_stops_clean(self):
        """Reviewer: doc write + read-only Bash → exit 0."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'reviewer-mixed-doc'
            producer_sid = 'producer-mixed-doc'
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)
            now = time.time()
            _write_dirty_entries(state_dir, reviewer_sid, [
                {
                    'timestamp': now,
                    'iso_time': '2026-06-23T00:00:00Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:grep1',
                    'display': 'grep -r "pattern" /workspace/',
                    'bash_cmd': 'grep -r "pattern" /workspace/',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
                {
                    'timestamp': now + 1,
                    'iso_time': '2026-06-23T00:00:01Z',
                    'tool': 'Bash',
                    'file_path': 'BASH:catappend2',
                    'display': 'cat >> _review-gate-catch-register.md',
                    'bash_cmd': 'cat >> /workspace/second-brain/_meta/handoffs/_review-gate-catch-register.md',
                    'tier': 'full',
                    'source': 'claude-code',
                    'entry_source': 'producer',
                },
            ])
            r = _run_gate(reviewer_sid, state_dir)
            self.assertEqual(r.returncode, 0,
                             f'Reviewer doc-write + read-only MUST stop clean. '
                             f'Got exit {r.returncode}. stderr: {r.stderr}')

    def test_reviewer_compound_bypass_still_blocks(self):
        """B3 guard: reviewer with compound non-bookkeeping write + deploy → blocks.

        Updated per CR-107: original test used _review-skill-firing-tracker.md
        which is in BOOKKEEPING_BASENAMES (CR-161 exemption). The bookkeeping
        exemption is session-role-independent and pre-empts the B3 guard.
        Changed to a non-bookkeeping write target to actually test B3."""
        with tempfile.TemporaryDirectory() as state_dir:
            reviewer_sid = 'reviewer-compound-bypass'
            producer_sid = 'producer-compound'
            _write_reviewed_entry(state_dir, producer_sid, reviewer_sid)
            _write_dirty_entries(state_dir, reviewer_sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-23T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:compound1',
                'display': 'cat >> some-deliverable.md && npm deploy',
                'bash_cmd': 'cat >> /workspace/repos/some-deliverable.md && npm run deploy',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(reviewer_sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Compound non-bookkeeping write + deploy MUST block '
                             f'even for verified reviewer. Got exit {r.returncode}')

    def test_unverified_session_non_bookkeeping_write_blocks(self):
        """Unverified session writing to a non-bookkeeping path → blocks.

        Updated per CR-107: original test used _review-skill-firing-tracker.md
        which is in BOOKKEEPING_BASENAMES (CR-161 exemption, session-role-
        independent). Changed to a non-bookkeeping target to actually test
        that unverified sessions are blocked on state-changing Bash."""
        with tempfile.TemporaryDirectory() as state_dir:
            sid = 'unverified-doc-writer'
            _write_dirty_entries(state_dir, sid, [{
                'timestamp': time.time(),
                'iso_time': '2026-06-23T00:00:00Z',
                'tool': 'Bash',
                'file_path': 'BASH:unverified1',
                'display': 'cat >> some-deliverable.md',
                'bash_cmd': 'cat >> /workspace/repos/some-deliverable.md',
                'tier': 'full',
                'source': 'claude-code',
                'entry_source': 'producer',
            }])
            r = _run_gate(sid, state_dir)
            self.assertEqual(r.returncode, 2,
                             f'Unverified session non-bookkeeping write MUST block. '
                             f'Got exit {r.returncode}')


if __name__ == '__main__':
    unittest.main()
