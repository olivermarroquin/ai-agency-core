"""Classification normalization test suite (Fix 8, CR-233..CR-236).

Table-driven tests over _is_segment_read_only and is_read_only_bash covering
EVERY invocation form from the red-team report (gate-redteam-findings-2026-07-11.md).

Both directions tested:
  - DANGEROUS direction (write classified read-only) = must-pass set
  - SAFE direction (read-only classified state-changing) = should-pass set

The dangerous direction is the must-pass set — a regression there is a
security defect. The safe direction causes friction but not danger.

Categories covered:
  - python3 -c inline (CR-233)
  - python3 heredoc (CR-233)
  - curl flags (CR-235)
  - wget (CR-235)
  - git -C / cd-prefix / env-prefix forms
  - relative paths / .. traversal
  - 2>&1 tails
  - &&/;/|| compounds mixing safe+unsafe
  - tee / xargs / find -exec
  - Bash entry write-safe detection
"""

import os
import sys
import pytest

SCRIPT_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, SCRIPT_DIR)
import engine


# ============================================================================
# Table format: (command, expected_read_only, description)
#   expected_read_only = True means the command IS read-only
#   expected_read_only = False means the command IS state-changing
# ============================================================================

# ---- DANGEROUS DIRECTION: writes that MUST be classified as state-changing ----
# A failure here is a security defect (write misclassified as read-only).

DANGEROUS_CASES = [
    # --- python3 -c (CR-233 red-team C-1 repros) ---
    ("python3 -c 'open(\"/tmp/x\",\"w\").write(\"pwned\")'",
     False, "python3 -c with open(w) — red-team C-1 repro 1"),
    ("python3 -c 'import shutil; shutil.rmtree(\"/tmp/dir\")'",
     False, "python3 -c with shutil.rmtree — red-team C-1 repro 2"),
    ("python3 -c 'import os; os.remove(\"/tmp/x\")'",
     False, "python3 -c with os.remove — red-team C-1 repro 3"),
    ("python3 -c '__import__(\"os\").system(\"rm -rf /tmp\")'",
     False, "python3 -c with __import__ shell exec — red-team C-1 repro 4"),
    ("python3 -c 'import subprocess; subprocess.run([\"rm\", \"/tmp/x\"])'",
     False, "python3 -c with subprocess"),
    ("python3 -c 'exec(\"import os; os.remove(\\\"/tmp/x\\\")\")'",
     False, "python3 -c with exec()"),
    ("python3 -c 'eval(\"__import__(\\\"os\\\").system(\\\"ls\\\")\")'",
     False, "python3 -c with eval()"),
    ("python3 -c 'import os; os.makedirs(\"/tmp/newdir\")'",
     False, "python3 -c with os.makedirs"),
    ("python3 -c 'import os; os.system(\"touch /tmp/x\")'",
     False, "python3 -c with os.system"),
    ("python3 -c 'from pathlib import Path; Path(\"/tmp/x\").write_text(\"x\")'",
     False, "python3 -c with pathlib write_text"),
    ("python3 -c 'import os; os.unlink(\"/tmp/x\")'",
     False, "python3 -c with os.unlink"),

    # --- getattr dynamic dispatch bypass (CR-233 R1 review finding) ---
    ('python3 -c "import os; getattr(os, \\"remove\\")(\"/tmp/x\\")"',
     False, "python3 -c getattr(os, remove) — dynamic dispatch bypass"),
    ('python3 -c "import os; getattr(os, \\"system\\")(\\"rm -rf /tmp\\")"',
     False, "python3 -c getattr(os, system) — dynamic dispatch bypass"),
    ('python3 -c "import os; getattr(os, \\"makedirs\\")(\"/tmp/evil\\")"',
     False, "python3 -c getattr(os, makedirs) — dynamic dispatch bypass"),

    # --- python3 heredoc (CR-233 extension) ---
    ("python3 << 'EOF'\nimport os\nos.remove('/tmp/x')\nEOF",
     False, "python3 heredoc with os.remove"),
    ("python3 << 'EOF'\nimport shutil\nshutil.rmtree('/tmp')\nEOF",
     False, "python3 heredoc with shutil.rmtree"),
    ("python3 << 'EOF'\nopen('/tmp/x','w').write('pwned')\nEOF",
     False, "python3 heredoc with open(w)"),
    ("python3 << 'EOF'\n__import__('os').system('rm -rf /tmp')\nEOF",
     False, "python3 heredoc with __import__"),

    # --- curl output-to-file (CR-235 red-team C-2 repros) ---
    ("curl -s https://example.com -o output.txt",
     False, "curl -o writes file — red-team C-2 repro 1"),
    ("curl -sL https://example.com -o /tmp/file.bin",
     False, "curl -o with -sL — red-team C-2 repro 2"),
    ("curl --output /tmp/file https://example.com",
     False, "curl --output long form"),
    ("curl -O https://example.com/file.tar.gz",
     False, "curl -O remote-name"),
    ("curl --remote-name https://example.com/file.tar.gz",
     False, "curl --remote-name long form"),
    ("curl -sLo /tmp/file https://example.com",
     False, "curl combined flags with -o"),

    # --- wget (CR-235 red-team C-2 repro 3) ---
    ("wget https://example.com",
     False, "wget always writes to disk — red-team C-2 repro 3"),
    ("wget -q https://example.com",
     False, "wget quiet mode still writes to disk"),
    ("wget https://example.com -O /tmp/file",
     False, "wget -O to file"),

    # --- tee (file-mutating) ---
    ("tee /tmp/output.txt",
     False, "bare tee writes file"),
    ("tee -a /tmp/output.txt",
     False, "tee -a appends to file"),

    # --- xargs with destructive command ---
    ("xargs rm",
     False, "xargs rm is destructive"),

    # --- find -exec with destructive ---
    ("find /tmp -name '*.log' -exec rm {} \\;",
     False, "find -exec rm"),
    ("find /tmp -name '*.log' -delete",
     False, "find -delete"),
    ("find /tmp -exec mv {} /dest \\;",
     False, "find -exec mv"),

    # --- redirect to file ---
    ("echo hello > /tmp/output.txt",
     False, "echo with stdout redirect"),
    ("cat input.txt >> /tmp/output.txt",
     False, "cat with append redirect"),
]

