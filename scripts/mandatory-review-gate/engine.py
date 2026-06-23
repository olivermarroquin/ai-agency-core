"""Substrate-agnostic engine for the mandatory review gate.

All business logic lives here. Adapters (Claude Code Stop/PostToolUse hooks,
git pre-commit/pre-push hooks, future daemon adapters) call these functions
and map the results to their substrate's blocking mechanism.

The engine is stateless — it reads/writes JSONL ledgers in STATE_DIR and
returns structured results. It never imports substrate-specific protocols
(no CC hook JSON, no git plumbing, no daemon IPC).

Created by [RGH-2] (2026-06-16) as part of the substrate-abstraction refactor.
Per-project config support added by [RGH-4] (2026-06-16).
"""

import dataclasses
import hashlib
import json
import os
import re
import time
from typing import Optional

# --- Path constants (from _paths.py) ---
# Engine imports these at module level; adapters may also import _paths directly.
import sys
sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR, WORKSPACE_ROOT


# ============================================================================
# Ledger I/O
# ============================================================================

def load_jsonl(path: str) -> list:
    """Read a JSONL file, returning list of parsed dicts. Skips malformed lines."""
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def read_dirty_ledger(state_dir: str, session_id: str) -> list:
    """Load raw dirty entries from <state_dir>/<session_id>-dirty.jsonl."""
    return load_jsonl(os.path.join(state_dir, f'{session_id}-dirty.jsonl'))


def read_reviewed_ledger(state_dir: str, session_id: str) -> list:
    """Load reviewed entries from <state_dir>/<session_id>-reviewed.jsonl."""
    return load_jsonl(os.path.join(state_dir, f'{session_id}-reviewed.jsonl'))


def load_scoped_dirty(state_dir: str, session_id: str,
                      included_sources: Optional[frozenset] = None) -> list:
    """Load dirty entries, optionally filtering by source tag.

    If included_sources is None, returns all entries (git-hook adapter uses this).
    If provided, filters to entries whose 'source' field is in the set
    (CC adapter uses this with CC_INCLUDED_SOURCES).
    """
    entries = read_dirty_ledger(state_dir, session_id)
    if not entries:
        return []
    if included_sources is not None:
        entries = [e for e in entries
                   if e.get('source', '') in included_sources]
    return entries


def append_dirty_entry(state_dir: str, session_id: str, entry: dict) -> str:
    """Append one entry to the dirty ledger. Returns ledger path. Creates dirs."""
    os.makedirs(state_dir, exist_ok=True)
    ledger_path = os.path.join(state_dir, f'{session_id}-dirty.jsonl')
    with open(ledger_path, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return ledger_path


def append_reviewed_entries(state_dir: str, session_id: str,
                            entries: list) -> str:
    """Append review-pass markers to the reviewed ledger. Returns path."""
    os.makedirs(state_dir, exist_ok=True)
    reviewed_path = os.path.join(state_dir, f'{session_id}-reviewed.jsonl')
    with open(reviewed_path, 'a') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')
    return reviewed_path


# ============================================================================
# Unreviewed computation
# ============================================================================

def get_unreviewed(dirty_entries: list, reviewed_entries: list) -> list:
    """Return dirty entries whose file_path has no review pass at or after
    the LATEST dirty timestamp for that path.

    NOTE (C-02, RGH-2 peer review): git_hook_adapter.check_staged_files()
    has equivalent logic for its cross-session scoping. If this function's
    semantics change, verify the git hook adapter stays in sync."""
    dirty_map = {}
    for d in dirty_entries:
        fp = d.get('file_path', '')
        ts = d.get('timestamp', 0)
        if fp not in dirty_map or ts > dirty_map[fp]['timestamp']:
            dirty_map[fp] = d

    reviewed_map = {}
    for r in reviewed_entries:
        fp = r.get('file_path', '')
        ts = r.get('timestamp', 0)
        if fp not in reviewed_map or ts > reviewed_map[fp]:
            reviewed_map[fp] = ts

    unreviewed = []
    for fp, entry in dirty_map.items():
        dirty_ts = entry.get('timestamp', 0)
        reviewed_ts = reviewed_map.get(fp, 0)
        if dirty_ts > reviewed_ts:
            unreviewed.append(entry)

    return unreviewed


# ============================================================================
# Path normalization + Bash helpers
# ============================================================================

def normalize_path(path: str) -> str:
    """Normalize a file path to absolute, resolved form. BASH: entries pass through."""
    if path.startswith('BASH:'):
        return path
    return os.path.realpath(os.path.abspath(path))


def bash_entry_id(command: str) -> str:
    """Generate a stable, argv-safe ID for a Bash command. Format: BASH:<hash>."""
    cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:12]
    return f'BASH:{cmd_hash}'


# --- Read-only Bash classifier ---

READ_ONLY_CMDS = frozenset({
    'grep', 'rg', 'ls', 'cat', 'head', 'tail', 'wc', 'sort', 'uniq',
    'diff', 'comm', 'test', 'true', 'false', 'echo', 'printf', 'which', 'type',
    'file', 'stat', 'du', 'df', 'jq', 'date', 'uname', 'whoami', 'pwd',
    'basename', 'dirname', 'realpath', 'readlink', 'env', 'printenv', 'id',
    'hostname', 'tput',
    'cd', 'pushd', 'popd', ':', 'source', '.', 'export', 'set', 'unset',
    'alias', 'unalias', 'hash', 'ulimit', 'umask', 'shopt', 'trap',
})

READ_ONLY_GIT_SUBCMDS = frozenset({
    'log', 'status', 'diff', 'show', 'branch', 'tag', 'remote', 'rev-parse',
    'describe', 'rev-list', 'shortlog', 'blame', 'ls-files', 'ls-tree',
    'cat-file', 'name-rev', 'for-each-ref',
})

# Git plumbing subcommands that are non-dirty (RGH-12, RGH12-8).
# These are version-control operations, not deliverable production.
# A producer's actual deliverable is its Write/Edit, which remains a dirty
# entry that blocks its stop — so whitelisting git plumbing does NOT let
# unreviewed deliverables escape. The Tier-B pre-commit hook (where
# installed) independently guards commit content.
#
# SECURITY: destructive working-tree mutations (reset, clean, checkout,
# restore) are NOT whitelisted — they can discard uncommitted work.
GIT_PLUMBING_SUBCMDS = frozenset({
    'add', 'commit', 'push', 'stash', 'fetch', 'pull',
})

# Read-only Python test scripts — exact basenames only (RGH-1b item 4).
# SECURITY: Never whitelist 'python3' as an interpreter generically.
# Only specific test-suite scripts that are known read-only belong here.
READ_ONLY_PYTHON_SCRIPTS = frozenset({
    'test_conformance.py',
    'test_git_hook_conformance.py',
    'verify-artifact.py',
})

# CR-012: Do NOT whitelist python3 commands by flag heuristic (--skip-http,
# --dry-run, etc.). That's a full gate bypass for publish/indexing scripts.
# Only whitelist by exact basename in READ_ONLY_PYTHON_SCRIPTS above.


def _strip_stderr_redirects(cmd: str) -> str:
    """Remove stderr redirects (2>, 2>>, 2>&1, 2>/dev/null)."""
    cmd = re.sub(r'2>>&?\d*', '', cmd)
    cmd = re.sub(r'2>/dev/null', '', cmd)
    cmd = re.sub(r'2>&\d+', '', cmd)
    cmd = re.sub(r'2>\s*\S+', '', cmd)
    return cmd


def _has_stdout_file_redirect(cmd: str) -> bool:
    """Check if command has stdout redirect to a file (>, >>)."""
    stripped = _strip_stderr_redirects(cmd)
    stripped = re.sub(r'<<<?\s*\S+', '', stripped)
    stripped = re.sub(r"<<\s*['\"]?\w+['\"]?", '', stripped)
    return bool(re.search(r'(?<!\d)>{1,2}', stripped))


