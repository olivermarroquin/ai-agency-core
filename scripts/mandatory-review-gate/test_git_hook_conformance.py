#!/usr/bin/env python3
"""Conformance suite for the git hook adapter (Tier B enforcement).

All tests run against THROWAWAY git repos created in temp dirs.
No live workspace repos are touched.

TEST ISOLATION: Every test creates a fresh temp dir with:
  - A fresh git repo (git init)
  - An isolated REVIEW_GATE_STATE_DIR
  - No connection to production .review-gate/state/

Tests cover:
  - Block-on-skip: unreviewed agent artifact → commit refused
  - Approve-clean: reviewed artifact → commit succeeds
  - Read-only exempt: no dirty entry → commit succeeds
  - Override audited: --no-verify bypasses the hook
  - Installer idempotent: install → re-install → uninstall → re-install
  - OC-16 git-integration: commit-staging-audit runs against real git status

Created by [RGH-2] (2026-06-16).

Usage:
    python3 test_git_hook_conformance.py            # from any directory
    python3 test_git_hook_conformance.py -v          # verbose output
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
ADAPTER = os.path.join(SCRIPTS, 'git_hook_adapter.py')
INSTALLER = os.path.join(SCRIPTS, 'install-git-hooks.sh')
OC16 = os.path.join(SCRIPTS, 'oc-16-commit-staging-audit.py')
LOG = os.path.join(SCRIPTS, 'log-review-pass.py')


def _make_env(state_dir):
    """Build env dict with isolated state dir."""
    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = state_dir
    if 'REVIEW_GATE_EVENT_LOG' not in env:
        env['REVIEW_GATE_EVENT_LOG'] = os.path.join(state_dir, '_event-log-test.md')
    return env


def _init_repo(tmpdir):
    """Create a fresh git repo in tmpdir. Returns repo path."""
    repo = os.path.join(tmpdir, 'repo')
    os.makedirs(repo)
    subprocess.run(['git', 'init'], cwd=repo, capture_output=True, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'],
                   cwd=repo, capture_output=True, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'],
                   cwd=repo, capture_output=True, check=True)
    # Initial commit
    readme = os.path.join(repo, 'README.md')
    with open(readme, 'w') as f:
        f.write('# init\n')
    subprocess.run(['git', 'add', 'README.md'], cwd=repo, capture_output=True, check=True)
    subprocess.run(['git', 'commit', '-m', 'initial'], cwd=repo,
                   capture_output=True, check=True)
    return repo


def _write_dirty_entry(state_dir, session_id, file_path, source='claude-code',
                       tier='full', tool='Write'):
    """Write a dirty entry to the ledger."""
    os.makedirs(state_dir, exist_ok=True)
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': tool,
        'file_path': os.path.realpath(file_path),
        'tier': tier,
        'source': source,
    }
    ledger = os.path.join(state_dir, f'{session_id}-dirty.jsonl')
    with open(ledger, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def _write_reviewed_entry(state_dir, session_id, file_path):
    """Write a review-pass marker."""
    os.makedirs(state_dir, exist_ok=True)
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'file_path': os.path.realpath(file_path),
        'verdict': 'PASS',
        'tier': 'full',
        'gate_id': 'G-default',
        'evidence': 'conformance test review',
        'verdict_file': None,
        'verdict_data': None,
        'findings': None,
    }
    reviewed = os.path.join(state_dir, f'{session_id}-reviewed.jsonl')
    with open(reviewed, 'a') as f:
        f.write(json.dumps(entry) + '\n')


def _run_adapter(repo, state_dir, hook_type='pre-commit'):
    """Run the git hook adapter."""
    return subprocess.run(
        ['python3', ADAPTER, '--hook', hook_type],
        capture_output=True, text=True,
        cwd=repo,
        env=_make_env(state_dir),
    )


class TestGitHookBlockOnSkip(unittest.TestCase):
    """Unreviewed agent-produced artifact → commit blocked."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_block_on_unreviewed_agent_artifact(self):
        """Staged file with dirty entry and no review → exit 1."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# modified by agent\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'test-session',
                           readme)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1, f'Must block: {r.stderr}')
        self.assertIn('COMMIT BLOCKED', r.stderr)
        self.assertIn('README.md', r.stderr)

    def test_block_shows_resolution_instructions(self):
        """Block message includes log-review-pass command."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# modified\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'test-session', readme)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1)
        self.assertIn('log-review-pass.py', r.stderr)
        self.assertIn('--no-verify', r.stderr)


