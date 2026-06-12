#!/usr/bin/env python3
"""Standing conformance suite for the mandatory pre-land review gate.

Run from ANY cwd — the tests assert absolute paths, not relative ones.

TEST ISOLATION: Every test class creates a fresh temp dir and passes it via
REVIEW_GATE_STATE_DIR env var. No test reads or writes live .review-gate/state/
or .claude/state/. This is a hard requirement — production state pollution was
a defect in the v1 test suite.

WHAT THIS IS: subprocess-level unit tests. They validate script behavior
(exit codes, JSONL entries, JSON output schema) via subprocess.run. They do
NOT exercise the real Claude Code hook runner and therefore do NOT constitute
real-runner acceptance evidence (D-12).

WHAT REAL-RUNNER EVIDENCE REQUIRES: The conformance suite must be EXECUTED
against actual Claude Code Stop/PostToolUse hook events with captured output
(hook transcript + exit codes + stdout), including the subdirectory-cwd case
asserting the resolved absolute STATE_DIR. A deliberate re-break must
demonstrate the suite CAN fail. See the README for the real-runner integration
test protocol. A protocol doc alone is not evidence.

Usage:
    python3 test_conformance.py            # from any directory
    python3 test_conformance.py -v         # verbose output
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
TRACK = os.path.join(SCRIPTS, 'dirty-ledger-track.py')
GATE = os.path.join(SCRIPTS, 'mandatory-review-gate.py')
STATUS = os.path.join(SCRIPTS, 'gate-status.py')
SKIP = os.path.join(SCRIPTS, 'gate-skip.py')
LOG = os.path.join(SCRIPTS, 'log-review-pass.py')

# Workspace root derived the same way _paths.py does it
EXPECTED_WORKSPACE_ROOT = os.path.realpath(os.path.join(SCRIPTS, '..', '..', '..', '..'))

# A subdirectory cwd that is NOT the workspace root — this is the condition
# that hid D-14 (state dir derived from cwd, not script location).
SUBDIR_CWD = os.path.join(EXPECTED_WORKSPACE_ROOT, 'repos', 'ai-agency-core', 'scripts')


def _make_env(state_dir, source=None, event_log=None):
    """Build a subprocess env dict with REVIEW_GATE_STATE_DIR set."""
    env = os.environ.copy()
    env['REVIEW_GATE_STATE_DIR'] = state_dir
    # Isolate event-log writes (gate-skip) to a temp file by default
    if event_log is not None:
        env['REVIEW_GATE_EVENT_LOG'] = event_log
    elif 'REVIEW_GATE_EVENT_LOG' not in env:
        env['REVIEW_GATE_EVENT_LOG'] = os.path.join(state_dir, '_event-log-test.md')
    if source is not None:
        env['REVIEW_GATE_SOURCE'] = source
    elif 'REVIEW_GATE_SOURCE' in env:
        del env['REVIEW_GATE_SOURCE']  # default to claude-code
    return env


def _run_track(sid, tool_name, tool_input, state_dir, cwd=None, source=None):
    return subprocess.run(
        ['python3', TRACK],
        input=json.dumps({
            'session_id': sid, 'tool_name': tool_name,
            'tool_input': tool_input, 'tool_result': {'text': 'ok'},
        }),
        capture_output=True, text=True,
        cwd=cwd or SUBDIR_CWD,
        env=_make_env(state_dir, source=source),
    )


def _run_gate(sid, state_dir, cwd=None):
    return subprocess.run(
        ['python3', GATE],
        input=json.dumps({'session_id': sid, 'stop_reason': 'end_turn'}),
        capture_output=True, text=True,
        cwd=cwd or SUBDIR_CWD,
        env=_make_env(state_dir),
    )


def _make_verdict_file(state_dir, verdict='PASS', tier='full', checks=None,
                       catches=None, cost_usd=0.0, extra_fields=None):
    """Create a valid verdict JSON file in state_dir. Returns path."""
    if checks is None:
        base_checks = [
            {'name': 'placeholder-sweep', 'result': 'PASS', 'count': 0},
            {'name': 'leak-audit', 'result': 'PASS', 'count': 0},
            {'name': 'link-resolution', 'result': 'PASS', 'count': 0},
        ]
        if tier == 'full':
            base_checks.append(
                {'name': 'ground-truth-cross-check', 'result': 'PASS', 'count': 0})
        checks = base_checks
    data = {
        'verdict': verdict,
        'checks_run': checks,
        'catches': catches or [],
        'cost_usd': cost_usd,
    }
    if extra_fields:
        data.update(extra_fields)
    os.makedirs(state_dir, exist_ok=True)
    vf_path = os.path.join(state_dir, f'verdict-{int(time.time() * 1000)}.json')
    with open(vf_path, 'w') as f:
        json.dump(data, f)
    return vf_path


def _run_log(sid, files, state_dir, verdict='PASS', tier='full',
             gate_id='G-default', evidence=None, findings=None,
             verdict_file=None, cwd=None):
    """Run log-review-pass. Creates a verdict file automatically if none provided."""
    if verdict_file is None:
        verdict_file = _make_verdict_file(state_dir, verdict=verdict, tier=tier)
    args = ['python3', LOG, '--session', sid, '--files'] + files + [
        '--verdict', verdict, '--tier', tier, '--gate-id', gate_id,
        '--verdict-file', verdict_file,
    ]
    if evidence:
        args += ['--evidence', evidence]
    if findings:
        args += ['--findings', findings]
    return subprocess.run(
        args, capture_output=True, text=True,
        cwd=cwd or SUBDIR_CWD,
        env=_make_env(state_dir),
    )


class TestStateDirDerivation(unittest.TestCase):
    """D-14/D-15: WORKSPACE_ROOT must resolve correctly from script location,
    not cwd, not global ~/.claude/state. Verified by absolute path."""

    def test_workspace_root_basename(self):
        self.assertEqual(os.path.basename(EXPECTED_WORKSPACE_ROOT), 'workspace',
                         f'WORKSPACE_ROOT={EXPECTED_WORKSPACE_ROOT}')

    def test_default_state_dir_under_workspace(self):
        """Without REVIEW_GATE_STATE_DIR, default is <workspace>/.review-gate/state."""
        r = subprocess.run(
            ['python3', '-c',
             'import sys; sys.path.insert(0, "' + SCRIPTS + '"); '
             'import importlib.util, os; '
             'os.environ.pop("REVIEW_GATE_STATE_DIR", None); '
             # Re-import fresh to get default path
             'spec = importlib.util.spec_from_file_location("_paths", "' + os.path.join(SCRIPTS, '_paths.py') + '"); '
             'mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); '
             'print(mod.STATE_DIR); print(mod.WORKSPACE_ROOT)'],
            capture_output=True, text=True,
            cwd=SUBDIR_CWD,
            env={k: v for k, v in os.environ.items() if k != 'REVIEW_GATE_STATE_DIR'},
        )
        self.assertEqual(r.returncode, 0, f'_paths.py failed: {r.stderr}')
        lines = r.stdout.strip().split('\n')
        state_dir = lines[0]
        workspace_root = lines[1]
        expected_default = os.path.join(EXPECTED_WORKSPACE_ROOT, '.review-gate', 'state')
        self.assertEqual(state_dir, expected_default,
                         f'Default STATE_DIR={state_dir} != {expected_default}')
        self.assertEqual(os.path.basename(workspace_root), 'workspace')

    def test_state_dir_not_global(self):
        """Default STATE_DIR must not be the global ~/.claude/state."""
        expected_default = os.path.join(EXPECTED_WORKSPACE_ROOT, '.review-gate', 'state')
        global_state = os.path.expanduser('~/.claude/state')
        self.assertNotEqual(expected_default, global_state,
                            'Default STATE_DIR must not be the global ~/.claude/state')

    def test_env_override_respected(self):
        """REVIEW_GATE_STATE_DIR env var overrides the default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            r = subprocess.run(
                ['python3', '-c',
                 'import sys; sys.path.insert(0, "' + SCRIPTS + '"); '
                 'from _paths import STATE_DIR; print(STATE_DIR)'],
                capture_output=True, text=True,
                cwd=SUBDIR_CWD,
                env=_make_env(tmpdir),
            )
            self.assertEqual(r.returncode, 0, f'_paths.py failed: {r.stderr}')
            resolved = r.stdout.strip()
            self.assertEqual(resolved, os.path.realpath(tmpdir))


