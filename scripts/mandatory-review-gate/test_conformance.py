#!/usr/bin/env python3
"""Standing conformance suite for the mandatory pre-land review gate.

Run from ANY cwd — the tests assert absolute paths, not relative ones.
This is the D-15 regression guard: simulated tests that only check "did a
marker clear" can't catch a shared-but-wrong STATE_DIR; these tests assert
the resolved absolute path.

Usage:
    python3 test_conformance.py            # from any directory
    python3 test_conformance.py -v         # verbose output
"""

import json
import os
import subprocess
import sys
import time
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(SCRIPTS, 'dirty-ledger-track.py')
GATE = os.path.join(SCRIPTS, 'mandatory-review-gate.py')
LOG = os.path.join(SCRIPTS, 'log-review-pass.py')

EXPECTED_WORKSPACE_ROOT = os.path.realpath(os.path.join(SCRIPTS, '..', '..', '..', '..'))
EXPECTED_STATE_DIR = os.path.join(EXPECTED_WORKSPACE_ROOT, '.claude', 'state')

# A subdirectory cwd that is NOT the workspace root — this is the condition
# that hid D-14 (state dir derived from cwd, not script location).
SUBDIR_CWD = os.path.join(EXPECTED_WORKSPACE_ROOT, 'repos', 'ai-agency-core', 'wordpress-plugins')


def _clean_session(sid):
    for suffix in ['-dirty.jsonl', '-reviewed.jsonl']:
        p = os.path.join(EXPECTED_STATE_DIR, f'{sid}{suffix}')
        if os.path.exists(p):
            os.remove(p)


def _run_track(sid, tool_name, tool_input, cwd=None):
    return subprocess.run(
        ['python3', TRACK],
        input=json.dumps({
            'session_id': sid, 'tool_name': tool_name,
            'tool_input': tool_input, 'tool_result': {'text': 'ok'},
        }),
        capture_output=True, text=True, cwd=cwd or SUBDIR_CWD,
    )


def _run_gate(sid, cwd=None):
    return subprocess.run(
        ['python3', GATE],
        input=json.dumps({'session_id': sid, 'stop_reason': 'end_turn'}),
        capture_output=True, text=True, cwd=cwd or SUBDIR_CWD,
    )


def _run_log(sid, files, verdict='PASS', tier='full', gate_id='G-default',
             evidence=None, findings=None, cwd=None):
    if evidence is None:
        evidence = ('placeholder-sweep: 0 FILL/TBD tokens across 7 surfaces; '
                    'leak-audit: 0 foreign-client strings; structural integrity verified')
    args = ['python3', LOG, '--session', sid, '--files'] + files + [
        '--verdict', verdict, '--tier', tier, '--gate-id', gate_id,
        '--evidence', evidence,
    ]
    if findings:
        args += ['--findings', findings]
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd or SUBDIR_CWD)


class TestStateDirDerivation(unittest.TestCase):
    """D-14/D-15: STATE_DIR must resolve to workspace-root/.claude/state,
    not cwd-relative, not global ~/.claude/state, verified by absolute path."""

    def test_workspace_root_basename(self):
        sys.path.insert(0, SCRIPTS)
        from _paths import WORKSPACE_ROOT
        self.assertEqual(os.path.basename(WORKSPACE_ROOT), 'workspace',
                         f'WORKSPACE_ROOT={WORKSPACE_ROOT}')

    def test_state_dir_absolute_path(self):
        sys.path.insert(0, SCRIPTS)
        from _paths import STATE_DIR
        self.assertEqual(STATE_DIR, EXPECTED_STATE_DIR,
                         f'STATE_DIR={STATE_DIR} != {EXPECTED_STATE_DIR}')

    def test_state_dir_not_global(self):
        sys.path.insert(0, SCRIPTS)
        from _paths import STATE_DIR
        global_state = os.path.expanduser('~/.claude/state')
        self.assertNotEqual(STATE_DIR, global_state,
                            'STATE_DIR must not be the global ~/.claude/state')


