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


# =====================================================================
# CR-160 Calibration Fixtures (RGH-18/19-CAL)
# Each class has a false-positive (must NOT fire) + true-positive (must fire)
# =====================================================================

class TestCR160_OC24_DateMisParse(unittest.TestCase):
    """CR-160 class 1: OC-24 must parse filing date from column 2 only."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_resolution_date_does_not_fire(self):
        """CR row with today in resolution column but old filing date → NO fire."""
        import time
        today = time.strftime('%Y-%m-%d')

        # Write a catch register with a CR filed on an old date,
        # but resolved today (today's date appears in a later column)
        register_content = textwrap.dedent(f"""\
            # Catch Register
            | CR | Filed | Surface | Severity | Description | Status |
            |---|---|---|---|---|---|
            | CR-010 | 2026-06-18 | RGH-19/OC-24 | blocking | old catch | Resolved ({today}) |
            | CR-054 | 2026-06-22 | RGH-18/sweep | blocking | another old | Resolved ({today}) |
        """)
        self.f.write_file(
            'second-brain/_meta/handoffs/_review-gate-catch-register.md',
            register_content)

        # Write exec log that does NOT mention CR-010 or CR-054
        exec_log = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            status: draft
            created: 2026-07-02
            updated: 2026-07-02
            ---
            ## What Happened
            - Calibrated the gate checks
            - Fixed false positives
            - Resolved old CRs
        """))

        result = run_rgh19(self.f, exec_log=exec_log, tier='Capture-only')
        oc24 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'catches-referenced']
        self.assertTrue(oc24)
        # Should NOT fail — those CRs were filed on old dates, not today
        self.assertNotEqual(oc24[0]['result'], 'FAIL',
                            'OC-24 must not fire on CRs resolved today but filed earlier')

    def test_true_positive_cr_filed_today_missing_from_log(self):
        """CR genuinely filed today BY THIS SESSION and absent from exec log → MUST fire.

        CR-166 update: OC-24 now scopes by session_id in the "surfaced by"
        column (col 3). The fixture must include the session_id to match.
        """
        import time
        today = time.strftime('%Y-%m-%d')

        register_content = textwrap.dedent(f"""\
            # Catch Register
            | CR | Filed | Surfaced by | Surface | Severity | Description | Status |
            |---|---|---|---|---|---|---|
            | CR-999 | {today} | {self.f.session_id} / this-run | RGH-18/sweep | blocking | new catch today | Open |
        """)
        self.f.write_file(
            'second-brain/_meta/handoffs/_review-gate-catch-register.md',
            register_content)

        # Exec log does NOT mention CR-999
        exec_log = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            ## What Happened
            - Did some work
            - No CR references here
            - Just regular build steps
        """))

        result = run_rgh19(self.f, exec_log=exec_log, tier='Capture-only')
        oc24 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'catches-referenced']
        self.assertTrue(oc24)
        self.assertEqual(oc24[0]['result'], 'FAIL',
                         'OC-24 must fire on CR filed today by this session but missing from exec log')


class TestCR160_PlaceholderSweepSpecExemption(unittest.TestCase):
    """CR-160 class 2: placeholder sweep must exempt spec/reference docs."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_spec_routing_table_tokens(self):
        """spec-routing-table.md with {client_name}/<slug>/FILL: → NO block."""
        # Write a spec file with template tokens (documented syntax)
        spec_path = self.f.write_repo_file(
            'references/spec-routing-table.md',
            textwrap.dedent("""\
                # Spec Routing Table
                | Artifact type | Route | Spec |
                |---|---|---|
                | content-brief | repos/{client_name}/briefs/ | FILL: per-client |
                | page | repos/<service-slug>/pages/ | template-page.md |
                | schema | repos/{client_name}/schema/ | PLACEHOLDER syntax doc |
            """))
        self.f.write_dirty_ledger([
            {'file_path': spec_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')
                         and 'placeholder' in c.get('surface', '').lower()]
        self.assertEqual(len(sweep_catches), 0,
                         'Placeholder sweep must not fire on spec/reference docs')

    def test_true_positive_real_deliverable_with_fill(self):
        """A real deliverable file with unresolved FILL: → MUST block."""
        deliverable_path = self.f.write_repo_file(
            'pages/ev-charger-vienna.md',
            textwrap.dedent("""\
                # EV Charger Installation Vienna
                The cost is FILL: per unit.
                Contact PLACEHOLDER for details.
            """))
        self.f.write_dirty_ledger([
            {'file_path': deliverable_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertGreater(len(sweep_catches), 0,
                           'Placeholder sweep must block on real deliverable with FILL:')


class TestCR160_CompletenessDiffTierNone(unittest.TestCase):
    """CR-160 class 3: tier-None must NOT default to Productize."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_no_tier_no_manifest_passes(self):
        """Run with no tier and no manifest → must NOT block."""
        handoff = self.f.write_handoff(textwrap.dedent("""\
            ---
            status: active
            ---
            # One-function robustness patch
            No deliverable manifest needed — this is a small fix.
        """))
        self.f.write_dirty_ledger([])
        # tier=None (not passed) — detect_tier should return None, not Productize
        result = run_rgh18(self.f, handoff=handoff)
        self.assertEqual(result['verdict'], 'PASS',
                         'tier-None must not default to Productize and demand a manifest')

    def test_true_positive_productize_no_manifest_blocks(self):
        """Explicitly Productize tier with no manifest → MUST block."""
        handoff = self.f.write_handoff(textwrap.dedent("""\
            ---
            status: active
            ---
            # Major skill build
            No manifest here but tier is Productize.
        """))
        self.f.write_dirty_ledger([])
        result = run_rgh18(self.f, handoff=handoff, tier='Productize')
        self.assertEqual(result['verdict'], 'BLOCKING',
                         'Explicit Productize tier with no manifest must block')


class TestCR160_StagingAuditAlreadyStaged(unittest.TestCase):
    """CR-160 class 4: staging audit must not flag touched-not-staged."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_touched_file_not_yet_staged(self):
        """File in dirty ledger but not yet git-staged → must NOT block.

        The producer's commit block hasn't run yet, so the file is
        legitimately unstaged. The session knows about it (it's in the
        dirty ledger). This is not a 'forgot to stage' situation.
        """
        # Create a file in the repo, commit it, then modify it (unstaged)
        path = self.f.write_repo_file('script.py', '# original', commit=True)
        with open(path, 'w') as fh:
            fh.write('# modified by this session')

        self.f.write_dirty_ledger([
            {'file_path': path, 'timestamp': 1000, 'tool': 'Edit'}
        ])

        result = run_rgh18(self.f, tier='Capture-only')
        staging_catches = [c for c in result.get('catches', [])
                           if 'staging-audit' in c.get('surface', '')]
        self.assertEqual(len(staging_catches), 0,
                         'Staging audit must not block on touched-not-yet-staged files')

    def test_true_positive_foreign_staged_file_blocks(self):
        """File staged by another chat (not in dirty ledger) → MUST block."""
        # Create and commit two files
        path_foreign = self.f.write_repo_file('foreign.py', '# original', commit=True)
        path_own = self.f.write_repo_file('own.py', '# own file', commit=True)

        # Modify and stage foreign.py (simulating another chat's work)
        with open(path_foreign, 'w') as fh:
            fh.write('# modified by ANOTHER session')
        subprocess.run(['git', 'add', 'foreign.py'],
                       cwd=self.f.repo, capture_output=True)

        # Dirty ledger only has own.py — NOT foreign.py
        # (need at least one entry in this repo so the audit runs)
        self.f.write_dirty_ledger([
            {'file_path': path_own, 'timestamp': 1000, 'tool': 'Edit'}
        ])

        result = run_rgh18(self.f, tier='Capture-only')
        staging_catches = [c for c in result.get('catches', [])
                           if 'staging-audit' in c.get('surface', '')]
        self.assertGreater(len(staging_catches), 0,
                           'Staging audit must block on foreign staged files')


# =====================================================================
# RGH-16 AC-2 Regression: Orchestrator completeness — missing
# tracker/lesson must BLOCK through the deterministic pre-checks
# that the reviewer-orchestrator invokes at Step 2.
# =====================================================================

class TestRGH16OrchestratorCompletenessBlocking(unittest.TestCase):
    """AC-2: synthetic run missing a required tracker/lesson → BLOCKING.

    These fixtures simulate what the reviewer-orchestrator's Step 2 does:
    run RGH-19 on a producer session. If required documentation artifacts
    are missing, RGH-19 must return BLOCKING — which the orchestrator
    surfaces as DETERMINISTIC-BLOCKED (skipping LLM dispatch).
    """

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def _make_minimal_exec_log(self, *, include_knowledge_audit=True,
                               include_evidence=True):
        """Build an exec log with controllable completeness."""
        sections = textwrap.dedent("""\
            ---
            type: execution-log
            status: draft
            created: 2026-07-03
            updated: 2026-07-03
            venture: test
            tags: [execution-log, test]
            ---

            ## 2026-07-03 — Test task

            **What was built:** Test feature
            **Decision made:** Used approach A
            **Alternatives considered:** Approach B
            **Why this approach:** Simpler
            **Reusable for future apps?:** No
        """)
        if include_evidence:
            sections += "\n### Acceptance criteria results\n- AC-1: PASS — verified on disk\n"
        if include_knowledge_audit:
            sections += textwrap.dedent("""
                ### Knowledge capture audit
                1. Bugs/failures: none
                2. Decisions: documented above
                3. Patterns: none emerging
                4. Lessons: none
                5. State updates: done
                6. Productization (if applicable): N/A
            """)
        return sections

    def _make_handoff_with_dod(self):
        """Build a handoff with a DoD that references an exec log."""
        return textwrap.dedent("""\
            ---
            status: active
            ---
            # Test handoff

            ## Definition of Done
            | # | Deliverable | Path or glob | Assertion | Source | Check |
            |---|---|---|---|---|---|
            | 1 | Execution log | repos/test-repo/.kos/execution-logs/*.md | exists + substantive | handoff | OC-21 |
        """)

    def test_missing_exec_log_blocks(self):
        """RGH-19 OC-21: no execution log at all → BLOCKING."""
        handoff = self.f.write_handoff(self._make_handoff_with_dod())
        # Write a dirty-ledger entry for some file, but NO exec log
        path = self.f.write_file('repos/test-repo/feature.py', 'print("hello")')
        self.f.write_dirty_ledger([
            {'file_path': path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh19(self.f, handoff=handoff, tier='Productize')
        self.assertEqual(result['verdict'], 'BLOCKING',
                         'Missing exec log must produce BLOCKING through RGH-19')
        oc21_fails = [c for c in result.get('catches', [])
                      if 'OC-21' in c.get('surface', '')]
        self.assertGreater(len(oc21_fails), 0,
                           'OC-21 (exec-log substantiveness) must fire')

    def test_exec_log_missing_knowledge_audit_blocks(self):
        """RGH-19 OC-26: exec log exists but missing knowledge capture audit → BLOCKING."""
        handoff = self.f.write_handoff(self._make_handoff_with_dod())
        exec_content = self._make_minimal_exec_log(include_knowledge_audit=False)
        exec_path = self.f.write_file(
            'repos/test-repo/.kos/execution-logs/execution-log-2026-07-03-test.md',
            exec_content)
        self.f.write_dirty_ledger([
            {'file_path': exec_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh19(self.f, exec_log=exec_path, handoff=handoff,
                           tier='Productize')
        self.assertEqual(result['verdict'], 'BLOCKING',
                         'Missing knowledge-capture audit must BLOCK')
        oc26_fails = [c for c in result.get('catches', [])
                      if 'OC-26' in c.get('surface', '')]
        self.assertGreater(len(oc26_fails), 0,
                           'OC-26 (knowledge-capture-audit-ran) must fire')

    def test_productize_tier_missing_dod_b1b6_blocks(self):
        """RGH-19 OC-20: Productize tier run without B1-B6 DoD → BLOCKING."""
        handoff = self.f.write_handoff(self._make_handoff_with_dod())
        # Exec log has everything EXCEPT productization section
        exec_content = self._make_minimal_exec_log()
        exec_path = self.f.write_file(
            'repos/test-repo/.kos/execution-logs/execution-log-2026-07-03-test.md',
            exec_content)
        self.f.write_dirty_ledger([
            {'file_path': exec_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh19(self.f, exec_log=exec_path, handoff=handoff,
                           tier='Productize')
        # Unconditional: Productize tier missing B1-B6 MUST block
        self.assertEqual(result['verdict'], 'BLOCKING',
                         'Productize tier without B1-B6 must BLOCK')
        # Verify OC-20 / productization-dod check ran and caught it
        oc20_checks = [c for c in result.get('checks_run', [])
                       if 'b1-b6' in c.get('name', '').lower()
                       or 'productization-dod' in c.get('name', '')]
        self.assertGreater(len(oc20_checks), 0,
                           'productization-dod-b1-b6 check must appear in checks_run')
        oc20_catches = [c for c in result.get('catches', [])
                       if 'OC-20' in c.get('surface', '')
                       or 'b1-b6' in c.get('surface', '').lower()]
        self.assertGreater(len(oc20_catches), 0,
                           'OC-20 must catch missing Productize DoD B1-B6')

    def test_complete_run_passes(self):
        """Complete exec log + handoff + all artifacts → PASS (control)."""
        handoff = self.f.write_handoff(self._make_handoff_with_dod())
        # Build an exec log that satisfies OC-21 requirements:
        # - Has a "What Happened" section header
        # - Has ≥3 list items
        exec_content = textwrap.dedent("""\
            ---
            type: execution-log
            status: draft
            created: 2026-07-03
            updated: 2026-07-03
            venture: test
            tags: [execution-log, test]
            ---

            ## 2026-07-03 — Test task

            ### What Happened
            - Built the test feature
            - Resolved configuration issue
            - Verified on disk
            - Committed changes

            ### Decisions Made
            - Used approach A over B for simplicity

            ### Acceptance criteria results
            - AC-1: PASS — verified on disk

            ### Knowledge capture audit
            1. Bugs/failures: none
            2. Decisions: documented above
            3. Patterns: none emerging
            4. Lessons: none
            5. State updates: done
            6. Productization (if applicable): N/A
        """)
        exec_path = self.f.write_file(
            'repos/test-repo/.kos/execution-logs/execution-log-2026-07-03-test.md',
            exec_content)
        self.f.write_dirty_ledger([
            {'file_path': exec_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        # Write a minimal omission-check-registry so OC-27 doesn't fail
        self.f.write_registry(textwrap.dedent("""\
            ---
            type: reference
            ---
            # Omission-check registry
            | OC | Name | Check |
            |---|---|---|
            | OC-21 | exec-log-substantive | presence |
        """))
        # For Capture-only tier, B1-B6 not required
        result = run_rgh19(self.f, exec_log=exec_path, handoff=handoff,
                           tier='Capture-only')
        self.assertEqual(result['verdict'], 'PASS',
                         f'Complete Capture-only run should PASS, got catches: '
                         f'{json.dumps(result.get("catches", []), indent=2)}')


# =====================================================================
# CR-161 / RGH-18/19-CAL-2 Fixtures
# Each fix has a false-positive (must NOT fire) + true-positive (must fire)
# =====================================================================

class TestCR161_SourceCodePlaceholderExemption(unittest.TestCase):
    """CR-161 class 1: placeholder sweep must not fire on source-code files
    that contain trigger words as string literals / regex / constants."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_py_file_with_trigger_words(self):
        """A .py file containing FILL/TODO/PLACEHOLDER as string literals → NO block."""
        py_path = self.f.write_repo_file(
            'engine.py',
            textwrap.dedent("""\
                # Detection engine
                PLACEHOLDER_PATTERNS = re.compile(
                    r'(FILL|TBD|PLACEHOLDER|TODO|FIXME|XXX|CHANGEME)')
                TRIGGER_WORDS = ['FILL', 'TODO', 'PLACEHOLDER']
                def check_placeholder(text):
                    return any(w in text for w in TRIGGER_WORDS)
            """))
        self.f.write_dirty_ledger([
            {'file_path': py_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')
                         and 'placeholder' in c.get('surface', '').lower()]
        self.assertEqual(len(sweep_catches), 0,
                         'Placeholder sweep must not fire on .py source-code files')

    def test_false_positive_test_file_with_trigger_words(self):
        """A test_*.py file with FILL/PLACEHOLDER as test data → NO block."""
        test_path = self.f.write_repo_file(
            'test_checks.py',
            textwrap.dedent("""\
                import unittest
                class TestPlaceholder(unittest.TestCase):
                    def test_detects_fill(self):
                        self.assertIn('FILL', detect('The FILL value'))
                    def test_detects_placeholder(self):
                        content = 'PLACEHOLDER text TODO item'
                        self.assertTrue(has_placeholder(content))
            """))
        self.f.write_dirty_ledger([
            {'file_path': test_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')
                         and 'placeholder' in c.get('surface', '').lower()]
        self.assertEqual(len(sweep_catches), 0,
                         'Placeholder sweep must not fire on test_*.py files')

    def test_true_positive_markdown_deliverable_still_blocks(self):
        """A shipped markdown deliverable with unresolved FILL → MUST block."""
        md_path = self.f.write_repo_file(
            'deliverable-page.md',
            textwrap.dedent("""\
                # Service Page
                Contact us at FILL for a free quote.
                Our PLACEHOLDER service covers TODO areas.
            """))
        self.f.write_dirty_ledger([
            {'file_path': md_path, 'timestamp': 1000, 'tool': 'Write'}
        ])
        result = run_rgh18(self.f, tier='Capture-only')
        sweep_catches = [c for c in result.get('catches', [])
                         if 'dirty-file-sweep' in c.get('surface', '')]
        self.assertGreater(len(sweep_catches), 0,
                           'Placeholder sweep must still block on markdown deliverables')


class TestCR161_BookkeepingFastPath(unittest.TestCase):
    """CR-161 class 2: close-out bookkeeping files are self-review-exempt."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_tracker_is_bookkeeping(self):
        """_active-chats-tracker.md is classified as bookkeeping → exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        tracker_path = os.path.join(
            self.f.workspace, 'second-brain', '_meta', 'handoffs',
            '_active-chats-tracker.md')
        self.assertTrue(
            engine.is_bookkeeping_entry(tracker_path, self.f.workspace),
            '_active-chats-tracker.md must be classified as bookkeeping')

    def test_false_positive_event_log_is_bookkeeping(self):
        """_event-log.md is classified as bookkeeping → exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        event_log_path = os.path.join(
            self.f.workspace, 'second-brain', '_meta', '_event-log.md')
        self.assertTrue(
            engine.is_bookkeeping_entry(event_log_path, self.f.workspace),
            '_event-log.md must be classified as bookkeeping')

    def test_false_positive_recently_closed_is_bookkeeping(self):
        """_recently-closed.md is classified as bookkeeping → exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        path = os.path.join(
            self.f.workspace, 'second-brain', '_meta', 'handoffs',
            '_recently-closed.md')
        self.assertTrue(
            engine.is_bookkeeping_entry(path, self.f.workspace),
            '_recently-closed.md must be classified as bookkeeping')

    def test_true_positive_deliverable_not_bookkeeping(self):
        """A real deliverable file is NOT bookkeeping → still gates."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        deliverable_path = os.path.join(
            self.f.workspace, 'repos', 'test-repo', 'pages', 'service.md')
        self.assertFalse(
            engine.is_bookkeeping_entry(deliverable_path, self.f.workspace),
            'A deliverable file must NOT be classified as bookkeeping')

    def test_bash_event_log_append_is_bookkeeping(self):
        """A BASH printf >> _event-log.md is bookkeeping → exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        entry = {
            'file_path': 'BASH:51f8293aa3bf',
            'bash_cmd': "printf '| 2026-07-03 | shipped |\\n' >> /Users/olivermarroquin/workspace/second-brain/_meta/_event-log.md",
            'display': "printf '| 2026-07-03 | shipped |\\n' >> /Users/olivermarroquin/workspace/second-brain/_meta/_event-log.md",
        }
        self.assertTrue(
            engine.is_bookkeeping_entry(entry, self.f.workspace),
            'BASH printf >> _event-log.md must be classified as bookkeeping')

    def test_bash_non_bookkeeping_target_not_exempt(self):
        """A BASH command writing to a non-bookkeeping file is NOT exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        entry = {
            'file_path': 'BASH:deadbeef1234',
            'bash_cmd': "echo 'exploit' >> /tmp/deliverable.md",
            'display': "echo 'exploit' >> /tmp/deliverable.md",
        }
        self.assertFalse(
            engine.is_bookkeeping_entry(entry, self.f.workspace),
            'BASH writing to non-bookkeeping file must NOT be exempt')

    def test_bash_no_command_not_exempt(self):
        """A BASH: entry with no command info is NOT exempt (fail closed)."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        entry = {'file_path': 'BASH:abc123def456'}
        self.assertFalse(
            engine.is_bookkeeping_entry(entry, self.f.workspace),
            'BASH entry with no command must NOT be bookkeeping (fail closed)')

    def test_bash_string_path_not_bookkeeping(self):
        """A bare BASH: string (backward compat) is NOT bookkeeping."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        self.assertFalse(
            engine.is_bookkeeping_entry('BASH:abc123def456', self.f.workspace),
            'Bare BASH string must NOT be classified as bookkeeping')

    def test_handoff_status_only_flip_is_bookkeeping(self):
        """A handoff edit with ONLY status/consumed/updated + blockquote → exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        # Create and commit a handoff with original status
        handoff_path = self.f.write_repo_file(
            'handoffs/handoff-2026-07-03-status-test.md',
            textwrap.dedent("""\
                ---
                status: ready
                created: 2026-07-02
                priority: high
                ---
                # Test handoff
                ## Scope
                Build the widget.
            """),
            commit=True)
        # Flip to consumed with updated + blockquote (the real close shape)
        with open(handoff_path, 'w') as fh:
            fh.write(textwrap.dedent("""\
                ---
                status: consumed
                created: 2026-07-02
                updated: 2026-07-03
                consumed: 2026-07-03
                priority: high
                ---
                > **Actual deliverable:** 4 fixes shipped including `engine.py` changes and 15 new test fixtures. Independent review PASS session abc123. CR-161 Resolved.

                # Test handoff
                ## Scope
                Build the widget.
            """))
        self.assertTrue(
            engine._is_handoff_status_only_edit(handoff_path, self.f.workspace),
            'Handoff with only status/consumed/updated + blockquote must be bookkeeping-exempt')

    def test_anti_smuggle_handoff_scope_change_not_exempt(self):
        """A handoff edit that changes scope/AC text is NOT bookkeeping (anti-smuggle)."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        # Create and commit a handoff with original scope
        handoff_path = self.f.write_repo_file(
            'handoffs/handoff-2026-07-03-test.md',
            textwrap.dedent("""\
                ---
                status: active
                ---
                # Test handoff
                ## Scope
                Build the widget.
                ## Acceptance criteria
                - AC-1: widget works
            """),
            commit=True)
        # Now modify the scope (not just status flip)
        with open(handoff_path, 'w') as fh:
            fh.write(textwrap.dedent("""\
                ---
                status: consumed
                consumed: 2026-07-03
                ---
                # Test handoff
                ## Scope
                Build the widget AND the gadget (scope change!).
                ## Acceptance criteria
                - AC-1: widget works
                - AC-2: gadget works too (new AC!)
            """))
        self.assertFalse(
            engine._is_handoff_status_only_edit(handoff_path, self.f.workspace),
            'Handoff edit with scope/AC changes must NOT be bookkeeping-exempt (anti-smuggle)')

    def test_anti_smuggle_body_key_value_not_exempt(self):
        """A handoff edit that adds body lines like 'note: ...' or 'fix: ...'
        is NOT bookkeeping — these look like YAML but are scope content.
        (BLOCKING-1 regression fixture.)"""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        handoff_path = self.f.write_repo_file(
            'handoffs/handoff-2026-07-03-smuggle.md',
            textwrap.dedent("""\
                ---
                status: active
                ---
                # Test handoff
                ## Scope
                Build the widget.
            """),
            commit=True)
        # Add body lines that look like key:value but are scope content
        with open(handoff_path, 'w') as fh:
            fh.write(textwrap.dedent("""\
                ---
                status: consumed
                consumed: 2026-07-03
                ---
                # Test handoff
                ## Scope
                Build the widget.
                note: weakened AC to skip safety check
                fix: remove the guard entirely
                reason: old approach was too strict
            """))
        self.assertFalse(
            engine._is_handoff_status_only_edit(handoff_path, self.f.workspace),
            'Body key:value lines (note:/fix:/reason:) must NOT pass anti-smuggle (BLOCKING-1)')

    def test_anti_smuggle_arbitrary_blockquote_not_exempt(self):
        """A handoff edit that adds an arbitrary blockquote (not Actual-deliverable)
        is NOT bookkeeping — only the Actual-deliverable block is exempt."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine
        handoff_path = self.f.write_repo_file(
            'handoffs/handoff-2026-07-03-bq-smuggle.md',
            textwrap.dedent("""\
                ---
                status: active
                ---
                # Test handoff
                ## Scope
                Build the widget.
            """),
            commit=True)
        with open(handoff_path, 'w') as fh:
            fh.write(textwrap.dedent("""\
                ---
                status: consumed
                consumed: 2026-07-03
                ---
                > This is a smuggled scope note disguised as a blockquote.
                > It changes the deliverable requirements.
                # Test handoff
                ## Scope
                Build the widget.
            """))
        self.assertFalse(
            engine._is_handoff_status_only_edit(handoff_path, self.f.workspace),
            'Arbitrary blockquote (not Actual-deliverable) must NOT pass anti-smuggle')


class TestCR166_OC24_OtherChatsExemption(unittest.TestCase):
    """CR-166: OC-24 must only flag CRs this run filed, not other chats'."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_false_positive_cr_filed_by_other_chat_does_not_fire(self):
        """CR filed today by a DIFFERENT session → NO fire on this session."""
        import time
        today = time.strftime('%Y-%m-%d')
        other_session = 'other-chat-session-723cb41b'

        register_content = textwrap.dedent(f"""\
            # Catch Register
            | CR | Filed | Surfaced by | Surface | Severity | Description | Status |
            |---|---|---|---|---|---|---|
            | CR-163 | {today} | {other_session} / hermes-p3-scoping | RGH-19 | blocking | stale substrate | Open |
        """)
        self.f.write_file(
            'second-brain/_meta/handoffs/_review-gate-catch-register.md',
            register_content)

        exec_log = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            ## What Happened
            - Fixed the reviewer orchestrator
            - Shipped completeness checks
            - No CR-163 here — that's another chat's catch
        """))

        result = run_rgh19(self.f, exec_log=exec_log, tier='Capture-only')
        oc24 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'catches-referenced']
        self.assertTrue(oc24)
        self.assertNotEqual(oc24[0]['result'], 'FAIL',
                            'OC-24 must not fire on CRs filed by other chats')

    def test_true_positive_cr_filed_by_this_session_missing_from_log(self):
        """CR filed today by THIS session, absent from exec log → MUST fire."""
        import time
        today = time.strftime('%Y-%m-%d')

        register_content = textwrap.dedent(f"""\
            # Catch Register
            | CR | Filed | Surfaced by | Surface | Severity | Description | Status |
            |---|---|---|---|---|---|---|
            | CR-888 | {today} | {self.f.session_id} / this-run | RGH-18/sweep | blocking | real catch | Open |
        """)
        self.f.write_file(
            'second-brain/_meta/handoffs/_review-gate-catch-register.md',
            register_content)

        exec_log = self.f.write_exec_log(textwrap.dedent("""\
            ---
            type: execution-log
            ---
            ## What Happened
            - Did some work
            - No CR references here
            - Just regular build steps
        """))

        result = run_rgh19(self.f, exec_log=exec_log, tier='Capture-only')
        oc24 = [c for c in result.get('checks_run', [])
                if c.get('name') == 'catches-referenced']
        self.assertTrue(oc24)
        self.assertEqual(oc24[0]['result'], 'FAIL',
                         'OC-24 must fire on CR filed by this session but missing from exec log')


class TestCR165_ReviewerSessionRequired(unittest.TestCase):
    """CR-165: log-review-pass.py must REJECT a PASS with no --reviewer-session."""

    def setUp(self):
        self.f = TempFixture()
        # Create a valid verdict file
        self.verdict_data = {
            'verdict': 'PASS',
            'checks_run': [{'name': 'ground-truth-cross-check', 'result': 'PASS'}],
            'catches': [],
            'cost_usd': 0.0,
            'reviewer_type': 'independent',
            'mandate_version': '3.0',
        }
        self.verdict_path = os.path.join(self.f.state_dir, 'verdict-test.json')
        with open(self.verdict_path, 'w') as fv:
            json.dump(self.verdict_data, fv)

        # Create a registered reviewer session
        self.reviewer_session = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        marker_path = os.path.join(self.f.state_dir,
                                   f'{self.reviewer_session}-role.json')
        with open(marker_path, 'w') as fm:
            json.dump({'role': 'reviewer',
                       'reviewing_session': self.f.session_id}, fm)

        # Create a firing tracker with a row for the run
        self.f.write_file(
            'second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
            '| test-run-id | Gate | Yes | ... |\n')

        # Write a dummy file to review
        self.reviewed_file = self.f.write_file(
            'repos/test-repo/feature.py', 'print("hello")')

    def tearDown(self):
        self.f.cleanup()

    def test_no_reviewer_session_rejects(self):
        """log-review-pass with NO --reviewer-session → REJECTED."""
        log_script = os.path.join(SCRIPT_DIR, 'log-review-pass.py')
        cmd = [
            sys.executable, log_script,
            '--session', self.f.session_id,
            '--files', self.reviewed_file,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-default',
            '--verdict-file', self.verdict_path,
            # NO --reviewer-session
        ]
        env = os.environ.copy()
        env['REVIEW_GATE_STATE_DIR'] = self.f.state_dir
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              env=env)
        self.assertNotEqual(proc.returncode, 0,
                            'Must REJECT when --reviewer-session is omitted')
        self.assertIn('REJECTED', proc.stderr,
                      'Error message must contain REJECTED')
        self.assertIn('CR-165', proc.stderr,
                      'Error must reference CR-165')

    def test_unregistered_reviewer_session_rejects(self):
        """log-review-pass with an unregistered UUID → REJECTED."""
        log_script = os.path.join(SCRIPT_DIR, 'log-review-pass.py')
        fake_uuid = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        cmd = [
            sys.executable, log_script,
            '--session', self.f.session_id,
            '--files', self.reviewed_file,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-default',
            '--verdict-file', self.verdict_path,
            '--reviewer-session', fake_uuid,
        ]
        env = os.environ.copy()
        env['REVIEW_GATE_STATE_DIR'] = self.f.state_dir
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              env=env)
        self.assertNotEqual(proc.returncode, 0,
                            'Must REJECT unregistered reviewer session')
        self.assertIn('REJECTED', proc.stderr)

    def test_registered_reviewer_session_passes(self):
        """log-review-pass with a genuinely registered reviewer → PASSES.

        Uses --reviewer-type producer (not independent) to test the
        universal --reviewer-session requirement without needing the
        firing-tracker (which requires WORKSPACE_ROOT resolution).
        The independent-specific firing-tracker check is tested by
        the existing conformance suite.
        """
        log_script = os.path.join(SCRIPT_DIR, 'log-review-pass.py')
        cmd = [
            sys.executable, log_script,
            '--session', self.f.session_id,
            '--files', self.reviewed_file,
            '--verdict', 'PASS',
            '--tier', 'full',
            '--gate-id', 'G-default',
            '--verdict-file', self.verdict_path,
            '--reviewer-type', 'producer',
            '--reviewer-session', self.reviewer_session,
        ]
        env = os.environ.copy()
        env['REVIEW_GATE_STATE_DIR'] = self.f.state_dir
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              env=env)
        self.assertEqual(proc.returncode, 0,
                         f'Registered reviewer must PASS. stderr: {proc.stderr}')
        self.assertIn('Logged PASS', proc.stdout,
                      'Should confirm the PASS was logged')


class TestE2E_SyntheticCleanClose(unittest.TestCase):
    """AC-4: synthetic clean close with reviewed deliverable + bookkeeping
    lands green with zero gate-skip."""

    def setUp(self):
        self.f = TempFixture()

    def tearDown(self):
        self.f.cleanup()

    def test_clean_close_with_bookkeeping_passes_gate(self):
        """Reviewed deliverable + normal bookkeeping (including BASH event-log
        append) → gate PASS (no skip). This is the full close shape."""
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import engine

        session_id = self.f.session_id
        reviewer_session = 'r1e2v3i4-e5w6-4e7r-8s9e-0s1s2i3o4n5d'

        # 1. Create a deliverable file (already reviewed)
        deliverable = self.f.write_file(
            'repos/test-repo/feature.py', 'print("delivered")')

        # 2. Create bookkeeping files (the close paperwork)
        tracker = self.f.write_file(
            'second-brain/_meta/handoffs/_active-chats-tracker.md',
            '# Tracker\n| row |\n')
        changelog = self.f.write_file(
            'second-brain/_meta/handoffs/_active-chats-tracker-changelog.md',
            '### Pass 356\nClosed the chat.\n')
        recently_closed = self.f.write_file(
            'second-brain/_meta/handoffs/_recently-closed.md',
            '# Recently closed\n| chat | outcome |\n')
        event_log = self.f.write_file(
            'second-brain/_meta/_event-log.md',
            '| 2026-07-03 | shipped |\n')
        firing_tracker = self.f.write_file(
            'second-brain/_meta/handoffs/_review-skill-firing-tracker.md',
            '| run | gate | yes |\n')

        # 3. BASH event-log append command (the exact close-out pattern)
        bash_event_log_cmd = (
            f"printf '| 2026-07-03 | shipped |\\n' >> {event_log}")
        bash_entry_key = engine.bash_entry_id(bash_event_log_cmd)

        # 4. Write dirty ledger with all files INCLUDING BASH entries
        now = 1000.0
        dirty_entries = [
            {'file_path': deliverable, 'timestamp': now,
             'tool': 'Write', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            {'file_path': tracker, 'timestamp': now + 1,
             'tool': 'Edit', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            {'file_path': changelog, 'timestamp': now + 2,
             'tool': 'Write', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            {'file_path': recently_closed, 'timestamp': now + 3,
             'tool': 'Edit', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            {'file_path': event_log, 'timestamp': now + 4,
             'tool': 'Edit', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            {'file_path': firing_tracker, 'timestamp': now + 5,
             'tool': 'Edit', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code'},
            # BASH event-log append — the entry that was missing from Fix B
            {'file_path': bash_entry_key, 'timestamp': now + 6,
             'tool': 'Bash', 'tier': 'full', 'entry_source': 'producer',
             'source': 'claude-code',
             'bash_cmd': bash_event_log_cmd,
             'display': bash_event_log_cmd},
        ]
        self.f.write_dirty_ledger(dirty_entries)

        # 5. Write a reviewed-ledger entry for the deliverable (cleared by
        #    a genuinely registered independent reviewer)
        reviewed_entry = {
            'timestamp': now + 0.5,
            'file_path': deliverable,
            'verdict': 'PASS',
            'tier': 'full',
            'gate_id': 'G-independent',
            'evidence': 'independent review PASS',
            'verdict_file': '/tmp/verdict.json',
            'verdict_data': {'verdict': 'PASS',
                             'checks_run': [{'name': 'ground-truth-cross-check',
                                             'result': 'PASS'}],
                             'catches': [], 'cost_usd': 0.0},
            'reviewer_type': 'independent',
            'reviewer_session': reviewer_session,
        }
        reviewed_path = os.path.join(
            self.f.state_dir, f'{session_id}-reviewed.jsonl')
        os.makedirs(self.f.state_dir, exist_ok=True)
        with open(reviewed_path, 'w') as f:
            f.write(json.dumps(reviewed_entry) + '\n')

        # 6. Register the reviewer session
        marker_path = os.path.join(
            self.f.state_dir, f'{reviewer_session}-role.json')
        with open(marker_path, 'w') as fm:
            json.dump({'role': 'reviewer',
                       'reviewing_session': session_id}, fm)

        # 7. Run the gate check — should PASS (bookkeeping exempt including
        #    BASH event-log append, deliverable reviewed by independent)
        result = engine.check_gate(
            state_dir=self.f.state_dir,
            session_id=session_id,
            workspace_root=self.f.workspace,
            included_sources=frozenset({'claude-code', ''}),
            attempt_auto_clear=True,
        )

        self.assertIn(result.status, ('clear', 'clean', 'auto-cleared', 'exempt'),
                      f'Gate must PASS on clean close with bookkeeping + BASH. '
                      f'Got status={result.status}, '
                      f'unreviewed={[e.get("file_path") for e in result.unreviewed]}')


if __name__ == '__main__':
    unittest.main()