class TestFullCycleFromSubdir(unittest.TestCase):
    """D-14: Write + block + review + approve from a subdirectory cwd.
    Writer and reader must use the SAME state dir (the isolated temp dir)."""

    SID = 'conformance-cycle'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_write_creates_ledger_in_state_dir(self):
        _run_track(self.SID, 'Write', {
            'file_path': os.path.join(self.state_dir, 'test-artifact.md'),
            'content': '# test',
        }, self.state_dir)
        dirty_path = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty_path),
                        f'Dirty ledger not at {dirty_path}')

    def test_write_does_not_create_subdir_ledger(self):
        _run_track(self.SID, 'Write', {
            'file_path': os.path.join(self.state_dir, 'test-artifact.md'),
            'content': '# test',
        }, self.state_dir)
        # Should NOT create state in the cwd
        subdir_dirty = os.path.join(SUBDIR_CWD, '.claude', 'state',
                                    f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(subdir_dirty),
                         f'Dirty ledger written to cwd subdir: {subdir_dirty}')

    def test_write_does_not_pollute_production_state(self):
        _run_track(self.SID, 'Write', {
            'file_path': os.path.join(self.state_dir, 'test-artifact.md'),
            'content': '# test',
        }, self.state_dir)
        prod_dirty = os.path.join(EXPECTED_WORKSPACE_ROOT, '.review-gate', 'state',
                                  f'{self.SID}-dirty.jsonl')
        prod_dirty_old = os.path.join(EXPECTED_WORKSPACE_ROOT, '.claude', 'state',
                                      f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(prod_dirty),
                         f'Test polluted production state: {prod_dirty}')
        self.assertFalse(os.path.exists(prod_dirty_old),
                         f'Test polluted old production state: {prod_dirty_old}')

    def test_full_block_review_approve_cycle(self):
        test_file = os.path.join(self.state_dir, 'test-artifact.md')
        # Write — creates dirty entry
        _run_track(self.SID, 'Write', {
            'file_path': test_file, 'content': '# test',
        }, self.state_dir)
        # Block — dirty entry exists, no review
        r_block = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r_block.returncode, 2, 'Stop must block (exit 2)')
        # Review — log the pass
        time.sleep(0.05)
        # Use the realpath of the test file (what the tracker normalizes to)
        _run_log(self.SID, [os.path.realpath(test_file)], self.state_dir)
        # Approve — review marker clears the entry
        time.sleep(0.05)
        r_approve = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r_approve.returncode, 0, 'Stop must approve (exit 0)')

    def test_approve_schema_valid(self):
        """Approve path emits {"continue": true} with no hookSpecificOutput (D-11)."""
        r = _run_gate(self.SID, self.state_dir)  # empty session = immediate approve
        self.assertEqual(r.returncode, 0)
        parsed = json.loads(r.stdout.strip())
        self.assertTrue(parsed.get('continue'))
        self.assertNotIn('hookSpecificOutput', parsed,
                         'Stop approve must not contain hookSpecificOutput')


