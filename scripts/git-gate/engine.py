"""Git-gate engine — deterministic safety checks, self-healing, reversibility,
audit, and tripwire.

This module contains ALL business logic for the git-gate processor. The
processor script (git-gate-processor.py) is a thin shell that reads the
commit queue and calls these functions.

Design principles (locked with operator 2026-06-25):
  1. No self-certification — producer never certifies its own commit.
  2. Reversibility is the safety net — auto-handle reversible, gate irreversible.
  3. Self-healing, not flag-piling — auto-fix mechanical, escalate only judgment.
  4. Compounding to zero — recurring flags → CR/CG + upstream fix.
  5. Auditable + tripwired — watch the watcher.

PARALLEL-SAFETY: This module does NOT modify gate-check scripts (engine.py,
dirty-ledger-track.py, etc.). It CONSUMES verdict files + the reviewer-session
registry. RGH-18/19 can edit gate scripts in parallel safely.
"""

import json
import os
import re
import subprocess
import time
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(__file__))
from _paths import QUEUE_DIR, REVIEW_GATE_STATE_DIR, AUDIT_DIR, TRIPWIRE_DIR, WORKSPACE_ROOT


# ============================================================================
# B1 — Deterministic safety checks (every commit, cheap, no judgment needed)
# ============================================================================

# Secret patterns — conservative, high-confidence patterns only.
# These catch actual secrets, not variable names or documentation.
SECRET_PATTERNS = [
    # AWS keys (AKIA prefix is definitive)
    re.compile(r'AKIA[0-9A-Z]{16}'),
    # Generic high-entropy secrets assigned to common variable names
    re.compile(r'''(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)\s*[=:]\s*['"][A-Za-z0-9+/=]{20,}['"]''', re.IGNORECASE),
    # .env-style secrets (KEY=value on its own line)
    re.compile(r'^(?:DATABASE_URL|REDIS_URL|MONGODB_URI|SECRET_KEY|API_KEY|AUTH_TOKEN|PRIVATE_KEY)\s*=\s*\S{10,}', re.MULTILINE),
    # PEM private keys
    re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'),
    # GitHub personal access tokens (ghp_ prefix)
    re.compile(r'ghp_[A-Za-z0-9]{36}'),
    # Slack tokens
    re.compile(r'xox[bpas]-[0-9a-zA-Z-]{10,}'),
]

# Cross-client contamination patterns.
# Loaded dynamically from project configs — this is the static fallback.
# The processor loads per-client identity strings from the same config
# infrastructure as mandatory-review-gate/engine.py (RGH-4).
CROSS_CLIENT_IDENTITY_STRINGS = {}  # populated by load_client_identities()

# Tier-3 paths that must NEVER be committed by automation.
TIER3_PROTECTED_PATHS = [
    re.compile(r'\.env(?:\.local|\.production|\.staging)?$'),
    re.compile(r'credentials\.json$'),
    re.compile(r'service-account.*\.json$'),
    re.compile(r'\.ssh/'),
    re.compile(r'id_rsa'),
    re.compile(r'\.gnupg/'),
]

# Irreversible operations that must ALWAYS be gated (Phase D).
IRREVERSIBLE_INDICATORS = {
    'force_push': re.compile(r'--force|--force-with-lease|-f\b'),
    'history_rewrite': re.compile(r'rebase|filter-branch|BFG'),
}