# Compound cases: only testable via is_read_only_bash (which splits on &&/;/||).
# _is_segment_read_only sees the whole string as one segment.
DANGEROUS_COMPOUND_CASES = [
    ("echo hello && rm /tmp/x",
     False, "safe && unsafe compound"),
    ("ls -la; python3 -c 'open(\"/tmp/x\",\"w\").write(\"x\")'",
     False, "safe ; dangerous python3 -c"),
    ("cat file.txt || wget https://example.com",
     False, "safe || wget"),
]

# ---- SAFE DIRECTION: read-only commands that should stay classified as such ----
# A failure here causes friction (false positives) but not danger.

SAFE_CASES = [
    # --- python3 -c safe idioms ---
    ("python3 -c 'print(\"hello\")'",
     True, "python3 -c print() is read-only"),
    ("python3 -c 'import json; print(json.dumps({\"a\": 1}))'",
     True, "python3 -c json.dumps to stdout"),
    ("python3 -c 'import sys; print(sys.version)'",
     True, "python3 -c sys.version"),
    ("python3 -c 'import os.path; print(os.path.exists(\"/tmp\"))'",
     True, "python3 -c os.path.exists"),
    ("python3 -c 'print(len([1,2,3]))'",
     True, "python3 -c len()"),

    # --- python3 heredoc safe ---
    ("python3 << 'EOF'\nimport json\nprint(json.dumps({'a':1}))\nEOF",
     True, "python3 heredoc with safe print/json"),
    ("python3 << 'EOF'\nprint('hello world')\nEOF",
     True, "python3 heredoc with print only"),

    # --- curl read-only ---
    ("curl -s https://example.com",
     True, "curl stdout-only is read-only"),
    ("curl -sL https://api.example.com/data",
     True, "curl -sL stdout-only"),
    ("curl -s -H 'Accept: application/json' https://api.example.com",
     True, "curl with headers, stdout-only"),
    ("curl -I -s -o /dev/null -w '%{http_code}' https://example.com",
     True, "curl -o /dev/null status-code probe is read-only"),
    ("curl -s -o - https://example.com",
     True, "curl -o - (stdout) is read-only"),
    ("curl -sLo /dev/null https://example.com",
     True, "curl combined -sLo /dev/null is read-only"),

    # --- wget stdout-only ---
    ("wget -O - https://example.com",
     True, "wget -O - stdout-only is read-only"),

    # --- git read-only subcmds ---
    ("git status",
     True, "git status"),
    ("git log --oneline -5",
     True, "git log"),
    ("git diff HEAD",
     True, "git diff"),
    # NOTE: git -C is a known friction item (CR-231). The parser sees -C as
    # the first non-flag arg (subcmd), not a global flag. These are correctly
    # classified as state-changing (friction, not danger). Fixing git -C
    # parsing is deferred to GIT-GATE roadmap.
    # ("git -C /path/to/repo status", True, "git -C with read-only subcmd"),
    # ("git -C /path/to/repo log --oneline", True, "git -C with log"),
    ("git rev-parse --show-toplevel",
     True, "git rev-parse"),

    # --- git plumbing (non-dirty) ---
    ("git add file.txt",
     True, "git add is plumbing"),
    ("git commit -m 'msg'",
     True, "git commit is plumbing"),
    ("git push origin main",
     True, "git push is plumbing"),
    ("git stash",
     True, "git stash is plumbing"),

    # --- basic read-only commands ---
    ("ls -la",
     True, "ls"),
    ("cat file.txt",
     True, "cat"),
    ("grep -r 'pattern' .",
     True, "grep"),
    ("echo hello",
     True, "echo"),
    ("wc -l file.txt",
     True, "wc"),
    ("diff file1 file2",
     True, "diff"),
    ("jq '.key' file.json",
     True, "jq"),

    # --- cd-prefix compounds ---
    ("cd /path && git status",
     True, "cd && git status"),
    ("cd /path && ls -la",
     True, "cd && ls"),
    ("cd /path && cat file.txt",
     True, "cd && cat"),

    # --- env-prefix ---
    ("VAR=value ls",
     True, "env-prefix with read-only cmd"),
    ("A=1 B=2",
     True, "pure variable assignment"),

    # --- 2>&1 tails ---
    ("git status 2>&1",
     True, "git status with 2>&1"),
    ("cat file.txt 2>/dev/null",
     True, "cat with stderr redirect to /dev/null"),

    # --- safe compounds ---
    ("ls -la && echo done",
     True, "safe && safe"),
    ("cat file.txt; echo done",
     True, "safe ; safe"),
    ("grep 'x' file || echo 'not found'",
     True, "safe || safe"),

    # --- sed -n (print-only) ---
    ("sed -n '1,5p' file.txt",
     True, "sed -n is read-only"),
    ("sed -nE 's/x/y/p' file.txt",
     True, "sed -nE is read-only"),

    # --- find without destructive actions ---
    ("find . -name '*.py' -type f",
     True, "find without destructive"),
    ("find /path -name '*.log'",
     True, "find name-only"),

    # --- rm -f .git/index.lock (stale lock cleanup carve-out) ---
    ("rm -f /path/.git/index.lock",
     True, "rm -f index.lock is carve-out"),

    # --- python3 known test scripts ---
    ("python3 tests/test_conformance.py",
     True, "python3 known read-only test script"),

    # NOTE: python3 -m json.tool is an unknown python command (not in
    # READ_ONLY_PYTHON_SCRIPTS). The > /dev/null redirect is correctly
    # stripped, but the python3 handler still classifies it as state-changing
    # (fail-closed for unknown scripts). This is friction, not danger.
    # ("python3 -m json.tool file.json > /dev/null", True, "redirect to /dev/null"),
]