class TestReadOnlyExempt(unittest.TestCase):
    """Read-only commands must NOT create dirty entries."""

    SID = 'conformance-readonly'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _assert_no_dirty(self, command, desc):
        _run_track(self.SID, 'Bash', {'command': command}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        if os.path.exists(dirty):
            with open(dirty) as f:
                entries = [json.loads(l) for l in f if l.strip()]
            self.fail(f'{desc}: created {len(entries)} dirty entries')

    def test_cd_and_git_status(self):
        self._assert_no_dirty('cd ~/workspace && git status', 'cd + git status')

    def test_grep_with_stderr_redirect(self):
        self._assert_no_dirty('grep -r "pattern" /tmp 2>/dev/null',
                              'grep 2>/dev/null')

    def test_curl_get(self):
        self._assert_no_dirty('curl -s https://example.com/api/status',
                              'curl GET')

    def test_curl_get_implicit(self):
        self._assert_no_dirty('curl https://example.com/page.html',
                              'curl GET implicit')

    def test_python3_c_inspection(self):
        self._assert_no_dirty('python3 -c "import json; print(1)"',
                              'python3 -c')

    def test_heredoc_python(self):
        self._assert_no_dirty("python3 << 'EOF'\nimport json\nprint(1)\nEOF",
                              'heredoc python3')


class TestStateChangingCaught(unittest.TestCase):
    """State-changing commands MUST create dirty entries."""

    SID = 'conformance-statechg'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _assert_dirty(self, command, desc):
        _run_track(self.SID, 'Bash', {'command': command}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty), f'{desc}: no dirty entry')

    def test_curl_post(self):
        self._assert_dirty('curl -X POST https://example.com/api', 'curl POST')

    def test_curl_data(self):
        self._assert_dirty('curl -d "key=val" https://example.com/api',
                           'curl --data')

    def test_curl_form_upload(self):
        self._assert_dirty('curl -F "file=@/tmp/x" https://example.com/upload',
                           'curl form upload')

    def test_git_push(self):
        self._assert_dirty('git push origin main', 'git push')

    def test_compound_with_push(self):
        self._assert_dirty('echo done && git push origin main',
                           'echo + git push')


class TestSelfReferentialExclusion(unittest.TestCase):
    """Gate's own commands must NOT create dirty entries (D-16)."""

    SID = 'conformance-selfref'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_full_path_log_review_pass(self):
        cmd = (f'python3 {LOG} --session x --files /tmp/x --verdict PASS '
               f'--tier full --gate-id G-default '
               f'--evidence "placeholder-sweep clean"')
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'log-review-pass.py command must be excluded')

    def test_full_path_mandatory_review_gate(self):
        cmd = f'python3 {GATE}'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'mandatory-review-gate.py command must be excluded')

    def test_full_path_dirty_ledger_track(self):
        cmd = f'python3 {TRACK}'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'dirty-ledger-track.py command must be excluded')


class TestReviewGatePathExclusion(unittest.TestCase):
    """Writes to .review-gate/ paths must NOT create dirty entries (verdict-loop fix)."""

    SID = 'conformance-rg-exclude'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_verdict_file_write_excluded(self):
        """Writing a verdict JSON into .review-gate/ must not create a dirty entry."""
        verdict_path = os.path.join(self.state_dir, '.review-gate', 'state', 'verdict-test.json')
        os.makedirs(os.path.dirname(verdict_path), exist_ok=True)
        # Use a path that contains /.review-gate/ when normalized
        _run_track(self.SID, 'Write', {
            'file_path': '/tmp/workspace/.review-gate/state/verdict-test.json',
            'content': '{}',
        }, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         '.review-gate/ verdict file must be excluded from tracking')

    def test_non_review_gate_path_still_tracked(self):
        """A normal file path must still be tracked."""
        _run_track(self.SID, 'Write', {
            'file_path': os.path.join(self.state_dir, 'normal-artifact.md'),
            'content': '# test',
        }, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty),
                        'Normal file must still be tracked')


class TestSelfRefPerSegment(unittest.TestCase):
    """Self-referential exclusion must be per-segment: mixed commands tracked."""

    SID = 'conformance-selfref-seg'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_pure_gate_command_excluded(self):
        """A pure gate command (all segments self-ref) is excluded."""
        cmd = f'python3 {LOG} --session x --files /tmp/x --verdict PASS --tier full --gate-id G-default --verdict-file /tmp/v.json'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'Pure gate command must be excluded')

    def test_mixed_command_tracked(self):
        """cat > file && python3 .../log-review-pass.py must be TRACKED (evasion shape)."""
        cmd = f'cat > /tmp/evil.md && python3 {LOG} --session x --files /tmp/x --verdict PASS --tier full --gate-id G-default --verdict-file /tmp/v.json'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty),
                        'Mixed command (state-change + gate) must be tracked')

    def test_two_gate_segments_excluded(self):
        """Two gate commands chained are still excluded."""
        cmd = f'python3 {STATUS} --session x && python3 {LOG} --session x --files /tmp/x --verdict PASS --tier full --gate-id G-default --verdict-file /tmp/v.json'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'Multiple gate commands chained must be excluded')

    def test_state_change_before_gate_tracked(self):
        """echo data > file ; python3 .../gate-status.py must be TRACKED."""
        cmd = f'echo data > /tmp/out.txt ; python3 {STATUS} --session x'
        _run_track(self.SID, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty),
                        'State-change segment before gate command must be tracked')