class TestGitHookApproveClean(unittest.TestCase):
    """Reviewed artifact → commit succeeds."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_approve_after_review(self):
        """Staged file with dirty entry AND review → exit 0."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# reviewed content\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'test-session', readme)
        time.sleep(0.05)
        _write_reviewed_entry(self.state_dir, 'test-session', readme)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 0, f'Must approve: {r.stderr}')

    def test_approve_no_dirty_entries(self):
        """Staged file with NO dirty entry → exit 0 (human edit pass-through)."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# human edit\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'No dirty entry = human edit = pass through')

    def test_approve_empty_staging(self):
        """Nothing staged → exit 0."""
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 0)


class TestGitHookSubstrateAgnostic(unittest.TestCase):
    """The git hook catches artifacts from ANY source."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_catches_codex_source(self):
        """Dirty entry with source=codex → still blocked at commit."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# codex edit\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'codex-session', readme,
                           source='codex')
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1,
                         'Git hook must catch codex-source artifacts')

    def test_catches_local_model_source(self):
        """Dirty entry with source=local-model → still blocked."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# local model edit\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'local-session', readme,
                           source='local-model')
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1,
                         'Git hook must catch local-model-source artifacts')

    def test_catches_cross_session(self):
        """Dirty entry from a different session → still caught by git hook."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# cross session\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        # Session A writes, Session B tries to commit
        _write_dirty_entry(self.state_dir, 'session-A', readme)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1,
                         'Git hook must see dirty entries from all sessions')


class TestGitHookMetrics(unittest.TestCase):
    """Git hook logs metrics for approve and block."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_approve_logs_metrics(self):
        """Approve with dirty entries (all reviewed) logs metrics."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# clean\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        # Must have a dirty entry + review so the hook reaches the check
        _write_dirty_entry(self.state_dir, 'test', readme)
        time.sleep(0.05)
        _write_reviewed_entry(self.state_dir, 'test', readme)
        _run_adapter(self.repo, self.state_dir)
        metrics = os.path.join(self.state_dir, 'metrics.jsonl')
        self.assertTrue(os.path.exists(metrics))
        with open(metrics) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'git-hook-approve')
        self.assertIn('hook_type', entry)

    def test_block_logs_metrics(self):
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# dirty\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'test', readme)
        _run_adapter(self.repo, self.state_dir)
        metrics = os.path.join(self.state_dir, 'metrics.jsonl')
        with open(metrics) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'git-hook-block')
        self.assertIn('staged_count', entry)


class TestGitHookReEditBlocks(unittest.TestCase):
    """Re-edit after review must re-block (D-04 parity with CC adapter)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_re_edit_after_review_blocks(self):
        readme = os.path.join(self.repo, 'README.md')
        # First edit + review
        with open(readme, 'w') as f:
            f.write('# v1\n')
        _write_dirty_entry(self.state_dir, 'test', readme)
        time.sleep(0.05)
        _write_reviewed_entry(self.state_dir, 'test', readme)
        # Re-edit (new dirty timestamp after review)
        time.sleep(0.05)
        _write_dirty_entry(self.state_dir, 'test', readme)
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        r = _run_adapter(self.repo, self.state_dir)
        self.assertEqual(r.returncode, 1,
                         'Re-edit after review must re-block')


