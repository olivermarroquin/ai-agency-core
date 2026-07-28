"""Tests for CR-244 gate-skip exemption + CR-253 read-only classifier fixes.

CR-244: reviewer coordination writes (firing-tracker, catch-register,
  event-log, execution logs, verdict files) must not be classified as
  deliverables by gate-skip.py's classify_unreviewed.

CR-253: read-only inspection commands must not create dirty entries:
  - mkdir -p (scratch/state dirs) — no reviewable content artifact
  - mktemp — empty temp files/dirs
  - python3 -m pytest / python3 -m unittest — test runners
  - grep | head, for-loops with grep/curl -I, cat — already worked (regression)

Still-blocks: genuine deliverables (.py, content files) MUST still block.
"""

import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import engine

# Import gate-skip classify_unreviewed
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, SCRIPT_DIR)
import importlib
gate_skip = importlib.import_module('gate-skip')
classify_unreviewed = gate_skip.classify_unreviewed


# ============================================================================
# Helpers
# ============================================================================

def _make_workspace(tmpdir):
    """Create a minimal workspace structure with plumbing whitelist."""
    ws = os.path.join(tmpdir, 'workspace')
    os.makedirs(ws, exist_ok=True)

    # Plumbing whitelist config
    rg_dir = os.path.join(ws, '.review-gate')
    os.makedirs(rg_dir, exist_ok=True)
    config = {
        'schema_version': 1,
        'plumbing_patterns': [
            {
                'pattern': '/workspace/_scratch/',
                'name': 'scratch-file'
            },
            {
                'pattern': r'execution-log-\d{4}-\d{2}-\d{2}.*\.md$',
                'name': 'execution-log'
            },
            {
                'pattern': r'^(python3\s+-m\s+)?pytest\b',
                'name': 'pytest-test-run',
                'scope': 'bash',
                'reviewer_only': True
            },
            {
                'pattern': r'^python3\s+-m\s+unittest\b',
                'name': 'unittest-test-run',
                'scope': 'bash',
                'reviewer_only': True
            },
        ],
        'hard_non_exempt': [
            '04_projects/',
            '05_shared-intelligence/',
            'skills/',
            'repos/',
        ],
    }
    with open(os.path.join(rg_dir, 'plumbing-whitelist.json'), 'w') as f:
        json.dump(config, f)

    # Create second-brain dirs for bookkeeping paths
    meta_dir = os.path.join(ws, 'second-brain', '_meta', 'handoffs')
    os.makedirs(meta_dir, exist_ok=True)

    return ws


def _make_entry(file_path, tool='Write', tier='full', bash_cmd=''):
    """Create a dirty-ledger-style entry dict."""
    entry = {
        'file_path': file_path,
        'tool': tool,
        'tier': tier,
        'timestamp': time.time(),
        'source': 'claude-code',
    }
    if bash_cmd:
        entry['bash_cmd'] = bash_cmd
        entry['display'] = bash_cmd[:80]
    return entry


# ============================================================================
# CR-244: gate-skip classify_unreviewed exemptions
# ============================================================================

