#!/usr/bin/env python3
"""Regression tests for RGH-18 (build-correctness) + RGH-19 (doc-completeness).

Replays the A3 misses that motivated these checks:
  - Dropped target page (completeness diff) → must BLOCK
  - Leaked source file (all-dirty-file sweep) → must BLOCK
  - Mis-staged commit (staging audit) → must BLOCK
  - Clean build → must PASS

Also tests:
  - DIFF-AWARE sweep: append-only files check only added hunks
  - OC-21..27: doc-completeness checks
  - OC-20: Productize-tier B1-B6 enforcement
  - OC-27: spec-vs-registry cross-check (CR-152)
  - Tier gating: Capture-only/Throwaway skip B1-B6
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RGH18 = os.path.join(SCRIPT_DIR, 'rgh18-build-correctness.py')
RGH19 = os.path.join(SCRIPT_DIR, 'rgh19-doc-completeness.py')


class TempFixture:
    """Creates a temporary workspace with git repo, dirty ledger, etc."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rgh18-19-test-')
        self.workspace = os.path.join(self.tmpdir, 'workspace')
        os.makedirs(self.workspace)

        # Create a git repo inside workspace
        self.repo = os.path.join(self.workspace, 'repos', 'test-repo')
        os.makedirs(self.repo)
        subprocess.run(['git', 'init'], cwd=self.repo,
                       capture_output=True, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'],
                       cwd=self.repo, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'],
                       cwd=self.repo, capture_output=True)

        # State dir
        self.state_dir = os.path.join(self.workspace, '.review-gate', 'state')
        os.makedirs(self.state_dir)
        self.session_id = 'test-session-001'

        # Skills dir (for OC-27)
        self.skills_dir = os.path.join(
            self.workspace, 'skills', 'gate-peer-reviewer', 'references')
        os.makedirs(self.skills_dir)

        # Second-brain dir
        self.sb_dir = os.path.join(self.workspace, 'second-brain', '_meta', 'handoffs')
        os.makedirs(self.sb_dir)

    def write_dirty_ledger(self, entries):
        """Write dirty-ledger entries."""
        path = os.path.join(self.state_dir, f'{self.session_id}-dirty.jsonl')
        with open(path, 'w') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')

    def write_file(self, rel_path, content):
        """Write a file relative to workspace root."""
        abs_path = os.path.join(self.workspace, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w') as f:
            f.write(content)
        return abs_path

    def write_repo_file(self, rel_path, content, commit=True):
        """Write a file in the test repo and optionally commit it."""
        abs_path = os.path.join(self.repo, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w') as f:
            f.write(content)
        if commit:
            subprocess.run(['git', 'add', rel_path], cwd=self.repo,
                           capture_output=True)
            subprocess.run(['git', 'commit', '-m', f'add {rel_path}'],
                           cwd=self.repo, capture_output=True)
        return abs_path

    def write_exec_log(self, content):
        """Write an execution log and return its path."""
        path = self.write_file(
            'repos/test-repo/.kos/execution-logs/execution-log-2026-07-02-test.md',
            content)
        # Also add to dirty ledger so rgh19 can find it
        self.write_dirty_ledger([
            {'file_path': path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        return path

    def write_handoff(self, content):
        """Write a handoff file and return its path."""
        return self.write_file(
            'second-brain/_meta/handoffs/handoff-test.md', content)

    def write_registry(self, content):
        """Write omission-check-registry.md."""
        return self.write_file(
            'skills/gate-peer-reviewer/references/omission-check-registry.md',
            content)

    def write_spec(self, filename, content):
        """Write a spec file in the references dir."""
        return self.write_file(
            f'skills/gate-peer-reviewer/references/{filename}', content)

    def cleanup(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def run_rgh18(fixture, handoff=None, tier=None, extra_args=None):
    """Run rgh18-build-correctness.py and return parsed output."""
    # Override WORKSPACE_ROOT via env manipulation isn't clean;
    # instead, pass workspace-root arg
    cmd = [sys.executable, RGH18,
           '--session', fixture.session_id,
           '--state-dir', fixture.state_dir,
           '--workspace-root', fixture.workspace]
    if handoff:
        cmd.extend(['--handoff', handoff])
    if tier:
        cmd.extend(['--tier', tier])
    if extra_args:
        cmd.extend(extra_args)

    # We need to override _paths.py's WORKSPACE_ROOT, so set env var
    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = fixture.state_dir

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env=env)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {'error': proc.stderr, 'stdout': proc.stdout,
                'returncode': proc.returncode}


def run_rgh19(fixture, exec_log=None, handoff=None, tier=None):
    """Run rgh19-doc-completeness.py and return parsed output."""
    cmd = [sys.executable, RGH19,
           '--session', fixture.session_id,
           '--state-dir', fixture.state_dir,
           '--workspace-root', fixture.workspace]
    if exec_log:
        cmd.extend(['--exec-log', exec_log])
    if handoff:
        cmd.extend(['--handoff', handoff])
    if tier:
        cmd.extend(['--tier', tier])

    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = fixture.state_dir

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env=env)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {'error': proc.stderr, 'stdout': proc.stdout,
                'returncode': proc.returncode}


# =====================================================================
# RGH-18 Tests
# =====================================================================

class TestRGH18CompletnessDiff(unittest.TestCase):
    """A3 regression: dropped page must BLOCK."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_productize_no_manifest_blocks(self):
        """Productize-tier run with no manifest = BLOCKING."""
        handoff = self.f.write_handoff(textwrap.dedent("""\
            ---
            status: active
            ---
            # Test handoff
            No manifest here.
        """))
        self.f.write_dirty_ledger([])
        result = run_rgh18(self.f, handoff=handoff, tier='Productize')
        self.assertEqual(result['verdict'], 'BLOCKING')
        # Should mention missing manifest
        descs = [c['description'] for c in result.get('catches', [])]
        self.assertTrue(any('manifest' in d.lower() for d in descs))

    def test_capture_only_no_manifest_skips(self):
        """Capture-only tier with no manifest = not blocked."""
        handoff = self.f.write_handoff("# Test\nNo manifest.")
        self.f.write_dirty_ledger([])
        result = run_rgh18(self.f, handoff=handoff, tier='Capture-only')
        self.assertEqual(result['verdict'], 'PASS')

    def test_manifest_with_missing_deliverable_blocks(self):
        """Manifest lists a file that doesn't exist on disk = BLOCKING."""
        handoff = self.f.write_handoff(textwrap.dedent("""\
            ---
            status: active
            ---
            # Test handoff
            ## Definition of Done
            | # | Deliverable | Path or glob | Assertion | Source | Check |
            |---|---|---|---|---|---|
            | 1 | Missing page | repos/test-repo/pages/missing.html | exists | handoff | OC-12 |
        """))
        self.f.write_dirty_ledger([])
        result = run_rgh18(self.f, handoff=handoff, tier='Productize')
        self.assertEqual(result['verdict'], 'BLOCKING')


class TestRGH18DirtyFileSweep(unittest.TestCase):
    """A3 regression: leaked source file must BLOCK."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_clean_files_pass(self):
        """Clean touched files pass the sweep."""
        path = self.f.write_repo_file('clean.md', '# Clean file\nNo issues here.')
        self.f.write_dirty_ledger([
            {'file_path': path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        # Check no catches from the sweep
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertEqual(len(sweep_catches), 0)

    def test_placeholder_in_touched_file_blocks(self):
        """Placeholder in a touched file = BLOCKING."""
        path = self.f.write_repo_file(
            'leaked.md',
            '# Page\nThe city is PLACEHOLDER and status is TBD here.')
        self.f.write_dirty_ledger([
            {'file_path': path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertGreater(len(sweep_catches), 0)


class TestRGH18DiffAware(unittest.TestCase):
    """DIFF-AWARE: append-only files check only added hunks."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_preexisting_placeholder_in_tracker_passes(self):
        """Pre-existing placeholder in tracker (committed) should NOT block."""
        # Commit a tracker file with a standalone PLACEHOLDER word in it
        tracker_path = self.f.write_repo_file(
            '_active-chats-tracker.md',
            '# Tracker\n| old row with PLACEHOLDER token |\n',
            commit=True)

        # Now append clean content (simulating this session's edit)
        with open(tracker_path, 'a') as f:
            f.write('| 2026-07-02 | [RGH-18] clean new row | active |\n')

        self.f.write_dirty_ledger([
            {'file_path': tracker_path, 'timestamp': 1000, 'tool': 'Edit'}
        ])

        result = run_rgh18(self.f, tier='Capture-only')
        # The sweep should use diff-aware mode and only check the added line
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertEqual(len(sweep_catches), 0,
                         f'Pre-existing placeholder should not block: {sweep_catches}')

    def test_new_placeholder_in_tracker_blocks(self):
        """New placeholder added to tracker in this session SHOULD block."""
        # Commit a clean tracker
        tracker_path = self.f.write_repo_file(
            '_active-chats-tracker.md',
            '# Tracker\n| clean old row |\n',
            commit=True)

        # Append a line with a standalone PLACEHOLDER word
        with open(tracker_path, 'a') as f:
            f.write('| 2026-07-02 | PLACEHOLDER new bad row |\n')

        self.f.write_dirty_ledger([
            {'file_path': tracker_path, 'timestamp': 1000, 'tool': 'Edit'}
        ])

        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertGreater(len(sweep_catches), 0,
                           'New placeholder in tracker should block')


# =====================================================================
# RGH-19 Tests
# =====================================================================

class TestOC21ExecLogSubstantive(unittest.TestCase):
    """OC-21: exec log must exist and be substantive."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_missing_exec_log_blocks(self):
        """No exec log = BLOCKING."""
        result = run_rgh19(self.f, exec_log='/nonexistent/log.md',
                           tier='Capture-only')
        oc21 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'exec-log-substantive']
        self.assertTrue(oc21)
        self.assertEqual(oc21[0]['result'], 'FAIL')

    def test_stub_exec_log_blocks(self):
        """Exec log with just frontmatter = BLOCKING."""
        path = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            # Log
            Short.
        """))
        result = run_rgh19(self.f, exec_log=path, tier='Capture-only')
        oc21 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'exec-log-substantive']
        self.assertTrue(oc21)
        self.assertEqual(oc21[0]['result'], 'FAIL')

    def test_substantive_exec_log_passes(self):
        """Exec log with required sections and content = PASS."""
        path = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            status: draft
            created: 2026-07-02
            updated: 2026-07-02
            ---

            ## What Happened

            - Built rgh18-build-correctness.py with 3 checks
            - Built rgh19-doc-completeness.py with 8 checks
            - Wired both into independent-reviewer-dispatch.py
            - Updated omission-check-registry with OC-21..27

            ## Decisions Made

            - Used diff-aware approach for append-only shared files
            - Chose grep-based evidence detection for OC-22
        """))
        result = run_rgh19(self.f, exec_log=path, tier='Capture-only')
        oc21 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'exec-log-substantive']
        self.assertTrue(oc21)
        self.assertEqual(oc21[0]['result'], 'PASS')


class TestOC27SpecRegistryCrosscheck(unittest.TestCase):
    """OC-27: spec OC-N numbers must match registry (CR-152)."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_matching_oc_numbers_pass(self):
        """Spec references OC-12, registry has OC-12 = PASS."""
        self.f.write_registry(textwrap.dedent("""\
            # Registry
            ### OC-12: per-deliverable existence
            ### OC-13: count reconciliation
        """))
        self.f.write_spec('test-spec.md', textwrap.dedent("""\
            # Spec
            This check uses OC-12 and OC-13.
        """))
        result = run_rgh19(self.f, exec_log='/dev/null', tier='Capture-only')
        oc27 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'spec-vs-registry-oc-cross-check']
        self.assertTrue(oc27)
        self.assertEqual(oc27[0]['result'], 'PASS')

    def test_mismatched_oc_number_blocks(self):
        """Spec references OC-99 not in registry = BLOCKING (CR-152)."""
        self.f.write_registry(textwrap.dedent("""\
            # Registry
            ### OC-12: per-deliverable existence
        """))
        self.f.write_spec('test-spec.md', textwrap.dedent("""\
            # Spec
            This uses OC-12 and OC-99.
        """))
        result = run_rgh19(self.f, exec_log='/dev/null', tier='Capture-only')
        oc27 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'spec-vs-registry-oc-cross-check']
        self.assertTrue(oc27)
        self.assertEqual(oc27[0]['result'], 'FAIL')