class TestInstallerIdempotent(unittest.TestCase):
    """Installer is idempotent and reversible."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_creates_hook(self):
        r = subprocess.run([INSTALLER, self.repo],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f'Install failed: {r.stderr}')
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        self.assertTrue(os.path.exists(hook))
        self.assertTrue(os.access(hook, os.X_OK))
        with open(hook) as f:
            content = f.read()
        self.assertIn('REVIEW_GATE_HOOK', content)

    def test_reinstall_idempotent(self):
        subprocess.run([INSTALLER, self.repo], capture_output=True)
        r = subprocess.run([INSTALLER, self.repo],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn('already installed', r.stdout)

    def test_uninstall_removes_hook(self):
        subprocess.run([INSTALLER, self.repo], capture_output=True)
        r = subprocess.run([INSTALLER, '--uninstall', self.repo],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        self.assertFalse(os.path.exists(hook))

    def test_uninstall_preserves_non_gate_hook(self):
        """Uninstall skips hooks it didn't install."""
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "custom hook"\n')
        os.chmod(hook, 0o755)
        r = subprocess.run([INSTALLER, '--uninstall', self.repo],
                           capture_output=True, text=True)
        self.assertIn('NOT the review gate hook', r.stdout)
        self.assertTrue(os.path.exists(hook), 'Must not delete foreign hook')

    def test_install_backs_up_existing_hook(self):
        """Installing over existing hook creates backup."""
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        backup = os.path.join(self.repo, '.git', 'hooks',
                              'pre-commit.pre-review-gate')
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "original"\n')
        os.chmod(hook, 0o755)
        subprocess.run([INSTALLER, self.repo], capture_output=True)
        self.assertTrue(os.path.exists(backup), 'Backup must exist')
        with open(backup) as f:
            self.assertIn('original', f.read())

    def test_uninstall_restores_backup(self):
        """Uninstall restores the backed-up original hook."""
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "original"\n')
        os.chmod(hook, 0o755)
        # Install (creates backup)
        subprocess.run([INSTALLER, self.repo], capture_output=True)
        # Uninstall (restores backup)
        subprocess.run([INSTALLER, '--uninstall', self.repo],
                       capture_output=True)
        self.assertTrue(os.path.exists(hook))
        with open(hook) as f:
            self.assertIn('original', f.read())


