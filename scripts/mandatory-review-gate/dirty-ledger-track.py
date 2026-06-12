#!/usr/bin/env python3
"""PostToolUse hook: tracks dirty (written/edited/state-changed) files in a per-session ledger.

Invoked by Claude Code after every Write, Edit, NotebookEdit, or Bash tool use.
Appends an entry to .claude/state/<session>-dirty.jsonl when the tool call is
artifact-producing or state-changing. Read-only Bash commands are exempt.

Stdin:  JSON from Claude Code hook system (session_id, tool_name, tool_input, etc.)
Stdout: JSON acknowledgment (hookSpecificOutput)
Exit 0: always (tracking is non-blocking)
"""

import json
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR

# --- Self-referential exclusion ---
# Match these substrings anywhere in the Bash command. Covers:
#   python3 /abs/.../mandatory-review-gate.py ...
#   python3 /abs/.../log-review-pass.py --session ...
#   python3 /abs/.../dirty-ledger-track.py
#   python3 -c "...from _paths import STATE_DIR..." (inline inspection)
SELF_PATTERNS = [
    'mandatory-review-gate',
    'log-review-pass',
    'dirty-ledger-track',
    'gate-status',
    'gate-skip',
    '_paths.py',
    'from _paths import',
]

# --- Read-only Bash classifier ---

READ_ONLY_CMDS = frozenset({
    'grep', 'rg', 'find', 'ls', 'cat', 'head', 'tail', 'wc', 'sort', 'uniq',
    'diff', 'comm', 'test', 'true', 'false', 'echo', 'printf', 'which', 'type',
    'file', 'stat', 'du', 'df', 'jq', 'date', 'uname', 'whoami', 'pwd',
    'basename', 'dirname', 'realpath', 'readlink', 'env', 'printenv', 'id',
    'hostname', 'tput',
    # Benign directory/shell builtins (change shell state, not filesystem state)
    'cd', 'pushd', 'popd', ':', 'source', '.', 'export', 'set', 'unset',
    'alias', 'unalias', 'hash', 'ulimit', 'umask', 'shopt', 'trap',
    # NOTE: curl/wget handled separately in _is_segment_read_only (need flag inspection)
})

READ_ONLY_GIT_SUBCMDS = frozenset({
    'log', 'status', 'diff', 'show', 'branch', 'tag', 'remote', 'rev-parse',
    'describe', 'rev-list', 'shortlog', 'blame', 'ls-files', 'ls-tree',
    'cat-file', 'name-rev', 'for-each-ref',
})


def _strip_stderr_redirects(cmd: str) -> str:
    """Remove stderr redirects (2>, 2>>, 2>&1, 2>/dev/null) — NOT file writes."""
    cmd = re.sub(r'2>>&?\d*', '', cmd)
    cmd = re.sub(r'2>/dev/null', '', cmd)
    cmd = re.sub(r'2>&\d+', '', cmd)
    cmd = re.sub(r'2>\s*\S+', '', cmd)
    return cmd


def _has_stdout_file_redirect(cmd: str) -> bool:
    """Check if command has stdout redirect to a file (>, >>), NOT stderr redirects."""
    stripped = _strip_stderr_redirects(cmd)
    stripped = re.sub(r'<<<?\s*\S+', '', stripped)  # Remove heredocs
    # Also strip heredoc markers like << 'EOF' or << 'PYEOF'
    stripped = re.sub(r"<<\s*['\"]?\w+['\"]?", '', stripped)
    return bool(re.search(r'(?<!\d)>{1,2}', stripped))


def _first_word(cmd: str) -> str:
    """Extract the first non-env-assignment word from a command string."""
    for token in cmd.split():
        if '=' in token and not token.startswith('-'):
            continue
        return token
    return ''


def _is_segment_read_only(segment: str) -> bool:
    """Check if a single command segment (no pipes/&&/;) is read-only."""
    segment = segment.strip()
    if not segment:
        return True

    if _has_stdout_file_redirect(segment):
        return False

    first = _first_word(segment)
    base = os.path.basename(first)

    # python3 -c "..." is inspection
    if base in ('python3', 'python') and re.match(r'^python3?\s+-c\s+', segment):
        return True

    # python3 with heredoc (python3 << 'EOF' ... EOF) is inspection
    # The full command text includes the heredoc body; match the heredoc marker
    if base in ('python3', 'python') and re.search(r"<<\s*['\"]?\w+['\"]?", segment):
        return True

    # git subcommand check
    if base == 'git':
        parts = segment.split()
        subcmd = None
        for p in parts[1:]:
            if not p.startswith('-'):
                subcmd = p
                break
        if subcmd and subcmd in READ_ONLY_GIT_SUBCMDS:
            return True
        return False

    # curl/wget: read-only if no mutation flags
    # curl is GET by default; -X POST/PUT/PATCH/DELETE, --data, -d, -F make it state-changing
    if base in ('curl', 'wget'):
        curl_write_flags = {'-X', '--request', '-d', '--data', '--data-raw',
                            '--data-binary', '--data-urlencode', '-F', '--form',
                            '--upload-file', '-T'}
        parts = segment.split()
        for p in parts[1:]:
            if p in curl_write_flags:
                return False
            # -X POST (flag + value)
            if p.startswith('-X') and len(p) > 2:
                return False
        return True

    if base in READ_ONLY_CMDS:
        return True

    return False


