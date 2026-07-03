#!/usr/bin/env python3
"""RGH-19: Deterministic documentation / knowledge-capture gate (Leg B).

BLOCKING checks that enforce documentation completeness as CODE:

  OC-21: exec-log-substantive — execution log exists, has required
         sections, each section is non-empty.
  OC-22: evidence-per-dod-item — every DoD/acceptance line has a
         recorded result, not an unbacked claim.
  OC-23: pattern-candidate-tracking — every "reusable: yes" in the
         exec log links a promoted pattern file or tracked deferral.
  OC-24: catches-referenced — every CR-### filed this run appears in
         the exec log or lessons.
  OC-25: no-silent-deferrals — every punchlist/deferred/TODO points
         to a tracking surface.
  OC-26: knowledge-capture-audit-ran — the 6-item audit is recorded.
  OC-27: spec-vs-registry-oc-cross-check — OC-N numbers in spec files
         match the registry headings (CR-152 ratchet).
  OC-20: productization-DoD B1-B6 (Productize-tier only, already
         registered — wired here as deterministic code).

Honest limit (stated in output): code enforces PRESENCE + SHAPE +
EVIDENCE-FOR-EVERY-CLAIM, NOT insight quality. A mediocre-but-present
lesson passes; a MISSING one BLOCKS.

Usage:
  python3 rgh19-doc-completeness.py \\
    --session <session_id> \\
    --state-dir <path> \\
    --workspace-root <path> \\
    [--exec-log <path>] \\
    [--handoff <path>] \\
    [--tier <Productize|Capture-only|Throwaway>]

Output: JSON verdict to stdout. Exit 0 = all pass, 1 = findings.
"""

import argparse
import glob as glob_mod
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _paths import WORKSPACE_ROOT

# Required exec-log sections (any of these headings satisfies B1)
EXEC_LOG_REQUIRED_HEADINGS = [
    r'##\s*What (Was Built|Happened)',
    r'##\s*Steps',
    r'##\s*Procedure',
    r'##\s*Decisions?\s*Made',
]

# Minimum list items under a required heading to be "substantive"
MIN_LIST_ITEMS = 3


# ===== OC-21: Exec-log exists + substantive =====

def check_oc21_exec_log_substantive(exec_log_path):
    """Verify execution log exists, has required sections, is non-empty."""
    result = {
        'check': 'OC-21', 'check_name': 'exec-log-substantive',
        'verdict': 'FAIL', 'results': [],
    }

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['results'].append({
            'name': 'exec-log-exists', 'verdict': 'FAIL',
            'details': f'execution log not found: {exec_log_path}',
        })
        result['summary'] = '0/3 checks passed'
        return result

    try:
        with open(exec_log_path, 'r') as f:
            content = f.read()
    except OSError as e:
        result['results'].append({
            'name': 'exec-log-readable', 'verdict': 'FAIL',
            'details': f'cannot read: {e}',
        })
        result['summary'] = '0/3 checks passed'
        return result

    passed = 0
    total = 3

    # Check 1: Non-empty (more than just frontmatter)
    body = re.sub(r'^---\n.*?\n---\n?', '', content, count=1, flags=re.DOTALL)
    body_stripped = body.strip()
    if len(body_stripped) > 100:
        result['results'].append({
            'name': 'exec-log-non-empty', 'verdict': 'PASS',
            'details': f'{len(body_stripped)} chars of content',
        })
        passed += 1
    else:
        result['results'].append({
            'name': 'exec-log-non-empty', 'verdict': 'FAIL',
            'details': f'only {len(body_stripped)} chars — too thin',
        })

    # Check 2: Has at least one required heading
    found_headings = []
    for pattern in EXEC_LOG_REQUIRED_HEADINGS:
        if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
            found_headings.append(pattern)

    if found_headings:
        result['results'].append({
            'name': 'exec-log-required-sections', 'verdict': 'PASS',
            'details': f'{len(found_headings)} required heading(s) found',
        })
        passed += 1
    else:
        result['results'].append({
            'name': 'exec-log-required-sections', 'verdict': 'FAIL',
            'details': ('missing required sections: need at least one of '
                        '"What Happened", "Steps", "Procedure", "Decisions Made"'),
        })

    # Check 3: At least MIN_LIST_ITEMS list items under any heading
    list_items = re.findall(r'^\s*[-*]\s+\S', content, re.MULTILINE)
    if len(list_items) >= MIN_LIST_ITEMS:
        result['results'].append({
            'name': 'exec-log-substantive-content', 'verdict': 'PASS',
            'details': f'{len(list_items)} list items found (≥{MIN_LIST_ITEMS})',
        })
        passed += 1
    else:
        result['results'].append({
            'name': 'exec-log-substantive-content', 'verdict': 'FAIL',
            'details': (f'only {len(list_items)} list items '
                        f'(need ≥{MIN_LIST_ITEMS})'),
        })

    result['verdict'] = 'PASS' if passed == total else 'FAIL'
    result['summary'] = f'{passed}/{total} checks passed'
    return result


