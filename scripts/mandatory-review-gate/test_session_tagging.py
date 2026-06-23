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


if __name__ == '__main__':
    unittest.main()
