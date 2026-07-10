#!/usr/bin/env python3
"""Tests for skip-provenance hardening (CR-224, CR-225, CR-226).

Covers:
- Fix 1: gate-skip.py refuses deliverable blocks (+ --force-deliverables path)
- Fix 2: D-09 detection on Write/Edit of .gate-skip*.sh files
- Fix 3: assert-gate-clear.py exit codes
- Fix 5: bash-scope scratch cleanup plumbing exemption
"""

import json
import os
import subprocess
import sys
import tempfile
import time

# Script paths
SCRIPT_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
GATE_SKIP_SCRIPT = os.path.join(SCRIPT_DIR, 'gate-skip.py')
ASSERT_GATE_CLEAR_SCRIPT = os.path.join(SCRIPT_DIR, 'assert-gate-clear.py')
DIRTY_LEDGER_TRACK_SCRIPT = os.path.join(SCRIPT_DIR, 'dirty-ledger-track.py')

# Engine for direct testing
sys.path.insert(0, SCRIPT_DIR)
import engine


def _make_state_dir(tmpdir):
    """Create a state dir and return its path."""
    state_dir = os.path.join(tmpdir, 'state')
    os.makedirs(state_dir, exist_ok=True)
    return state_dir


def _write_dirty_entry(state_dir, session_id, file_path, tier='full',
                        tool='Write', bash_cmd=None):
    """Write a dirty-ledger entry directly."""
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': tool,
        'file_path': file_path,
        'tier': tier,
        'source': 'claude-code',
        'entry_source': 'producer',
    }
    if bash_cmd:
        entry['bash_cmd'] = bash_cmd
        entry['display'] = bash_cmd[:80]
    engine.append_dirty_entry(state_dir, session_id, entry)


def _run_script(script, args, env_override=None):
    """Run a script and return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        [sys.executable, script] + args,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ============================================================================
# Fix 1: gate-skip.py refuses deliverable blocks
# ============================================================================

class TestGateSkipDeliverableGuard:
    """gate-skip.py must refuse when deliverables are present (CR-224)."""

    def test_refuses_deliverable_block(self):
        """Deliverable in unreviewed → exit 1 with REFUSED message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            session = 'test-refuse-deliverables'
            # Write a deliverable (a file in repos/)
            _write_dirty_entry(state_dir, session,
                               '/Users/olivermarroquin/workspace/repos/ai-agency-core/scripts/foo.py')

            rc, stdout, stderr = _run_script(GATE_SKIP_SCRIPT, [
                '--session', session,
                '--reason', 'test skip on deliverables',
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
                'REVIEW_GATE_EVENT_LOG': event_log,
            })

            assert rc == 1, f'Expected exit 1, got {rc}. stderr={stderr}'
            assert 'REFUSED' in stderr
            assert 'deliverable' in stderr.lower()
            assert 'log-review-pass.py' in stderr
            assert '--force-deliverables' in stderr

    def test_allows_plumbing_only_block(self):
        """Plumbing-only unreviewed → skip succeeds (exit 0)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            session = 'test-plumbing-only'
            # Write a plumbing entry (relay file)
            _write_dirty_entry(state_dir, session,
                               '/Users/olivermarroquin/workspace/_scratch/relay/_relay-producer-test.md')

            rc, stdout, stderr = _run_script(GATE_SKIP_SCRIPT, [
                '--session', session,
                '--reason', 'test plumbing-only skip',
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
                'REVIEW_GATE_EVENT_LOG': event_log,
            })

            assert rc == 0, f'Expected exit 0, got {rc}. stderr={stderr}'
            assert 'SKIPPED' in stdout

    def test_force_deliverables_overrides_refusal(self):
        """--force-deliverables allows skipping deliverables with LOUD audit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            session = 'test-force-deliverables'
            _write_dirty_entry(state_dir, session,
                               '/Users/olivermarroquin/workspace/repos/ai-agency-core/scripts/foo.py')

            rc, stdout, stderr = _run_script(GATE_SKIP_SCRIPT, [
                '--session', session,
                '--reason', 'emergency operator override test',
                '--force-deliverables',
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
                'REVIEW_GATE_EVENT_LOG': event_log,
            })

            assert rc == 0, f'Expected exit 0, got {rc}. stderr={stderr}'
            assert 'SKIPPED' in stdout
            assert 'FORCED' in stdout

            # Verify LOUD event-log row
            with open(event_log, 'r') as f:
                log_content = f.read()
            assert 'FORCED' in log_content
            assert 'deliverable' in log_content

    def test_force_metrics_entry(self):
        """--force-deliverables writes SKIP-FORCED to metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            session = 'test-force-metrics'
            _write_dirty_entry(state_dir, session,
                               '/Users/olivermarroquin/workspace/repos/ai-agency-core/scripts/bar.py')

            _run_script(GATE_SKIP_SCRIPT, [
                '--session', session,
                '--reason', 'forced metrics test reason',
                '--force-deliverables',
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
                'REVIEW_GATE_EVENT_LOG': event_log,
            })

            metrics_path = os.path.join(state_dir, 'metrics.jsonl')
            assert os.path.isfile(metrics_path), 'metrics.jsonl not written'
            with open(metrics_path, 'r') as f:
                lines = [json.loads(l) for l in f if l.strip()]
            assert any(m.get('outcome') == 'SKIP-FORCED' for m in lines), \
                f'No SKIP-FORCED in metrics: {lines}'

    def test_nothing_to_skip_passes(self):
        """Empty unreviewed set → exit 0 with informational message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            session = 'test-nothing-to-skip'

            rc, stdout, stderr = _run_script(GATE_SKIP_SCRIPT, [
                '--session', session,
                '--reason', 'nothing here test',
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
                'REVIEW_GATE_EVENT_LOG': event_log,
            })

            assert rc == 0
            assert 'Nothing to skip' in stdout