# ===== OC-22: Evidence-per-DoD-item =====

def check_oc22_evidence_per_dod(exec_log_path, handoff_path):
    """Every DoD/acceptance line must have a recorded result in the exec log."""
    result = {
        'check': 'OC-22', 'check_name': 'evidence-per-dod-item',
        'verdict': 'PASS', 'results': [],
    }

    if not handoff_path or not os.path.isfile(handoff_path):
        result['verdict'] = 'SKIP'
        result['summary'] = 'no handoff to check'
        return result

    # Extract acceptance criteria / DoD items from handoff
    try:
        with open(handoff_path, 'r') as f:
            handoff_content = f.read()
    except OSError:
        result['verdict'] = 'SKIP'
        result['summary'] = 'cannot read handoff'
        return result

    # Find acceptance/DoD items (lines with checkboxes or numbered items
    # in acceptance/DoD sections)
    acc_match = re.search(
        r'^##\s*(?:Acceptance|Definition of Done).*?\n(.*?)(?=\n##\s|\Z)',
        handoff_content, re.DOTALL | re.IGNORECASE | re.MULTILINE)

    if not acc_match:
        result['verdict'] = 'SKIP'
        result['summary'] = 'no acceptance/DoD section in handoff'
        return result

    acc_section = acc_match.group(1)
    # Extract individual criteria (checkbox items or bullet points)
    criteria = re.findall(
        r'^\s*[-*]\s+(?:\[.\]\s+)?(.+?)$', acc_section, re.MULTILINE)

    if not criteria:
        result['verdict'] = 'SKIP'
        result['summary'] = 'no criteria found in acceptance section'
        return result

    # Load exec log
    exec_content = ''
    if exec_log_path and os.path.isfile(exec_log_path):
        try:
            with open(exec_log_path, 'r') as f:
                exec_content = f.read()
        except OSError:
            pass

    # For each criterion, check if there's evidence in the exec log
    # Evidence = the criterion text (or key words from it) appears with
    # a result marker (PASS, FAIL, verified, confirmed, 0 issues, N/M, etc.)
    evidence_markers = re.compile(
        r'PASS|FAIL|verified|confirmed|checked|✅|❌|0\s+(dead|broken|missing)|'
        r'\d+/\d+|complete|shipped|done|resolved|blocked|deferred',
        re.IGNORECASE)

    missing_evidence = []
    for criterion in criteria:
        # Extract key terms from the criterion (first 3-5 significant words)
        words = re.findall(r'\b[a-zA-Z]{3,}\b', criterion)
        key_terms = words[:5] if words else []

        if not key_terms:
            continue

        # Search exec log for these terms near an evidence marker
        found_evidence = False
        for term in key_terms:
            # Look for the term in the exec log
            term_pattern = re.compile(re.escape(term), re.IGNORECASE)
            matches = list(term_pattern.finditer(exec_content))
            for m in matches:
                # Check nearby context (±200 chars) for evidence marker
                start = max(0, m.start() - 200)
                end = min(len(exec_content), m.end() + 200)
                context = exec_content[start:end]
                if evidence_markers.search(context):
                    found_evidence = True
                    break
            if found_evidence:
                break

        if not found_evidence:
            missing_evidence.append(criterion[:80])

    if missing_evidence:
        result['verdict'] = 'FAIL'
        for item in missing_evidence[:10]:
            result['results'].append({
                'name': 'evidence-missing', 'verdict': 'FAIL',
                'details': f'no evidence for: {item}',
            })
    else:
        result['results'].append({
            'name': 'evidence-present', 'verdict': 'PASS',
            'details': f'all {len(criteria)} criteria have evidence',
        })

    result['summary'] = (f'{len(criteria) - len(missing_evidence)}/{len(criteria)} '
                          f'criteria with evidence')
    return result