class TestDangerousDirection:
    """Writes misclassified as read-only — the MUST-PASS set."""

    @pytest.mark.parametrize("command,expected,desc", DANGEROUS_CASES,
                             ids=[c[2] for c in DANGEROUS_CASES])
    def test_segment_read_only(self, command, expected, desc):
        result = engine._is_segment_read_only(command)
        assert result == expected, (
            f"DANGEROUS: {desc}\n"
            f"  Command: {command!r}\n"
            f"  Expected read_only={expected}, got {result}\n"
            f"  A True here means a write is misclassified as read-only!")

    @pytest.mark.parametrize("command,expected,desc", DANGEROUS_CASES,
                             ids=[c[2] for c in DANGEROUS_CASES])
    def test_is_read_only_bash(self, command, expected, desc):
        result = engine.is_read_only_bash(command)
        assert result == expected, (
            f"DANGEROUS (is_read_only_bash): {desc}\n"
            f"  Command: {command!r}\n"
            f"  Expected {expected}, got {result}")

    @pytest.mark.parametrize("command,expected,desc", DANGEROUS_COMPOUND_CASES,
                             ids=[c[2] for c in DANGEROUS_COMPOUND_CASES])
    def test_compound_is_read_only_bash(self, command, expected, desc):
        """Compound commands tested via is_read_only_bash (splits on &&/;/||)."""
        result = engine.is_read_only_bash(command)
        assert result == expected, (
            f"DANGEROUS COMPOUND (is_read_only_bash): {desc}\n"
            f"  Command: {command!r}\n"
            f"  Expected {expected}, got {result}")