class TestRealGitCommitBlocked(unittest.TestCase):
    """Real git commit is blocked/allowed by the installed hook."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-test-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.repo = _init_repo(self.tmpdir)
        # Install the hook
        subprocess.run([INSTALLER, self.repo], capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _git_commit(self, msg):
        env = _make_env(self.state_dir)
        return subprocess.run(
            ['git', 'commit', '-m', msg],
            capture_output=True, text=True,
            cwd=self.repo, env=env)

    def test_real_commit_blocked(self):
        """Real git commit refused when unreviewed agent file staged."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# agent edit\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'agent-session', readme)
        r = self._git_commit('should fail')
        self.assertNotEqual(r.returncode, 0,
                            f'Commit must be refused: {r.stdout}')
        self.assertIn('COMMIT BLOCKED', r.stderr)

    def test_real_commit_succeeds_after_review(self):
        """Real git commit succeeds when agent file is reviewed."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# reviewed edit\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'agent-session', readme)
        time.sleep(0.05)
        _write_reviewed_entry(self.state_dir, 'agent-session', readme)
        r = self._git_commit('should succeed')
        self.assertEqual(r.returncode, 0,
                         f'Commit must succeed after review: {r.stderr}')

    def test_real_commit_no_verify_bypass(self):
        """--no-verify bypasses the hook."""
        readme = os.path.join(self.repo, 'README.md')
        with open(readme, 'w') as f:
            f.write('# bypass\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        _write_dirty_entry(self.state_dir, 'agent-session', readme)
        env = _make_env(self.state_dir)
        r = subprocess.run(
            ['git', 'commit', '--no-verify', '-m', 'bypass'],
            capture_output=True, text=True,
            cwd=self.repo, env=env)
        self.assertEqual(r.returncode, 0,
                         f'--no-verify must bypass: {r.stderr}')


class TestOC16GitIntegration(unittest.TestCase):
    """OC-16 commit-staging audit against real git repos.
    Absorbed from [RGH-FIN]'s handoff — OC-16 needs a real git-repo test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh2-oc16-')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        os.makedirs(self.state_dir, exist_ok=True)
        self.repo = _init_repo(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_oc16(self, ledger_path):
        return subprocess.run(
            ['python3', OC16, '--ledger', ledger_path,
             '--repo-root', self.repo],
            capture_output=True, text=True,
            cwd=self.repo,
            env=_make_env(self.state_dir),
        )

    def test_staged_and_touched_passes(self):
        """File both staged and in dirty-ledger → PASS."""
        readme = os.path.join(self.repo, 'README.md')
        real_readme = os.path.realpath(readme)
        with open(readme, 'w') as f:
            f.write('# modified\n')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo,
                       capture_output=True, check=True)
        # Write dirty ledger
        ledger = os.path.join(self.state_dir, 'test-dirty.jsonl')
        with open(ledger, 'w') as f:
            f.write(json.dumps({
                'timestamp': time.time(),
                'tool': 'Write',
                'file_path': real_readme,
                'tier': 'full',
                'source': 'claude-code',
            }) + '\n')
        r = self._run_oc16(ledger)
        self.assertEqual(r.returncode, 0, f'Must PASS: {r.stdout}')
        data = json.loads(r.stdout)
        self.assertEqual(data['verdict'], 'PASS')

    def test_staged_not_touched_fails(self):
        """File staged but NOT in dirty-ledger → FAIL (foreign work)."""
        # Create a new file and stage it
        newfile = os.path.join(self.repo, 'foreign.md')
        with open(newfile, 'w') as f:
            f.write('# foreign\n')
        subprocess.run(['git', 'add', 'foreign.md'], cwd=self.repo,
                       capture_output=True, check=True)
        # Empty dirty ledger (file NOT in it)
        ledger = os.path.join(self.state_dir, 'test-dirty.jsonl')
        with open(ledger, 'w') as f:
            f.write('')  # empty
        r = self._run_oc16(ledger)
        self.assertEqual(r.returncode, 1, f'Must FAIL: {r.stdout}')
        data = json.loads(r.stdout)
        self.assertEqual(data['verdict'], 'FAIL')
        self.assertTrue(any(f['issue'] == 'staged-not-touched'
                            for f in data['findings']))

    def test_touched_not_staged_fails(self):
        """File in dirty-ledger but not staged → FAIL."""
        # Touch a file but don't stage it
        newfile = os.path.join(self.repo, 'forgot.md')
        real_newfile = os.path.realpath(newfile)
        with open(newfile, 'w') as f:
            f.write('# forgot to stage\n')
        # Write dirty ledger with this file
        ledger = os.path.join(self.state_dir, 'test-dirty.jsonl')
        with open(ledger, 'w') as f:
            f.write(json.dumps({
                'timestamp': time.time(),
                'tool': 'Write',
                'file_path': real_newfile,
                'tier': 'full',
                'source': 'claude-code',
            }) + '\n')
        r = self._run_oc16(ledger)
        self.assertEqual(r.returncode, 1, f'Must FAIL: {r.stdout}')
        data = json.loads(r.stdout)
        self.assertEqual(data['verdict'], 'FAIL')
        self.assertTrue(any(f['issue'] == 'touched-not-staged'
                            for f in data['findings']))

    def test_build_artifact_staged_fails(self):
        """Build artifact (__pycache__) staged → FAIL."""
        pycache_dir = os.path.join(self.repo, '__pycache__')
        os.makedirs(pycache_dir)
        pyc = os.path.join(pycache_dir, 'test.pyc')
        with open(pyc, 'w') as f:
            f.write('bytecode')
        subprocess.run(['git', 'add', '-f', '__pycache__/test.pyc'],
                       cwd=self.repo, capture_output=True, check=True)
        ledger = os.path.join(self.state_dir, 'test-dirty.jsonl')
        with open(ledger, 'w') as f:
            f.write(json.dumps({
                'timestamp': time.time(),
                'tool': 'Write',
                'file_path': os.path.realpath(pyc),
                'tier': 'full',
            }) + '\n')
        r = self._run_oc16(ledger)
        self.assertEqual(r.returncode, 1, f'Must FAIL: {r.stdout}')
        data = json.loads(r.stdout)
        self.assertTrue(any(f['issue'] == 'build-artifact'
                            for f in data['findings']))


if __name__ == '__main__':
    unittest.main()