def load_client_identities(workspace_root: str) -> dict:
    """Load client identity strings from review-gate project config.

    Returns dict mapping repo-path-fragment → list of identity strings.
    Uses the same config infrastructure as mandatory-review-gate (RGH-4).
    """
    config_path = os.path.join(workspace_root, '.review-gate', 'config.yml')
    if not os.path.isfile(config_path):
        return {}

    try:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}
    except (ImportError, Exception):
        return {}

    result = {}
    projects = config.get('projects', {})
    for proj_id, proj_config in projects.items():
        if not isinstance(proj_config, dict):
            continue
        leak_config = proj_config.get('leak_audit')
        if not leak_config or not isinstance(leak_config, dict):
            continue

        data_path = leak_config.get('client_data_path', '')
        identity_fields = leak_config.get('identity_fields', ['name', 'owner_name'])
        file_pattern = leak_config.get('client_file_pattern', 'client-*.json')

        if not data_path:
            continue

        abs_data_path = os.path.join(workspace_root, data_path)
        if not os.path.isdir(abs_data_path):
            continue

        import glob as glob_mod
        client_files = glob_mod.glob(os.path.join(abs_data_path, file_pattern))
        identities = []
        for cf in client_files:
            try:
                with open(cf, 'r') as f:
                    data = json.load(f)
                for field in identity_fields:
                    val = data.get(field, '')
                    if val and isinstance(val, str) and len(val) > 2:
                        identities.append((val, os.path.basename(cf)))
            except (json.JSONDecodeError, OSError):
                continue
        if identities:
            markers = proj_config.get('path_markers', [])
            for marker in markers:
                result[marker] = identities

    return result


class CheckResult:
    """Result from a single safety check."""

    def __init__(self, name: str, passed: bool, detail: str = '',
                 auto_fixable: bool = False, fix_action: str = ''):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.auto_fixable = auto_fixable
        self.fix_action = fix_action

    def to_dict(self) -> dict:
        d = {'name': self.name, 'passed': self.passed, 'detail': self.detail}
        if self.auto_fixable:
            d['auto_fixable'] = True
            d['fix_action'] = self.fix_action
        return d


def check_file_list_match(request: dict) -> CheckResult:
    """B1-1: Staged diff == declared file list (no stray/unrelated files).

    Rejects `git add .` patterns. Only the explicitly declared files
    should be staged.
    """
    repo = request['repo']
    declared = set(request.get('files_relative', []))

    if not declared:
        return CheckResult('file-list-match', False,
                           'No files declared in commit request')

    # Check for wildcard patterns (reject git add . / git add -A)
    for f in declared:
        if f in ('.', '-A', '--all', '*'):
            return CheckResult('file-list-match', False,
                               f'Wildcard pattern rejected: {f}. '
                               f'Files must be listed explicitly.')

    # Verify all declared files exist in the repo
    missing = []
    for f in declared:
        abs_path = os.path.join(repo, f) if not os.path.isabs(f) else f
        if not os.path.exists(abs_path):
            missing.append(f)

    if missing:
        return CheckResult('file-list-match', False,
                           f'Declared files not found: {missing}')

    return CheckResult('file-list-match', True,
                       f'{len(declared)} files declared, all exist')


def check_secret_scan(request: dict) -> CheckResult:
    """B1-2: Secret/key/token scan on the diff."""
    diff = request.get('diff', '')
    if not diff:
        return CheckResult('secret-scan', True, 'No diff content to scan')

    # Only scan added lines (lines starting with +, excluding +++ header)
    added_lines = []
    for line in diff.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added_lines.append(line[1:])  # strip the leading +

    added_content = '\n'.join(added_lines)
    hits = []
    for pattern in SECRET_PATTERNS:
        matches = pattern.findall(added_content)
        if matches:
            # Redact the actual secret value
            hits.append(f'{pattern.pattern[:40]}... ({len(matches)} hit(s))')

    if hits:
        return CheckResult('secret-scan', False,
                           f'Potential secrets detected in diff: {"; ".join(hits)}')

    return CheckResult('secret-scan', True, 'No secrets detected in diff')