# ============================================================================
# Fix 2: D-09 detection on Write/Edit of .gate-skip*.sh
# ============================================================================

class TestD09WriteDetection:
    """dirty-ledger-track.py must fire D-09 on Write/Edit of skip scripts (CR-224)."""

    def test_d09_fires_on_write_of_skip_script(self):
        """Write tool targeting .gate-skip*.sh → D-09 warning in stderr."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            hook_input = json.dumps({
                'session_id': 'test-d09-write',
                'tool_name': 'Write',
                'tool_input': {
                    'file_path': '/Users/olivermarroquin/workspace/_scratch/skip/.gate-skip-test.sh',
                    'content': '#!/bin/bash\npython3 gate-skip.py ...\n',
                },
            })

            proc = subprocess.run(
                [sys.executable, DIRTY_LEDGER_TRACK_SCRIPT],
                input=hook_input, capture_output=True, text=True,
                env={**os.environ,
                     'REVIEW_GATE_STATE_DIR': state_dir,
                     'REVIEW_GATE_EVENT_LOG': event_log},
            )

            assert 'D-09' in proc.stderr, \
                f'Expected D-09 warning in stderr, got: {proc.stderr}'
            assert 'Write' in proc.stderr

            # Verify event-log row was written
            with open(event_log, 'r') as f:
                log_content = f.read()
            assert 'D-09' in log_content

    def test_d09_fires_on_edit_of_skip_script(self):
        """Edit tool targeting .gate-skip*.sh → D-09 warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            hook_input = json.dumps({
                'session_id': 'test-d09-edit',
                'tool_name': 'Edit',
                'tool_input': {
                    'file_path': '/Users/olivermarroquin/workspace/_scratch/skip/.gate-skip-foo.sh',
                    'old_string': 'old',
                    'new_string': 'new',
                },
            })

            proc = subprocess.run(
                [sys.executable, DIRTY_LEDGER_TRACK_SCRIPT],
                input=hook_input, capture_output=True, text=True,
                env={**os.environ,
                     'REVIEW_GATE_STATE_DIR': state_dir,
                     'REVIEW_GATE_EVENT_LOG': event_log},
            )

            assert 'D-09' in proc.stderr

    def test_no_d09_on_normal_file_write(self):
        """Write of a non-skip-script file → no D-09 warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            event_log = os.path.join(tmpdir, 'event-log.md')
            with open(event_log, 'w') as f:
                f.write('| header |\n')

            hook_input = json.dumps({
                'session_id': 'test-no-d09',
                'tool_name': 'Write',
                'tool_input': {
                    'file_path': '/Users/olivermarroquin/workspace/repos/test/foo.py',
                    'content': 'print("hello")\n',
                },
            })

            proc = subprocess.run(
                [sys.executable, DIRTY_LEDGER_TRACK_SCRIPT],
                input=hook_input, capture_output=True, text=True,
                env={**os.environ,
                     'REVIEW_GATE_STATE_DIR': state_dir,
                     'REVIEW_GATE_EVENT_LOG': event_log},
            )

            assert 'D-09' not in proc.stderr


# ============================================================================
# Fix 3: assert-gate-clear.py exit codes
# ============================================================================

class TestAssertGateClear:
    """assert-gate-clear.py must exit 0 when clear, 1 when blocked (CR-225)."""

    def test_exits_0_when_gate_clear(self):
        """No unreviewed entries → exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            session = 'test-clear'

            rc, stdout, stderr = _run_script(ASSERT_GATE_CLEAR_SCRIPT, [
                '--session', session,
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
            })

            assert rc == 0, f'Expected exit 0, got {rc}'
            assert 'clear' in stdout.lower()

    def test_exits_1_when_blocked(self):
        """Unreviewed entries present → exit 1 with list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            session = 'test-blocked'
            _write_dirty_entry(state_dir, session,
                               '/Users/olivermarroquin/workspace/repos/test/file.py')

            rc, stdout, stderr = _run_script(ASSERT_GATE_CLEAR_SCRIPT, [
                '--session', session,
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
            })

            assert rc == 1, f'Expected exit 1, got {rc}'
            assert 'BLOCKED' in stderr
            assert 'file.py' in stderr

    def test_exits_0_after_review_pass(self):
        """Dirty entry + matching review pass → exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = _make_state_dir(tmpdir)
            session = 'test-reviewed'
            fp = '/Users/olivermarroquin/workspace/repos/test/file.py'
            _write_dirty_entry(state_dir, session, fp)

            # Write a review-pass marker
            time.sleep(0.01)  # ensure timestamp is after dirty entry
            marker = {
                'timestamp': time.time(),
                'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'file_path': fp,
                'verdict': 'PASS',
                'tier': 'full',
                'gate_id': 'G-test',
            }
            engine.append_reviewed_entries(state_dir, session, [marker])

            rc, stdout, stderr = _run_script(ASSERT_GATE_CLEAR_SCRIPT, [
                '--session', session,
            ], env_override={
                'REVIEW_GATE_STATE_DIR': state_dir,
            })

            assert rc == 0, f'Expected exit 0 after review pass, got {rc}'