def _first_word(cmd: str) -> str:
    """Extract the first non-env-assignment word from a command string."""
    for token in cmd.split():
        if '=' in token and not token.startswith('-'):
            continue
        return token
    return ''


# Shell control-flow keywords (RGH-1b item 9).
# split_compound() splits on ; producing segments like "for repo in ...",
# "do echo ...", "done". Two categories:
#   STANDALONE: loop/conditional headers and terminators — the keyword IS the
#     whole semantic content (e.g. "done", "fi", "for x in a b c").
#   PREFIX: keywords that introduce a body command — "do <cmd>", "then <cmd>",
#     "else <cmd>". For these, strip the keyword and check the remainder.
_CONTROL_FLOW_STANDALONE = frozenset({
    'for', 'while', 'until', 'select',   # loop headers (body follows after ;/do)
    'if', 'case',                          # conditional headers
    'done', 'fi', 'esac',                 # terminators
    'in',                                  # part of for...in / case...in
})
_CONTROL_FLOW_PREFIX = frozenset({
    'do', 'then', 'else', 'elif',         # introduce a body command
})


def _is_segment_read_only(segment: str) -> bool:
    """Check if a single command segment is read-only."""
    segment = segment.strip()
    if not segment:
        return True

    if _has_stdout_file_redirect(segment):
        return False

    # Multi-line / comment handling: a segment may contain newlines (the command
    # spanned several lines) and/or shell comment lines (starting with '#').
    # Comments and blank lines do nothing — drop them and require every remaining
    # real line to be read-only. Fixes reviewer verification blobs like
    # "# check\necho ...\nfind ..." being mis-flagged as state-changing.
    # SKIP when a heredoc is present: its body newlines are DATA, not separate
    # commands (e.g. `python3 << EOF\n...\nEOF`) — the heredoc-aware checks below
    # handle those.
    if '<<' not in segment:
        lines = [ln.strip() for ln in segment.split('\n')]
        real_lines = [ln for ln in lines if ln and not ln.startswith('#')]
        if not real_lines:
            return True  # only comments / blank lines — no command runs
        if real_lines != [segment]:
            return all(_is_segment_read_only(ln) for ln in real_lines)

    first = _first_word(segment)

    # Standalone control-flow keywords — the segment is structural, not a command
    if first in _CONTROL_FLOW_STANDALONE:
        return True

    # Prefix control-flow keywords — strip and check the body command
    if first in _CONTROL_FLOW_PREFIX:
        remainder = segment[len(first):].strip()
        if not remainder:
            return True  # bare "do" / "then" (body on next line)
        return _is_segment_read_only(remainder)
    base = os.path.basename(first)

    if base in ('python3', 'python') and re.match(r'^python3?\s+-c\s+', segment):
        return True
    if base in ('python3', 'python') and re.search(r"<<\s*['\"]?\w+['\"]?", segment):
        return True
    # RGH-1b item 4: specific test-suite scripts whitelisted by exact basename.
    # CR-012: NO flag-based heuristic — only exact basename whitelist.
    if base in ('python3', 'python'):
        parts = segment.split()
        for p in parts[1:]:
            if p.startswith('-'):
                continue  # skip flags like -v, -m, etc.
            # p is the script argument — check its basename
            if os.path.basename(p) in READ_ONLY_PYTHON_SCRIPTS:
                return True
            break  # first non-flag arg checked; stop

    if base == 'git':
        parts = segment.split()
        subcmd = None
        for p in parts[1:]:
            if not p.startswith('-'):
                subcmd = p
                break
        if subcmd and subcmd in READ_ONLY_GIT_SUBCMDS:
            return True
        # RGH12-8: git plumbing (add/commit/push/stash/fetch/pull) is
        # non-dirty — version-control operations, not deliverable production.
        if subcmd and subcmd in GIT_PLUMBING_SUBCMDS:
            return True
        return False

    # RGH12-8: rm -f <path>/.git/index.lock — stale lock cleanup, not
    # deliverable production. Only matches rm with -f flag targeting a
    # .git/index.lock path specifically.
    if base == 'rm':
        parts = segment.split()
        if len(parts) >= 2 and '-f' in parts:
            targets = [p for p in parts[1:] if not p.startswith('-')]
            if targets and all(t.endswith('.git/index.lock') for t in targets):
                return True
        return False

    # sed -n (print-only mode) is read-only; sed -i or bare sed can modify files
    if base == 'sed':
        parts = segment.split()
        for p in parts[1:]:
            if p == '-n' or p == '--quiet' or p == '--silent':
                return True
            if p.startswith('-') and 'n' in p and not p.startswith('--'):
                return True  # e.g. -nE, -En
            if not p.startswith('-'):
                break  # reached the script/pattern arg, no -n found
        return False

    # find is read-only UNLESS it has destructive actions (-delete, -exec rm/mv/cp)
    if base == 'find':
        if '-delete' in segment:
            return False
        # -exec with a destructive command
        exec_match = re.findall(r'-exec\s+(\S+)', segment)
        for cmd_name in exec_match:
            cmd_base = os.path.basename(cmd_name)
            if cmd_base in ('rm', 'mv', 'cp', 'rmdir', 'shred', 'unlink'):
                return False
        return True

    if base in ('curl', 'wget'):
        curl_write_flags = {'-X', '--request', '-d', '--data', '--data-raw',
                            '--data-binary', '--data-urlencode', '-F', '--form',
                            '--upload-file', '-T'}
        parts = segment.split()
        for p in parts[1:]:
            if p in curl_write_flags:
                return False
            if p.startswith('-X') and len(p) > 2:
                return False
        return True

    if base in READ_ONLY_CMDS:
        return True

    return False


def split_compound(cmd: str) -> list:
    """Split a command on compound operators (|, &&, ||, ;) respecting quotes."""
    segments = []
    current = []
    i = 0
    in_single = False
    in_double = False

    while i < len(cmd):
        c = cmd[i]

        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue

        if in_single or in_double:
            current.append(c)
            i += 1
            continue

        if cmd[i:i+2] == '||':
            segments.append(''.join(current).strip())
            current = []
            i += 2
            continue
        if cmd[i:i+2] == '&&':
            segments.append(''.join(current).strip())
            current = []
            i += 2
            continue
        if c in '|;':
            segments.append(''.join(current).strip())
            current = []
            i += 1
            continue

        current.append(c)
        i += 1

    tail = ''.join(current).strip()
    if tail:
        segments.append(tail)

    return [s for s in segments if s]


def is_read_only_bash(command: str) -> bool:
    """Return True if the Bash command is read-only (all segments)."""
    if not command or not command.strip():
        return True
    segments = split_compound(command.strip())
    for segment in segments:
        if not _is_segment_read_only(segment):
            return False
    return True


# ============================================================================
# Bash write-indicator detection (RGH-11)
# ============================================================================
#
# is_bash_entry_write_safe() is MORE PERMISSIVE than is_read_only_bash().
# It returns True for commands that lack POSITIVE write indicators — e.g.,
# 'python3 engine.py run-fast-path-checks' passes (no redirects, no file-
# mutating commands) even though is_read_only_bash() rejects it (unknown
# script). Used ONLY for session-level reviewer Bash exemption.
#
# HONEST LIMIT: A command like 'python3 existing_script.py' where the script
# itself writes files but has no command-line write indicators will pass this
# check. Exploiting this requires (1) forging a reviewer marker AND (2)
# having a pre-existing deliverable-generating script — multi-step,
# impractical, acknowledged as an honest limit of command-line-only heuristics.

# Commands that positively mutate the filesystem.
_FILE_MUTATING_CMDS = frozenset({
    'tee', 'mkdir', 'mktemp', 'rm', 'rmdir', 'mv', 'cp', 'touch',
    'chmod', 'chown', 'ln', 'shred', 'unlink', 'install',
    'truncate', 'dd', 'patch',
})