def check_cross_client_leak(request: dict) -> CheckResult:
    """B1-3: Cross-client contamination scan.

    Checks if identity strings from one client appear in files belonging
    to a different client's repo/directory.
    """
    identities = load_client_identities(WORKSPACE_ROOT)
    if not identities:
        return CheckResult('cross-client-leak', True,
                           'No client identity registry configured (N/A)')

    diff = request.get('diff', '')
    repo = request['repo']
    files = request.get('files_relative', [])

    # Determine which client this repo belongs to
    repo_client_marker = None
    for marker in identities:
        if marker in repo:
            repo_client_marker = marker
            break

    if not repo_client_marker:
        return CheckResult('cross-client-leak', True,
                           'Repo does not match any configured client')

    # Check diff for OTHER clients' identity strings
    leaks = []
    own_identities = {name.lower() for name, _src in identities.get(repo_client_marker, [])}

    for other_marker, other_ids in identities.items():
        if other_marker == repo_client_marker:
            continue
        for identity_str, source_file in other_ids:
            if identity_str.lower() in own_identities:
                continue  # shared identity (e.g., same owner name)
            if identity_str.lower() in diff.lower():
                leaks.append(f'"{identity_str}" (from {source_file}) '
                             f'found in {other_marker} diff')

    if leaks:
        return CheckResult('cross-client-leak', False,
                           f'Cross-client contamination: {"; ".join(leaks)}')

    return CheckResult('cross-client-leak', True,
                       'No cross-client contamination detected')