# ============================================================================
# Fix 5: bash-scope scratch cleanup exemption
# ============================================================================

class TestBashScratchCleanupExemption:
    """Bash rm/mv of _scratch/ files must be plumbing-exempt (CR-226)."""

    def test_rm_scratch_is_exempt(self):
        """rm -f of _scratch/ path → plumbing exempt."""
        result = engine.is_plumbing_exempt(
            'BASH:abc123', 'Bash',
            'rm -f ~/workspace/_scratch/skip/.gate-skip-test.sh')
        assert result is not None, 'Expected plumbing exempt for rm of _scratch/'
        assert 'scratch-cleanup' in result

    def test_rm_multiple_scratch_is_exempt(self):
        """rm of multiple _scratch/ paths → plumbing exempt."""
        result = engine.is_plumbing_exempt(
            'BASH:abc456', 'Bash',
            'rm -f ~/workspace/_scratch/skip/.gate-skip-a.sh ~/workspace/_scratch/skip/.gate-skip-b.sh')
        assert result is not None

    def test_rm_non_scratch_not_exempt(self):
        """rm of non-scratch path → NOT exempt."""
        result = engine.is_plumbing_exempt(
            'BASH:def789', 'Bash',
            'rm -f ~/workspace/repos/ai-agency-core/scripts/foo.py')
        assert result is None, 'Non-scratch rm should NOT be exempt'

    def test_compound_rm_scratch_plus_repo_not_exempt(self):
        """rm _scratch/ && git push → NOT exempt (compound with non-scratch)."""
        result = engine.is_plumbing_exempt(
            'BASH:ghi012', 'Bash',
            'rm -f ~/workspace/_scratch/skip/.gate-skip-test.sh && git push')
        assert result is None, 'Compound command touching non-scratch must NOT be exempt'

    def test_mv_scratch_is_exempt(self):
        """mv within _scratch/ → plumbing exempt."""
        result = engine.is_plumbing_exempt(
            'BASH:jkl345', 'Bash',
            'mv ~/workspace/_scratch/relay/_relay-old.md ~/workspace/_scratch/relay/_relay-new.md')
        assert result is not None

    def test_mv_scratch_to_repo_not_exempt(self):
        """mv from _scratch/ to repo → NOT exempt."""
        result = engine.is_plumbing_exempt(
            'BASH:mno678', 'Bash',
            'mv ~/workspace/_scratch/relay/_relay.md ~/workspace/repos/test/file.md')
        assert result is None, 'mv to non-scratch destination must NOT be exempt'


# ============================================================================
# Run with pytest
# ============================================================================

if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