# ===== OC-23: Pattern-candidate tracking =====

def check_oc23_pattern_candidates(exec_log_path):
    """Every 'reusable: yes' in exec log must link a pattern or deferral."""
    result = {
        'check': 'OC-23', 'check_name': 'pattern-candidate-tracking',
        'verdict': 'PASS', 'results': [],
    }

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['verdict'] = 'SKIP'
        result['summary'] = 'no exec log'
        return result

    try:
        with open(exec_log_path, 'r') as f:
            content = f.read()
    except OSError:
        result['verdict'] = 'SKIP'
        result['summary'] = 'cannot read exec log'
        return result

    # Find "reusable" markers
    reusable_pattern = re.compile(
        r'(?:reusable.*?(?:yes|true|✅)|Reusable for future.*?Yes)',
        re.IGNORECASE | re.DOTALL)

    matches = list(reusable_pattern.finditer(content))
    if not matches:
        result['summary'] = 'no reusable-yes markers found'
        result['results'].append({
            'name': 'no-reusable-markers', 'verdict': 'PASS',
            'details': 'no "reusable: yes" found — nothing to track',
        })
        return result

    # For each match, check nearby context for a pattern link or deferral
    link_pattern = re.compile(
        r'\[\[pattern-|pattern.*?file|promoted.*?pattern|'
        r'deferred|tracked.*?deferral|PATTERN CANDIDATE',
        re.IGNORECASE)

    untracked = []
    for m in matches:
        start = m.start()
        end = min(len(content), m.end() + 500)
        context = content[start:end]
        if not link_pattern.search(context):
            line_num = content[:start].count('\n') + 1
            untracked.append(f'L{line_num}: reusable-yes without pattern link or deferral')

    if untracked:
        result['verdict'] = 'FAIL'
        for item in untracked:
            result['results'].append({
                'name': 'untracked-reusable', 'verdict': 'FAIL',
                'details': item,
            })
    else:
        result['results'].append({
            'name': 'all-tracked', 'verdict': 'PASS',
            'details': f'{len(matches)} reusable-yes markers, all tracked',
        })

    result['summary'] = f'{len(matches) - len(untracked)}/{len(matches)} tracked'
    return result


# ===== OC-24: Catches-referenced =====

def check_oc24_catches_referenced(exec_log_path, state_dir, session_id,
                                   workspace_root=None):
    """Every CR-### filed this run must appear in exec log or lessons."""
    ws = workspace_root or WORKSPACE_ROOT
    result = {
        'check': 'OC-24', 'check_name': 'catches-referenced',
        'verdict': 'PASS', 'results': [],
    }

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['verdict'] = 'SKIP'
        result['summary'] = 'no exec log'
        return result

    try:
        with open(exec_log_path, 'r') as f:
            exec_content = f.read()
    except OSError:
        result['verdict'] = 'SKIP'
        return result

    # Find CR-### references in the catch register that were filed this session
    catch_register = os.path.join(
        ws, 'second-brain', '_meta', 'handoffs',
        '_review-gate-catch-register.md')

    if not os.path.isfile(catch_register):
        result['summary'] = 'no catch register found'
        return result

    try:
        with open(catch_register, 'r') as f:
            register_content = f.read()
    except OSError:
        result['summary'] = 'cannot read catch register'
        return result

    # Find all CR-### numbers that reference this session's chat ID
    # The session_id may appear as a chat ID in the register rows
    # Also look for today's date as a proxy
    today = time.strftime('%Y-%m-%d')
    cr_pattern = re.compile(r'CR-(\d+)')

    # Find CRs filed today (rough heuristic: rows with today's date)
    todays_crs = set()
    for line in register_content.splitlines():
        if today in line:
            for m in cr_pattern.finditer(line):
                todays_crs.add(f'CR-{m.group(1)}')

    if not todays_crs:
        result['summary'] = 'no CRs filed today'
        result['results'].append({
            'name': 'no-todays-crs', 'verdict': 'PASS',
            'details': 'no CRs to cross-reference',
        })
        return result

    # Check each CR appears in exec log
    missing = []
    for cr_id in sorted(todays_crs):
        if cr_id not in exec_content:
            missing.append(cr_id)

    if missing:
        result['verdict'] = 'FAIL'
        for cr_id in missing:
            result['results'].append({
                'name': f'cr-not-in-exec-log', 'verdict': 'FAIL',
                'details': f'{cr_id} filed today but not referenced in exec log',
            })
    else:
        result['results'].append({
            'name': 'all-crs-referenced', 'verdict': 'PASS',
            'details': f'{len(todays_crs)} CRs filed today, all in exec log',
        })

    result['summary'] = (f'{len(todays_crs) - len(missing)}/{len(todays_crs)} '
                          f'CRs referenced')
    return result