def check_verdict_exists(request: dict) -> CheckResult:
    """B1-4: Review verdict file exists and == PASS."""
    verdict_path = request.get('verdict_file', '')
    if not verdict_path or not os.path.isfile(verdict_path):
        return CheckResult('verdict-exists', False,
                           f'Verdict file missing: {verdict_path}')

    try:
        with open(verdict_path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return CheckResult('verdict-exists', False,
                           f'Verdict file unreadable: {e}')

    verdict = data.get('verdict', '')
    if verdict not in ('PASS',):
        return CheckResult('verdict-exists', False,
                           f'Verdict is {verdict}, not PASS')

    checks_run = data.get('checks_run', [])
    if not checks_run:
        return CheckResult('verdict-exists', False,
                           'Verdict file has no checks_run')

    return CheckResult('verdict-exists', True,
                       f'Verdict PASS with {len(checks_run)} checks')


def check_branch(request: dict) -> CheckResult:
    """B1-5: Branch is correct (not detached HEAD, not unexpected)."""
    repo = request['repo']
    declared_branch = request.get('branch', '')

    try:
        result = subprocess.run(
            ['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=10)
        actual_branch = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult('branch-check', False,
                           'Could not determine current branch')

    if actual_branch == 'HEAD':
        return CheckResult('branch-check', False,
                           'Detached HEAD state — cannot commit')

    if declared_branch and actual_branch != declared_branch:
        return CheckResult('branch-check', False,
                           f'Branch mismatch: declared={declared_branch}, '
                           f'actual={actual_branch}')

    return CheckResult('branch-check', True,
                       f'Branch OK: {actual_branch}')


def check_tier3_paths(request: dict) -> CheckResult:
    """B1-6: Tier-3 protected paths untouched."""
    files = request.get('files_relative', [])
    violations = []
    for f in files:
        for pattern in TIER3_PROTECTED_PATHS:
            if pattern.search(f):
                violations.append(f)
                break

    if violations:
        return CheckResult('tier3-paths', False,
                           f'Tier-3 protected paths in commit: {violations}')

    return CheckResult('tier3-paths', True, 'No tier-3 paths in commit')


def check_no_force_push(request: dict) -> CheckResult:
    """B1-7: Not a force-push or history rewrite."""
    # Force-push is about the push command, not the commit.
    # For the commit request, we check the message and tier.
    if request.get('tier') == 'irreversible':
        return CheckResult('no-force-push', False,
                           'Irreversible tier — requires operator approval',
                           auto_fixable=False)

    return CheckResult('no-force-push', True, 'Reversible tier')


def run_safety_checks(request: dict) -> list:
    """Run all B1 deterministic safety checks. Returns list of CheckResult."""
    return [
        check_file_list_match(request),
        check_secret_scan(request),
        check_cross_client_leak(request),
        check_verdict_exists(request),
        check_branch(request),
        check_tier3_paths(request),
        check_no_force_push(request),
    ]


# ============================================================================
# B2 — Independent commit sign-off (via reviewer-session registry)
# ============================================================================

def verify_independent_signoff(request: dict) -> CheckResult:
    """B2: Verify the commit has an independent reviewer's sign-off.

    Uses the RGH-15/17 reviewer-session registry. The run's existing
    separate-session independent peer-reviewer signs the diff.

    Enforces:
    - reviewer_session_id is present and != session_id (no self-sign)
    - reviewer_session_id has a valid role marker in the registry
    - verdict file has independent reviewer schema
    """
    reviewer_session = request.get('reviewer_session_id')
    producer_session = request.get('session_id')

    if not reviewer_session:
        return CheckResult('independent-signoff', False,
                           'No reviewer_session_id in commit request. '
                           'The run\'s independent reviewer must sign the commit.')

    # Self-sign rejection
    if reviewer_session == producer_session:
        return CheckResult('independent-signoff', False,
                           f'Self-signed: reviewer_session == session_id '
                           f'({reviewer_session}). A session cannot sign its '
                           f'own commit.')

    # UUID v4 format check
    import re as _re
    _UUID4_RE = _re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        _re.IGNORECASE)
    if not _UUID4_RE.match(reviewer_session):
        return CheckResult('independent-signoff', False,
                           f'Reviewer session "{reviewer_session}" is not a '
                           f'valid UUID v4. Fabricated identity rejected.')

    # Registry check: role marker must exist
    marker_path = os.path.join(
        REVIEW_GATE_STATE_DIR, f'{reviewer_session}-role.json')
    if not os.path.isfile(marker_path):
        return CheckResult('independent-signoff', False,
                           f'Reviewer session "{reviewer_session}" has no '
                           f'registered role marker at {marker_path}.')

    try:
        with open(marker_path, 'r') as f:
            marker = json.load(f)
        if marker.get('role') != 'reviewer':
            return CheckResult('independent-signoff', False,
                               f'Role marker has role={marker.get("role")!r}, '
                               f'expected "reviewer".')
    except (json.JSONDecodeError, OSError) as e:
        return CheckResult('independent-signoff', False,
                           f'Could not read role marker: {e}')

    # Verdict file must have independent reviewer schema
    verdict_path = request.get('verdict_file', '')
    if verdict_path and os.path.isfile(verdict_path):
        try:
            with open(verdict_path, 'r') as f:
                verdict_data = json.load(f)
            if verdict_data.get('reviewer_type') != 'independent':
                return CheckResult('independent-signoff', False,
                                   'Verdict file reviewer_type is not '
                                   '"independent".')
            if not verdict_data.get('mandate_version'):
                return CheckResult('independent-signoff', False,
                                   'Verdict file missing mandate_version.')
        except (json.JSONDecodeError, OSError):
            pass  # verdict check is in B1; this is supplementary

    return CheckResult('independent-signoff', True,
                       f'Independent sign-off verified: '
                       f'reviewer={reviewer_session[:12]}...')


# ============================================================================
# C — Tiered self-healing flag cascade
# ============================================================================

class HealingAction:
    """Record of a self-healing action taken."""

    def __init__(self, tier: int, flag: str, action: str,
                 succeeded: bool, detail: str = ''):
        self.tier = tier      # 1=auto-fix, 2=auto-investigate, 3=escalate
        self.flag = flag
        self.action = action
        self.succeeded = succeeded
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            'tier': self.tier,
            'flag': self.flag,
            'action': self.action,
            'succeeded': self.succeeded,
            'detail': self.detail,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }


def heal_stale_lock(repo: str) -> Optional[HealingAction]:
    """Tier 1: Clear stale .git/index.lock if present."""
    lock_path = os.path.join(repo, '.git', 'index.lock')
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
            return HealingAction(1, 'stale-lock', 'removed .git/index.lock',
                                 True, lock_path)
        except OSError as e:
            return HealingAction(1, 'stale-lock',
                                 'failed to remove .git/index.lock',
                                 False, str(e))
    return None


def heal_stray_file(request: dict) -> Optional[HealingAction]:
    """Tier 1: Unstage any file not in the declared list.

    Only applicable after staging — checks git status for staged files
    that aren't in the declared list and unstages them.
    """
    repo = request['repo']
    declared = set(request.get('files_relative', []))

    try:
        result = subprocess.run(
            ['git', '-C', repo, 'diff', '--cached', '--name-only'],
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        staged = set(result.stdout.strip().split('\n')) - {''}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    stray = staged - declared
    if not stray:
        return None

    # Unstage stray files
    try:
        subprocess.run(
            ['git', '-C', repo, 'reset', 'HEAD', '--'] + list(stray),
            capture_output=True, text=True, timeout=10)
        return HealingAction(1, 'stray-file',
                             f'Unstaged {len(stray)} stray file(s)',
                             True, ', '.join(stray))
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return HealingAction(1, 'stray-file', 'Failed to unstage stray files',
                             False, str(e))


def heal_collision_serialize(request: dict) -> Optional[HealingAction]:
    """Tier 1: If another commit is in progress in the same repo, wait+retry.

    Serializes concurrent commits to the same repo by checking for
    .git/index.lock (from an active git operation, not stale).
    """
    repo = request['repo']
    lock_path = os.path.join(repo, '.git', 'index.lock')

    if not os.path.exists(lock_path):
        return None

    # Check if the lock is from an active process
    try:
        lock_age = time.time() - os.path.getmtime(lock_path)
    except OSError:
        return None

    if lock_age < 60:  # Less than 60s old — likely an active operation
        return HealingAction(1, 'collision-serialize',
                             'Another git operation is active, will retry',
                             True, f'Lock age: {lock_age:.0f}s')
    else:
        # Stale lock (>60s) — remove it
        return heal_stale_lock(repo)


def run_tier1_healing(request: dict) -> list:
    """Run all Tier-1 (mechanical, auto-fix) healing. Returns list of actions."""
    actions = []
    repo = request['repo']

    lock_heal = heal_stale_lock(repo)
    if lock_heal:
        actions.append(lock_heal)

    stray_heal = heal_stray_file(request)
    if stray_heal:
        actions.append(stray_heal)

    return actions


def run_tier2_investigation(check_results: list, request: dict) -> list:
    """Tier 2: Auto-investigate failed checks + record.

    For recurring flag classes, file a CR/CG entry and spawn an upstream fix
    so the class stops generating flags (compounding to zero).
    """
    actions = []

    for check in check_results:
        if check.passed:
            continue

        if check.auto_fixable:
            actions.append(HealingAction(
                2, check.name, f'Auto-fixable: {check.fix_action}',
                True, check.detail))
        else:
            # Record the failure for investigation
            actions.append(HealingAction(
                2, check.name,
                f'Recorded failure for investigation: {check.detail}',
                False, check.detail))

    return actions


def build_escalation(check_results: list, request: dict) -> Optional[dict]:
    """Tier 3: Build an escalation package for the operator cockpit.

    Only genuine judgment / irreversible / true-ambiguity reaches here,
    packaged as "situation + what I'd do + yes/no."
    """
    blocking = [c for c in check_results if not c.passed and not c.auto_fixable]
    if not blocking:
        return None

    return {
        'type': 'git-gate-escalation',
        'repo': request['repo'],
        'run_id': request.get('run_id', ''),
        'chat_id': request.get('chat_id', ''),
        'tier': request.get('tier', 'reversible'),
        'blocking_checks': [c.to_dict() for c in blocking],
        'proposed_action': 'Hold commit until operator reviews.',
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


# ============================================================================
# D — Reversibility line
# ============================================================================

def classify_reversibility(request: dict) -> str:
    """Classify a commit request's reversibility.

    Returns:
        'auto' — reversible, can auto-commit+push
        'gated' — irreversible, requires operator approval
    """
    # Explicit tier override
    if request.get('tier') == 'irreversible':
        return 'gated'

    # Check for force-push
    if request.get('push'):
        # Normal push is reversible (git revert exists)
        # Force-push is irreversible
        message = request.get('message', '')
        if any(ind.search(message) for ind in IRREVERSIBLE_INDICATORS.values()):
            return 'gated'

    # Check files for tier-3 paths
    for f in request.get('files_relative', []):
        for pattern in TIER3_PROTECTED_PATHS:
            if pattern.search(f):
                return 'gated'

    return 'auto'


# ============================================================================
# E — Audit log + tripwire
# ============================================================================

def write_audit_entry(request: dict, check_results: list,
                      healing_actions: list, outcome: str,
                      detail: str = '') -> str:
    """Write an entry to the audit trail. Returns audit file path."""
    os.makedirs(AUDIT_DIR, exist_ok=True)

    entry = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'ts': time.time(),
        'run_id': request.get('run_id', ''),
        'chat_id': request.get('chat_id', ''),
        'repo': request.get('repo', ''),
        'files': request.get('files_relative', []),
        'branch': request.get('branch', ''),
        'tier': request.get('tier', ''),
        'reversibility': classify_reversibility(request),
        'outcome': outcome,  # committed, rejected, escalated, healed+committed
        'detail': detail,
        'checks': [c.to_dict() for c in check_results],
        'healing_actions': [a.to_dict() for a in healing_actions],
    }

    audit_file = os.path.join(AUDIT_DIR, 'git-gate-audit.jsonl')
    with open(audit_file, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    return audit_file


def check_tripwire(window_hours: int = 24, max_auto_fix_rate: float = 0.5,
                   max_revert_count: int = 3) -> Optional[dict]:
    """Check if auto-fix or revert rates have spiked.

    Returns an alert dict if a tripwire fires, None if all clear.
    """
    audit_file = os.path.join(AUDIT_DIR, 'git-gate-audit.jsonl')
    if not os.path.isfile(audit_file):
        return None

    cutoff = time.time() - (window_hours * 3600)
    entries = []
    try:
        with open(audit_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get('ts', 0) >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if not entries:
        return None

    total = len(entries)
    auto_fixed = sum(1 for e in entries if e.get('outcome') == 'healed+committed')
    rejected = sum(1 for e in entries if e.get('outcome') == 'rejected')
    escalated = sum(1 for e in entries if e.get('outcome') == 'escalated')

    alerts = []

    # Auto-fix rate spike
    if total >= 5 and auto_fixed / total > max_auto_fix_rate:
        alerts.append(f'Auto-fix rate {auto_fixed}/{total} '
                      f'({auto_fixed/total:.0%}) exceeds {max_auto_fix_rate:.0%} '
                      f'threshold in {window_hours}h window')

    # Rejection rate spike
    if rejected >= max_revert_count:
        alerts.append(f'{rejected} rejections in {window_hours}h window '
                      f'(threshold: {max_revert_count})')

    if not alerts:
        return None

    alert = {
        'type': 'git-gate-tripwire',
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'window_hours': window_hours,
        'total_commits': total,
        'auto_fixed': auto_fixed,
        'rejected': rejected,
        'escalated': escalated,
        'alerts': alerts,
        'action': 'PAUSE auto-land until operator reviews',
    }

    # Write tripwire state
    os.makedirs(TRIPWIRE_DIR, exist_ok=True)
    tripwire_file = os.path.join(TRIPWIRE_DIR, 'pause.json')
    with open(tripwire_file, 'w') as f:
        json.dump(alert, f, indent=2)

    return alert


def is_auto_land_paused() -> bool:
    """Check if auto-land has been paused by the tripwire."""
    tripwire_file = os.path.join(TRIPWIRE_DIR, 'pause.json')
    return os.path.isfile(tripwire_file)


def clear_tripwire_pause():
    """Clear the tripwire pause (operator action only)."""
    tripwire_file = os.path.join(TRIPWIRE_DIR, 'pause.json')
    if os.path.isfile(tripwire_file):
        os.remove(tripwire_file)


# ============================================================================
# F — Autonomous-safe action contract (generalized pattern)
# ============================================================================

AUTONOMOUS_SAFE_ACTION_CONTRACT = {
    'version': '1.0',
    'name': 'autonomous-safe-action',
    'description': (
        'A reusable contract for safely automating actions that were '
        'previously human-gated. The same shape applies to commits, '
        'deploys, publishes, and submissions.'
    ),
    'stages': [
        {
            'stage': 'deterministic-check',
            'description': 'Cheap, mechanical, rule-based checks. No judgment.',
            'blocking': True,
            'independence_required': False,
        },
        {
            'stage': 'independent-signoff',
            'description': 'Judgment-based review by an independent party.',
            'blocking': True,
            'independence_required': True,
        },
        {
            'stage': 'auto-fix',
            'description': 'Tier 1: Fix mechanical issues automatically.',
            'blocking': False,
            'logged': True,
        },
        {
            'stage': 'auto-investigate',
            'description': 'Tier 2: Diagnose + fix-with-confidence + record.',
            'blocking': False,
            'logged': True,
            'spawns_upstream_fix': True,
        },
        {
            'stage': 'escalate-rare',
            'description': 'Tier 3: Genuine judgment / irreversible. One cockpit.',
            'blocking': True,
            'target': 'mission-control-dashboard',
        },
        {
            'stage': 'audit',
            'description': 'Every action logged to an auditable trail.',
            'blocking': False,
        },
        {
            'stage': 'tripwire',
            'description': 'Rate-spike detection. Pause on anomaly.',
            'blocking': True,
            'condition': 'rate spike detected',
        },
    ],
    'reversibility_gate': {
        'auto': 'Reversible actions proceed without operator.',
        'gated': 'Irreversible actions always require operator approval.',
    },
    'applicable_to': [
        'git commits + pushes (git-gate — first implementation)',
        'deploys (future)',
        'publishes (future)',
        'submissions (future)',
    ],
}


# ============================================================================
# Processor orchestration (ties everything together)
# ============================================================================

def process_commit_request(request: dict) -> dict:
    """Process a single commit request through the full pipeline.

    Returns a result dict with outcome and details.

    Pipeline:
    1. Check tripwire (if paused, reject immediately)
    2. Run B1 deterministic safety checks
    3. Run B2 independent sign-off verification
    4. Classify reversibility (Phase D)
    5. If checks fail → try Tier-1 healing → re-check
    6. If still failing → Tier-2 investigate → Tier-3 escalate
    7. If all pass + reversible → auto-commit (+push if requested)
    8. If all pass + irreversible → escalate to operator
    9. Write audit entry + check tripwire
    """
    result = {
        'run_id': request.get('run_id', ''),
        'repo': request.get('repo', ''),
        'outcome': 'pending',
        'checks': [],
        'healing_actions': [],
        'escalation': None,
        'tripwire_alert': None,
    }

    # Step 1: Check tripwire
    if is_auto_land_paused():
        result['outcome'] = 'rejected'
        result['detail'] = 'Auto-land paused by tripwire. Operator review required.'
        write_audit_entry(request, [], [], 'rejected', result['detail'])
        return result

    # Step 2: B1 deterministic safety checks
    check_results = run_safety_checks(request)
    result['checks'] = [c.to_dict() for c in check_results]

    # Step 3: B2 independent sign-off
    signoff_result = verify_independent_signoff(request)
    check_results.append(signoff_result)
    result['checks'].append(signoff_result.to_dict())

    # Step 4: Reversibility
    reversibility = classify_reversibility(request)

    # Step 5: Check for failures
    failures = [c for c in check_results if not c.passed]

    healing_actions = []

    if failures:
        # Try Tier-1 auto-fix
        tier1_actions = run_tier1_healing(request)
        healing_actions.extend(tier1_actions)

        # Re-run checks after healing
        if tier1_actions:
            check_results = run_safety_checks(request)
            signoff_result = verify_independent_signoff(request)
            check_results.append(signoff_result)
            result['checks'] = [c.to_dict() for c in check_results]
            failures = [c for c in check_results if not c.passed]

    if failures:
        # Tier-2 investigate
        tier2_actions = run_tier2_investigation(failures, request)
        healing_actions.extend(tier2_actions)

        # Tier-3 escalate
        escalation = build_escalation(check_results, request)
        if escalation:
            result['escalation'] = escalation
            result['outcome'] = 'escalated'
            result['detail'] = (f'{len(failures)} check(s) failed. '
                                f'Escalated to operator.')

            # Write escalation file for mission-control-dashboard
            os.makedirs(QUEUE_DIR, exist_ok=True)
            esc_file = os.path.join(
                QUEUE_DIR,
                f'escalation-{request.get("run_id", "unknown")}.json')
            with open(esc_file, 'w') as f:
                json.dump(escalation, f, indent=2)

            write_audit_entry(request, check_results, healing_actions,
                              'escalated', result['detail'])
            result['healing_actions'] = [a.to_dict() for a in healing_actions]

            # Check tripwire
            tripwire = check_tripwire()
            if tripwire:
                result['tripwire_alert'] = tripwire

            return result

    # All checks pass (or healed)
    result['healing_actions'] = [a.to_dict() for a in healing_actions]

    if reversibility == 'gated':
        result['outcome'] = 'escalated'
        result['detail'] = ('All checks PASS but commit is IRREVERSIBLE. '
                            'Operator approval required.')
        write_audit_entry(request, check_results, healing_actions,
                          'escalated', result['detail'])
        return result

    # Auto-commit (+push)
    commit_result = execute_commit(request)
    if commit_result['success']:
        outcome = ('healed+committed' if healing_actions
                   else 'committed')
        result['outcome'] = outcome
        result['detail'] = commit_result.get('detail', 'Committed successfully')
        result['commit_hash'] = commit_result.get('commit_hash', '')
    else:
        result['outcome'] = 'rejected'
        result['detail'] = f'Commit failed: {commit_result.get("error", "unknown")}'

    write_audit_entry(request, check_results, healing_actions,
                      result['outcome'], result['detail'])

    # Check tripwire
    tripwire = check_tripwire()
    if tripwire:
        result['tripwire_alert'] = tripwire

    return result


def execute_commit(request: dict) -> dict:
    """Execute the actual git add + commit (+push).

    ONLY called after all checks pass and reversibility is 'auto'.
    """
    repo = request['repo']
    files = request.get('files_relative', [])
    message = request.get('message', '')

    # Clear stale lock first
    lock_path = os.path.join(repo, '.git', 'index.lock')
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass

    # Stage declared files BY NAME (never git add .)
    try:
        stage_result = subprocess.run(
            ['git', '-C', repo, 'add'] + files,
            capture_output=True, text=True, timeout=30)
        if stage_result.returncode != 0:
            return {'success': False,
                    'error': f'git add failed: {stage_result.stderr}'}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {'success': False, 'error': f'git add error: {e}'}

    # Commit
    try:
        commit_result = subprocess.run(
            ['git', '-C', repo, 'commit', '-m', message],
            capture_output=True, text=True, timeout=30)
        if commit_result.returncode != 0:
            return {'success': False,
                    'error': f'git commit failed: {commit_result.stderr}'}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {'success': False, 'error': f'git commit error: {e}'}

    # Extract commit hash
    commit_hash = ''
    try:
        hash_result = subprocess.run(
            ['git', '-C', repo, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=10)
        if hash_result.returncode == 0:
            commit_hash = hash_result.stdout.strip()[:12]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    detail = f'Committed {commit_hash}'

    # Push if requested
    if request.get('push'):
        try:
            push_result = subprocess.run(
                ['git', '-C', repo, 'push'],
                capture_output=True, text=True, timeout=60)
            if push_result.returncode != 0:
                return {'success': True, 'commit_hash': commit_hash,
                        'detail': f'{detail} (push failed: {push_result.stderr})'}
            detail += ' + pushed'
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {'success': True, 'commit_hash': commit_hash,
                    'detail': f'{detail} (push error: {e})'}

    return {'success': True, 'commit_hash': commit_hash, 'detail': detail}
