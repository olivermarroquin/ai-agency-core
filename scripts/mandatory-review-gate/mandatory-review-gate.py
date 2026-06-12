#!/usr/bin/env python3
"""Stop hook: blocks turn-end if dirty files lack review-pass markers.

Invoked by Claude Code before the model stops (returns control to the user).

OWN-LEDGER-ONLY SCOPING (RGH-1.6): scans only the current session's dirty
ledger. Sub-agent coverage is preserved because sub-agent PostToolUse events
inherit the parent session_id in Claude Code (confirmed by real-runner evidence
2026-06-12, CC v2.1.85). This eliminates the sibling-ledger scan and the
parallel-chat cross-block residual. Source-tag filter retained as belt-and-
suspenders.

Cross-references against the session's *-reviewed.jsonl file. If any artifact
has been written/edited since its last review pass — or has never been
reviewed — the hook exits with code 2, blocking the stop.

Stdin:  JSON from Claude Code hook system (session_id, stop_reason, etc.)
Stdout: JSON acknowledgment when all clean (exit 0)
Stderr: Block message with unreviewed file list (exit 2)
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _paths import STATE_DIR


def load_jsonl(path: str) -> list:
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


# --- Protocol-file exemption (F2) ---
# These exact paths (relative to WORKSPACE_ROOT) are exempt from the review
# gate ONLY when they are the sole dirty entries in scope. If ANY other
# artifact is dirty, everything gets normal review.
from _paths import WORKSPACE_ROOT

PROTOCOL_EXEMPT_RELPATHS = frozenset({
    'second-brain/_meta/_event-log.md',
    'ai-factory/system-state/strategic/03_session-log.md',
    'second-brain/_meta/handoffs/_active-chats-tracker.md',
    'second-brain/_meta/handoffs/_active-chats-tracker-changelog.md',
})

PROTOCOL_EXEMPT_ABSPATHS = frozenset(
    os.path.realpath(os.path.join(WORKSPACE_ROOT, rp))
    for rp in PROTOCOL_EXEMPT_RELPATHS
)


def _all_protocol_exempt(unreviewed: list) -> bool:
    """Return True if every unreviewed entry is a protocol-exempt file."""
    for entry in unreviewed:
        fp = entry.get('file_path', '')
        if fp.startswith('BASH:'):
            return False  # Bash entries are never exempt
        if fp not in PROTOCOL_EXEMPT_ABSPATHS:
            return False
    return True


# Sources that the CC Stop hook includes in its scope.
# Cowork/other-substrate entries are excluded — coverage deferred to
# Tier B git pre-commit hook (RGH-2).
CC_INCLUDED_SOURCES = frozenset({'claude-code', ''})  # '' = legacy entries without source field


def load_scoped_dirty(state_dir: str, session_id: str) -> list:
    """Load dirty entries scoped to the current session's own ledger only.

    Own-ledger-only scoping (RGH-1.6): sub-agent PostToolUse events inherit
    the parent session_id in Claude Code (confirmed by real-runner evidence
    2026-06-12), so sub-agent artifacts land in the parent's own ledger.
    No sibling-ledger scan needed. Source-tag filter retained as belt-and-
    suspenders (F3).
    """
    own_path = os.path.join(state_dir, f'{session_id}-dirty.jsonl')
    own_entries = load_jsonl(own_path)

    if not own_entries:
        return []

    # Filter own entries by source (belt-and-suspenders F3)
    own_entries = [e for e in own_entries
                   if e.get('source', '') in CC_INCLUDED_SOURCES]

    return own_entries


def load_scoped_reviewed(state_dir: str, session_id: str) -> list:
    """Load reviewed entries for the current session."""
    own_path = os.path.join(state_dir, f'{session_id}-reviewed.jsonl')
    return load_jsonl(own_path)


def get_unreviewed(dirty_entries: list, reviewed_entries: list) -> list:
    """Return dirty entries whose file_path has no review pass at or after
    the LATEST dirty timestamp for that path."""
    # Latest dirty timestamp per path
    dirty_map = {}
    for d in dirty_entries:
        fp = d.get('file_path', '')
        ts = d.get('timestamp', 0)
        if fp not in dirty_map or ts > dirty_map[fp]['timestamp']:
            dirty_map[fp] = d

    # Latest reviewed timestamp per path
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


def determine_review_tier(unreviewed: list) -> str:
    for entry in unreviewed:
        if entry.get('tier') == 'full':
            return 'full'
    return 'fast-path'


def format_file_list(entries: list) -> str:
    """Format unreviewed entries. Shows display text for readability,
    but the file_path key (BASH:<hash> or absolute path) is what goes
    into --files for log-review-pass."""
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
    """Build the --files argument string with only the file_path keys.
    These are either absolute paths or BASH:<hash> — both are argv-safe."""
    return ' '.join(f'"{e["file_path"]}"' for e in entries)


# --- Fast-path auto-clear (F1) ---
# Placeholder/TBD family tokens — case-insensitive
PLACEHOLDER_PATTERNS = re.compile(
    r'\b(FILL|TBD|PLACEHOLDER|TODO|FIXME|XXX|CHANGEME)\b', re.IGNORECASE)

# Unresolved wikilinks — [[target]] where target contains suspicious markers
UNRESOLVED_LINK_PATTERN = re.compile(r'\[\[.*?(FILL|TBD|TODO|PLACEHOLDER).*?\]\]',
                                     re.IGNORECASE)


def _run_fast_path_checks(file_path: str) -> dict:
    """Run grep-level fast-path checks on a single file.

    Returns a check result dict with per-check outcomes.
    Only runs on real file paths (not BASH:<hash> entries).
    """
    result = {
        'file': file_path,
        'checks': [],
        'passed': True,
    }

    if file_path.startswith('BASH:') or not os.path.isfile(file_path):
        # Can't grep a Bash entry or missing file — conservative: fail
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

    # Check 1: placeholder sweep
    placeholders = PLACEHOLDER_PATTERNS.findall(content)
    ph_count = len(placeholders)
    result['checks'].append({
        'name': 'placeholder-sweep',
        'result': 'PASS' if ph_count == 0 else 'FAIL',
        'count': ph_count,
    })
    if ph_count > 0:
        result['passed'] = False

    # Check 2: leak audit (simplified — check for obvious foreign-client markers)
    # Conservative: only check for literal "source-client" / "foreign-client" markers
    # Full leak audit with client identity strings requires the facts registry (full tier)
    leak_markers = re.findall(r'(source.client|foreign.client)', content, re.IGNORECASE)
    leak_count = len(leak_markers)
    result['checks'].append({
        'name': 'leak-audit',
        'result': 'PASS' if leak_count == 0 else 'FAIL',
        'count': leak_count,
    })
    if leak_count > 0:
        result['passed'] = False

    # Check 3: link resolution (unresolved wikilinks with placeholder tokens)
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


def _try_fast_path_auto_clear(unreviewed: list, session_id: str) -> bool:
    """Attempt fast-path auto-clear for all-fast-path unreviewed entries.

    Returns True if auto-cleared (all checks passed, verdict file written).
    Returns False if any check failed or any entry is full-tier.

    Conservative: ANY doubt → return False (fall through to normal review).
    """
    # Guard: only auto-clear if ALL entries are fast-path
    for entry in unreviewed:
        if entry.get('tier') != 'fast-path':
            return False

    all_results = []
    all_passed = True

    for entry in unreviewed:
        fp = entry.get('file_path', '')
        result = _run_fast_path_checks(fp)
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

    # Deduplicate check names, keeping all counts
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

    os.makedirs(STATE_DIR, exist_ok=True)
    verdict_path = os.path.join(
        STATE_DIR, f'verdict-auto-{session_id}-{int(time.time() * 1000)}.json')
    with open(verdict_path, 'w') as f:
        json.dump(verdict_data, f)

    # Write review-pass markers for all files
    reviewed_path = os.path.join(STATE_DIR, f'{session_id}-reviewed.jsonl')
    now = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    evidence = (f'fast-path auto-clear: placeholder-sweep {seen.get("placeholder-sweep", {}).get("count", 0)} tokens; '
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


def log_metrics(session_id: str, outcome: str, unreviewed_count: int,
                tier: str, wall_ms: float):
    """Append a cost/timing metric entry to metrics.jsonl."""
    os.makedirs(STATE_DIR, exist_ok=True)
    metrics_path = os.path.join(STATE_DIR, 'metrics.jsonl')
    entry = {
        'timestamp': time.time(),
        'iso_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'session_id': session_id,
        'outcome': outcome,  # 'approve' or 'block'
        'unreviewed_count': unreviewed_count,
        'tier': tier,
        'wall_ms': round(wall_ms, 2),
    }
    try:
        with open(metrics_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass  # metrics are best-effort, never block


def main():
    t_start = time.monotonic()

    try:
        raw = sys.stdin.read()

        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        data = {}

    session_id = data.get('session_id', 'unknown')

    # Own-ledger-only scoping (RGH-1.6): sub-agents share parent session_id.
    dirty_entries = load_scoped_dirty(STATE_DIR, session_id)
    reviewed_entries = load_scoped_reviewed(STATE_DIR, session_id)

    if not dirty_entries:
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(session_id, 'approve', 0, 'n/a', wall_ms)
        print(json.dumps({'continue': True}))
        return

    unreviewed = get_unreviewed(dirty_entries, reviewed_entries)

    if not unreviewed:
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(session_id, 'approve', 0, 'n/a', wall_ms)
        print(json.dumps({'continue': True}))
        return

    # F2: protocol-file exemption — if ALL unreviewed entries are protocol
    # files (event-log, session-log, tracker, changelog), auto-approve
    if _all_protocol_exempt(unreviewed):
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(session_id, 'exempt', len(unreviewed), 'protocol', wall_ms)
        print(json.dumps({'continue': True}))
        return

    # F1: attempt fast-path auto-clear before blocking
    tier = determine_review_tier(unreviewed)
    if tier == 'fast-path' and _try_fast_path_auto_clear(unreviewed, session_id):
        wall_ms = (time.monotonic() - t_start) * 1000
        log_metrics(session_id, 'auto-clear', len(unreviewed), 'fast-path', wall_ms)
        print(json.dumps({'continue': True}))
        return

    # Unreviewed files exist -> BLOCK
    file_list = format_file_list(unreviewed)
    files_argv = format_files_argv(unreviewed)
    count = len(unreviewed)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_script = os.path.join(script_dir, 'log-review-pass.py')

    fast_desc = (
        'fast-path = grep-based surface sweep + source-client-leak-audit '
        '+ link-resolution (no Sonar/LLM-research)'
    )
    full_desc = (
        'full review = 7-surface sweep + source-client-leak-audit + link-resolution '
        '+ placeholder sweep + ground-truth value cross-check + '
        'live-rendered-cache-busted-verification Phase C (if live URL/external state touched)'
    )

    block_msg = f"""MANDATORY PRE-LAND REVIEW GATE — BLOCKED

{count} unreviewed artifact(s) detected. Review tier: {tier}.

Unreviewed:
{file_list}

REQUIRED ACTION before you can stop:
1. Dispatch gate-peer-reviewer on the touched artifacts/state.
   - Use the registered gate type if one applies; otherwise use G-default.
   - Tier '{tier}': {fast_desc if tier == 'fast-path' else full_desc}.
2. Run output-quality-loop on the produced artifacts.
3. The reviewer dispatch emits its verdict to a file, then logs the marker:
   python3 {log_script} --session {session_id} --files {files_argv} --verdict PASS --tier {tier} --gate-id G-default --verdict-file <path-to-verdict.json>
   The verdict file must be a valid gate-peer-reviewer return contract (JSON with verdict, checks_run[], catches[], cost_usd).
4. Then try stopping again.

If blocking findings exist, use --verdict BLOCKING --findings "description" instead.
The BLOCKING verdict still clears the gate (findings were surfaced, not hidden)."""

    wall_ms = (time.monotonic() - t_start) * 1000
    log_metrics(session_id, 'block', count, tier, wall_ms)

    print(block_msg, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