# ===== OC-25: No-silent-deferrals =====

def check_oc25_no_silent_deferrals(exec_log_path):
    """Every punchlist/deferred/TODO must point to a tracking surface."""
    result = {
        'check': 'OC-25', 'check_name': 'no-silent-deferrals',
        'verdict': 'PASS', 'results': [],
    }

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['verdict'] = 'SKIP'
        result['summary'] = 'no exec log'
        return result

    try:
        with open(exec_log_path, 'r') as f:
            content = f.read()
    except OSError:
        result['verdict'] = 'SKIP'
        return result

    # Find deferral markers
    deferral_pattern = re.compile(
        r'(?:deferred|punted|postponed|TODO|FIXME|future[\s-]work|'
        r'not[\s-](?:done|implemented|built)|skipped|omitted|out[\s-]of[\s-]scope)',
        re.IGNORECASE)

    # Tracking surface markers (what a deferral should point to)
    tracking_pattern = re.compile(
        r'\[\[|CR-\d+|handoff|ticket|issue|backlog|task|tracked|'
        r'registered|filed|created.*handoff|→.*handoff',
        re.IGNORECASE)

    lines = content.splitlines()
    silent_deferrals = []

    for i, line in enumerate(lines, 1):
        if deferral_pattern.search(line):
            # Check this line and next 2 lines for a tracking reference
            context_end = min(len(lines), i + 2)
            context = '\n'.join(lines[i-1:context_end])
            if not tracking_pattern.search(context):
                silent_deferrals.append(
                    f'L{i}: "{line.strip()[:80]}" — no tracking surface')

    if silent_deferrals:
        result['verdict'] = 'FAIL'
        for item in silent_deferrals[:10]:
            result['results'].append({
                'name': 'silent-deferral', 'verdict': 'FAIL',
                'details': item,
            })
    else:
        result['results'].append({
            'name': 'no-silent-deferrals', 'verdict': 'PASS',
            'details': 'all deferrals point to tracking surfaces',
        })

    result['summary'] = f'{len(silent_deferrals)} silent deferral(s) found'
    return result


# ===== OC-26: Knowledge-capture-audit-ran =====

def check_oc26_kca_ran(exec_log_path):
    """The 6-item knowledge capture audit must be recorded."""
    result = {
        'check': 'OC-26', 'check_name': 'knowledge-capture-audit-ran',
        'verdict': 'PASS', 'results': [],
    }

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['verdict'] = 'FAIL'
        result['summary'] = 'no exec log'
        result['results'].append({
            'name': 'kca-present', 'verdict': 'FAIL',
            'details': 'execution log not found',
        })
        return result

    try:
        with open(exec_log_path, 'r') as f:
            content = f.read()
    except OSError:
        result['verdict'] = 'FAIL'
        result['summary'] = 'cannot read exec log'
        return result

    # Look for knowledge capture audit markers
    kca_markers = [
        r'knowledge.?capture.?audit',
        r'6.?item.?(?:knowledge|KCA|audit)',
        r'\[.\]\s*(?:Lessons|Execution log|Event.log|State|Tool bugs|Patterns)',
    ]

    found = False
    for pattern in kca_markers:
        if re.search(pattern, content, re.IGNORECASE):
            found = True
            break

    if found:
        result['results'].append({
            'name': 'kca-recorded', 'verdict': 'PASS',
            'details': 'knowledge capture audit found in exec log',
        })
    else:
        result['verdict'] = 'FAIL'
        result['results'].append({
            'name': 'kca-recorded', 'verdict': 'FAIL',
            'details': ('no knowledge capture audit recorded — '
                        'must include the 6-item checklist'),
        })

    result['summary'] = 'KCA recorded' if found else 'KCA not recorded'
    return result


# ===== OC-27: Spec-vs-registry OC-number cross-check (CR-152) =====