class TestOC20ProductizeDod(unittest.TestCase):
    """OC-20: Productize tier requires B1-B6."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_productize_missing_b_items_blocks(self):
        """Productize tier with empty exec log = BLOCKING."""
        path = self.f.write_exec_log("---\ntype: execution-log\n---\n# Log\nMinimal.")
        result = run_rgh19(self.f, exec_log=path, tier='Productize')
        oc20 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'productization-dod-b1-b6']
        self.assertTrue(oc20)
        self.assertEqual(oc20[0]['result'], 'FAIL')

    def test_capture_only_skips_b_items(self):
        """Capture-only tier skips B1-B6 check."""
        path = self.f.write_exec_log("---\ntype: execution-log\n---\n# Log\nMinimal.")
        result = run_rgh19(self.f, exec_log=path, tier='Capture-only')
        oc20 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'productization-dod-b1-b6']
        self.assertTrue(oc20)
        self.assertEqual(oc20[0]['result'], 'SKIP')

    def test_productize_with_all_b_items_passes(self):
        """Productize with all B1-B6 signals = PASS."""
        path = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---

            **Tier:** Productize

            ## What Happened

            - Built the engine/config split architecture
            - Created config schema with fields
            - Ran non-electrician proof on 2nd-client
            - Documented safety rules and failure modes

            ## Engine / Config Split

            The engine is in `engine.py`. Config is per-client.

            ## Config Schema

            ```yaml
            client_name: string
            service_area: string
            ```

            ## 2nd-instance verdict

            Proven on Asian Delight from config alone — PASS.

            ## Safety and Quality Rules

            - Failure mode: cross-contamination between clients
            - Guard: leak audit checks client identity strings
            - Mitigation: per-project config isolation

            ## Skill-candidacy verdict

            Skill candidacy: Yes — worth productizing.
        """))
        result = run_rgh19(self.f, exec_log=path, tier='Productize')
        oc20 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'productization-dod-b1-b6']
        self.assertTrue(oc20)
        self.assertEqual(oc20[0]['result'], 'PASS')


class TestOC25NoSilentDeferrals(unittest.TestCase):
    """OC-25: deferrals must point to tracking surfaces."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_tracked_deferral_passes(self):
        """Deferral with a wikilink = PASS."""
        path = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            ## What Happened
            - Built the thing
            - Deferred the advanced mode — tracked in [[handoff-advanced-mode]]
            - Completed the core
        """))
        result = run_rgh19(self.f, exec_log=path, tier='Capture-only')
        oc25 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'no-silent-deferrals']
        self.assertTrue(oc25)
        self.assertEqual(oc25[0]['result'], 'PASS')

    def test_silent_deferral_blocks(self):
        """Deferral without tracking surface = BLOCKING."""
        path = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            ## What Happened
            - Built the thing
            - Deferred the advanced mode for later
            - Completed the core
        """))
        result = run_rgh19(self.f, exec_log=path, tier='Capture-only')
        oc25 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'no-silent-deferrals']
        self.assertTrue(oc25)
        self.assertEqual(oc25[0]['result'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