class TestFullCycleFromSubdir(unittest.TestCase):
    """D-14: Write + block + review + approve from a subdirectory cwd.
    Writer and reader must use the SAME state dir (workspace root)."""

    SID = 'conformance-cycle'

    def setUp(self):
        _clean_session(self.SID)

    def tearDown(self):
        _clean_session(self.SID)

    def test_write_creates_ledger_at_workspace_root(self):
        _run_track(self.SID, 'Write', {
            'file_path': '/tmp/conformance-test.md', 'content': '# test',
        })
        dirty_path = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty_path),
                        f'Dirty ledger not at {dirty_path}')

    def test_write_does_not_create_subdir_ledger(self):
        _run_track(self.SID, 'Write', {
            'file_path': '/tmp/conformance-test.md', 'content': '# test',
        })
        subdir_dirty = os.path.join(SUBDIR_CWD, '.claude', 'state',
                                    f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(subdir_dirty),
                         f'Dirty ledger written to subdir: {subdir_dirty}')

    def test_full_block_review_approve_cycle(self):
        # Write
        _run_track(self.SID, 'Write', {
            'file_path': '/tmp/conformance-test.md', 'content': '# test',
        })
        # Block
        r_block = _run_gate(self.SID)
        self.assertEqual(r_block.returncode, 2, 'Stop must block (exit 2)')
        # Review
        time.sleep(0.05)
        _run_log(self.SID, ['/private/tmp/conformance-test.md'])
        # Approve
        time.sleep(0.05)
        r_approve = _run_gate(self.SID)
        self.assertEqual(r_approve.returncode, 0, 'Stop must approve (exit 0)')

    def test_approve_schema_valid(self):
        """Approve path emits {"continue": true} with no hookSpecificOutput (D-11)."""
        r = _run_gate(self.SID)  # empty session = immediate approve
        self.assertEqual(r.returncode, 0)
        parsed = json.loads(r.stdout.strip())
        self.assertTrue(parsed.get('continue'))
        self.assertNotIn('hookSpecificOutput', parsed,
                         'Stop approve must not contain hookSpecificOutput')


class TestReadOnlyExempt(unittest.TestCase):
    """Read-only commands must NOT create dirty entries."""

    SID = 'conformance-readonly'

    def setUp(self):
        _clean_session(self.SID)

    def tearDown(self):
        _clean_session(self.SID)

    def _assert_no_dirty(self, command, desc):
        _run_track(self.SID, 'Bash', {'command': command})
        dirty = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
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
        _clean_session(self.SID)

    def tearDown(self):
        _clean_session(self.SID)

    def _assert_dirty(self, command, desc):
        _run_track(self.SID, 'Bash', {'command': command})
        dirty = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
        self.assertTrue(os.path.exists(dirty), f'{desc}: no dirty entry')
        # Clean for next test
        os.remove(dirty)

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
        _clean_session(self.SID)

    def tearDown(self):
        _clean_session(self.SID)

    def test_full_path_log_review_pass(self):
        cmd = f'python3 {LOG} --session x --files /tmp/x --verdict PASS --tier full --gate-id G-default --evidence "placeholder-sweep clean"'
        _run_track(self.SID, 'Bash', {'command': cmd})
        dirty = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'log-review-pass.py command must be excluded')

    def test_full_path_mandatory_review_gate(self):
        cmd = f'python3 {GATE}'
        _run_track(self.SID, 'Bash', {'command': cmd})
        dirty = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'mandatory-review-gate.py command must be excluded')

    def test_full_path_dirty_ledger_track(self):
        cmd = f'python3 {TRACK}'
        _run_track(self.SID, 'Bash', {'command': cmd})
        dirty = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-dirty.jsonl')
        self.assertFalse(os.path.exists(dirty),
                         'dirty-ledger-track.py command must be excluded')


class TestAntiGaming(unittest.TestCase):
    """Boilerplate evidence rejected; real evidence accepted (D-09)."""

    SID = 'conformance-antigaming'

    def test_reject_bare_pass(self):
        r = _run_log(self.SID, ['/tmp/x'], evidence='PASS')
        self.assertEqual(r.returncode, 1, 'Bare "PASS" must be rejected')

    def test_reject_template_text(self):
        r = _run_log(self.SID, ['/tmp/x'],
                     evidence='gate-peer-reviewer: [summary of checks run and findings]; output-quality-loop: [verdict]')
        self.assertEqual(r.returncode, 1, 'Template text must be rejected')

    def test_accept_real_evidence(self):
        r = _run_log(self.SID, ['/tmp/x'],
                     evidence='placeholder-sweep: 0 FILL/TBD tokens across 7 surfaces; leak-audit: 0 foreign-client strings')
        self.assertEqual(r.returncode, 0, 'Real evidence must be accepted')
        # Clean
        reviewed = os.path.join(EXPECTED_STATE_DIR, f'{self.SID}-reviewed.jsonl')
        if os.path.exists(reviewed):
            os.remove(reviewed)


if __name__ == '__main__':
    # Always run from the subdir cwd to catch D-14-class bugs
    if not os.path.isdir(SUBDIR_CWD):
        os.makedirs(SUBDIR_CWD, exist_ok=True)
    os.chdir(SUBDIR_CWD)
    unittest.main()