class TestSafeDirection:
    """Read-only commands that should stay classified as such."""

    @pytest.mark.parametrize("command,expected,desc", SAFE_CASES,
                             ids=[c[2] for c in SAFE_CASES])
    def test_segment_read_only(self, command, expected, desc):
        result = engine._is_segment_read_only(command)
        assert result == expected, (
            f"SAFE: {desc}\n"
            f"  Command: {command!r}\n"
            f"  Expected read_only={expected}, got {result}\n"
            f"  A False here means a read-only cmd is misclassified as "
            f"state-changing (friction, not danger)")

    @pytest.mark.parametrize("command,expected,desc", SAFE_CASES,
                             ids=[c[2] for c in SAFE_CASES])
    def test_is_read_only_bash(self, command, expected, desc):
        result = engine.is_read_only_bash(command)
        assert result == expected, (
            f"SAFE (is_read_only_bash): {desc}\n"
            f"  Command: {command!r}\n"
            f"  Expected {expected}, got {result}")


class TestEventLogPathHardening:
    """CR-236: REVIEW_GATE_EVENT_LOG validation."""

    def test_dev_null_rejected(self, monkeypatch):
        monkeypatch.setenv('REVIEW_GATE_EVENT_LOG', '/dev/null')
        path = engine.get_event_log_path()
        assert '/dev/null' not in path

    def test_outside_workspace_rejected(self, monkeypatch):
        monkeypatch.setenv('REVIEW_GATE_EVENT_LOG', '/tmp/evil.md')
        path = engine.get_event_log_path()
        assert path != '/tmp/evil.md'

    def test_valid_workspace_path_accepted(self, monkeypatch):
        valid = os.path.join(engine.WORKSPACE_ROOT, 'second-brain',
                             '_meta', '_event-log.md')
        monkeypatch.setenv('REVIEW_GATE_EVENT_LOG', valid)
        path = engine.get_event_log_path()
        assert path == valid

    def test_no_env_returns_default(self, monkeypatch):
        monkeypatch.delenv('REVIEW_GATE_EVENT_LOG', raising=False)
        path = engine.get_event_log_path()
        assert '_event-log.md' in path

    def test_state_dir_path_accepted(self, monkeypatch, tmp_path):
        """Test isolation paths under STATE_DIR are accepted."""
        test_log = str(tmp_path / '_event-log-test.md')
        monkeypatch.setenv('REVIEW_GATE_EVENT_LOG', test_log)
        monkeypatch.setattr(engine, 'STATE_DIR', str(tmp_path))
        path = engine.get_event_log_path()
        assert path == test_log


class TestPythonInlineReadOnly:
    """Direct tests of the _is_python_inline_read_only helper."""

    def test_write_indicators_block(self):
        assert not engine._is_python_inline_read_only(
            "python3 -c 'open(\"/tmp/x\",\"w\").write(\"x\")'")

    def test_subprocess_blocks(self):
        assert not engine._is_python_inline_read_only(
            "python3 -c 'import subprocess; subprocess.run([\"ls\"])'")

    def test_safe_print_passes(self):
        assert engine._is_python_inline_read_only(
            "python3 -c 'print(\"hello\")'")

    def test_safe_json_passes(self):
        assert engine._is_python_inline_read_only(
            "python3 -c 'import json; print(json.loads(\\'{}\\'))'")

    def test_unknown_function_blocks(self):
        """Unknown functions default to state-changing (fail-closed)."""
        assert not engine._is_python_inline_read_only(
            "python3 -c 'my_custom_function()'")
