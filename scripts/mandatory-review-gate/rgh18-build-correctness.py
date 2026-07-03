#!/usr/bin/env python3
"""RGH-18: Deterministic build-correctness gate (Leg A).

Three BLOCKING checks that run as CODE, not reviewer judgment:

  1. Completeness diff — deliverable manifest walk via dod-check.py.
     No manifest on a Productize/build-tier run = BLOCKING.
  2. All-dirty-file sweep (DIFF-AWARE) — runs fast-path checks
     (placeholder + leak + link) on EVERY dirty-ledger file.
     For append-only shared files, checks only the ADDED hunks
     (git diff), not the whole file — kills false-positive noise
     from pre-existing accumulated content.
  3. Staging-reality audit — extends OC-16 to verify commit claims
     match disk reality.

Born from A3 operator-QC (2026-06-25) where the independent reviewer
PASSED a build that (1) dropped a page, (2) leaked a source file,
(3) staged only 1 of 5 claimed services.

Usage:
  python3 rgh18-build-correctness.py \\
    --session <session_id> \\
    --state-dir <path> \\
    --workspace-root <path> \\
    [--handoff <path>] \\
    [--tier <Productize|Capture-only|Throwaway>]

Output: JSON verdict to stdout. Exit 0 = all pass, 1 = findings.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _paths import WORKSPACE_ROOT

# --- Append-only shared files: check only added hunks, not whole file ---
# These files accumulate content across sessions. Checking the whole file
# produces false positives on pre-existing content (e.g. 18 placeholder hits
# in tracker rows from months of accumulated history). The fix: only check
# the lines THIS session added.
APPEND_ONLY_BASENAMES = frozenset([
    '_active-chats-tracker.md',
    '_active-chats-tracker-changelog.md',
    '_recently-closed.md',
    '_event-log.md',
    '_review-skill-firing-tracker.md',
    '_review-gate-catch-register.md',
])

# Spec/reference/template-documenting files: these legitimately contain
# placeholder-like tokens ({client_name}, <slug>, FILL:, etc.) as
# DOCUMENTED SYNTAX, not unresolved deliverable placeholders. Exempting
# them from the placeholder sweep prevents false-positive blocks on
# skill specs, routing tables, and template docs. (CR-160 class 2.)
#
# Match by: (a) basename patterns, (b) parent-directory names.
SPEC_REFERENCE_BASENAMES = frozenset([
    'spec-routing-table.md',
])

SPEC_REFERENCE_DIR_NAMES = frozenset([
    'references',
    'templates',
])


def _is_spec_reference_doc(file_path):
    """Check if a file is a spec/reference/template-documenting doc.

    These files legitimately contain placeholder-like tokens as documented
    syntax (e.g. {client_name}, <service-slug>, FILL: in routing specs).
    They should be exempt from the placeholder sweep but NOT from the
    leak audit or link resolution checks.
    """
    basename = os.path.basename(file_path)
    if basename in SPEC_REFERENCE_BASENAMES:
        return True
    # Check if any parent directory is a known spec/reference dir
    parts = file_path.replace('\\', '/').split('/')
    for part in parts:
        if part in SPEC_REFERENCE_DIR_NAMES:
            return True
    # Check for spec/reference filename patterns
    if basename.startswith(('spec-', 'template-')) and basename.endswith('.md'):
        return True
    return False


def _is_append_only(file_path):
    """Check if a file is an append-only shared file."""
    return os.path.basename(file_path) in APPEND_ONLY_BASENAMES


def _find_repo_root(file_path):
    """Find the git repo root for a file path."""
    d = os.path.dirname(os.path.realpath(file_path))
    while d != '/':
        if os.path.isdir(os.path.join(d, '.git')):
            return d
        d = os.path.dirname(d)
    return None


def _get_added_lines(file_path):
    """Get only the added lines from git diff for a file.

    Returns the added content as a single string, or None if the file
    is untracked (in which case the caller should check the whole file).
    """
    repo_root = _find_repo_root(file_path)
    if not repo_root:
        return None  # not in a git repo — check whole file

    # Use realpath for both to avoid macOS /var vs /private/var mismatch
    rel_path = os.path.relpath(os.path.realpath(file_path), repo_root)

    # First check if the file is tracked
    proc = subprocess.run(
        ['git', 'ls-files', '--error-unmatch', rel_path],
        capture_output=True, text=True, cwd=repo_root)
    if proc.returncode != 0:
        return None  # untracked — check whole file

    # Get diff of working tree vs HEAD (includes both staged and unstaged)
    proc = subprocess.run(
        ['git', 'diff', 'HEAD', '--', rel_path],
        capture_output=True, text=True, cwd=repo_root, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        # No diff = no changes (or error) — nothing to check
        return ''

    # Extract only the added lines (lines starting with '+', not '+++')
    added = []
    for line in proc.stdout.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            added.append(line[1:])  # strip the leading '+'

    return '\n'.join(added)


def _run_fast_path_on_content(content, file_path, skip_placeholder=False):
    """Run fast-path checks on a string of content (for diff-aware mode).

    Mirrors engine.run_fast_path_checks() but on arbitrary content
    instead of reading from disk.

    If skip_placeholder is True, the placeholder sweep is skipped
    (used for spec/reference/template docs — CR-160 class 2).
    """
    import engine

    checks = []
    passed = True

    # Placeholder sweep with safe-content stripping
    if skip_placeholder:
        checks.append({
            'name': 'placeholder-sweep',
            'result': 'SKIP',
            'count': 0,
            'mode': 'diff-aware',
            'reason': 'spec/reference/template doc — tokens are documented syntax',
        })
    else:
        stripped = engine._strip_safe_content_for_placeholder_sweep(content)
        placeholders = engine.PLACEHOLDER_PATTERNS.findall(stripped)
        ph_count = len(placeholders)
        checks.append({
            'name': 'placeholder-sweep',
            'result': 'PASS' if ph_count == 0 else 'FAIL',
            'count': ph_count,
            'mode': 'diff-aware',
        })
        if ph_count > 0:
            passed = False

    # Leak audit — use the same project-aware loading
    identity_strings = engine._load_leak_identity_strings(file_path)
    if identity_strings:
        leak_count = sum(
            content.lower().count(s.lower()) for s in identity_strings)
        checks.append({
            'name': 'leak-audit',
            'result': 'PASS' if leak_count == 0 else 'FAIL',
            'count': leak_count,
            'mode': 'diff-aware',
        })
        if leak_count > 0:
            passed = False
    else:
        checks.append({
            'name': 'leak-audit', 'result': 'N/A', 'count': 0,
            'mode': 'diff-aware',
            'reason': 'no client identity registry configured',
        })

    # Link resolution
    bad_links = engine.UNRESOLVED_LINK_PATTERN.findall(content)
    link_count = len(bad_links)
    checks.append({
        'name': 'link-resolution',
        'result': 'PASS' if link_count == 0 else 'FAIL',
        'count': link_count,
        'mode': 'diff-aware',
    })
    if link_count > 0:
        passed = False

    return {'file': file_path, 'checks': checks, 'passed': passed}


# ===== Check 1: Completeness diff (manifest walk) =====

def run_completeness_diff(handoff_path, workspace_root, tier):
    """Run the deliverable manifest walk via dod-check.py.

    For Productize tier, a missing manifest is BLOCKING.
    """
    checks = []
    catches = []

    if not handoff_path:
        if tier == 'Productize':
            catches.append({
                'surface': 'RGH-18/completeness-diff',
                'severity': 'blocking',
                'description': ('Productize-tier run has no handoff with a '
                                'deliverable manifest — BLOCKING per PR-1 §C'),
            })
            checks.append({
                'name': 'completeness-diff-manifest-present',
                'result': 'FAIL',
            })
        else:
            checks.append({
                'name': 'completeness-diff-manifest-present',
                'result': 'SKIP',
                'detail': f'tier={tier}, no manifest required',
            })
        return checks, catches

    # Verify handoff has a deliverable manifest section
    try:
        with open(handoff_path, 'r') as f:
            content = f.read()
    except OSError as e:
        catches.append({
            'surface': 'RGH-18/completeness-diff',
            'severity': 'blocking',
            'description': f'cannot read handoff: {e}',
        })
        checks.append({'name': 'completeness-diff-manifest-present',
                        'result': 'FAIL'})
        return checks, catches

    has_manifest = bool(re.search(
        r'^##\s*(?:Deliverable manifest|Definition of Done|Acceptance)',
        content, re.MULTILINE | re.IGNORECASE))

    if not has_manifest and tier == 'Productize':
        catches.append({
            'surface': 'RGH-18/completeness-diff',
            'severity': 'blocking',
            'description': ('Productize-tier handoff has no ## Deliverable manifest '
                            'or ## Definition of Done section — BLOCKING per PR-1 §C'),
        })
        checks.append({'name': 'completeness-diff-manifest-present',
                        'result': 'FAIL'})
        return checks, catches

    if not has_manifest:
        checks.append({'name': 'completeness-diff-manifest-present',
                        'result': 'SKIP',
                        'detail': 'no manifest section found, non-Productize tier'})
        return checks, catches

    # Run dod-check.py
    dod_script = os.path.join(SCRIPT_DIR, 'dod-check.py')
    try:
        proc = subprocess.run(
            [sys.executable, dod_script, '--handoff', handoff_path,
             '--base-dir', workspace_root],
            capture_output=True, text=True, timeout=60)
        result = json.loads(proc.stdout)

        checks.append({
            'name': 'completeness-diff-dod-walk',
            'result': result.get('verdict', 'ERROR'),
            'rows_checked': result.get('dod_rows_checked', 0),
        })

        for c in result.get('catches', []):
            catches.append({
                'surface': f'RGH-18/completeness-diff/{c.get("surface", "?")}',
                'severity': 'blocking',
                'description': c.get('description', 'check failed'),
            })

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        catches.append({
            'surface': 'RGH-18/completeness-diff',
            'severity': 'blocking',
            'description': f'dod-check.py error: {e}',
        })
        checks.append({'name': 'completeness-diff-dod-walk', 'result': 'ERROR'})

    return checks, catches


# ===== Check 2: All-dirty-file sweep (DIFF-AWARE) =====

def run_all_dirty_file_sweep(state_dir, session_id):
    """Run fast-path checks on EVERY dirty-ledger file.

    DIFF-AWARE: for append-only shared files (tracker, changelog,
    event-log, etc.), runs checks only on the ADDED lines (git diff),
    not the whole file. This prevents false positives from pre-existing
    accumulated content.

    For all other files, runs full-file checks as before.
    """
    import engine

    dirty_entries = engine.read_dirty_ledger(state_dir, session_id)

    # Deduplicate file paths
    file_paths = set()
    for entry in dirty_entries:
        fp = entry.get('file_path', '')
        if fp and not fp.startswith('BASH:') and '/.review-gate/' not in fp:
            file_paths.add(fp)

    checks = []
    catches = []
    files_checked = 0
    diff_aware_files = 0

    for fp in sorted(file_paths):
        if not os.path.isfile(fp):
            continue

        files_checked += 1

        # CR-160 class 2: spec/reference/template docs get placeholder
        # exemption (tokens are documented syntax, not unresolved deliverables)
        skip_ph = _is_spec_reference_doc(fp)

        if _is_append_only(fp):
            # DIFF-AWARE: only check added lines
            diff_aware_files += 1
            added_content = _get_added_lines(fp)

            if added_content is None:
                # Untracked file — check whole file
                if skip_ph:
                    with open(fp, 'r', errors='replace') as fh:
                        result = _run_fast_path_on_content(
                            fh.read(), fp, skip_placeholder=True)
                else:
                    result = engine.run_fast_path_checks(fp)
            elif not added_content:
                # No changes — skip
                continue
            else:
                result = _run_fast_path_on_content(
                    added_content, fp, skip_placeholder=skip_ph)
        else:
            if skip_ph:
                # Read and run with placeholder exemption
                with open(fp, 'r', errors='replace') as fh:
                    result = _run_fast_path_on_content(
                        fh.read(), fp, skip_placeholder=True)
            else:
                # Standard: check entire file
                result = engine.run_fast_path_checks(fp)

        for c in result.get('checks', []):
            checks.append(c)
            if c.get('result') == 'FAIL':
                mode = c.get('mode', 'full-file')
                catches.append({
                    'surface': f'RGH-18/dirty-file-sweep/{c["name"]}',
                    'severity': 'blocking',
                    'description': (f'{c["name"]}: {c.get("count", "?")} hits '
                                   f'in {os.path.basename(fp)} ({mode})'),
                })

    # Summary check entry
    checks.insert(0, {
        'name': 'all-dirty-file-sweep',
        'result': 'PASS' if not catches else 'FAIL',
        'files_checked': files_checked,
        'diff_aware_files': diff_aware_files,
        'total_dirty_files': len(file_paths),
    })

    return checks, catches


# ===== Check 3: Staging-reality audit =====

def run_staging_reality_audit(state_dir, session_id, workspace_root):
    """Run OC-16 across all repos that have dirty-ledger entries.

    Extends OC-16 by discovering which repos have touched files
    and running the audit on each.
    """
    import engine

    dirty_entries = engine.read_dirty_ledger(state_dir, session_id)

    # Group dirty files by repo root
    repo_files = {}
    for entry in dirty_entries:
        fp = entry.get('file_path', '')
        if fp.startswith('BASH:') or '/.review-gate/' in fp:
            continue
        if not fp or not os.path.exists(fp):
            continue
        repo = _find_repo_root(fp)
        if repo:
            repo_files.setdefault(repo, set()).add(fp)

    checks = []
    catches = []

    if not repo_files:
        checks.append({
            'name': 'staging-reality-audit',
            'result': 'SKIP',
            'detail': 'no dirty files in git repos',
        })
        return checks, catches

    oc16_script = os.path.join(SCRIPT_DIR, 'oc-16-commit-staging-audit.py')
    ledger_path = os.path.join(state_dir, f'{session_id}-dirty.jsonl')

    for repo_root in sorted(repo_files.keys()):
        repo_name = os.path.basename(repo_root)
        try:
            proc = subprocess.run(
                [sys.executable, oc16_script,
                 '--ledger', ledger_path,
                 '--repo-root', repo_root],
                capture_output=True, text=True, timeout=30)
            result = json.loads(proc.stdout)

            # CR-160 class 4: filter out 'touched-not-staged' findings.
            # When rgh18 runs BEFORE the commit block executes, files the
            # session touched are still unstaged — but they ARE in the dirty
            # ledger and the producer's proposed commit block, so flagging
            # them as "forgot to stage" is a false positive. The dangerous
            # case is 'staged-not-touched' (foreign work); that still blocks.
            findings = result.get('findings', [])
            filtered_findings = [
                f for f in findings
                if f.get('issue') != 'touched-not-staged'
            ]

            has_blocking = any(
                f.get('severity') == 'blocking' for f in filtered_findings)
            verdict = 'FAIL' if has_blocking else 'PASS'

            checks.append({
                'name': f'staging-audit-{repo_name}',
                'result': verdict,
                'detail': result.get('summary', ''),
            })

            for finding in filtered_findings:
                if finding.get('severity') == 'blocking':
                    catches.append({
                        'surface': f'RGH-18/staging-audit/{repo_name}',
                        'severity': 'blocking',
                        'description': finding.get('detail', 'staging issue'),
                    })

        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            catches.append({
                'surface': f'RGH-18/staging-audit/{repo_name}',
                'severity': 'blocking',
                'description': f'OC-16 error on {repo_name}: {e}',
            })
            checks.append({
                'name': f'staging-audit-{repo_name}',
                'result': 'ERROR',
            })

    return checks, catches


# ===== Tier detection =====

def detect_tier(handoff_path, exec_log_paths=None):
    """Detect the run's tier from the execution log or handoff.

    Looks for **Tier:** line in execution logs, then handoff frontmatter.
    Returns None if no tier is found — callers must handle None gracefully.
    Previously defaulted to 'Productize' which caused false-positive blocks
    on non-Productize runs that didn't declare a tier. (CR-160 class 3.)
    """
    # Check execution logs first
    if exec_log_paths:
        for path in exec_log_paths:
            if os.path.isfile(path):
                try:
                    with open(path, 'r') as f:
                        content = f.read(4096)
                    m = re.search(r'\*\*Tier:\*\*\s*(\S+)', content)
                    if m:
                        return m.group(1)
                except OSError:
                    continue

    # Check handoff
    if handoff_path and os.path.isfile(handoff_path):
        try:
            with open(handoff_path, 'r') as f:
                content = f.read(2048)
            m = re.search(r'\*\*Tier:\*\*\s*(\S+)', content)
            if m:
                return m.group(1)
        except OSError:
            pass

    # No tier found — return None, NOT Productize.
    # Callers require the manifest only when tier is explicitly 'Productize'.
    return None


# ===== Main =====

def build_verdict(checks, catches):
    """Build JSON verdict compatible with the gate return contract."""
    has_blocking = any(c.get('severity') == 'blocking' for c in catches)
    verdict = 'BLOCKING' if has_blocking else 'PASS'

    # Ensure ground-truth-cross-check name is present for full-tier
    check_names = {c.get('name', '') for c in checks}
    if 'completeness-diff-dod-walk' in check_names:
        checks.append({'name': 'ground-truth-cross-check', 'result': verdict})
    if 'all-dirty-file-sweep' in check_names:
        checks.append({'name': 'value-cross-check', 'result': verdict})

    return {
        'verdict': verdict,
        'gate': 'RGH-18',
        'gate_name': 'deterministic-build-correctness',
        'checks_run': checks,
        'catches': catches,
        'cost_usd': 0.0,
        'honest_limit': ('RGH-18 enforces presence + structural correctness '
                         'of build outputs, NOT insight quality. A mediocre-but-'
                         'present artifact passes; a MISSING one BLOCKS.'),
    }


def main():
    parser = argparse.ArgumentParser(
        description='RGH-18: Deterministic build-correctness gate (Leg A)')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--state-dir', required=True, help='Gate state dir')
    parser.add_argument('--workspace-root', required=True, help='Workspace root')
    parser.add_argument('--handoff', default=None, help='Handoff file path')
    parser.add_argument('--tier', default=None,
                        help='Run tier (Productize|Capture-only|Throwaway)')
    parser.add_argument('--exec-log', nargs='*', default=None,
                        help='Execution log path(s) for tier detection')
    parser.add_argument('--output', default=None,
                        help='Write verdict JSON to this file')
    args = parser.parse_args()

    t_start = time.monotonic()

    # Detect tier
    tier = args.tier or detect_tier(args.handoff, args.exec_log)

    all_checks = []
    all_catches = []

    # Check 1: Completeness diff
    checks, catches = run_completeness_diff(
        args.handoff, args.workspace_root, tier)
    all_checks.extend(checks)
    all_catches.extend(catches)

    # Check 2: All-dirty-file sweep (DIFF-AWARE)
    checks, catches = run_all_dirty_file_sweep(args.state_dir, args.session)
    all_checks.extend(checks)
    all_catches.extend(catches)

    # Check 3: Staging-reality audit
    checks, catches = run_staging_reality_audit(
        args.state_dir, args.session, args.workspace_root)
    all_checks.extend(checks)
    all_catches.extend(catches)

    # Build verdict
    wall_ms = (time.monotonic() - t_start) * 1000
    verdict = build_verdict(all_checks, all_catches)
    verdict['wall_ms'] = round(wall_ms, 2)
    verdict['tier'] = tier

    output_str = json.dumps(verdict, indent=2)
    print(output_str)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(output_str)

    sys.exit(0 if verdict['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
