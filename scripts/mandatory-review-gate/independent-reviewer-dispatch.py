#!/usr/bin/env python3
"""Independent reviewer dispatch — the $0 deterministic layer.

Runs automatically when the Stop hook fires on unreviewed entries.
Executes all deterministic checks (OC-12..16 + fast-path sweeps) and
writes an independent verdict. For full-tier items, outputs instructions
for the producing chat to spawn the LLM reviewer agent.

This is the "auto-fire" mechanism from [RGH-5]: every artifact-producing
chat gets an independent review without the producer invoking it.

Usage (called by mandatory-review-gate.py, not directly):
  python3 independent-reviewer-dispatch.py \
    --session <session_id> \
    --state-dir <path> \
    --workspace-root <path> \
    [--handoff <path-to-handoff.md>] \
    [--tier fast-path|full]

Output: JSON verdict to stdout, block instructions to stderr.
Exit: 0 = all checks pass (auto-cleared), 1 = findings exist (blocked).
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Mandate location (fixed — producer cannot change this)
MANDATE_PATH = os.path.normpath(os.path.join(
    SCRIPT_DIR, '..', '..', '..', '..',
    'skills', 'gate-peer-reviewer', 'references',
    'independent-reviewer-mandate.md'))


def run_fast_path_checks(file_paths):
    """Run fast-path surface checks on all files. Returns (passed, checks)."""
    sys.path.insert(0, SCRIPT_DIR)
    import engine

    all_checks = []
    all_passed = True

    for fp in file_paths:
        if fp.startswith('BASH:') or not os.path.isfile(fp):
            continue
        result = engine.run_fast_path_checks(fp)
        all_checks.extend(result['checks'])
        if not result['passed']:
            all_passed = False

    return all_passed, all_checks


def run_oc15_frontmatter(file_paths, today=None):
    """Run OC-15 frontmatter freshness on .md files."""
    if today is None:
        today = time.strftime('%Y-%m-%d')

    md_files = [fp for fp in file_paths
                if fp.endswith('.md') and os.path.isfile(fp)
                and not fp.startswith('BASH:')]

    if not md_files:
        return 'PASS', 'no .md files to check', []

    script = os.path.join(SCRIPT_DIR, 'oc-15-frontmatter-freshness.py')
    if not os.path.isfile(script):
        return 'SKIP', 'oc-15 script not found', []

    try:
        proc = subprocess.run(
            [sys.executable, script, '--files'] + md_files + ['--date', today],
            capture_output=True, text=True, timeout=30)
        out = json.loads(proc.stdout)
        catches = out.get('failures', [])
        verdict = out.get('verdict', 'ERROR')
        return verdict, out.get('summary', ''), catches
    except Exception as e:
        return 'ERROR', str(e), []


def run_dod_check(handoff_path, base_dir):
    """Run dod-check.py on a handoff file. Returns (verdict, summary, catches)."""
    script = os.path.join(SCRIPT_DIR, 'dod-check.py')
    if not os.path.isfile(script):
        return 'SKIP', 'dod-check.py not found', []
    if not os.path.isfile(handoff_path):
        return 'SKIP', f'handoff not found: {handoff_path}', []

    try:
        proc = subprocess.run(
            [sys.executable, script, '--handoff', handoff_path,
             '--base-dir', base_dir],
            capture_output=True, text=True, timeout=60)
        out = json.loads(proc.stdout)
        catches = out.get('catches', [])
        verdict = out.get('verdict', 'ERROR')
        return verdict, out.get('summary', ''), catches
    except Exception as e:
        return 'ERROR', str(e), []


def detect_handoff(dirty_entries, workspace_root):
    """Try to detect the originating handoff from dirty-ledger entries.

    Looks for handoff files that were edited (status flip) or referenced
    in the dirty ledger paths.
    """
    handoff_patterns = [
        r'handoffs/.*handoff.*\.md$',
        r'handoffs/.*phase.*\.md$',
    ]

    for entry in dirty_entries:
        fp = entry.get('file_path', '')
        if fp.startswith('BASH:'):
            continue
        for pattern in handoff_patterns:
            if re.search(pattern, fp):
                if os.path.isfile(fp):
                    # Check if it has a DoD section
                    try:
                        with open(fp, 'r') as f:
                            content = f.read()
                        if 'Definition of Done' in content:
                            return fp
                    except OSError:
                        continue

    # Also search for handoff paths in the workspace's active handoffs
    handoff_dir = os.path.join(workspace_root, 'second-brain', '_meta', 'handoffs')
    if os.path.isdir(handoff_dir):
        for root, dirs, files in os.walk(handoff_dir):
            for fn in files:
                if fn.startswith('handoff-') and fn.endswith('.md'):
                    fp = os.path.join(root, fn)
                    try:
                        with open(fp, 'r') as f:
                            content = f.read(500)
                        if 'status: active' in content:
                            with open(fp, 'r') as f:
                                full = f.read()
                            if 'Definition of Done' in full:
                                return fp
                    except OSError:
                        continue

    return None


def build_verdict(checks_run, catches, tier, session_id, state_dir,
                  convergence_passes=1):
    """Build and write a verdict file. Returns the path."""
    has_blocking = any(c.get('severity') == 'blocking' for c in catches)
    verdict = 'BLOCKING' if has_blocking else 'PASS'

    verdict_data = {
        'verdict': verdict,
        'reviewer_type': 'independent',
        'checks_run': checks_run,
        'catches': catches,
        'convergence': {
            'passes': convergence_passes,
            'catches_per_pass': [len(catches)],
            'converged': len(catches) == 0,
        },
        'cost_usd': 0.0,
        'generator': 'independent-reviewer-dispatch/deterministic',
        'mandate_version': '1.0',
        'mandate_path': os.path.relpath(MANDATE_PATH,
                                         os.path.expanduser('~/workspace'))
                        if os.path.exists(MANDATE_PATH) else 'NOT_FOUND',
    }

    os.makedirs(state_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    verdict_path = os.path.join(
        state_dir, f'verdict-independent-{session_id}-{ts}.json')
    with open(verdict_path, 'w') as f:
        json.dump(verdict_data, f, indent=2)

    return verdict_path, verdict_data


def log_independent_marker(session_id, file_paths, verdict, tier,
                           verdict_path, verdict_data, state_dir, findings=None):
    """Log review-pass markers with reviewer_type=independent."""
    sys.path.insert(0, SCRIPT_DIR)
    import engine

    markers = engine.build_review_markers(
        file_paths=file_paths,
        verdict=verdict,
        tier=tier,
        gate_id='G-independent',
        evidence=engine.derive_evidence(verdict_data),
        verdict_file=verdict_path,
        verdict_data=verdict_data,
        findings=findings,
    )

    # Add reviewer_type to each marker
    for m in markers:
        m['reviewer_type'] = 'independent'

    engine.append_reviewed_entries(state_dir, session_id, markers)
    return markers


def main():
    parser = argparse.ArgumentParser(
        description='Independent reviewer dispatch — deterministic layer')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--state-dir', required=True, help='Gate state directory')
    parser.add_argument('--workspace-root', required=True, help='Workspace root')
    parser.add_argument('--handoff', default=None,
                        help='Path to the originating handoff (auto-detected if omitted)')
    parser.add_argument('--tier', default='full', choices=['fast-path', 'full'],
                        help='Review tier')
    parser.add_argument('--files', nargs='*', default=None,
                        help='Files to review (read from dirty ledger if omitted)')
    args = parser.parse_args()

    sys.path.insert(0, SCRIPT_DIR)
    import engine

    t_start = time.monotonic()

    # Load dirty entries to find files
    dirty_entries = engine.read_dirty_ledger(args.state_dir, args.session)
    reviewed_entries = engine.read_reviewed_ledger(args.state_dir, args.session)
    unreviewed = engine.get_unreviewed(dirty_entries, reviewed_entries)

    if args.files:
        file_paths = args.files
    else:
        file_paths = [e.get('file_path', '') for e in unreviewed
                      if not e.get('file_path', '').startswith('BASH:')]

    if not file_paths and not unreviewed:
        # Nothing to review
        print(json.dumps({'status': 'clean', 'message': 'no unreviewed entries'}))
        return

    checks_run = []
    catches = []

    # --- Fast-path checks (always run) ---
    fp_passed, fp_checks = run_fast_path_checks(file_paths)
    for c in fp_checks:
        checks_run.append(c)
        if c.get('result') == 'FAIL':
            catches.append({
                'surface': c.get('name', 'unknown'),
                'severity': 'blocking',
                'description': f'{c["name"]}: {c.get("count", "?")} issues found',
            })

    # --- OC-15 frontmatter freshness ---
    oc15_verdict, oc15_summary, oc15_catches = run_oc15_frontmatter(file_paths)
    checks_run.append({
        'name': 'frontmatter-freshness-OC-15',
        'result': oc15_verdict,
        'detail': oc15_summary,
    })
    for c in oc15_catches:
        catches.append({
            'surface': 'OC-15',
            'severity': 'blocking',
            'description': f'stale frontmatter: {c}' if isinstance(c, str) else str(c),
        })

    # --- DoD manifest walk (full tier only, if handoff found) ---
    handoff_path = None
    if args.tier == 'full':
        handoff_path = args.handoff or detect_handoff(dirty_entries, args.workspace_root)
    if handoff_path:
        dod_verdict, dod_summary, dod_catches = run_dod_check(
            handoff_path, args.workspace_root)
        checks_run.append({
            'name': 'dod-manifest-walk',
            'result': dod_verdict,
            'detail': dod_summary,
        })
        for c in dod_catches:
            desc = c.get('description', str(c)) if isinstance(c, dict) else str(c)
            catches.append({
                'surface': 'DoD-manifest',
                'severity': 'blocking',
                'description': desc,
            })
    else:
        checks_run.append({
            'name': 'dod-manifest-walk',
            'result': 'SKIP',
            'detail': 'no handoff with DoD section found',
        })

    # For full tier, add ground-truth cross-check deferred entry
    # (the LLM agent handles the actual cross-check)
    if args.tier == 'full':
        checks_run.append({
            'name': 'ground-truth-cross-check',
            'result': 'DEFERRED' if not catches else 'PARTIAL',
            'detail': 'deterministic layer completed; LLM adversarial layer '
                      'handles judgment-dependent cross-checks',
        })

    # --- Build verdict ---
    all_file_paths = [e.get('file_path', '') for e in unreviewed]
    verdict_path, verdict_data = build_verdict(
        checks_run, catches, args.tier, args.session, args.state_dir)

    wall_ms = (time.monotonic() - t_start) * 1000

    # If deterministic checks pass and fast-path tier → auto-clear
    if not catches and args.tier == 'fast-path':
        log_independent_marker(
            args.session, all_file_paths, 'PASS', args.tier,
            verdict_path, verdict_data, args.state_dir)

        engine.log_metrics(args.state_dir, args.session,
                           'independent-auto-clear', len(unreviewed),
                           args.tier, wall_ms,
                           {'reviewer_type': 'independent-deterministic'})

        result = {
            'status': 'auto-cleared',
            'verdict': 'PASS',
            'reviewer_type': 'independent',
            'checks': len(checks_run),
            'catches': 0,
            'wall_ms': round(wall_ms, 2),
            'verdict_file': verdict_path,
        }
        print(json.dumps(result))
        return

    # If deterministic checks pass but full tier → need LLM agent
    if not catches and args.tier == 'full':
        # Log the deterministic pass but mark as partial
        # (the LLM agent still needs to run for full coverage)
        engine.log_metrics(args.state_dir, args.session,
                           'independent-deterministic-pass', len(unreviewed),
                           args.tier, wall_ms,
                           {'reviewer_type': 'independent-deterministic',
                            'needs_llm_layer': True})

        mandate_msg = _format_llm_dispatch_instructions(
            args.session, all_file_paths, args.tier, handoff_path)

        result = {
            'status': 'deterministic-pass-needs-llm',
            'verdict': 'PARTIAL',
            'reviewer_type': 'independent-deterministic',
            'checks': len(checks_run),
            'catches': 0,
            'wall_ms': round(wall_ms, 2),
            'verdict_file': verdict_path,
            'llm_dispatch_instructions': mandate_msg,
        }
        print(json.dumps(result))
        print(mandate_msg, file=sys.stderr)
        sys.exit(1)

    # Deterministic checks found issues → block
    findings_desc = '; '.join(c['description'] for c in catches[:5])
    log_independent_marker(
        args.session, all_file_paths, 'BLOCKING', args.tier,
        verdict_path, verdict_data, args.state_dir,
        findings=findings_desc)

    engine.log_metrics(args.state_dir, args.session,
                       'independent-blocking', len(unreviewed),
                       args.tier, wall_ms,
                       {'reviewer_type': 'independent-deterministic',
                        'catch_count': len(catches)})

    result = {
        'status': 'blocked',
        'verdict': 'BLOCKING',
        'reviewer_type': 'independent',
        'checks': len(checks_run),
        'catches': len(catches),
        'findings': findings_desc,
        'wall_ms': round(wall_ms, 2),
        'verdict_file': verdict_path,
    }
    print(json.dumps(result))

    # Print blocking findings to stderr for the producer to see
    print(f'\n[independent-review] BLOCKING — {len(catches)} finding(s):',
          file=sys.stderr)
    for c in catches:
        print(f'  - [{c["surface"]}] {c["description"]}', file=sys.stderr)
    print(f'\nFix these findings, then the review will re-run automatically.',
          file=sys.stderr)

    sys.exit(1)


def _format_llm_dispatch_instructions(session_id, file_paths, tier,
                                       handoff_path=None):
    """Format the instructions for the producer to spawn the LLM reviewer agent."""
    files_str = ' '.join(f'"{fp}"' for fp in file_paths[:10])
    if len(file_paths) > 10:
        files_str += f' ... (+{len(file_paths) - 10} more)'

    handoff_note = ''
    if handoff_path:
        handoff_note = f'\n   Handoff: {handoff_path}'

    return f"""
INDEPENDENT REVIEWER DISPATCH — LLM LAYER REQUIRED

The deterministic checks passed ($0). Full-tier review requires the
adversarial LLM reviewer for judgment-dependent checks (ground-truth
cross-check, execution evidence verification, omission audit).

REQUIRED: Spawn the independent reviewer agent with this EXACT prompt:

  Read and execute the independent reviewer mandate at:
  {MANDATE_PATH}

  Session: {session_id}
  Tier: {tier}
  Files: {files_str}{handoff_note}

  Run the full review protocol (Phases A–E) per the mandate.
  Write the verdict file and log the review-pass marker.

The mandate is a FIXED reference doc on disk — do NOT modify it.
The agent reads it, follows it, and writes its own verdict.
"""


if __name__ == '__main__':
    main()