class TestCR244GateSkipExemptions:
    """gate-skip.py classify_unreviewed must classify reviewer coordination
    writes as plumbing, not deliverables."""

    @pytest.fixture(autouse=True)
    def setup_workspace(self, tmp_path):
        self.ws = _make_workspace(str(tmp_path))

    def test_firing_tracker_is_plumbing(self):
        """_review-skill-firing-tracker.md → plumbing (bookkeeping)."""
        entry = _make_entry(
            os.path.join(self.ws, 'second-brain', '_meta', 'handoffs',
                         '_review-skill-firing-tracker.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_catch_register_is_plumbing(self):
        """_review-gate-catch-register.md → plumbing (bookkeeping)."""
        entry = _make_entry(
            os.path.join(self.ws, 'second-brain', '_meta', 'handoffs',
                         '_review-gate-catch-register.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_event_log_is_plumbing(self):
        """_event-log.md → plumbing (bookkeeping)."""
        entry = _make_entry(
            os.path.join(self.ws, 'second-brain', '_meta', '_event-log.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_execution_log_is_plumbing(self):
        """execution-log-YYYY-MM-DD-*.md → plumbing (plumbing whitelist)."""
        entry = _make_entry(
            os.path.join(self.ws, 'repos', 'ai-agency-core', '.kos',
                         'execution-logs',
                         'execution-log-2026-07-28-gate-friction.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_scratch_relay_file_is_plumbing(self):
        """_scratch/relay/_relay-*.md → plumbing (plumbing whitelist)."""
        entry = _make_entry(
            os.path.join(self.ws, '_scratch', 'relay',
                         '_relay-producer-gate-friction-fix.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_active_chats_tracker_is_plumbing(self):
        """_active-chats-tracker.md → plumbing (bookkeeping)."""
        entry = _make_entry(
            os.path.join(self.ws, 'second-brain', '_meta', 'handoffs',
                         '_active-chats-tracker.md'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_pytest_bash_is_plumbing(self):
        """python3 -m pytest (reviewer_only whitelist) → plumbing."""
        entry = _make_entry(
            'BASH:abc123', tool='Bash', tier='full',
            bash_cmd='python3 -m pytest tests/')
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(plumbing) == 1
        assert len(deliverables) == 0

    def test_real_deliverable_still_deliverable(self):
        """A genuine .py file → deliverable (NOT exempt)."""
        entry = _make_entry(
            os.path.join(self.ws, 'repos', 'resume-saas', 'app.py'))
        deliverables, plumbing = classify_unreviewed([entry], self.ws)
        assert len(deliverables) == 1
        assert len(plumbing) == 0

    def test_mixed_deliverable_and_plumbing(self):
        """Mix of deliverable + plumbing correctly split."""
        entries = [
            _make_entry(os.path.join(self.ws, 'repos', 'resume-saas', 'app.py')),
            _make_entry(os.path.join(self.ws, 'second-brain', '_meta',
                                     'handoffs',
                                     '_review-skill-firing-tracker.md')),
            _make_entry(os.path.join(self.ws, 'second-brain', '_meta',
                                     '_event-log.md')),
        ]
        deliverables, plumbing = classify_unreviewed(entries, self.ws)
        assert len(deliverables) == 1
        assert len(plumbing) == 2


# ============================================================================
# CR-253: read-only classifier fixes
# ============================================================================

class TestCR253MkdirReadOnly:
    """mkdir creates empty directories — no reviewable artifact."""

    def test_mkdir_p_scratch(self):
        assert engine.is_read_only_bash('mkdir -p ~/workspace/_scratch/review/')

    def test_mkdir_p_review_gate_state(self):
        assert engine.is_read_only_bash('mkdir -p .review-gate/state/')

    def test_mkdir_bare(self):
        assert engine.is_read_only_bash('mkdir some_dir')

    def test_mkdir_p_multiple(self):
        assert engine.is_read_only_bash('mkdir -p dir1 dir2 dir3')

    def test_mkdir_in_compound(self):
        """mkdir -p && grep ... → both read-only."""
        assert engine.is_read_only_bash(
            'mkdir -p ~/workspace/_scratch/test && grep -r "pattern" .')

    def test_mktemp_read_only(self):
        assert engine.is_read_only_bash('mktemp')

    def test_mktemp_d_read_only(self):
        assert engine.is_read_only_bash('mktemp -d')


class TestCR253PytestReadOnly:
    """python3 -m pytest/unittest are inspection, not deliverables."""

    def test_pytest_m_flag(self):
        assert engine.is_read_only_bash('python3 -m pytest tests/')

    def test_pytest_m_verbose(self):
        assert engine.is_read_only_bash('python3 -m pytest -v tests/')

    def test_pytest_m_with_flags(self):
        assert engine.is_read_only_bash(
            'python3 -m pytest --tb=short -q --no-header')

    def test_unittest_m_flag(self):
        assert engine.is_read_only_bash('python3 -m unittest discover')

    def test_unittest_m_specific(self):
        assert engine.is_read_only_bash(
            'python3 -m unittest tests.test_engine')

    def test_cd_and_pytest(self):
        """cd dir && python3 -m pytest → compound, both read-only."""
        assert engine.is_read_only_bash(
            'cd ~/workspace/repos/ai-agency-core && python3 -m pytest')

    def test_python_m_other_NOT_read_only(self):
        """python3 -m http.server is NOT read-only (unknown module)."""
        assert not engine.is_read_only_bash('python3 -m http.server')

    def test_python_m_pip_NOT_read_only(self):
        """python3 -m pip install is NOT read-only."""
        assert not engine.is_read_only_bash('python3 -m pip install requests')


class TestCR253WriteSafeCarveOuts:
    """_is_segment_write_safe mirrors read-only carve-outs."""

    def test_mkdir_write_safe(self):
        assert engine._is_segment_write_safe('mkdir -p /tmp/test')

    def test_mktemp_write_safe(self):
        assert engine._is_segment_write_safe('mktemp -d')

    def test_pytest_write_safe(self):
        assert engine._is_segment_write_safe('python3 -m pytest tests/')

    def test_unittest_write_safe(self):
        assert engine._is_segment_write_safe('python3 -m unittest discover')


class TestCR253AlreadyWorking:
    """Regression: commands that already worked must keep working."""

    def test_grep_pipe_head(self):
        assert engine.is_read_only_bash('grep -r "pattern" . | head -20')

    def test_cat_file(self):
        assert engine.is_read_only_bash('cat ~/workspace/repos/some/file.py')

    def test_curl_I_head_request(self):
        assert engine.is_read_only_bash('curl -I https://example.com')

    def test_for_loop_grep(self):
        assert engine.is_read_only_bash(
            'for f in *.md; do grep -l "pattern" "$f"; done')

    def test_for_loop_curl_I(self):
        assert engine.is_read_only_bash(
            'for url in a b c; do curl -I "$url"; done')


# ============================================================================
# Still-blocks: genuine state-changing commands must NOT be exempted
# ============================================================================

class TestStillBlocks:
    """Ensure genuine state-changing commands are NOT mis-classified."""

    def test_rm_rf_still_blocks(self):
        assert not engine.is_read_only_bash('rm -rf /tmp/important')

    def test_cp_still_blocks(self):
        assert not engine.is_read_only_bash('cp file1 file2')

    def test_mv_still_blocks(self):
        assert not engine.is_read_only_bash('mv old new')

    def test_python_script_still_blocks(self):
        """python3 some_unknown_script.py is NOT read-only."""
        assert not engine.is_read_only_bash('python3 deploy.py')

    def test_curl_output_still_blocks(self):
        assert not engine.is_read_only_bash('curl -o output.html https://x.com')

    def test_wget_still_blocks(self):
        assert not engine.is_read_only_bash('wget https://example.com')

    def test_touch_still_blocks(self):
        assert not engine.is_read_only_bash('touch newfile.txt')

    def test_python_c_with_write_still_blocks(self):
        assert not engine.is_read_only_bash(
            "python3 -c \"open('x.txt','w').write('hack')\"")