def _split_compound(cmd: str) -> list:
    """Split a command on compound operators (|, &&, ||, ;) while respecting quotes.

    A naive regex split breaks on operators inside quoted strings
    (e.g. python3 -c "import json; print(1)" splits on the semicolon).
    This walks the string character by character tracking quote state.
    """
    segments = []
    current = []
    i = 0
    in_single = False
    in_double = False

    while i < len(cmd):
        c = cmd[i]

        # Track quote state
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

        # Inside quotes — no splitting
        if in_single or in_double:
            current.append(c)
            i += 1
            continue

        # Check for compound operators (outside quotes)
        # Order: || before |, && before &
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
    """Return True if the Bash command is read-only (doesn't change state).

    Splits on compound operators (|, &&, ||, ;) with quote-awareness.
    ALL segments must be read-only for the command to be read-only.
    """
    if not command or not command.strip():
        return True

    segments = _split_compound(command.strip())

    for segment in segments:
        if not _is_segment_read_only(segment):
            return False

    return True


# --- Bash entry ID ---

def bash_entry_id(command: str) -> str:
    """Generate a stable, argv-safe ID for a Bash command.

    Format: BASH:<hash_only>
    The hash is the key (round-trips through argv without quote issues).
    The command text is stored in a separate 'display' field in the JSONL entry.
    """
    cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:12]
    return f'BASH:{cmd_hash}'


# --- Tier classifier ---

STATE_PATH_PATTERNS = [
    r'ai-factory/system-state/',
    r'_meta/',
    r'\.claude/',
    r'_deployment-status\.md',
    r'_event-log\.md',
    r'publish-core-30-page\.py',
    r'gsc_indexing\.py',
    r'submit-gsc-indexing\.py',
    r'settings\.json',
    r'SKILL\.md',
    r'CLAUDE\.md',
]


def classify_tier(file_path: str, tool_input: dict, tool_name: str) -> str:
    """Classify as 'fast-path' (trivial) or 'full' (substantive)."""
    if tool_name == 'Bash':
        return 'full'

    for pattern in STATE_PATH_PATTERNS:
        if re.search(pattern, file_path):
            return 'full'

    if tool_name == 'Write':
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


def normalize_path(path: str) -> str:
    """Normalize a file path to absolute, resolved form for reliable matching."""
    if path.startswith('BASH:'):
        return path
    return os.path.realpath(os.path.abspath(path))


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return

    session_id = data.get('session_id', 'unknown')
    tool_name = data.get('tool_name', '')
    tool_input = data.get('tool_input', {})

    # Self-referential exclusion
    if tool_name == 'Bash':
        bash_cmd = tool_input.get('command', '')
        for pat in SELF_PATTERNS:
            if pat in bash_cmd:
                return

    file_path = None
    display = None  # Human-readable display for Bash commands

    if tool_name in ('Write', 'Edit', 'NotebookEdit'):
        file_path = tool_input.get('file_path', '')
        if not file_path:
            return
        file_path = normalize_path(file_path)
    elif tool_name == 'Bash':
        bash_cmd = tool_input.get('command', '')
        if is_read_only_bash(bash_cmd):
            return
        file_path = bash_entry_id(bash_cmd)
        # Store human-readable truncation in display, NOT in the key
        display = re.sub(r'\s+', ' ', bash_cmd.strip())[:80]
    else:
        return

    tier = classify_tier(file_path, tool_input, tool_name)

    os.makedirs(STATE_DIR, exist_ok=True)

    ledger_path = os.path.join(STATE_DIR, f'{session_id}-dirty.jsonl')

    # Substrate source: env override or default 'claude-code' (PostToolUse
    # only fires in Claude Code; other substrates set REVIEW_GATE_SOURCE)
    source = os.environ.get('REVIEW_GATE_SOURCE', 'claude-code')

    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': tool_name,
        'file_path': file_path,
        'tier': tier,
        'source': source,
    }
    if display is not None:
        entry['display'] = display

    with open(ledger_path, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    show = display if display else os.path.basename(file_path)
    print(json.dumps({
        'continue': True,
        'hookSpecificOutput': {
            'hookEventName': 'PostToolUse',
            'additionalContext': f'[review-gate] Tracked: {file_path} ({tier})',
        },
    }))


if __name__ == '__main__':
    main()