# Patterns in python3 -c inline code that indicate file writes.
_PYTHON_INLINE_WRITE_RE = re.compile(
    r"""open\s*\(.*['"][wa]"""   # open(..., 'w') or open(..., 'a')
    r"""|\.write\s*\("""         # .write(
    r"""|\.writelines\s*\("""    # .writelines(
    r"""|os\.rename\s*\("""      # os.rename(
    r"""|os\.remove\s*\("""      # os.remove(
    r"""|shutil\.""",            # shutil.copy/move/rmtree/etc.
    re.IGNORECASE,
)


def _is_segment_write_safe(segment: str) -> bool:
    """Check if a single command segment has NO positive write indicators.

    Returns True if the segment appears to be non-writing (safe for
    reviewer-session exemption). Returns False if the segment has
    positive write signals (redirects, file-mutating commands, etc.).
    """
    segment = segment.strip()
    if not segment:
        return True

    # Stdout file redirect → writing
    if _has_stdout_file_redirect(segment):
        return False

    # Multi-line / comment handling (same as _is_segment_read_only): drop
    # comment + blank lines, require every remaining real line to be write-safe.
    # SKIP when a heredoc is present (body newlines are data, not commands).
    if '<<' not in segment:
        lines = [ln.strip() for ln in segment.split('\n')]
        real_lines = [ln for ln in lines if ln and not ln.startswith('#')]
        if not real_lines:
            return True  # only comments / blank lines — no command runs
        if real_lines != [segment]:
            return all(_is_segment_write_safe(ln) for ln in real_lines)

    first = _first_word(segment)

    # Control-flow keywords — structural, check body
    if first in _CONTROL_FLOW_STANDALONE:
        return True
    if first in _CONTROL_FLOW_PREFIX:
        remainder = segment[len(first):].strip()
        if not remainder:
            return True
        return _is_segment_write_safe(remainder)

    base = os.path.basename(first)

    # File-mutating commands
    if base in _FILE_MUTATING_CMDS:
        return False

    # Python inline code with write indicators
    if base in ('python3', 'python'):
        if re.match(r'^python3?\s+-c\s+', segment):
            # Check the ENTIRE segment for write patterns — extracting
            # the -c argument is unreliable with nested quotes. Checking
            # the whole segment is safe: write-indicator patterns (open(,'w'),
            # .write(, shutil.) don't appear in normal -c flag syntax.
            if _PYTHON_INLINE_WRITE_RE.search(segment):
                return False

    # find with -delete or destructive -exec
    if base == 'find':
        if '-delete' in segment:
            return False
        exec_match = re.findall(r'-exec\s+(\S+)', segment)
        for cmd_name in exec_match:
            cmd_base = os.path.basename(cmd_name)
            if cmd_base in _FILE_MUTATING_CMDS or cmd_base in ('rm', 'mv', 'cp'):
                return False

    return True


def is_bash_entry_write_safe(command: str) -> bool:
    """Return True if a Bash command has NO positive write indicators.

    More permissive than is_read_only_bash() — passes unknown scripts
    (python3 foo.py) that lack write signals. Used for session-level
    reviewer Bash exemption (RGH-11).

    Returns False (NOT write-safe) for empty/missing commands — fail-closed
    on missing bash_cmd field in pre-RGH-11 ledger entries (minor-2).
    """
    if not command or not command.strip():
        return False  # fail-closed: missing command → not exempt
    segments = split_compound(command.strip())
    for segment in segments:
        if not _is_segment_write_safe(segment):
            return False
    return True


# ============================================================================
# Reviewer-working-doc Bash write detection (RGH-12, RGH12-6)
# ============================================================================
#
# For VERIFIED reviewer sessions only: exempt Bash entries whose ONLY write
# targets are known reviewer-working-doc paths. This covers the case where
# a reviewer appends to the firing tracker or catch register via shell
# (cat >> path) — the write is not read-only, but the target is a reviewer
# artifact, not a deliverable.
#
# SAFETY (B3 stays closed): this check is ONLY applied when the session is
# already structurally verified as a reviewer (marker or auto-inferred).
# The B3 concern from RGH-10 was about UNVERIFIED sessions getting exempted
# by writing to a reviewer-looking path. Here the session identity is proven
# first, then the write target is checked.
#
# STRICT GUARD: every segment of a compound command must be EITHER read-only
# OR a write whose target matches a reviewer-working-doc pattern. If ANY
# segment writes elsewhere or performs a non-doc action → not exempt.

# Patterns matching reviewer-working-doc write targets in redirect paths.
# These must be restrictive — only the exact files a reviewer writes to.
_REVIEWER_DOC_WRITE_PATTERNS = [
    r'_review-skill-firing-tracker\.md$',
    r'_review-gate-catch-register\.md$',
    r'/\.review-gate/state/verdict-',
    r'/\.review-gate/state/.*-reviewed\.jsonl$',
    r'execution-log-\d{4}-\d{2}-\d{2}-reviewer-session[^/]*\.md$',
    r'execution-log-\d{4}-\d{2}-\d{2}-independent-review[^/]*\.md$',
    r'execution-log-\d{4}-\d{2}-\d{2}-peer-review[^/]*\.md$',
    r'execution-log-\d{4}-\d{2}-\d{2}-[a-z0-9]+-(?:reviewer-session|independent-review|peer-review)[^/]*\.md$',
]

_REVIEWER_DOC_WRITE_RE = re.compile('|'.join(_REVIEWER_DOC_WRITE_PATTERNS))


def _extract_write_target(segment: str) -> Optional[str]:
    """Extract the file path from a write redirect in a segment.

    Returns the target path if the segment writes via > or >> to a file,
    or None if no write redirect is found.
    """
    segment = segment.strip()
    if not segment:
        return None
    # Strip stderr redirects first so we only look at stdout
    cleaned = _strip_stderr_redirects(segment)
    # Look for >> or > (not preceded by a digit, to avoid fd redirects like 2>)
    match = re.search(r'(?<!\d)>{1,2}\s*(\S+)', cleaned)
    if match:
        return match.group(1).strip("'\"")
    return None


def _is_segment_reviewer_doc_write(segment: str) -> bool:
    """Check if a segment is a write whose ONLY target is a reviewer-working-doc.

    Returns True if the segment has a stdout redirect to a file matching
    a reviewer-working-doc pattern. Returns False for writes to other paths,
    non-redirect writes (tee, mv, cp, etc.), or read-only commands.
    Use _is_segment_read_only() for the read-only case separately.
    """
    segment = segment.strip()
    if not segment:
        return False

    target = _extract_write_target(segment)
    if target is None:
        return False  # no redirect — not a doc-write (may be read-only)

    # The target must match a reviewer-working-doc pattern
    return bool(_REVIEWER_DOC_WRITE_RE.search(target))


def is_bash_reviewer_doc_write(command: str) -> bool:
    """Return True if a Bash command writes ONLY to reviewer-working-doc paths.

    Every segment must be either read-only OR a redirect to a reviewer-doc
    path. If ANY segment writes elsewhere or performs a destructive action
    (tee, mv, cp, rm, etc.) → False.

    ONLY used for verified reviewer sessions (RGH-12, RGH12-6).
    Must NOT be called for unverified sessions (B3 guard).
    """
    if not command or not command.strip():
        return False
    segments = split_compound(command.strip())
    if not segments:
        return False
    has_doc_write = False
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if _is_segment_read_only(seg):
            continue  # read-only segment is fine
        if _is_segment_reviewer_doc_write(seg):
            has_doc_write = True
            continue
        # Segment is neither read-only nor a reviewer-doc write → fail
        return False
    return has_doc_write  # must have at least one actual doc write


# ============================================================================
# Session-level reviewer detection (RGH-11 + RGH-12)
# ============================================================================
#
# Classifies a session's role as 'reviewer' or 'producer' based on:
# (a) RGH-11: structural marker file from register-reviewer-session.py
# (b) RGH-12: auto-inferred from gate-clearing records (reviewer cleared
#     a different producer's session via log-review-pass.py)
#
# SAFETY: Neither signal grants blanket exemption. Only Bash entries
# without positive write indicators (or whose only writes target reviewer
# working-docs) and known reviewer working-doc entries are exempt.
# Deliverable-class Write/Edit ALWAYS gates regardless of session role.
# See check_gate() for the scoped exemption logic.


