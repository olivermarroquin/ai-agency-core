#!/usr/bin/env python3
"""Tests for commit-time bookkeeping + plumbing exemptions in git_hook_adapter.

Verifies that the git hook adapter (pre-commit) applies the SAME exemptions
as the Stop hook (engine.check_gate):
  - Bookkeeping files (CR-161): BOOKKEEPING_BASENAMES are exempt
  - Plumbing files (CR-219): plumbing-whitelist patterns are exempt
  - Real deliverables: still blocked (no widened exemptions)
  - Fail safe: exemption check errors → treat as non-exempt (block)

Live occurrence 2026-07-26: operator blocked committing firing-tracker rows
written by a concurrent pair's reviewer.

Created by commit-exempt producer (2026-07-26).
"""

import json
import os
import sys
import tempfile
import time
import unittest

SCRIPT_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, SCRIPT_DIR)

import engine
import git_hook_adapter


class _IsolatedStateBase(unittest.TestCase):
    """Base class providing isolated state + workspace dirs per test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix='commit-exempt-test-')
        self.state_dir = os.path.join(self._tmpdir, 'state')
        os.makedirs(self.state_dir)
        self.workspace = os.path.join(self._tmpdir, 'workspace')
        os.makedirs(self.workspace)

        # Reset engine caches so plumbing whitelist picks up test config
        engine._reset_plumbing_cache()
        engine._plumbing_regex_cache.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        engine._reset_plumbing_cache()
        engine._plumbing_regex_cache.clear()

    def _write_dirty_entry(self, session_id, file_path, tool='Write',
                           source='claude-code', extra=None):
        """Write a dirty-ledger entry for the given session."""
        ledger_path = os.path.join(self.state_dir,
                                   f'{session_id}-dirty.jsonl')
        entry = {
            'timestamp': time.time(),
            'file_path': file_path,
            'tool': tool,
            'source': source,
            'tier': 'full',
        }
        if extra:
            entry.update(extra)
        with open(ledger_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    def _write_plumbing_config(self, patterns):
        """Write a plumbing-whitelist.json in the test workspace."""
        config_dir = os.path.join(self.workspace, '.review-gate')
        os.makedirs(config_dir, exist_ok=True)
        config = {
            'schema_version': 1,
            'plumbing_patterns': patterns,
        }
        with open(os.path.join(config_dir, 'plumbing-whitelist.json'), 'w') as f:
            json.dump(config, f)


class TestBookkeepingExemption(_IsolatedStateBase):
    """CR-161: Bookkeeping basenames must NOT block at commit time."""

    def test_firing_tracker_not_blocked(self):
        """The exact live failure: _review-skill-firing-tracker.md blocked."""
        fp = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs',
                          '_review-skill-firing-tracker.md')
        self._write_dirty_entry('other-session', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [],
                         'Bookkeeping file should NOT block commit')

    def test_event_log_not_blocked(self):
        fp = os.path.join(self.workspace, 'second-brain', '_meta',
                          '_event-log.md')
        self._write_dirty_entry('sess-1', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])

    def test_active_chats_tracker_not_blocked(self):
        fp = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs',
                          '_active-chats-tracker.md')
        self._write_dirty_entry('sess-2', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])

    def test_recently_closed_not_blocked(self):
        fp = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs',
                          '_recently-closed.md')
        self._write_dirty_entry('sess-3', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])

    def test_changelog_not_blocked(self):
        fp = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs',
                          '_active-chats-tracker-changelog.md')
        self._write_dirty_entry('sess-4', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])

    def test_all_five_bookkeeping_basenames(self):
        """All BOOKKEEPING_BASENAMES should be exempt."""
        for basename in engine.BOOKKEEPING_BASENAMES:
            with self.subTest(basename=basename):
                fp = os.path.join(self.workspace, 'some', 'path', basename)
                self._write_dirty_entry(f'sess-{basename}', fp)
                staged = {fp}
                result = git_hook_adapter.check_staged_files(
                    staged, self.state_dir, workspace_root=self.workspace)
                self.assertEqual(result, [],
                                 f'{basename} should be exempt')


class TestPlumbingExemption(_IsolatedStateBase):
    """CR-219: Plumbing-whitelist patterns must NOT block at commit time."""

    def test_relay_file_not_blocked(self):
        self._write_plumbing_config([
            {'pattern': '_scratch/relay/_relay-.*\\.md$', 'name': 'relay-file'}
        ])
        fp = os.path.join(self.workspace, '_scratch', 'relay',
                          '_relay-producer-test.md')
        self._write_dirty_entry('sess-relay', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [],
                         'Plumbing relay file should NOT block commit')

    def test_spawn_file_not_blocked(self):
        self._write_plumbing_config([
            {'pattern': '_scratch/spawn/_spawn-.*\\.md$', 'name': 'spawn-file'}
        ])
        fp = os.path.join(self.workspace, '_scratch', 'spawn',
                          '_spawn-producer-task.md')
        self._write_dirty_entry('sess-spawn', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])

    def test_commit_script_not_blocked(self):
        self._write_plumbing_config([
            {'pattern': '.+/\\.commit\\.sh$', 'name': 'commit-script'}
        ])
        fp = os.path.join(self.workspace, 'repos', 'some-repo', '.commit.sh')
        self._write_dirty_entry('sess-commit', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])


class TestDeliverableStillBlocks(_IsolatedStateBase):
    """Real deliverables must still block — no widened exemptions."""

    def test_python_file_blocks(self):
        fp = os.path.join(self.workspace, 'repos', 'project', 'app.py')
        self._write_dirty_entry('sess-py', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1,
                         'Unreviewed .py file MUST block commit')

    def test_tsx_file_blocks(self):
        fp = os.path.join(self.workspace, 'repos', 'project', 'Component.tsx')
        self._write_dirty_entry('sess-tsx', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1)

    def test_markdown_content_file_blocks(self):
        """A .md file that is NOT a bookkeeping basename should block."""
        fp = os.path.join(self.workspace, 'docs', 'readme.md')
        self._write_dirty_entry('sess-md', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1,
                         'Non-bookkeeping .md MUST block')

    def test_reviewed_file_passes(self):
        """A reviewed file should NOT block regardless of type."""
        fp = os.path.join(self.workspace, 'repos', 'project', 'app.py')
        self._write_dirty_entry('sess-rev', fp)
        # Write a reviewed entry with a later timestamp
        reviewed_path = os.path.join(self.state_dir,
                                     'sess-rev-reviewed.jsonl')
        marker = {
            'timestamp': time.time() + 1,
            'file_path': fp,
            'verdict': 'PASS',
        }
        with open(reviewed_path, 'w') as f:
            f.write(json.dumps(marker) + '\n')

        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])


class TestMixedStagedFiles(_IsolatedStateBase):
    """Mixed staging: bookkeeping + plumbing pass through, deliverables block."""

    def test_mixed_bookkeeping_and_deliverable(self):
        bk_fp = os.path.join(self.workspace, 'second-brain', '_meta',
                             '_event-log.md')
        deliv_fp = os.path.join(self.workspace, 'repos', 'project', 'main.py')
        self._write_dirty_entry('sess-mix', bk_fp)
        self._write_dirty_entry('sess-mix', deliv_fp)
        staged = {bk_fp, deliv_fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1, 'Only the deliverable should block')
        self.assertEqual(result[0]['file_path'], deliv_fp)

    def test_mixed_plumbing_and_deliverable(self):
        self._write_plumbing_config([
            {'pattern': '_scratch/relay/_relay-.*\\.md$', 'name': 'relay-file'}
        ])
        plumb_fp = os.path.join(self.workspace, '_scratch', 'relay',
                                '_relay-producer-x.md')
        deliv_fp = os.path.join(self.workspace, 'repos', 'project', 'app.js')
        self._write_dirty_entry('sess-mix2', plumb_fp)
        self._write_dirty_entry('sess-mix2', deliv_fp)
        staged = {plumb_fp, deliv_fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['file_path'], deliv_fp)

    def test_all_bookkeeping_no_block(self):
        """If ALL staged files are bookkeeping, commit should pass."""
        fps = []
        for basename in list(engine.BOOKKEEPING_BASENAMES)[:3]:
            fp = os.path.join(self.workspace, 'path', basename)
            self._write_dirty_entry('sess-allbk', fp)
            fps.append(fp)
        staged = set(fps)
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [])


class TestFailSafe(_IsolatedStateBase):
    """Exemption errors must fail closed (block, not pass)."""

    def test_bookkeeping_check_error_fails_closed(self):
        """If is_bookkeeping_entry raises, file should still block."""
        fp = os.path.join(self.workspace, 'some', 'file.py')
        self._write_dirty_entry('sess-err', fp)
        staged = {fp}

        original = engine.is_bookkeeping_entry
        def boom(*a, **kw):
            raise RuntimeError('simulated error')
        engine.is_bookkeeping_entry = boom
        try:
            result = git_hook_adapter.check_staged_files(
                staged, self.state_dir, workspace_root=self.workspace)
            self.assertEqual(len(result), 1,
                             'Must block when exemption check errors')
        finally:
            engine.is_bookkeeping_entry = original

    def test_plumbing_check_error_fails_closed(self):
        """If is_plumbing_exempt raises, file should still block."""
        fp = os.path.join(self.workspace, 'some', 'file.py')
        self._write_dirty_entry('sess-err2', fp)
        staged = {fp}

        original = engine.is_plumbing_exempt
        def boom(*a, **kw):
            raise RuntimeError('simulated plumbing error')
        engine.is_plumbing_exempt = boom
        try:
            result = git_hook_adapter.check_staged_files(
                staged, self.state_dir, workspace_root=self.workspace)
            self.assertEqual(len(result), 1)
        finally:
            engine.is_plumbing_exempt = original


class TestCrossSessionExemption(_IsolatedStateBase):
    """Exemptions work even when dirty entry is from a different session."""

    def test_bookkeeping_from_other_session(self):
        """The exact bug: another session wrote the firing tracker."""
        fp = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs',
                          '_review-skill-firing-tracker.md')
        self._write_dirty_entry('reviewer-session-abc', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(result, [],
                         'Bookkeeping from another session must be exempt')

    def test_deliverable_from_other_session_still_blocks(self):
        fp = os.path.join(self.workspace, 'repos', 'project', 'main.py')
        self._write_dirty_entry('other-session-xyz', fp)
        staged = {fp}
        result = git_hook_adapter.check_staged_files(
            staged, self.state_dir, workspace_root=self.workspace)
        self.assertEqual(len(result), 1,
                         'Deliverable from other session MUST block')


if __name__ == '__main__':
    unittest.main()