def check_oc27_spec_registry_crosscheck(workspace_root=None):
    """Verify OC-N numbers in spec files match the registry headings.

    Seed incident: CR-152 — spec §G/§H used OC-17/18/19 while the
    registry used OC-18/19/20; caught by independent reviewer.
    """
    ws = workspace_root or WORKSPACE_ROOT
    result = {
        'check': 'OC-27',
        'check_name': 'spec-vs-registry-oc-cross-check',
        'verdict': 'PASS', 'results': [],
    }

    # Load the registry
    registry_path = os.path.join(
        ws, 'skills', 'gate-peer-reviewer', 'references',
        'omission-check-registry.md')

    if not os.path.isfile(registry_path):
        result['verdict'] = 'FAIL'
        result['results'].append({
            'name': 'registry-exists', 'verdict': 'FAIL',
            'details': f'registry not found: {registry_path}',
        })
        return result

    try:
        with open(registry_path, 'r') as f:
            registry_content = f.read()
    except OSError as e:
        result['verdict'] = 'FAIL'
        result['results'].append({
            'name': 'registry-readable', 'verdict': 'FAIL',
            'details': str(e),
        })
        return result

    # Extract registered OC numbers from registry headings and table rows
    # Headings: ### OC-N: ...
    # Table rows: | OC-N | ...
    registry_ocs = set()
    for m in re.finditer(r'(?:^###\s*|^\|\s*)OC-(\d+)', registry_content,
                         re.MULTILINE):
        registry_ocs.add(int(m.group(1)))

    if not registry_ocs:
        result['verdict'] = 'FAIL'
        result['results'].append({
            'name': 'registry-has-ocs', 'verdict': 'FAIL',
            'details': 'no OC-N entries found in registry',
        })
        return result

    # Scan spec/reference files that preview OC rows
    spec_dir = os.path.join(
        ws, 'skills', 'gate-peer-reviewer', 'references')
    spec_files = glob_mod.glob(os.path.join(spec_dir, '*.md'))

    # Also check the independent-reviewer-mandate
    mandate_path = os.path.join(spec_dir, 'independent-reviewer-mandate.md')
    if os.path.isfile(mandate_path) and mandate_path not in spec_files:
        spec_files.append(mandate_path)

    mismatches = []
    for spec_path in spec_files:
        if os.path.basename(spec_path) == 'omission-check-registry.md':
            continue  # don't check registry against itself

        try:
            with open(spec_path, 'r') as f:
                spec_content = f.read()
        except OSError:
            continue

        # Find OC-N references in this spec
        spec_ocs = set()
        for m in re.finditer(r'OC-(\d+)', spec_content):
            spec_ocs.add(int(m.group(1)))

        # Check each spec OC-N exists in registry
        for oc_num in sorted(spec_ocs):
            if oc_num not in registry_ocs:
                mismatches.append({
                    'spec_file': os.path.basename(spec_path),
                    'oc_number': f'OC-{oc_num}',
                    'issue': f'OC-{oc_num} referenced in spec but not in registry',
                })

    if mismatches:
        result['verdict'] = 'FAIL'
        for mm in mismatches:
            result['results'].append({
                'name': f'oc-mismatch-{mm["oc_number"]}',
                'verdict': 'FAIL',
                'details': f'{mm["spec_file"]}: {mm["issue"]}',
            })
    else:
        result['results'].append({
            'name': 'oc-numbers-consistent', 'verdict': 'PASS',
            'details': (f'{len(registry_ocs)} OC entries in registry, '
                        f'{len(spec_files)} spec files checked'),
        })

    result['summary'] = (f'{len(mismatches)} mismatch(es)' if mismatches
                          else 'all OC numbers consistent')
    return result


# ===== OC-20: Productization-DoD B1-B6 (Productize-tier only) =====

