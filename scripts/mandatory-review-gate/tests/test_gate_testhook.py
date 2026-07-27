"""Tests for the gate-testhook feature in git_hook_adapter.py.

Covers:
- has_staged_gate_files: trigger detection (gate-path vs non-gate)
- run_gate_test_suite: success, failure, pytest-error paths
- _parse_pytest_count: summary line parsing
- Integration: main() flow with mocked subprocess calls
"""

import os
import sys
import subprocess

import pytest

SCRIPT_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, SCRIPT_DIR)

import git_hook_adapter


# ── has_staged_gate_files ──────────────────────────────────────────────


class TestHasStagedGateFiles:
    """Trigger detection: only fire when staged files are under gate dir."""

    def test_gate_file_triggers(self):
        gate_dir = git_hook_adapter.GATE_CODE_DIR
        staged = {os.path.join(gate_dir, 'engine.py')}
        assert git_hook_adapter.has_staged_gate_files(staged) is True

    def test_gate_test_file_triggers(self):
        gate_dir = git_hook_adapter.GATE_CODE_DIR
        staged = {os.path.join(gate_dir, 'tests', 'test_something.py')}
        assert git_hook_adapter.has_staged_gate_files(staged) is True

    def test_non_gate_file_does_not_trigger(self):
        staged = {'/Users/someone/workspace/repos/ai-agency-core/README.md'}
        assert git_hook_adapter.has_staged_gate_files(staged) is False

    def test_empty_staged_does_not_trigger(self):
        assert git_hook_adapter.has_staged_gate_files(set()) is False

    def test_mixed_files_triggers_if_any_gate(self):
        gate_dir = git_hook_adapter.GATE_CODE_DIR
        staged = {
            '/Users/someone/workspace/docs/README.md',
            os.path.join(gate_dir, 'dirty-ledger-track.py'),
        }
        assert git_hook_adapter.has_staged_gate_files(staged) is True

    def test_similar_prefix_no_false_positive(self):
        """A dir named mandatory-review-gate-extra should NOT trigger."""
        gate_dir = git_hook_adapter.GATE_CODE_DIR
        fake = gate_dir + '-extra/somefile.py'
        staged = {fake}
        assert git_hook_adapter.has_staged_gate_files(staged) is False

    def test_parent_dir_does_not_trigger(self):
        """A file in the parent of gate dir should NOT trigger."""
        parent = os.path.dirname(git_hook_adapter.GATE_CODE_DIR)
        staged = {os.path.join(parent, 'some_script.py')}
        assert git_hook_adapter.has_staged_gate_files(staged) is False


# ── _parse_pytest_count ────────────────────────────────────────────────


class TestParsePytestCount:
    def test_parse_passed(self):
        assert git_hook_adapter._parse_pytest_count(
            '731 passed in 28.21s', 'passed') == 731

    def test_parse_failed(self):
        assert git_hook_adapter._parse_pytest_count(
            '5 failed, 726 passed in 30.00s', 'failed') == 5

    def test_parse_missing_keyword(self):
        assert git_hook_adapter._parse_pytest_count(
            '731 passed in 28.21s', 'failed') == 0

    def test_parse_empty(self):
        assert git_hook_adapter._parse_pytest_count('', 'passed') == 0


# ── run_gate_test_suite ────────────────────────────────────────────────


class TestRunGateTestSuite:
    """Tests for the pytest runner — all subprocess calls are mocked."""

    def test_success_path(self, monkeypatch):
        """All tests pass → success=True."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='731 passed in 28.21s\n', stderr='')
        monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: mock_result)

        success, output, failed, total = git_hook_adapter.run_gate_test_suite()
        assert success is True
        assert failed == 0
        assert total == 731

    def test_failure_path(self, monkeypatch):
        """Some tests fail → success=False, failed>0."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout='FAILED test_foo.py::test_bar\n5 failed, 726 passed in 30s\n',
            stderr='')
        monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: mock_result)

        success, output, failed, total = git_hook_adapter.run_gate_test_suite()
        assert success is False
        assert failed == 5
        assert total == 731

    def test_timeout_fails_closed(self, monkeypatch):
        """Timeout → success=False, failed=0 (fail closed)."""
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd='pytest', timeout=120)
        monkeypatch.setattr(subprocess, 'run', raise_timeout)

        success, output, failed, total = git_hook_adapter.run_gate_test_suite()
        assert success is False
        assert failed == 0
        assert 'timed out' in output

    def test_file_not_found_fails_closed(self, monkeypatch):
        """pytest not found → success=False, failed=0 (fail closed)."""
        def raise_fnf(*a, **kw):
            raise FileNotFoundError('no such file')
        monkeypatch.setattr(subprocess, 'run', raise_fnf)

        success, output, failed, total = git_hook_adapter.run_gate_test_suite()
        assert success is False
        assert failed == 0
        assert 'not found' in output

    def test_generic_error_fails_closed(self, monkeypatch):
        """Unexpected error → success=False, failed=0 (fail closed)."""
        def raise_err(*a, **kw):
            raise RuntimeError('something broke')
        monkeypatch.setattr(subprocess, 'run', raise_err)

        success, output, failed, total = git_hook_adapter.run_gate_test_suite()
        assert success is False
        assert failed == 0
        assert 'something broke' in output

    def test_cwd_is_gate_dir(self, monkeypatch):
        """Verify pytest runs from the gate code directory."""
        captured_kw = {}
        def capture_run(*a, **kw):
            captured_kw.update(kw)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='10 passed\n', stderr='')
        monkeypatch.setattr(subprocess, 'run', capture_run)

        git_hook_adapter.run_gate_test_suite()
        assert captured_kw['cwd'] == git_hook_adapter.GATE_CODE_DIR

    def test_timeout_parameter_forwarded(self, monkeypatch):
        """Custom timeout is forwarded to subprocess."""
        captured_kw = {}
        def capture_run(*a, **kw):
            captured_kw.update(kw)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='10 passed\n', stderr='')
        monkeypatch.setattr(subprocess, 'run', capture_run)

        git_hook_adapter.run_gate_test_suite(timeout_seconds=60)
        assert captured_kw['timeout'] == 60