class TestVerdictFileRequired(unittest.TestCase):
    """RGH-1: verdict file kills the self-claim. Bare evidence rejected."""

    SID = 'conformance-verdict'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_reject_bare_evidence_no_verdict_file(self):
        """--evidence alone without --verdict-file must be rejected."""
        args = ['python3', LOG, '--session', self.SID,
                '--files', '/tmp/x',
                '--verdict', 'PASS', '--tier', 'full',
                '--gate-id', 'G-default',
                '--evidence', 'placeholder-sweep: 0 tokens; leak-audit: 0 strings; structural verified']
        r = subprocess.run(args, capture_output=True, text=True,
                           cwd=SUBDIR_CWD, env=_make_env(self.state_dir))
        self.assertEqual(r.returncode, 1,
                         f'Bare --evidence without --verdict-file must be rejected: {r.stderr}')
        self.assertIn('verdict-file', r.stderr.lower())

    def test_reject_nonexistent_verdict_file(self):
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     verdict_file='/tmp/nonexistent-verdict-file.json')
        self.assertEqual(r.returncode, 1, 'Nonexistent verdict file must be rejected')

    def test_reject_invalid_json_verdict_file(self):
        bad_path = os.path.join(self.state_dir, 'bad.json')
        with open(bad_path, 'w') as f:
            f.write('not json at all {{{')
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, verdict_file=bad_path)
        self.assertEqual(r.returncode, 1, 'Invalid JSON verdict file must be rejected')

    def test_reject_missing_required_keys(self):
        bad_path = os.path.join(self.state_dir, 'missing-keys.json')
        with open(bad_path, 'w') as f:
            json.dump({'verdict': 'PASS'}, f)  # missing checks_run
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, verdict_file=bad_path)
        self.assertEqual(r.returncode, 1, 'Missing checks_run must be rejected')

    def test_reject_empty_checks_run(self):
        bad_path = os.path.join(self.state_dir, 'empty-checks.json')
        with open(bad_path, 'w') as f:
            json.dump({'verdict': 'PASS', 'checks_run': []}, f)
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, verdict_file=bad_path)
        self.assertEqual(r.returncode, 1, 'Empty checks_run must be rejected')

    def test_reject_invalid_verdict_enum(self):
        bad_path = os.path.join(self.state_dir, 'bad-enum.json')
        with open(bad_path, 'w') as f:
            json.dump({'verdict': 'MAYBE', 'checks_run': [{'name': 'x'}]}, f)
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, verdict_file=bad_path)
        self.assertEqual(r.returncode, 1, 'Invalid verdict enum must be rejected')

    def test_accept_valid_verdict_file(self):
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Valid verdict file must be accepted: {r.stderr}')

    def test_accept_verdict_with_catches(self):
        vf = _make_verdict_file(self.state_dir, catches=[
            {'surface': 'title', 'severity': 'low',
             'description': 'Minor wording issue'}
        ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, verdict_file=vf)
        self.assertEqual(r.returncode, 0,
                         f'Verdict with catches must be accepted: {r.stderr}')

    def test_reviewed_entry_contains_verdict_data(self):
        """The reviewed JSONL entry must contain the verdict file data for audit."""
        _run_log(self.SID, ['/tmp/x'], self.state_dir)
        reviewed = os.path.join(self.state_dir, f'{self.SID}-reviewed.jsonl')
        self.assertTrue(os.path.exists(reviewed))
        with open(reviewed) as f:
            entry = json.loads(f.readline())
        self.assertIn('verdict_file', entry)
        self.assertIn('verdict_data', entry)
        self.assertIn('checks_run', entry['verdict_data'])


class TestTierEnforcement(unittest.TestCase):
    """Full-tier entries require full evidence fields in verdict file;
    fast-path accepts grep-only. Mismatch rejected."""

    SID = 'conformance-tier'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_full_tier_rejected_without_ground_truth_check(self):
        """A full entry cleared by a fast-path-shaped verdict is rejected."""
        # Verdict file with only grep-level checks (no ground-truth)
        vf = _make_verdict_file(self.state_dir, tier='fast-path',
                                checks=[
                                    {'name': 'placeholder-sweep', 'result': 'PASS'},
                                    {'name': 'leak-audit', 'result': 'PASS'},
                                ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     tier='full', verdict_file=vf)
        self.assertEqual(r.returncode, 1,
                         f'Full tier with fast-path verdict must be rejected: {r.stderr}')
        self.assertIn('ground-truth', r.stderr.lower())

    def test_full_tier_accepted_with_ground_truth_check(self):
        """A full entry with ground-truth checks in verdict file passes."""
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir, tier='full')
        self.assertEqual(r.returncode, 0,
                         f'Full tier with ground-truth check must pass: {r.stderr}')

    def test_full_tier_rejected_with_empty_check_name(self):
        """D-23: empty-string check name must not satisfy full-tier requirement."""
        vf = _make_verdict_file(self.state_dir, tier='fast-path',
                                checks=[
                                    {'name': '', 'result': 'PASS'},
                                    {'name': 'placeholder-sweep', 'result': 'PASS'},
                                ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     tier='full', verdict_file=vf)
        self.assertEqual(r.returncode, 1,
                         f'Empty check name must not pass full-tier: {r.stderr}')

    def test_full_tier_rejected_with_single_char_name(self):
        """D-23: single-char check name like 'v' must not satisfy full-tier."""
        vf = _make_verdict_file(self.state_dir, tier='fast-path',
                                checks=[
                                    {'name': 'v', 'result': 'PASS'},
                                    {'name': 'placeholder-sweep', 'result': 'PASS'},
                                ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     tier='full', verdict_file=vf)
        self.assertEqual(r.returncode, 1,
                         f'Single-char "v" must not pass full-tier: {r.stderr}')

    def test_full_tier_rejected_with_partial_prefix(self):
        """D-23: partial prefix like 'ground' must not satisfy full-tier."""
        vf = _make_verdict_file(self.state_dir, tier='fast-path',
                                checks=[
                                    {'name': 'ground', 'result': 'PASS'},
                                    {'name': 'placeholder-sweep', 'result': 'PASS'},
                                ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     tier='full', verdict_file=vf)
        self.assertEqual(r.returncode, 1,
                         f'Partial prefix "ground" must not pass full-tier: {r.stderr}')

    def test_fast_path_accepted_without_ground_truth(self):
        """Fast-path entries don't need ground-truth checks."""
        vf = _make_verdict_file(self.state_dir, tier='fast-path',
                                checks=[
                                    {'name': 'placeholder-sweep', 'result': 'PASS'},
                                    {'name': 'leak-audit', 'result': 'PASS'},
                                ])
        r = _run_log(self.SID, ['/tmp/x'], self.state_dir,
                     tier='fast-path', verdict_file=vf)
        self.assertEqual(r.returncode, 0,
                         f'Fast-path without ground-truth must pass: {r.stderr}')


class TestSubAgentCoverage(unittest.TestCase):
    """D-13 (RGH-1.6): Sub-agent artifacts are caught because sub-agent
    PostToolUse events inherit the parent session_id in Claude Code
    (confirmed by real-runner evidence 2026-06-12). All entries land in
    the parent's own dirty ledger — no sibling scan needed."""

    SID = 'conformance-parent'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_parent_stop_blocks_on_subagent_artifact(self):
        """Sub-agent artifact (same session_id) blocks parent Stop."""
        # Parent writes
        parent_file = os.path.join(self.state_dir, 'parent-work.md')
        _run_track(self.SID, 'Write', {
            'file_path': parent_file, 'content': '# parent work',
        }, self.state_dir)
        time.sleep(0.05)
        # Sub-agent writes (same session_id — real CC behavior)
        child_file = os.path.join(self.state_dir, 'subagent-artifact.md')
        _run_track(self.SID, 'Write', {
            'file_path': child_file, 'content': '# subagent output',
        }, self.state_dir)
        # Review parent file only
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(parent_file)],
                 self.state_dir)
        time.sleep(0.05)
        # Must still block (child file unreviewed)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Stop must block on unreviewed sub-agent artifact')
        self.assertIn('unreviewed', r.stderr.lower())

    def test_stop_clears_after_subagent_reviewed(self):
        """Stop approves once sub-agent artifact is reviewed."""
        child_file = os.path.join(self.state_dir, 'subagent-artifact.md')
        _run_track(self.SID, 'Write', {
            'file_path': child_file, 'content': '# subagent output',
        }, self.state_dir)
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(child_file)], self.state_dir)
        time.sleep(0.05)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'Stop must approve after sub-agent artifact reviewed')

    def test_mixed_parent_and_subagent_dirty(self):
        """Both parent and sub-agent entries must be reviewed."""
        parent_file = os.path.join(self.state_dir, 'parent-artifact.md')
        child_file = os.path.join(self.state_dir, 'child-artifact.md')
        _run_track(self.SID, 'Write', {
            'file_path': parent_file, 'content': '# parent',
        }, self.state_dir)
        _run_track(self.SID, 'Write', {
            'file_path': child_file, 'content': '# child',
        }, self.state_dir)
        # Only review parent — should still block
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(parent_file)], self.state_dir)
        time.sleep(0.05)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Must still block when child artifact unreviewed')
        # Now review child too — should approve
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(child_file)], self.state_dir)
        time.sleep(0.05)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'Must approve when both parent and child reviewed')

    def test_sibling_ledger_ignored(self):
        """A separate session's dirty ledger is NOT scanned (own-ledger-only).
        This eliminates the parallel-chat cross-block residual."""
        # Parent session writes + reviews its own file
        parent_file = os.path.join(self.state_dir, 'parent.md')
        _run_track(self.SID, 'Write', {
            'file_path': parent_file, 'content': '# parent',
        }, self.state_dir)
        time.sleep(0.05)
        # A completely separate session writes an unreviewed file
        other_sid = 'conformance-other-chat'
        other_file = os.path.join(self.state_dir, 'other-chat.md')
        _run_track(other_sid, 'Write', {
            'file_path': other_file, 'content': '# other chat',
        }, self.state_dir)
        # Review parent's own file
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(parent_file)], self.state_dir)
        time.sleep(0.05)
        # Parent Stop must approve — other session's ledger is not scanned
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'Sibling session ledger must be ignored (own-ledger-only)')


