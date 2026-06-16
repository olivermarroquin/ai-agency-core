"""Substrate-agnostic engine for the mandatory review gate.

All business logic lives here. Adapters (Claude Code Stop/PostToolUse hooks,
git pre-commit/pre-push hooks, future daemon adapters) call these functions
and map the results to their substrate's blocking mechanism.

The engine is stateless — it reads/writes JSONL ledgers in STATE_DIR and
returns structured results. It never imports substrate-specific protocols
(no CC hook JSON, no git plumbing, no daemon IPC).

Created by [RGH-2] (2026-06-16) as part of the substrate-abstraction refactor.
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
    'grep', 'rg', 'find', 'ls', 'cat', 'head', 'tail', 'wc', 'sort', 'uniq',
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


def _is_segment_read_only(segment: str) -> bool:
    """Check if a single command segment is read-only."""
    segment = segment.strip()
    if not segment:
        return True

    if _has_stdout_file_redirect(segment):
        return False

    first = _first_word(segment)
    base = os.path.basename(first)

    if base in ('python3', 'python') and re.match(r'^python3?\s+-c\s+', segment):
        return True
    if base in ('python3', 'python') and re.search(r"<<\s*['\"]?\w+['\"]?", segment):
        return True

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
# Tier classification
# ============================================================================

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


def determine_review_tier(unreviewed: list) -> str:
    """Overall tier: 'full' if any entry is full, else 'fast-path'."""
    for entry in unreviewed:
        if entry.get('tier') == 'full':
            return 'full'
    return 'fast-path'


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


def run_fast_path_checks(file_path: str) -> dict:
    """Run grep-level fast-path checks on a single file."""
    result = {
        'file': file_path,
        'checks': [],
        'passed': True,
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

    placeholders = PLACEHOLDER_PATTERNS.findall(content)
    ph_count = len(placeholders)
    result['checks'].append({
        'name': 'placeholder-sweep',
        'result': 'PASS' if ph_count == 0 else 'FAIL',
        'count': ph_count,
    })
    if ph_count > 0:
        result['passed'] = False

    leak_markers = re.findall(r'(source.client|foreign.client)', content, re.IGNORECASE)
    leak_count = len(leak_markers)
    result['checks'].append({
        'name': 'leak-audit',
        'result': 'PASS' if leak_count == 0 else 'FAIL',
        'count': leak_count,
    })
    if leak_count > 0:
        result['passed'] = False

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
                              state_dir: str) -> bool:
    """Attempt fast-path auto-clear for all-fast-path unreviewed entries.
    Returns True if auto-cleared. Conservative: ANY doubt -> False."""
    for entry in unreviewed:
        if entry.get('tier') != 'fast-path':
            return False

    all_results = []
    all_passed = True

    for entry in unreviewed:
        fp = entry.get('file_path', '')
        result = run_fast_path_checks(fp)
        all_results.append(result)
        if not result['passed']:
            all_passed = False

    if not all_passed:
        return False

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

    verdict_data = {
        'verdict': 'PASS',
        'checks_run': deduped_checks,
        'catches': [],
        'cost_usd': 0.0,
        'auto_clear': True,
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

    return True


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

    if not unreviewed:
        # All files have review markers — but full-tier items require
        # independent review (RGH-5). Check reviewer_type before clearing.
        tier = determine_review_tier(dirty_entries)
        if tier == 'full' and not has_independent_review(
                reviewed_entries,
                [e.get('file_path', '') for e in dirty_entries],
                tier):
            wall_ms = (time.monotonic() - t_start) * 1000
            log_metrics(state_dir, session_id, 'block-needs-independent',
                        len(dirty_entries), tier, wall_ms)
            # Re-surface the dirty entries so the adapter can list them
            return GateResult(status='blocked', unreviewed=dirty_entries,
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
    if attempt_auto_clear and tier == 'fast-path':
        if try_fast_path_auto_clear(unreviewed, session_id, state_dir):
            wall_ms = (time.monotonic() - t_start) * 1000
            log_metrics(state_dir, session_id, 'auto-clear', len(unreviewed),
                        'fast-path', wall_ms)
            return GateResult(status='auto-cleared', unreviewed=[],
                              tier='fast-path', wall_ms=wall_ms)

    # Blocked
    wall_ms = (time.monotonic() - t_start) * 1000
    log_metrics(state_dir, session_id, 'block', len(unreviewed), tier, wall_ms)
    return GateResult(status='blocked', unreviewed=unreviewed, tier=tier,
                      wall_ms=wall_ms)


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
                or r.get('gate_id') == 'G-skip'):
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