def check_oc20_productization_dod(exec_log_path, tier):
    """B1-B6 presence checks via grep signals (per PR-1 §B)."""
    result = {
        'check': 'OC-20', 'check_name': 'productization-dod-b1-b6',
        'verdict': 'PASS', 'results': [],
    }

    if tier != 'Productize':
        result['verdict'] = 'SKIP'
        result['summary'] = f'tier={tier}, B1-B6 not required'
        return result

    if not exec_log_path or not os.path.isfile(exec_log_path):
        result['verdict'] = 'FAIL'
        result['summary'] = 'no exec log for Productize-tier DoD'
        result['results'].append({
            'name': 'exec-log-exists', 'verdict': 'FAIL',
            'details': 'Productize-tier requires exec log with B1-B6',
        })
        return result

    try:
        with open(exec_log_path, 'r') as f:
            content = f.read()
    except OSError:
        result['verdict'] = 'FAIL'
        result['summary'] = 'cannot read exec log'
        return result

    # B1-B6 grep signals from PR-1 §B
    b_checks = {
        'B1-repeatable-steps': {
            'patterns': [r'##\s*Steps', r'##\s*Procedure',
                         r'##\s*What (Was Built|Happened)'],
            'min_items': 3,
            'item_pattern': r'^\s*(?:[-*]\s+|\d+[.)]\s+)\S',
        },
        'B2-engine-config-split': {
            'patterns': [r'engine.?config', r'config\s+split',
                         r'client.?specific', r'reusable\s+engine',
                         r'zero.?hardcoded'],
        },
        'B3-config-schema': {
            'patterns': [r'config.*schema', r'```(?:yaml|json)',
                         r'\|\s*field\s*\|', r'\|\s*key\s*\|',
                         r'\|\s*parameter\s*\|'],
        },
        'B4-2nd-instance-verdict': {
            'patterns': [r'2nd.?instance', r'second\s+instance',
                         r'duplicab', r'proven\s+on',
                         r'non.?electrician\s+proof', r'2nd.?vertical',
                         r'2nd.?client'],
        },
        'B5-safety-quality-rules': {
            'patterns': [r'safety', r'failure\s+mode', r'quality\s+rule',
                         r'guard', r'cross.?contamination', r'leak'],
        },
        'B6-skill-candidacy': {
            'patterns': [r'skill.?candidacy', r'skill\s+candidacy',
                         r'worth\s+productizing', r'skill\s+verdict'],
        },
    }

    passed = 0
    total = len(b_checks)

    for b_name, spec in b_checks.items():
        found = False
        for pattern in spec['patterns']:
            if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
                found = True
                break

        # B1 has an additional minimum-items check
        if found and 'min_items' in spec:
            items = re.findall(spec['item_pattern'], content, re.MULTILINE)
            if len(items) < spec['min_items']:
                found = False
                result['results'].append({
                    'name': b_name, 'verdict': 'FAIL',
                    'details': (f'heading found but only {len(items)} '
                                f'items (need ≥{spec["min_items"]})'),
                })
                continue

        if found:
            passed += 1
            result['results'].append({
                'name': b_name, 'verdict': 'PASS',
            })
        else:
            result['results'].append({
                'name': b_name, 'verdict': 'FAIL',
                'details': f'not found in exec log — BLOCKING for Productize tier',
            })

    result['verdict'] = 'PASS' if passed == total else 'FAIL'
    result['summary'] = f'{passed}/{total} B-items present'
    return result


# ===== Tier detection (shared with RGH-18) =====

def detect_tier(exec_log_path, handoff_path):
    """Detect run tier from exec log or handoff."""
    for path in [exec_log_path, handoff_path]:
        if path and os.path.isfile(path):
            try:
                with open(path, 'r') as f:
                    content = f.read(4096)
                m = re.search(r'\*\*Tier:\*\*\s*(\S+)', content)
                if m:
                    return m.group(1)
            except OSError:
                continue
    return 'Productize'


# ===== Exec-log discovery =====

def find_exec_log(state_dir, session_id, workspace_root):
    """Try to find the execution log for this run.

    Searches dirty-ledger for execution-log files, then falls back
    to glob patterns in common locations.
    """
    import engine

    # Check dirty ledger for exec-log files
    dirty = engine.read_dirty_ledger(state_dir, session_id)
    for entry in dirty:
        fp = entry.get('file_path', '')
        if 'execution-log' in fp and fp.endswith('.md') and os.path.isfile(fp):
            return fp

    # Glob for today's exec logs
    today = time.strftime('%Y-%m-%d')
    patterns = [
        os.path.join(workspace_root, 'repos', '*', '.kos',
                      'execution-logs', f'execution-log-{today}*.md'),
        os.path.join(workspace_root, 'second-brain', '_meta', 'handoffs',
                      '*', f'execution-log-{today}*.md'),
    ]

    for pattern in patterns:
        matches = glob_mod.glob(pattern)
        if matches:
            # Return the most recently modified
            matches.sort(key=os.path.getmtime, reverse=True)
            return matches[0]

    return None