class TestSubstrateTagging(unittest.TestCase):
    """F3: Dirty entries get a source field; CC Stop excludes non-CC sources."""

    SID_CC = 'conformance-substrate-cc'
    SID_COWORK = 'conformance-substrate-cowork'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_default_source_is_claude_code(self):
        """Without REVIEW_GATE_SOURCE, entries are tagged claude-code."""
        _run_track(self.SID_CC, 'Write', {
            'file_path': os.path.join(self.state_dir, 'cc.md'),
            'content': '# cc',
        }, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{self.SID_CC}-dirty.jsonl')
        with open(dirty) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry.get('source'), 'claude-code')

    def test_cowork_source_tagged(self):
        """REVIEW_GATE_SOURCE=cowork tags entries as cowork."""
        _run_track(self.SID_COWORK, 'Write', {
            'file_path': os.path.join(self.state_dir, 'cowork.md'),
            'content': '# cowork',
        }, self.state_dir, source='cowork')
        dirty = os.path.join(self.state_dir, f'{self.SID_COWORK}-dirty.jsonl')
        with open(dirty) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry.get('source'), 'cowork')

    def test_cc_stop_excludes_concurrent_cowork_entries(self):
        """Concurrent cowork-source entries must NOT block CC Stop."""
        # CC session writes
        cc_file = os.path.join(self.state_dir, 'cc-artifact.md')
        _run_track(self.SID_CC, 'Write', {
            'file_path': cc_file, 'content': '# cc',
        }, self.state_dir)
        time.sleep(0.05)
        # Concurrent cowork session writes (same state dir, after CC start)
        _run_track(self.SID_COWORK, 'Write', {
            'file_path': os.path.join(self.state_dir, 'cowork.md'),
            'content': '# cowork',
        }, self.state_dir, source='cowork')
        # Review CC artifact only
        time.sleep(0.05)
        _run_log(self.SID_CC, [os.path.realpath(cc_file)], self.state_dir)
        time.sleep(0.05)
        # CC Stop must approve — cowork entries excluded by source
        r = _run_gate(self.SID_CC, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Cowork-source entries must not block CC Stop: {r.stderr}')

    def test_cc_stop_still_catches_cc_subagent(self):
        """CC-source sub-agent entries still block CC Stop (D-13 preserved).
        RGH-1.6: sub-agent uses same session_id as parent."""
        # CC parent writes
        parent_file = os.path.join(self.state_dir, 'parent.md')
        _run_track(self.SID_CC, 'Write', {
            'file_path': parent_file, 'content': '# parent',
        }, self.state_dir)
        time.sleep(0.05)
        # CC sub-agent writes (same session_id — real CC behavior)
        _run_track(self.SID_CC, 'Write', {
            'file_path': os.path.join(self.state_dir, 'child.md'),
            'content': '# child',
        }, self.state_dir)
        # Review parent only
        time.sleep(0.05)
        _run_log(self.SID_CC, [os.path.realpath(parent_file)], self.state_dir)
        time.sleep(0.05)
        # Must still block on unreviewed CC child
        r = _run_gate(self.SID_CC, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'CC sub-agent entries must still block CC Stop')


class TestOwnLedgerOnlyScoping(unittest.TestCase):
    """RGH-1.6: own-ledger-only scoping. Other sessions' ledgers are never
    scanned. Sub-agent coverage comes from session-id inheritance, not
    sibling-ledger scanning."""

    CURRENT_SID = 'conformance-current'
    OTHER_SID = 'conformance-other'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_stale_ledger_does_not_block_current(self):
        """A dirty ledger from another session must not block current Stop."""
        os.makedirs(self.state_dir, exist_ok=True)
        stale_path = os.path.join(self.state_dir,
                                  f'{self.OTHER_SID}-dirty.jsonl')
        stale_entry = {
            'timestamp': 1000.0,
            'iso_time': '2020-01-01T00:00:00Z',
            'tool': 'Write',
            'file_path': '/tmp/stale-artifact.md',
            'tier': 'full',
        }
        with open(stale_path, 'w') as f:
            f.write(json.dumps(stale_entry) + '\n')
        r = _run_gate(self.CURRENT_SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'Other session ledger must not block current Stop')

    def test_other_ledger_ignored_when_current_has_entries(self):
        """Even when current session is dirty, other session's entries are ignored."""
        os.makedirs(self.state_dir, exist_ok=True)
        other_path = os.path.join(self.state_dir,
                                  f'{self.OTHER_SID}-dirty.jsonl')
        other_entry = {
            'timestamp': time.time() + 100,  # even in the future
            'iso_time': '2099-01-01T00:00:00Z',
            'tool': 'Write',
            'file_path': '/tmp/other-artifact.md',
            'tier': 'full',
        }
        with open(other_path, 'w') as f:
            f.write(json.dumps(other_entry) + '\n')
        # Current session writes + reviews its own file
        current_file = os.path.join(self.state_dir, 'current.md')
        _run_track(self.CURRENT_SID, 'Write', {
            'file_path': current_file, 'content': '# current',
        }, self.state_dir)
        time.sleep(0.05)
        _run_log(self.CURRENT_SID, [os.path.realpath(current_file)],
                 self.state_dir)
        time.sleep(0.05)
        r = _run_gate(self.CURRENT_SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Other session ledger must be ignored: {r.stderr}')

    def test_concurrent_other_session_also_ignored(self):
        """A concurrent other session's ledger is also ignored (no cross-block)."""
        # Current session writes
        parent_file = os.path.join(self.state_dir, 'parent.md')
        _run_track(self.CURRENT_SID, 'Write', {
            'file_path': parent_file, 'content': '# parent',
        }, self.state_dir)
        time.sleep(0.05)
        # Another session writes concurrently (after current started)
        other_file = os.path.join(self.state_dir, 'other.md')
        _run_track(self.OTHER_SID, 'Write', {
            'file_path': other_file, 'content': '# concurrent other',
        }, self.state_dir)
        # Review current's file only
        time.sleep(0.05)
        _run_log(self.CURRENT_SID, [os.path.realpath(parent_file)],
                 self.state_dir)
        time.sleep(0.05)
        # Must approve — other session is not scanned
        r = _run_gate(self.CURRENT_SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         'Concurrent other session must not cross-block')


class TestBlockMessageContent(unittest.TestCase):
    """Block message must contain actionable info for the model."""

    SID = 'conformance-blockmsg'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_block_message_includes_file_and_tier(self):
        test_file = os.path.join(self.state_dir, 'artifact.md')
        _run_track(self.SID, 'Write', {
            'file_path': test_file, 'content': '# test',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        msg = r.stderr
        self.assertIn('MANDATORY PRE-LAND REVIEW GATE', msg)
        self.assertIn('unreviewed', msg.lower())
        self.assertIn('log-review-pass.py', msg)

    def test_block_on_re_edit_after_review(self):
        """D-04: re-edit after review must re-block."""
        test_file = os.path.join(self.state_dir, 'artifact.md')
        # Write + review
        _run_track(self.SID, 'Write', {
            'file_path': test_file, 'content': '# v1',
        }, self.state_dir)
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(test_file)], self.state_dir)
        time.sleep(0.05)
        # Re-edit — new dirty timestamp exceeds review timestamp
        _run_track(self.SID, 'Edit', {
            'file_path': test_file,
            'old_string': '# v1', 'new_string': '# v2',
        }, self.state_dir)
        time.sleep(0.05)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2, 'Re-edit after review must re-block')


class TestProtocolFileExemption(unittest.TestCase):
    """F2: Protocol files exempt ONLY when they are the sole dirty entries."""

    SID = 'conformance-protocol'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')
        # Import the exempt paths to use in tests
        sys.path.insert(0, SCRIPTS)

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_sole_protocol_file_exempt(self):
        """A protocol file as the sole dirty entry auto-approves."""
        from _paths import WORKSPACE_ROOT
        event_log = os.path.realpath(os.path.join(
            WORKSPACE_ROOT, 'second-brain/_meta/_event-log.md'))
        # Track as Edit on the event log
        _run_track(self.SID, 'Edit', {
            'file_path': event_log,
            'old_string': '| old', 'new_string': '| new',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Sole protocol file must be exempt: {r.stderr}')
        # Verify metrics logged as exempt
        metrics = os.path.join(self.state_dir, 'metrics.jsonl')
        with open(metrics) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'exempt')

    def test_multiple_protocol_files_exempt(self):
        """Multiple protocol files (all on allowlist) still exempt."""
        from _paths import WORKSPACE_ROOT
        event_log = os.path.realpath(os.path.join(
            WORKSPACE_ROOT, 'second-brain/_meta/_event-log.md'))
        tracker = os.path.realpath(os.path.join(
            WORKSPACE_ROOT, 'second-brain/_meta/handoffs/_active-chats-tracker.md'))
        _run_track(self.SID, 'Edit', {
            'file_path': event_log,
            'old_string': '| old', 'new_string': '| new',
        }, self.state_dir)
        _run_track(self.SID, 'Edit', {
            'file_path': tracker,
            'old_string': '| old', 'new_string': '| new',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Multiple protocol files must be exempt: {r.stderr}')

    def test_protocol_plus_other_file_not_exempt(self):
        """Protocol file + a non-allowlist file → normal review of everything."""
        from _paths import WORKSPACE_ROOT
        event_log = os.path.realpath(os.path.join(
            WORKSPACE_ROOT, 'second-brain/_meta/_event-log.md'))
        other = os.path.join(self.state_dir, 'real-artifact.md')
        with open(other, 'w') as f:
            f.write('# Real artifact\n')
        _run_track(self.SID, 'Edit', {
            'file_path': event_log,
            'old_string': '| old', 'new_string': '| new',
        }, self.state_dir)
        _run_track(self.SID, 'Write', {
            'file_path': other, 'content': '# Real artifact\n',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Protocol + non-allowlist file must NOT be exempt')

    def test_non_protocol_file_not_exempt(self):
        """A non-protocol file alone is not exempt."""
        other = os.path.join(self.state_dir, 'some-file.md')
        with open(other, 'w') as f:
            f.write('# Content\n')
        _run_track(self.SID, 'Write', {
            'file_path': other, 'content': '# Content\n',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Non-protocol file must not be exempt')

    def test_bash_entry_not_exempt(self):
        """BASH entries are never protocol-exempt."""
        _run_track(self.SID, 'Bash', {
            'command': 'git push origin main',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Bash entries must not be protocol-exempt')


class TestFastPathAutoClear(unittest.TestCase):
    """F1: Stop hook auto-clears fast-path entries by running grep checks
    itself and writing a machine-generated verdict file."""

    SID = 'conformance-autoclear'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_auto_clears_clean_fast_path_edit(self):
        """A clean fast-path edit auto-clears without manual review."""
        # Create the artifact file (clean content)
        artifact = os.path.join(self.state_dir, 'clean-artifact.md')
        with open(artifact, 'w') as f:
            f.write('# Clean content\n\nNo placeholders here.\n')
        # Track as a small Edit (fast-path tier)
        _run_track(self.SID, 'Edit', {
            'file_path': artifact,
            'old_string': '# Old', 'new_string': '# Clean content',
        }, self.state_dir)
        # Stop should auto-clear (exit 0, not exit 2)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Clean fast-path must auto-clear: {r.stderr}')
        # Verify a machine verdict file was written
        verdicts = [f for f in os.listdir(self.state_dir)
                    if f.startswith('verdict-auto-')]
        self.assertTrue(len(verdicts) > 0,
                        'Machine verdict file must be written on auto-clear')
        # Verify metrics logged as auto-clear
        metrics = os.path.join(self.state_dir, 'metrics.jsonl')
        with open(metrics) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'auto-clear')

    def test_auto_clear_refuses_full_tier(self):
        """A full-tier entry must NOT auto-clear even if content is clean."""
        artifact = os.path.join(self.state_dir, 'full-artifact.md')
        with open(artifact, 'w') as f:
            f.write('# Clean content\n')
        # Track as Write (full tier)
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# Clean content\n',
        }, self.state_dir)
        # Stop must block (full tier → no auto-clear)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Full-tier must not auto-clear')

    def test_auto_clear_refuses_placeholder_content(self):
        """A fast-path file with placeholder tokens must NOT auto-clear."""
        artifact = os.path.join(self.state_dir, 'placeholder-artifact.md')
        with open(artifact, 'w') as f:
            f.write('# Title\n\nTBD — fill this in later.\n')
        _run_track(self.SID, 'Edit', {
            'file_path': artifact,
            'old_string': '# Old', 'new_string': '# Title',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'File with TBD must not auto-clear')

    def test_auto_clear_refuses_mixed_tiers(self):
        """If ANY entry is full-tier, no auto-clear even if others are fast-path."""
        clean = os.path.join(self.state_dir, 'clean.md')
        with open(clean, 'w') as f:
            f.write('# Clean\n')
        _run_track(self.SID, 'Edit', {
            'file_path': clean,
            'old_string': '# X', 'new_string': '# Clean',
        }, self.state_dir)
        full = os.path.join(self.state_dir, 'full.md')
        with open(full, 'w') as f:
            f.write('# Also clean\n')
        _run_track(self.SID, 'Write', {
            'file_path': full, 'content': '# Also clean\n',
        }, self.state_dir)
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2,
                         'Mixed fast-path + full must not auto-clear')

    def test_auto_clear_verdict_has_auto_flag(self):
        """Machine verdict file must contain auto_clear: true for audit."""
        artifact = os.path.join(self.state_dir, 'audit-artifact.md')
        with open(artifact, 'w') as f:
            f.write('# Clean\n')
        _run_track(self.SID, 'Edit', {
            'file_path': artifact,
            'old_string': '# X', 'new_string': '# Clean',
        }, self.state_dir)
        _run_gate(self.SID, self.state_dir)
        verdicts = [f for f in os.listdir(self.state_dir)
                    if f.startswith('verdict-auto-')]
        self.assertTrue(len(verdicts) > 0)
        with open(os.path.join(self.state_dir, verdicts[0])) as f:
            vdata = json.load(f)
        self.assertTrue(vdata.get('auto_clear'))
        self.assertEqual(vdata['generator'],
                         'mandatory-review-gate/fast-path-auto-clear')


class TestCostMetrics(unittest.TestCase):
    """RGH-1: Stop hook logs wall-clock per invocation to metrics.jsonl."""

    SID = 'conformance-metrics'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_approve_logs_metrics(self):
        """Empty session approve still logs a metrics entry."""
        _run_gate(self.SID, self.state_dir)
        metrics_path = os.path.join(self.state_dir, 'metrics.jsonl')
        self.assertTrue(os.path.exists(metrics_path),
                        'metrics.jsonl must exist after Stop')
        with open(metrics_path) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'approve')
        self.assertIn('wall_ms', entry)
        self.assertGreater(entry['wall_ms'], 0)

    def test_block_logs_metrics(self):
        """Block path logs metrics with unreviewed count and tier."""
        test_file = os.path.join(self.state_dir, 'artifact.md')
        _run_track(self.SID, 'Write', {
            'file_path': test_file, 'content': '# test',
        }, self.state_dir)
        _run_gate(self.SID, self.state_dir)
        metrics_path = os.path.join(self.state_dir, 'metrics.jsonl')
        self.assertTrue(os.path.exists(metrics_path))
        with open(metrics_path) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['outcome'], 'block')
        self.assertGreater(entry['unreviewed_count'], 0)
        self.assertIn(entry['tier'], ('fast-path', 'full'))


class TestGateStatus(unittest.TestCase):
    """F4: gate-status shows what's blocking and why."""

    SID = 'conformance-status'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _run_status(self, as_json=False):
        args = ['python3', STATUS, '--session', self.SID]
        if as_json:
            args.append('--json')
        return subprocess.run(
            args, capture_output=True, text=True,
            cwd=SUBDIR_CWD, env=_make_env(self.state_dir))

    def test_clean_session(self):
        r = self._run_status(as_json=True)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data['status'], 'clean')

    def test_blocked_shows_files(self):
        artifact = os.path.join(self.state_dir, 'blocked.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        r = self._run_status(as_json=True)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data['status'], 'blocked')
        self.assertGreater(data['unreviewed_count'], 0)
        self.assertTrue(any('blocked.md' in u['file_path']
                            for u in data['unreviewed']))

    def test_clear_after_review(self):
        artifact = os.path.join(self.state_dir, 'reviewed.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        time.sleep(0.05)
        _run_log(self.SID, [os.path.realpath(artifact)], self.state_dir)
        time.sleep(0.05)
        r = self._run_status(as_json=True)
        data = json.loads(r.stdout)
        self.assertEqual(data['status'], 'clear')


class TestGateSkip(unittest.TestCase):
    """F4: gate-skip clears with mandatory --reason, writes loud logs."""

    SID = 'conformance-skip'

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix='rg-test-')

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _run_skip(self, reason='Testing emergency skip for conformance'):
        return subprocess.run(
            ['python3', SKIP, '--session', self.SID, '--reason', reason],
            capture_output=True, text=True,
            cwd=SUBDIR_CWD, env=_make_env(self.state_dir))

    def test_skip_clears_unreviewed(self):
        """Skip clears unreviewed entries so gate approves."""
        artifact = os.path.join(self.state_dir, 'skipped.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        # Gate blocks before skip
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 2)
        # Skip
        r = self._run_skip()
        self.assertEqual(r.returncode, 0, f'Skip failed: {r.stderr}')
        # Gate approves after skip
        r = _run_gate(self.SID, self.state_dir)
        self.assertEqual(r.returncode, 0,
                         f'Gate must approve after skip: {r.stderr}')

    def test_skip_writes_metrics(self):
        """Skip writes a SKIP entry to metrics.jsonl."""
        artifact = os.path.join(self.state_dir, 'skipped.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        self._run_skip()
        metrics = os.path.join(self.state_dir, 'metrics.jsonl')
        self.assertTrue(os.path.exists(metrics))
        with open(metrics) as f:
            lines = f.readlines()
        skip_entries = [json.loads(l) for l in lines
                        if json.loads(l).get('outcome') == 'SKIP']
        self.assertTrue(len(skip_entries) > 0,
                        'metrics.jsonl must contain a SKIP entry')
        self.assertIn('skip_reason', skip_entries[0])

    def test_skip_rejects_short_reason(self):
        """Reason less than 10 chars is rejected."""
        artifact = os.path.join(self.state_dir, 'skipped.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(self.SID, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        r = self._run_skip(reason='short')
        self.assertEqual(r.returncode, 1,
                         'Short reason must be rejected')

    def test_skip_writes_to_isolated_event_log(self):
        """gate-skip must write to REVIEW_GATE_EVENT_LOG, not production event log."""
        # Use a unique SID to avoid matching pre-existing pollution in prod log
        unique_sid = f'conformance-skip-isolation-{int(time.time() * 1000)}'
        artifact = os.path.join(self.state_dir, 'skipped.md')
        with open(artifact, 'w') as f:
            f.write('# content\n')
        _run_track(unique_sid, 'Write', {
            'file_path': artifact, 'content': '# content\n',
        }, self.state_dir)
        isolated_log = os.path.join(self.state_dir, '_event-log-test.md')
        # Run skip with the unique SID
        r = subprocess.run(
            ['python3', SKIP, '--session', unique_sid,
             '--reason', 'Testing event-log isolation for conformance'],
            capture_output=True, text=True,
            cwd=SUBDIR_CWD, env=_make_env(self.state_dir))
        self.assertEqual(r.returncode, 0, f'Skip failed: {r.stderr}')
        # The isolated event log must exist and have content
        self.assertTrue(os.path.exists(isolated_log),
                        'gate-skip must write to REVIEW_GATE_EVENT_LOG')
        with open(isolated_log) as f:
            content = f.read()
        self.assertIn('GATE SKIP', content)
        self.assertIn(unique_sid, content)
        # Production event log must NOT contain the unique SID
        prod_log = os.path.join(EXPECTED_WORKSPACE_ROOT, 'second-brain',
                                '_meta', '_event-log.md')
        if os.path.exists(prod_log):
            with open(prod_log) as f:
                prod_content = f.read()
            self.assertNotIn(unique_sid, prod_content,
                             'gate-skip must NOT write test session to production event log')

    def test_skip_on_empty_is_noop(self):
        """Skip on an already-clear gate is a harmless no-op."""
        r = self._run_skip()
        self.assertEqual(r.returncode, 0)
        self.assertIn('Nothing to skip', r.stdout)

    def test_skip_self_referential_exclusion(self):
        """gate-skip command must not create dirty entries (D-16)."""
        sid = 'conformance-skip-selfref'
        cmd = f'python3 {SKIP} --session x --reason "testing self exclusion for conformance"'
        _run_track(sid, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{sid}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'gate-skip command must be self-excluded')

    def test_status_self_referential_exclusion(self):
        """gate-status command must not create dirty entries (D-16)."""
        sid = 'conformance-status-selfref'
        cmd = f'python3 {STATUS} --session x'
        _run_track(sid, 'Bash', {'command': cmd}, self.state_dir)
        dirty = os.path.join(self.state_dir, f'{sid}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'gate-status command must be self-excluded')


if __name__ == '__main__':
    # Ensure the subdir cwd exists
    if not os.path.isdir(SUBDIR_CWD):
        os.makedirs(SUBDIR_CWD, exist_ok=True)
    # Run from subdir to exercise D-14 path derivation
    os.chdir(SUBDIR_CWD)
    unittest.main()