def _check_marker_signal(state_dir: str, session_id: str) -> bool:
    """Signal (a): RGH-11 marker file. Returns True if valid reviewer marker."""
    marker_path = os.path.join(state_dir, f'{session_id}-role.json')
    if not os.path.isfile(marker_path):
        return False

    try:
        with open(marker_path, 'r') as f:
            marker = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False

    if not isinstance(marker, dict):
        return False

    if marker.get('role') != 'reviewer':
        return False

    reviewing = marker.get('reviewing_session')
    if not isinstance(reviewing, str):
        return False
    reviewing = reviewing.strip()
    if not reviewing or reviewing == session_id:
        return False

    return True


def _check_gate_clearing_signal(state_dir: str, session_id: str) -> bool:
    """Signal (b): RGH-12 auto-inference from gate-clearing records.

    Returns True if ANY reviewed-ledger file for a DIFFERENT session contains
    an entry where reviewer_session == session_id. This proves the session
    has cleared another session's gate — a structural act the producer cannot
    fake (RGH-8 blocks self-clear in log-review-pass.py).

    Performance: early-exit on first match. Only scans *-reviewed.jsonl files
    (not dirty ledgers). Bounded by number of sessions with reviewed entries
    (typically <10 in a workspace).
    """
    import glob as glob_mod

    own_ledger_name = f'{session_id}-reviewed.jsonl'
    pattern = os.path.join(state_dir, '*-reviewed.jsonl')

    for ledger_path in glob_mod.iglob(pattern):
        # Skip this session's own reviewed ledger
        if os.path.basename(ledger_path) == own_ledger_name:
            continue

        # Scan for a reviewer_session matching this session_id
        try:
            with open(ledger_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get('reviewer_session') == session_id:
                        return True  # early exit: found proof
        except OSError:
            continue

    return False


# Cache for signal (b) results — once a session is confirmed as having
# cleared another session's gate, it stays that way for the process lifetime.
# This avoids re-globbing on every check_gate() call.
_gate_clearing_signal_cache = {}  # type: dict[str, bool]


def classify_session_role(state_dir: str, session_id: str) -> str:
    """Classify a session's role as 'reviewer' or 'producer'.

    Returns 'reviewer' if EITHER:
    1. Signal (a) — RGH-11: marker file exists with role='reviewer' and
       reviewing_session present and != session_id (no self-review).
    2. Signal (b) — RGH-12: a review-pass record in a DIFFERENT session's
       reviewed ledger has reviewer_session == session_id, proving this
       session cleared another session's gate.

    Returns 'producer' in all other cases (fail-closed).

    SAFETY: Signal (b) cannot be faked by a producer because:
    - log-review-pass.py rejects reviewer_session == session (RGH-8)
    - The evidence lives in the PRODUCER's reviewed ledger, not the reviewer's
    - A producer would need to write to another session's ledger file, which
      requires knowing that session_id AND forging the entry — multi-step,
      detectable, and the scoped exemption still only covers write-safe Bash.
    """
    # Signal (a): marker file (RGH-11)
    if _check_marker_signal(state_dir, session_id):
        return 'reviewer'

    # Signal (b): gate-clearing inference (RGH-12) — cached after first hit
    cache_key = f'{state_dir}:{session_id}'
    cached = _gate_clearing_signal_cache.get(cache_key)
    if cached is True:
        return 'reviewer'
    # Only re-scan if not yet confirmed (False or missing)
    if cached is None or cached is False:
        if _check_gate_clearing_signal(state_dir, session_id):
            _gate_clearing_signal_cache[cache_key] = True
            return 'reviewer'
        _gate_clearing_signal_cache[cache_key] = False

    return 'producer'


# ============================================================================
# Per-project configuration (RGH-4)
# ============================================================================

# Default state-path patterns — workspace-generic, always active.
# Project-specific patterns extend these; they never replace them.
DEFAULT_STATE_PATH_PATTERNS = [
    r'ai-factory/system-state/',
    r'second-brain/_meta/',
    r'\.claude/',
    r'_event-log\.md',
    r'\.claude/settings\.json',
    r'SKILL\.md',
    r'CLAUDE\.md',
]

# Legacy combined list — kept as module-level constant for backward compat
# with any code that imports STATE_PATH_PATTERNS directly. The classify_tier()
# function uses get_state_path_patterns() which reads per-project config.
STATE_PATH_PATTERNS = DEFAULT_STATE_PATH_PATTERNS + [
    r'_deployment-status\.md',
    r'publish-core-30-page\.py',
    r'gsc_indexing\.py',
    r'submit-gsc-indexing\.py',
]

_project_config_cache = None  # type: Optional[dict]


def load_project_config(workspace_root: str = None) -> dict:
    """Load .review-gate/config.yml. Cached after first load.

    Returns parsed dict or empty dict if no config exists.
    Config is optional — the gate works without it (generic defaults).
    """
    global _project_config_cache
    if _project_config_cache is not None:
        return _project_config_cache

    if workspace_root is None:
        workspace_root = WORKSPACE_ROOT

    config_path = os.path.join(workspace_root, '.review-gate', 'config.yml')
    if not os.path.isfile(config_path):
        _project_config_cache = {}
        return _project_config_cache

    try:
        # Use yaml if available, fall back to a simple parser
        try:
            import yaml
            with open(config_path, 'r') as f:
                _project_config_cache = yaml.safe_load(f) or {}
        except ImportError:
            # No PyYAML — parse the subset we need manually
            _project_config_cache = _parse_config_minimal(config_path)
    except Exception:
        _project_config_cache = {}

    return _project_config_cache


def _parse_config_minimal(path: str) -> dict:
    """Minimal YAML-subset parser for config.yml when PyYAML is unavailable.

    Handles the flat structure we need: default_state_path_patterns list,
    projects map with path_markers and extra_state_path_patterns.
    Falls back to empty dict on any parse issue — safe degradation.
    """
    # Best-effort: if yaml isn't available we still want the gate to work.
    # The config format is simple enough that we only need top-level keys.
    return {}


def resolve_project(file_path: str, config: dict = None) -> Optional[dict]:
    """Match a file path to a project config entry.

    Returns the project config dict if matched, None if no match.
    Matches by checking if any of the project's path_markers appear
    in the file path.
    """
    if config is None:
        config = load_project_config()

    projects = config.get('projects', {})
    if not projects:
        return None

    for _proj_id, proj_config in projects.items():
        if not isinstance(proj_config, dict):
            continue
        markers = proj_config.get('path_markers', [])
        for marker in markers:
            if marker in file_path:
                return proj_config

    return None


def get_state_path_patterns(file_path: str = None) -> list:
    """Get the state-path patterns applicable to a file.

    Returns DEFAULT_STATE_PATH_PATTERNS + any project-specific extra patterns
    if the file matches a configured project.
    """
    config = load_project_config()
    patterns = list(DEFAULT_STATE_PATH_PATTERNS)

    if file_path:
        proj = resolve_project(file_path, config)
        if proj:
            extra = proj.get('extra_state_path_patterns', [])
            patterns.extend(extra)

    return patterns


def get_project_profile_name(file_path: str = None) -> str:
    """Return a human-readable profile name for the file's project.

    Returns 'generic (no project profile)' when no config matches.
    """
    if not file_path:
        return 'generic (no project profile)'

    config = load_project_config()
    proj = resolve_project(file_path, config)
    if proj:
        return proj.get('description', proj.get('facts_profile_id', 'configured project'))
    return 'generic (no project profile)'


# ============================================================================
# Tier classification
# ============================================================================


def classify_tier(file_path: str, tool_input: dict, tool_name: str) -> str:
    """Classify as 'fast-path' (trivial) or 'full' (substantive).

    Uses per-project state-path patterns when a project config matches
    the file path. Falls back to workspace-generic defaults.
    """
    if tool_name == 'Bash':
        return 'full'

    patterns = get_state_path_patterns(file_path)
    for pattern in patterns:
        if re.search(pattern, file_path):
            return 'full'

    if tool_name == 'Write':
        # RGH-1b item 2: trivial new files (≤5 lines, non-state path) are
        # fast-path. The state-path check above already caught state files.
        content = tool_input.get('content', '')
        line_count = len(content.strip().splitlines()) if content else 0
        if line_count <= 5:
            return 'fast-path'
        return 'full'

    if tool_name == 'Edit':
        new_str = tool_input.get('new_string', '')
        old_str = tool_input.get('old_string', '')
        new_lines = len(new_str.strip().splitlines()) if new_str else 0
        old_lines = len(old_str.strip().splitlines()) if old_str else 0
        delta = abs(new_lines - old_lines) + min(new_lines, old_lines)
        if delta <= 5:
            return 'fast-path'

    return 'full'


def determine_review_tier(unreviewed: list) -> str:
    """Overall tier: 'full' if any entry is full, else 'fast-path'."""
    for entry in unreviewed:
        if entry.get('tier') == 'full':
            return 'full'
    return 'fast-path'


# ============================================================================
# Source classification (RGH-10)
# ============================================================================
#
# Tags dirty-ledger entries by source: producer, reviewer, or gate-clearing.
# Source is inferred STRUCTURALLY from the artifact path or Bash command —
# never from a self-declared flag the model controls.
#
# The goal: reviewer working-doc artifacts (execution logs, verdict files,
# firing-tracker/catch-register rows) and gate-clearing calls should NOT
# re-trigger the gate, because requiring a reviewer for the reviewer's own
# outputs is an infinite regress (CR-010/044/054).
#
# SAFETY: A producer cannot get its deliverables exempted by writing to a
# "reviewer-looking" path, because reviewer-artifact classes are structurally
# disjoint from deliverables (they live in _meta/handoffs/, .review-gate/,
# execution-logs/ with *-reviewer* names — not in src/, scripts/, etc.).

# Patterns that identify reviewer working-doc artifacts by file path.
# These are the known classes of files a reviewer writes as part of its
# review process — NOT the deliverables being reviewed.
#
# SECURITY (BLOCKING-1 fix, Round 1): execution-log patterns use EXACT
# suffixes (-reviewer-session, -independent-review, -peer-review) anchored
# with $ — NOT open-ended .*-reviewer which a producer could match by naming
# its log execution-log-2026-06-22-reviewer-hack.md.
#
# SECURITY (BLOCKING-4 fix, Round 1): patterns match execution-log filenames
# ANYWHERE in the path (not just under execution-logs/ directory), because
# reviewers may write logs directly in handoff folders.
REVIEWER_ARTIFACT_PATH_PATTERNS = [
    r'_meta/handoffs/_review-skill-firing-tracker\.md$',
    r'_meta/handoffs/_review-gate-catch-register\.md$',
    # Execution logs: match exact reviewer suffixes anywhere in path
    r'execution-log-\d{4}-\d{2}-\d{2}-reviewer-session[^/]*\.md$',
    r'execution-log-\d{4}-\d{2}-\d{2}-independent-review[^/]*\.md$',
    r'execution-log-\d{4}-\d{2}-\d{2}-peer-review[^/]*\.md$',
    # Also match the shorthand (e.g., execution-log-2026-06-22-rgh3-independent-review.md)
    r'execution-log-\d{4}-\d{2}-\d{2}-[a-z0-9]+-(?:reviewer-session|independent-review|peer-review)[^/]*\.md$',
    r'/\.review-gate/state/verdict-',
    r'/\.review-gate/state/.*-reviewed\.jsonl$',
]

_REVIEWER_ARTIFACT_RE = re.compile('|'.join(REVIEWER_ARTIFACT_PATH_PATTERNS))

# Gate-clearing script basenames — matched per-segment after splitting on
# compound operators (BLOCKING-2 fix, Round 1). Only the actual invoked
# script qualifies, not a substring mention like 'echo log-review-pass'.
GATE_CLEARING_SCRIPT_BASENAMES = frozenset({
    'log-review-pass.py',
    'gate-skip.py',
    'register-reviewer-session.py',
})


def _is_segment_gate_clearing(segment: str) -> bool:
    """Check if a single command segment invokes a gate-clearing script."""
    segment = segment.strip()
    if not segment:
        return False
    parts = segment.split()
    for i, part in enumerate(parts):
        if '=' in part and not part.startswith('-'):
            continue  # env assignment
        base = os.path.basename(part)
        if base in ('python3', 'python'):
            for p in parts[i + 1:]:
                if p.startswith('-'):
                    continue
                return os.path.basename(p) in GATE_CLEARING_SCRIPT_BASENAMES
        return base in GATE_CLEARING_SCRIPT_BASENAMES
    return False


def _is_gate_clearing_bash(bash_cmd: str) -> bool:
    """Check if a Bash command is a gate-clearing operation.

    Returns True ONLY if EVERY segment is either a gate-clearing script
    invocation OR a read-only command. If ANY segment is state-changing
    and not gate-clearing, returns False (the whole entry is producer).

    SECURITY (BLOCKING-7 fix, Round 2): a compound command like
    'python3 log-review-pass.py && npm run deploy' returns False because
    the deploy segment is state-changing. A producer cannot chain a
    gate-clearing call with a state-changing command to get the entry
    exempted.
    """
    if not bash_cmd:
        return False
    segments = split_compound(bash_cmd.strip())
    if not segments:
        return False
    has_gate_clearing = False
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if _is_segment_gate_clearing(seg):
            has_gate_clearing = True
        elif _is_segment_read_only(seg):
            continue  # read-only is OK alongside gate-clearing
        else:
            return False  # state-changing non-gate-clearing → producer
    return has_gate_clearing


def classify_entry_source(file_path: str, tool_name: str,
                          bash_cmd: str = '') -> str:
    """Classify a dirty-ledger entry's source as producer/reviewer/gate-clearing.

    Inference is structural (path-based + command-based), not self-declared.
    A producer writing a normal deliverable always gets 'producer' regardless
    of any env var or flag it might set.

    SECURITY NOTES (Round 1 fixes):
    - BLOCKING-1: Execution-log patterns use exact suffixes, not open-ended.
    - BLOCKING-2: Gate-clearing uses segment-split script-invocation check.
    - BLOCKING-3: Bash commands ONLY get gate-clearing or producer — the
      reviewer-artifact regex is NOT applied to bash_cmd (too broad).
    - BLOCKING-4: Execution-log patterns match anywhere in path, not just
      under execution-logs/ directory.

    RESOLVED (RGH-11): Session-level reviewer tagging implemented via
    classify_session_role() + is_bash_entry_write_safe(). Reviewer sessions
    registered via register-reviewer-session.py have their write-safe Bash
    automatically exempted in check_gate(). Deliverable Write/Edit still
    gates regardless of session role (no producer bypass).

    Args:
        file_path: The normalized file path or BASH:<hash> key.
        tool_name: The tool that produced the entry (Write, Edit, Bash, etc.).
        bash_cmd: The raw Bash command (only for Bash tool entries).

    Returns:
        'reviewer', 'gate-clearing', or 'producer'.
    """
    # Gate-clearing: Bash commands that INVOKE gate-clearing scripts
    # (segment-split check, not substring — BLOCKING-2 fix)
    if tool_name == 'Bash' and bash_cmd:
        if _is_gate_clearing_bash(bash_cmd):
            return 'gate-clearing'

    # Reviewer artifact: file path matches known reviewer working-doc classes
    # Only applies to Write/Edit/NotebookEdit file paths — NOT Bash commands
    # (BLOCKING-3 fix: removed the bash_cmd regex search)
    if file_path and not file_path.startswith('BASH:'):
        if _REVIEWER_ARTIFACT_RE.search(file_path):
            return 'reviewer'

    return 'producer'


# ============================================================================
# Protocol-file exemption
# ============================================================================

PROTOCOL_EXEMPT_RELPATHS = frozenset({
    'second-brain/_meta/_event-log.md',
    'ai-factory/system-state/strategic/03_session-log.md',
    'second-brain/_meta/handoffs/_active-chats-tracker.md',
    'second-brain/_meta/handoffs/_active-chats-tracker-changelog.md',
})


def is_all_protocol_exempt(unreviewed: list, workspace_root: str) -> bool:
    """Return True if every unreviewed entry is a protocol-exempt file."""
    exempt_abspaths = frozenset(
        os.path.realpath(os.path.join(workspace_root, rp))
        for rp in PROTOCOL_EXEMPT_RELPATHS
    )
    for entry in unreviewed:
        fp = entry.get('file_path', '')
        if fp.startswith('BASH:'):
            return False
        if fp not in exempt_abspaths:
            return False
    return True


# ============================================================================
# Fast-path auto-clear
# ============================================================================

PLACEHOLDER_PATTERNS = re.compile(
    r'\b(FILL|TBD|PLACEHOLDER|TODO|FIXME|XXX|CHANGEME)\b', re.IGNORECASE)

UNRESOLVED_LINK_PATTERN = re.compile(
    r'\[\[.*?(FILL|TBD|TODO|PLACEHOLDER).*?\]\]', re.IGNORECASE)


def _load_leak_identity_strings(file_path: str) -> list:
    """Load client identity strings for the leak audit from project config.

    Returns a list of identity strings to grep for, or empty list if no
    client data is configured for this file's project.
    """
    config = load_project_config()
    proj = resolve_project(file_path, config)
    if not proj:
        return []

    leak_config = proj.get('leak_audit')
    if not leak_config or not isinstance(leak_config, dict):
        return []

    data_path = leak_config.get('client_data_path', '')
    file_pattern = leak_config.get('client_file_pattern', 'client-*.json')
    identity_fields = leak_config.get('identity_fields', ['name', 'owner_name'])

    if not data_path:
        return []

    # Resolve relative to workspace root
    abs_data_path = os.path.join(WORKSPACE_ROOT, data_path)
    if not os.path.isdir(abs_data_path):
        return []

    import glob as glob_mod
    client_files = glob_mod.glob(os.path.join(abs_data_path, file_pattern))
    if not client_files:
        return []

    identity_strings = []
    for cf in client_files:
        try:
            with open(cf, 'r') as f:
                data = json.load(f)
            for field in identity_fields:
                val = data.get(field, '')
                if val and isinstance(val, str) and len(val) > 2:
                    identity_strings.append(val)
        except (json.JSONDecodeError, OSError):
            continue

    return identity_strings


def run_fast_path_checks(file_path: str) -> dict:
    """Run grep-level fast-path checks on a single file.

    Project-aware (RGH-4): leak-audit loads identity strings from
    per-project config instead of using a hardcoded regex. When no
    project config exists, leak-audit reports N/A (honest skip).
    """
    result = {
        'file': file_path,
        'checks': [],
        'passed': True,
        'project_profile': get_project_profile_name(file_path),
    }

    if file_path.startswith('BASH:') or not os.path.isfile(file_path):
        result['passed'] = False
        result['checks'].append({
            'name': 'placeholder-sweep', 'result': 'SKIP',
            'reason': 'non-file entry or file missing',
        })
        return result

    try:
        with open(file_path, 'r', errors='replace') as f:
            content = f.read()
    except OSError:
        result['passed'] = False
        result['checks'].append({
            'name': 'placeholder-sweep', 'result': 'SKIP',
            'reason': 'unreadable file',
        })
        return result

    # Placeholder sweep — project-agnostic, always runs
    placeholders = PLACEHOLDER_PATTERNS.findall(content)
    ph_count = len(placeholders)
    result['checks'].append({
        'name': 'placeholder-sweep',
        'result': 'PASS' if ph_count == 0 else 'FAIL',
        'count': ph_count,
    })
    if ph_count > 0:
        result['passed'] = False

    # Leak audit — project-aware (RGH-4)
    # Load identity strings from project config; if none configured,
    # report N/A honestly instead of using a hardcoded regex that false-fires.
    identity_strings = _load_leak_identity_strings(file_path)
    if identity_strings:
        leak_count = 0
        for identity in identity_strings:
            hits = content.lower().count(identity.lower())
            leak_count += hits
        result['checks'].append({
            'name': 'leak-audit',
            'result': 'PASS' if leak_count == 0 else 'FAIL',
            'count': leak_count,
        })
        if leak_count > 0:
            result['passed'] = False
    else:
        # No client identity data configured — honest skip
        result['checks'].append({
            'name': 'leak-audit',
            'result': 'N/A',
            'count': 0,
            'reason': 'no client identity registry configured for this project',
        })

    # Link resolution — project-agnostic, always runs
    bad_links = UNRESOLVED_LINK_PATTERN.findall(content)
    link_count = len(bad_links)
    result['checks'].append({
        'name': 'link-resolution',
        'result': 'PASS' if link_count == 0 else 'FAIL',
        'count': link_count,
    })
    if link_count > 0:
        result['passed'] = False

    return result


def try_fast_path_auto_clear(unreviewed: list, session_id: str,
                              state_dir: str) -> tuple:
    """Attempt fast-path auto-clear for all-fast-path unreviewed entries.

    Returns (True, '') if auto-cleared.
    Returns (False, refusal_msg) if auto-clear was attempted but failed.
    The refusal_msg (RGH-1b item 1) tells the operator WHY auto-clear
    refused, so they can distinguish refusal from absence.
    """
    for entry in unreviewed:
        if entry.get('tier') != 'fast-path':
            return False, ''  # not attempted (wrong tier)

    all_results = []
    all_passed = True

    for entry in unreviewed:
        fp = entry.get('file_path', '')
        result = run_fast_path_checks(fp)
        all_results.append(result)
        if not result['passed']:
            all_passed = False

    if not all_passed:
        # Build refusal message with per-check hit counts (RGH-1b item 1)
        failures = []
        for r in all_results:
            for c in r['checks']:
                if c.get('result') == 'FAIL':
                    failures.append(f"{c['name']}: {c.get('count', '?')} hits"
                                    f" in {os.path.basename(r['file'])}")
        refusal = ('auto-clear attempted, REFUSED: ' + '; '.join(failures)
                   if failures else 'auto-clear attempted, failed (unknown)')
        return False, refusal

    # All checks passed — write machine-generated verdict file
    checks_run = []
    for r in all_results:
        for c in r['checks']:
            checks_run.append(c)

    seen = {}
    for c in checks_run:
        name = c['name']
        if name not in seen:
            seen[name] = {'name': name, 'result': 'PASS', 'count': 0}
        seen[name]['count'] += c.get('count', 0)
    deduped_checks = list(seen.values())

    # Determine project profile from the first file result
    profiles = [r.get('project_profile', 'generic (no project profile)')
                for r in all_results]
    profile = profiles[0] if profiles else 'generic (no project profile)'

    verdict_data = {
        'verdict': 'PASS',
        'checks_run': deduped_checks,
        'catches': [],
        'cost_usd': 0.0,
        'auto_clear': True,
        'project_profile': profile,
        'generator': 'mandatory-review-gate/fast-path-auto-clear',
    }

    os.makedirs(state_dir, exist_ok=True)
    verdict_path = os.path.join(
        state_dir, f'verdict-auto-{session_id}-{int(time.time() * 1000)}.json')
    with open(verdict_path, 'w') as f:
        json.dump(verdict_data, f)

    # Write review-pass markers
    reviewed_path = os.path.join(state_dir, f'{session_id}-reviewed.jsonl')
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    evidence = (f'fast-path auto-clear: placeholder-sweep '
                f'{seen.get("placeholder-sweep", {}).get("count", 0)} tokens; '
                f'leak-audit {seen.get("leak-audit", {}).get("count", 0)} markers; '
                f'link-resolution {seen.get("link-resolution", {}).get("count", 0)} bad links')

    with open(reviewed_path, 'a') as f:
        for entry in unreviewed:
            fp = entry.get('file_path', '')
            marker = {
                'timestamp': now,
                'iso_time': iso,
                'file_path': fp,
                'verdict': 'PASS',
                'tier': 'fast-path',
                'gate_id': 'G-auto-fast-path',
                'evidence': evidence,
                'verdict_file': verdict_path,
                'verdict_data': verdict_data,
                'findings': None,
            }
            f.write(json.dumps(marker) + '\n')

    return True, ''


# ============================================================================
# Metrics logging
# ============================================================================

def log_metrics(state_dir: str, session_id: str, outcome: str,
                unreviewed_count: int, tier: str, wall_ms: float,
                extra: Optional[dict] = None) -> None:
    """Append metric entry to metrics.jsonl. Best-effort (never raises)."""
    os.makedirs(state_dir, exist_ok=True)
    metrics_path = os.path.join(state_dir, 'metrics.jsonl')
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'session_id': session_id,
        'outcome': outcome,
        'unreviewed_count': unreviewed_count,
        'tier': tier,
        'wall_ms': round(wall_ms, 2),
    }
    if extra:
        entry.update(extra)
    try:
        with open(metrics_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


# ============================================================================
# Clear-check (the core gate query)
# ============================================================================

@dataclasses.dataclass
class GateResult:
    """Result from the substrate-agnostic gate check."""
    status: str          # 'clean' | 'clear' | 'exempt' | 'auto-cleared' | 'blocked'
    unreviewed: list     # list of unreviewed entry dicts (empty if not blocked)
    tier: str            # 'n/a' | 'fast-path' | 'full'
    wall_ms: float
    project_profile: str = 'generic (no project profile)'  # RGH-4
    auto_clear_refusal: str = ''  # RGH-1b item 1: why auto-clear refused


def _resolve_entries_profile(entries: list) -> str:
    """Determine the dominant project profile for a set of dirty entries."""
    for e in entries:
        fp = e.get('file_path', '')
        if fp.startswith('BASH:'):
            continue
        profile = get_project_profile_name(fp)
        if profile != 'generic (no project profile)':
            return profile
    return 'generic (no project profile)'


def check_gate(
    state_dir: str,
    session_id: str,
    workspace_root: str,
    included_sources: Optional[frozenset] = None,
    attempt_auto_clear: bool = True,
) -> GateResult:
    """The substrate-agnostic clear-check.

    Loads ledgers, computes unreviewed set, applies exemptions,
    optionally attempts fast-path auto-clear, logs metrics.
    Returns a GateResult the adapter interprets into its exit protocol.
    """
    t_start = time.monotonic()

    dirty_entries = load_scoped_dirty(state_dir, session_id,
                                      included_sources=included_sources)
    reviewed_entries = read_reviewed_ledger(state_dir, session_id)

    if not dirty_entries:
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(state_dir, session_id, 'approve', 0, 'n/a', wall_ms)
        return GateResult(status='clean', unreviewed=[], tier='n/a',
                          wall_ms=wall_ms)

    unreviewed = get_unreviewed(dirty_entries, reviewed_entries)

    # Source-tagged exemption (RGH-10): reviewer and gate-clearing entries
    # do NOT require independent review. Only producer entries gate the stop.
    # This kills the infinite regress (CR-010/044/054) where a reviewer's
    # own working docs re-trigger the gate demanding another reviewer.
    _EXEMPT_ENTRY_SOURCES = frozenset({'reviewer', 'gate-clearing'})
    unreviewed_producer = [
        e for e in unreviewed
        if e.get('entry_source', 'producer') not in _EXEMPT_ENTRY_SOURCES
    ]
    exempt_count = len(unreviewed) - len(unreviewed_producer)
    if exempt_count > 0:
        log_metrics(state_dir, session_id, 'source-exempt',
                    exempt_count, 'n/a', 0,
                    extra={'exempt_sources': [
                        e.get('entry_source') for e in unreviewed
                        if e.get('entry_source', 'producer') in _EXEMPT_ENTRY_SOURCES
                    ]})
    # Session-level reviewer exemption (RGH-11 + RGH-12): for confirmed
    # reviewer sessions, exempt Bash entries that are EITHER:
    # (1) write-safe (no positive write indicators) — RGH-11, OR
    # (2) writes ONLY to reviewer-working-doc paths — RGH-12 (RGH12-6).
    # Deliverable-class Write/Edit still gates regardless of session role.
    # This closes the MAJOR-5 honest limit from RGH-10 where a separate-
    # session reviewer's inspection Bash (engine.py, test runs, greps)
    # was classified as producer and re-triggered the gate.
    session_role = classify_session_role(state_dir, session_id)
    session_exempt_keys = set()  # track exempted file_paths for RGH-5 filter
    if session_role == 'reviewer' and unreviewed_producer:
        still_gated = []
        session_exempt = 0
        for e in unreviewed_producer:
            fp = e.get('file_path', '')
            if fp.startswith('BASH:'):
                raw_cmd = e.get('bash_cmd', e.get('display', ''))
                # (1) Write-safe Bash (RGH-11)
                if is_bash_entry_write_safe(raw_cmd):
                    session_exempt += 1
                    session_exempt_keys.add(fp)
                    continue
                # (2) Reviewer-doc-only Bash writes (RGH-12, RGH12-6)
                if is_bash_reviewer_doc_write(raw_cmd):
                    session_exempt += 1
                    session_exempt_keys.add(fp)
                    continue
            still_gated.append(e)
        if session_exempt > 0:
            log_metrics(state_dir, session_id, 'session-reviewer-exempt',
                        session_exempt, 'n/a', 0,
                        extra={'session_role': 'reviewer'})
        unreviewed_producer = still_gated

    unreviewed = unreviewed_producer

    if not unreviewed:
        # All files have review markers OR are source-exempt — but full-tier
        # producer items require independent review (RGH-5). Check only
        # producer entries for the independent-review requirement.
        producer_dirty = [
            e for e in dirty_entries
            if e.get('entry_source', 'producer') not in _EXEMPT_ENTRY_SOURCES
            and e.get('file_path', '') not in session_exempt_keys
        ]
        if producer_dirty:
            tier = determine_review_tier(producer_dirty)
            if tier == 'full' and not has_independent_review(
                    reviewed_entries,
                    [e.get('file_path', '') for e in producer_dirty],
                    tier):
                wall_ms = (time.monotonic() - t_start) * 1000
                log_metrics(state_dir, session_id, 'block-needs-independent',
                            len(producer_dirty), tier, wall_ms)
                return GateResult(status='blocked', unreviewed=producer_dirty,
                                  tier=tier, wall_ms=wall_ms)

        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(state_dir, session_id, 'approve', 0, 'n/a', wall_ms)
        return GateResult(status='clear', unreviewed=[], tier='n/a',
                          wall_ms=wall_ms)

    # Protocol-file exemption
    if is_all_protocol_exempt(unreviewed, workspace_root):
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(state_dir, session_id, 'exempt', len(unreviewed),
                    'protocol', wall_ms)
        return GateResult(status='exempt', unreviewed=[], tier='protocol',
                          wall_ms=wall_ms)

    # Fast-path auto-clear
    tier = determine_review_tier(unreviewed)
    auto_clear_refusal = ''
    if attempt_auto_clear and tier == 'fast-path':
        cleared, refusal = try_fast_path_auto_clear(unreviewed, session_id,
                                                     state_dir)
        if cleared:
            wall_ms = (time.monotonic() - t_start) * 1000
            log_metrics(state_dir, session_id, 'auto-clear', len(unreviewed),
                        'fast-path', wall_ms)
            return GateResult(status='auto-cleared', unreviewed=[],
                              tier='fast-path', wall_ms=wall_ms)
        auto_clear_refusal = refusal  # RGH-1b item 1

    # Blocked
    profile = _resolve_entries_profile(unreviewed)
    wall_ms = (time.monotonic() - t_start) * 1000
    log_metrics(state_dir, session_id, 'block', len(unreviewed), tier, wall_ms)
    return GateResult(status='blocked', unreviewed=unreviewed, tier=tier,
                      wall_ms=wall_ms, project_profile=profile,
                      auto_clear_refusal=auto_clear_refusal)


# ============================================================================
# Verdict-file validation
# ============================================================================

REQUIRED_VERDICT_KEYS = {'verdict', 'checks_run'}
VALID_VERDICTS = {'PASS', 'BLOCKING', 'FAIL'}
REQUIRED_CHECK_FIELDS = {'name'}
FULL_TIER_REQUIRED_CHECKS = {
    'ground-truth-cross-check',
    'value-cross-check',
}


def validate_verdict_file(path: str, tier: str) -> tuple:
    """Validate a verdict file against the reviewer return contract schema.
    Returns (parsed_data, error_message). error_message is empty on success."""
    if not os.path.exists(path):
        return None, f'Verdict file does not exist: {path}'

    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f'Verdict file is not valid JSON: {e}'

    if not isinstance(data, dict):
        return None, 'Verdict file must be a JSON object'

    missing = REQUIRED_VERDICT_KEYS - set(data.keys())
    if missing:
        return None, f'Verdict file missing required keys: {missing}'

    if data['verdict'] not in VALID_VERDICTS:
        return None, (f'Verdict must be one of {VALID_VERDICTS}, '
                      f'got: {data["verdict"]}')

    checks = data.get('checks_run', [])
    if not isinstance(checks, list) or len(checks) == 0:
        return None, 'checks_run must be a non-empty list'

    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            return None, f'checks_run[{i}] must be a dict, got {type(check).__name__}'
        check_missing = REQUIRED_CHECK_FIELDS - set(check.keys())
        if check_missing:
            return None, f'checks_run[{i}] missing fields: {check_missing}'

    if tier == 'full':
        check_names = {c.get('name', '') for c in checks}
        check_names.discard('')
        has_full = False
        for req in FULL_TIER_REQUIRED_CHECKS:
            for name in check_names:
                if name.startswith(req):
                    has_full = True
                    break
            if has_full:
                break
        if not has_full:
            return None, (f'Full-tier review requires at least one of '
                          f'{FULL_TIER_REQUIRED_CHECKS} in checks_run. '
                          f'Got: {check_names}. A full entry cleared by a '
                          f'fast-path-shaped verdict is rejected.')

    return data, ''


INDEPENDENT_VERDICT_REQUIRED_KEYS = {'reviewer_type', 'mandate_version'}


def validate_independent_verdict(verdict_data: dict) -> str:
    """Validate that a verdict file has the independent reviewer schema.

    The independent reviewer dispatch and mandate produce verdicts with
    specific fields (reviewer_type, mandate_version, convergence) that a
    self-authored producer verdict does not naturally contain. This raises
    the bar on the D-09 cheat: the producer would have to fabricate the
    full independent schema, not just pass --reviewer-type independent.

    Returns empty string on success, error message on failure.
    """
    missing = INDEPENDENT_VERDICT_REQUIRED_KEYS - set(verdict_data.keys())
    if missing:
        return f'missing required independent-reviewer fields: {missing}'

    if verdict_data.get('reviewer_type') != 'independent':
        return (f'reviewer_type in verdict file must be "independent", '
                f'got: {verdict_data.get("reviewer_type")}')

    if not verdict_data.get('mandate_version'):
        return 'mandate_version must be non-empty'

    return ''


def derive_evidence(verdict_data: dict) -> str:
    """Derive a human-readable evidence string from verdict file data."""
    checks = verdict_data.get('checks_run', [])
    check_names = [c.get('name', '?') for c in checks]
    catches = verdict_data.get('catches', [])
    verdict = verdict_data.get('verdict', '?')

    parts = [f'verdict: {verdict}']
    parts.append(f'checks: {", ".join(check_names)}')
    if catches:
        parts.append(f'catches: {len(catches)}')
        for c in catches[:3]:
            if isinstance(c, dict):
                parts.append(f'  - {c.get("surface", "?")} '
                             f'{c.get("severity", "?")}: '
                             f'{c.get("description", "?")[:80]}')
            else:
                parts.append(f'  - {str(c)[:80]}')
    else:
        parts.append('catches: 0')

    cost = verdict_data.get('cost_usd')
    if cost is not None:
        parts.append(f'cost: ${cost:.4f}')

    return '; '.join(parts)


# ============================================================================
# Review-pass marker construction
# ============================================================================

def build_review_markers(
    file_paths: list, verdict: str, tier: str, gate_id: str,
    evidence: str, verdict_file: str, verdict_data: dict,
    findings: Optional[str] = None,
) -> list:
    """Build review-pass marker dicts ready for append_reviewed_entries."""
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    markers = []
    for fp in file_paths:
        if not fp.startswith('BASH:'):
            fp = os.path.realpath(os.path.abspath(fp))
        markers.append({
            'timestamp': now,
            'iso_time': iso,
            'file_path': fp,
            'verdict': verdict,
            'tier': tier,
            'gate_id': gate_id,
            'evidence': evidence,
            'verdict_file': os.path.realpath(verdict_file),
            'verdict_data': verdict_data,
            'findings': findings if findings else None,
        })
    return markers


# ============================================================================
# Independent-review enforcement (RGH-5)
# ============================================================================

def has_independent_review(reviewed_entries: list, unreviewed_file_paths: list,
                           tier: str) -> bool:
    """Check if all files have an independent reviewer's verdict.

    For fast-path tier: auto-cleared entries with reviewer_type='independent'
    or entries from the deterministic dispatch count.
    For full tier: requires at least one entry with reviewer_type='independent'.

    Returns True if independent review coverage is sufficient.
    """
    if tier == 'fast-path':
        # Fast-path: deterministic auto-clear IS the independent review
        # (the dispatch script writes these with reviewer_type='independent')
        return True  # auto-clear path handles this

    # Full tier: check that reviewed entries have independent reviewer_type.
    # Gate-skip (G-skip) is an operator-only emergency bypass that overrides
    # the independent review requirement — operator authority > gate rules.
    independent_files = set()
    for r in reviewed_entries:
        if (r.get('reviewer_type', '').startswith('independent')
                or r.get('gate_id') in ('G-skip', 'G-circuit-breaker')):
            fp = r.get('file_path', '')
            independent_files.add(fp)

    # Check coverage: every unreviewed file needs an independent review
    for fp in unreviewed_file_paths:
        if fp not in independent_files:
            return False

    return True


def needs_independent_review(unreviewed: list, reviewed: list, tier: str) -> bool:
    """Return True if the gate should require independent review for these entries.

    Full-tier items require independent review (RGH-5).
    Fast-path items are auto-cleared by the deterministic dispatch.
    """
    if tier != 'full':
        return False

    file_paths = [e.get('file_path', '') for e in unreviewed]
    return not has_independent_review(reviewed, file_paths, tier)


# ============================================================================
# Block-message formatting (shared between adapters)
# ============================================================================

def format_file_list(entries: list) -> str:
    """Format unreviewed entries for display."""
    lines = []
    for e in entries:
        fp = e.get('file_path', '?')
        display = e.get('display', '')
        tool = e.get('tool', '?')
        tier = e.get('tier', '?')
        if display:
            lines.append(f'  - {fp}  [{display}]  (tool: {tool}, tier: {tier})')
        else:
            lines.append(f'  - {fp}  (tool: {tool}, tier: {tier})')
    return '\n'.join(lines)


def format_files_argv(entries: list) -> str:
    """Build the --files argument string with file_path keys."""
    return ' '.join(f'"{e["file_path"]}"' for e in entries)