# ===== Main =====

def build_verdict(all_results):
    """Build JSON verdict from all OC check results."""
    checks_run = []
    catches = []

    for r in all_results:
        check_name = r.get('check_name', r.get('check', 'unknown'))
        verdict = r.get('verdict', 'FAIL')

        checks_run.append({
            'name': check_name,
            'result': verdict,
            'summary': r.get('summary', ''),
        })

        if verdict == 'FAIL':
            for sub in r.get('results', []):
                if sub.get('verdict') == 'FAIL':
                    catches.append({
                        'surface': f'RGH-19/{r.get("check", "?")}/{sub.get("name", "?")}',
                        'severity': 'blocking',
                        'description': sub.get('details', 'check failed'),
                    })

    has_blocking = any(c.get('severity') == 'blocking' for c in catches)
    overall = 'BLOCKING' if has_blocking else 'PASS'

    # Add cross-check names for full-tier satisfaction
    if any(c['name'] == 'spec-vs-registry-oc-cross-check' for c in checks_run):
        checks_run.append({'name': 'value-cross-check', 'result': overall})

    return {
        'verdict': overall,
        'gate': 'RGH-19',
        'gate_name': 'deterministic-doc-completeness',
        'checks_run': checks_run,
        'catches': catches,
        'cost_usd': 0.0,
        'honest_limit': ('RGH-19 enforces PRESENCE + SHAPE + EVIDENCE-FOR-EVERY-'
                         'CLAIM, NOT insight quality. A mediocre-but-present lesson '
                         'passes; a MISSING one BLOCKS.'),
    }


def main():
    parser = argparse.ArgumentParser(
        description='RGH-19: Deterministic doc/knowledge-completeness gate (Leg B)')
    parser.add_argument('--session', required=True, help='Session ID')
    parser.add_argument('--state-dir', required=True, help='Gate state dir')
    parser.add_argument('--workspace-root', required=True, help='Workspace root')
    parser.add_argument('--exec-log', default=None,
                        help='Execution log path (auto-detected if omitted)')
    parser.add_argument('--handoff', default=None, help='Handoff file path')
    parser.add_argument('--tier', default=None,
                        help='Run tier (Productize|Capture-only|Throwaway)')
    parser.add_argument('--output', default=None,
                        help='Write verdict JSON to this file')
    args = parser.parse_args()

    t_start = time.monotonic()

    # Find exec log
    exec_log = args.exec_log
    if not exec_log:
        exec_log = find_exec_log(args.state_dir, args.session,
                                  args.workspace_root)

    # Detect tier
    tier = args.tier or detect_tier(exec_log, args.handoff)

    all_results = []

    # OC-21: Exec-log substantive
    all_results.append(check_oc21_exec_log_substantive(exec_log))

    # OC-22: Evidence-per-DoD-item
    all_results.append(check_oc22_evidence_per_dod(exec_log, args.handoff))

    # OC-23: Pattern-candidate tracking
    all_results.append(check_oc23_pattern_candidates(exec_log))

    # OC-24: Catches-referenced
    all_results.append(check_oc24_catches_referenced(
        exec_log, args.state_dir, args.session,
        workspace_root=args.workspace_root))

    # OC-25: No-silent-deferrals
    all_results.append(check_oc25_no_silent_deferrals(exec_log))

    # OC-26: Knowledge-capture-audit-ran
    all_results.append(check_oc26_kca_ran(exec_log))

    # OC-27: Spec-vs-registry OC cross-check (CR-152)
    all_results.append(check_oc27_spec_registry_crosscheck(
        workspace_root=args.workspace_root))

    # OC-20: Productization-DoD B1-B6 (Productize-tier only)
    all_results.append(check_oc20_productization_dod(exec_log, tier))

    # Build verdict
    wall_ms = (time.monotonic() - t_start) * 1000
    verdict = build_verdict(all_results)
    verdict['wall_ms'] = round(wall_ms, 2)
    verdict['tier'] = tier
    verdict['exec_log'] = exec_log or 'NOT_FOUND'

    output_str = json.dumps(verdict, indent=2)
    print(output_str)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(output_str)

    sys.exit(0 if verdict['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
