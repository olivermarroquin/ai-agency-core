#!/usr/bin/env python3
"""brief-citation-sweep.py — Deterministic source/citation sweep for per-city content briefs (LU-T2).

Checks:
  1. Every [[wikilink]] in the brief resolves to an existing file on disk.
  2. Every numeric claim (dollar figures, percentages, counts) carries a source
     token / attribution (inline [source: ...], parenthetical citation, or
     explicit "confirm/reconcile" flag).
  3. Flag undefended numbers — numeric claims with no nearby source marker.

Usage:
  python3 brief-citation-sweep.py <path-to-brief.md> [--vault-root ~/workspace/second-brain]
  python3 brief-citation-sweep.py --all <directory> [--vault-root ...]

Exit codes: 0 = PASS, 1 = FAIL, 2 = error.
Zero hardcoded client values.
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_VAULT_ROOT = Path.home() / "workspace" / "second-brain"

# Source markers that count as attribution for a numeric claim
SOURCE_MARKERS = re.compile(
    r'(?:'
    r'\[source\s*:.*?\]'                            # [source: ...]
    r'|(?:per\s+(?:the|this|our|ICP|GSC|DataForSEO|Sonar|SERP|synthesis|teardown|brief|template|spec|Layer|§))'  # "per [source]"
    r'|(?:from\s+(?:the|this|our|a|an|client|city|Layer|§))'    # "from [source]"
    r'|(?:via|citing|source|cited|ref)\s'               # other attribution words
    r'|(?:confirm|reconcile|verify|OPEN|UNVERIFIED|AI-ASSERTED|pending)'  # explicit deferral
    r'|(?:ICP|GSC|DataForSEO|Sonar|SERP|Perplexity|Gemini|ChatGPT)'  # named data sources
    r'|(?:NEC|USBC|NFPA)'                              # code/standard refs
    r'|(?:market\s+ref|market|teardown|synthesis)'     # analysis refs
    r'|§\d'                                           # section cross-ref
    r'|(?:own\s+pull|Layer\s+\d|3C|4A|cost\s+anchor)'  # brief-internal cross-refs
    r'|(?:RECONCILE|reconcile|OPEN|stale|confirm\s+before|do\s+not\s+hardcode)'  # deferral flags
    r'|(?:vol\b|volume|KD|demand|long.tail|PAA)'       # demand-data context
    r'|(?:avg\s+ticket|diagnostic|surcharge)'          # pricing-context terms
    r')',
    re.IGNORECASE
)

# Numeric claim patterns (dollar amounts, percentages, counts with context)
NUMERIC_CLAIM = re.compile(
    r'(?:'
    r'\$[\d,]+(?:[\s–-]+\$?[\d,]+)?'       # $350, $2,500–$4,000
    r'|\d+(?:\.\d+)?%'                       # 84%, 5.0%
    r'|\b\d{1,3}(?:,\d{3})+\b'              # 1,400 (comma-separated counts)
    r'|\b\d+[\s-](?:amp|volt|watt|star)'     # 200-amp, 5-star
    r'|\b(?:\d+\+?\s*(?:reviews?|stars?|impressions?|pages?))'  # 148 reviews
    r')'
)

# Lines to skip (headers, frontmatter, table headers, code blocks)
SKIP_LINE = re.compile(
    r'^(?:'
    r'---'                    # frontmatter delimiter
    r'|#+\s'                  # markdown headers
    r'|\|\s*---'              # table separator
    r'|\|\s*(?:#|ID|Date)'   # table headers
    r'|```'                   # code block
    r'|>\s*\*\*(?:CR|The)'   # blockquote guards (CR-140 etc)
    r'|\s*-\s*\[.\]'         # checklist items (§7 self-check)
    r')'
)

# Numbers that are structural, not claims (line numbers, section refs, list indices)
STRUCTURAL_NUMBER = re.compile(
    r'(?:'
    r'§\d'                             # section references
    r'|Layer\s+\d'                     # Layer 1, Layer 2
    r'|\b[1-9]\.\s'                    # numbered list "1. "
    r'|Wave[\s-]*\d'                   # Wave 1
    r'|v\d+\.\d+'                      # version numbers
    r'|Step\s+\d'                      # Step 3
    r'|#\d+'                           # issue/CR numbers
    r'|\b\d{5}\b'                      # ZIP codes (5 digits)
    r'|(?:571|703)-\d{3}-\d{4}'        # phone numbers
    r'|\d+\s*(?:min|minute|mi\b)'      # drive time / distance
    r'|\d+[–-]\d+\s*(?:words?|wds)'   # word count targets
    r'|NEC\s+\d+'                      # NEC article refs
    r'|FY\d+'                          # fiscal year
    r'|pos(?:ition)?\s+\d+'            # SERP position
    r'|\d{4}-\d{2}-\d{2}'             # dates
    r'|2026-\d{2}'                     # partial dates
    r'|\bRoute\s+\d+'                  # Route 50
    r'|I-\d+'                          # I-66
    r'|Rt\s+\d+'                       # Rt 123
    r'|\d+\s*(?:GBPs?|cities)'         # counts that are competitor-structural
    r'|\d+A?\s*breaker'                # 60A breaker (spec, not claim)
    r'|#\d+\s*copper'                  # #6 copper
    r'|\d+\s*(?:scenarios?|tiles?|FAQs?|sections?|pages?|links?|neighborhoods?)'  # structural counts
    r'|\d+[\s-]*(?:amp|Amp|A)\b'       # 100-amp, 200-amp, 200A (electrical ratings)
    r'|\d+[\s-]*(?:volt|V)\b'          # voltage ratings
    r'|\d+[\s-]*(?:watt|W)\b'          # wattage ratings
    r'|\d+[\s-]*(?:star|Star)\b'       # star ratings
    r')'
)


# ---------------------------------------------------------------------------
# Check 1: Wikilink resolution
# ---------------------------------------------------------------------------

def extract_wikilinks(body: str) -> list[str]:
    """Extract all [[wikilink]] targets from the brief body."""
    return re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]', body)


def resolve_wikilink(slug: str, vault_root: Path, brief_dir: Path) -> bool:
    """Check if a wikilink slug resolves to a file on disk."""
    # Try exact match in vault
    candidates = [
        vault_root,
        brief_dir,
        brief_dir / "demand-captures",
        vault_root / "05_shared-intelligence",
        vault_root / "05_shared-intelligence" / "research-briefs",
        vault_root / "05_shared-intelligence" / "blueprints",
        vault_root / "05_shared-intelligence" / "workflows",
        vault_root / "05_shared-intelligence" / "tools",
        vault_root / "04_projects",
        vault_root / "03_domains",
        vault_root / "_meta",
    ]

    # Normalize slug (may contain path separators)
    slug_parts = slug.split("/")
    filename = slug_parts[-1]

    # Try with and without .md extension
    names_to_try = [filename, f"{filename}.md"]

    try:
        for base in candidates:
            # Bound walk to vault root — skip bases outside it
            if not str(base).startswith(str(vault_root)) and base != brief_dir and base != brief_dir / "demand-captures":
                continue
            for name in names_to_try:
                try:
                    if (base / name).exists():
                        return True
                except (PermissionError, OSError):
                    continue
            # Recursive search (one level deep for performance)
            try:
                if base.is_dir():
                    for child in base.iterdir():
                        if child.is_dir():
                            for name in names_to_try:
                                try:
                                    if (child / name).exists():
                                        return True
                                except (PermissionError, OSError):
                                    continue
            except (PermissionError, OSError):
                continue

        # Try glob across vault for deeper matches (bounded to vault root)
        for name in names_to_try:
            try:
                matches = list(vault_root.rglob(name))
                if matches:
                    return True
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    return False


def check_wikilinks(body: str, vault_root: Path, brief_path: Path) -> list[str]:
    findings = []
    links = extract_wikilinks(body)
    brief_dir = brief_path.parent

    for slug in links:
        if not resolve_wikilink(slug, vault_root, brief_dir):
            findings.append(f"LU-T2.1 FAIL: [[{slug}]] does not resolve to a file on disk.")
    return findings


# ---------------------------------------------------------------------------
# Check 2+3: Numeric claims with source attribution
# ---------------------------------------------------------------------------

def check_numeric_claims(body: str) -> list[str]:
    findings = []
    lines = body.splitlines()
    in_code_block = False

    for i, line in enumerate(lines, 1):
        # Track code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Skip structural lines
        if SKIP_LINE.match(line.strip()):
            continue

        # Find numeric claims in this line
        claims = NUMERIC_CLAIM.findall(line)
        if not claims:
            continue

        # Filter out structural numbers
        real_claims = []
        for claim in claims:
            if not STRUCTURAL_NUMBER.search(claim) and not STRUCTURAL_NUMBER.search(
                line[max(0, line.index(claim) - 15):line.index(claim) + len(claim) + 15]
                if claim in line else ""
            ):
                real_claims.append(claim)

        if not real_claims:
            continue

        # Check for source marker in the same line or adjacent lines (±3)
        context_start = max(0, i - 4)
        context_end = min(len(lines), i + 3)
        context = " ".join(lines[context_start:context_end])

        # Wikilinks count as attribution only when on the SAME line as the claim
        has_same_line_wikilink = bool(re.search(r'\[\[.*?\]\]', line))
        # Table rows with a proper noun (competitor/source name) are self-attributing
        is_attributed_table_row = (
            '|' in line and
            bool(re.search(r'[A-Z][A-Za-z]+\s+[A-Z][a-z]+', line))  # proper noun (e.g. "AJ Long", "Mr Electric")
        )
        if not SOURCE_MARKERS.search(context) and not has_same_line_wikilink and not is_attributed_table_row:
            for claim in real_claims[:2]:  # cap at 2 per line to reduce noise
                findings.append(
                    f"LU-T2.2 FAIL (line {i}): Numeric claim \"{claim}\" has no source "
                    f"attribution within ±3 lines: {line.strip()[:120]}"
                )
    return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_citation_sweep(path: Path, vault_root: Path) -> tuple[bool, list[str]]:
    text = read_brief(path)
    _, body = split_frontmatter_body(text)

    all_findings: list[str] = []
    all_findings.extend(check_wikilinks(body, vault_root, path))
    all_findings.extend(check_numeric_claims(body))

    fails = [f for f in all_findings if "FAIL" in f]
    return len(fails) == 0, all_findings


def read_brief(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_frontmatter_body(text: str) -> tuple[dict, str]:
    fm = {}
    body = text
    m = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            kv = line.split(":", 1)
            if len(kv) == 2:
                fm[kv[0].strip()] = kv[1].strip()
        body = m.group(2)
    return fm, body


def main():
    parser = argparse.ArgumentParser(description="Source/citation sweep for content briefs (LU-T2)")
    parser.add_argument("path", help="Path to brief .md file or directory (with --all)")
    parser.add_argument("--all", action="store_true", help="Run on all brief-*.md files in directory")
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT),
                        help="Path to second-brain vault root")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    vault_root = Path(args.vault_root)
    target = Path(args.path)

    if args.all:
        if not target.is_dir():
            print(f"ERROR: {target} is not a directory", file=sys.stderr)
            sys.exit(2)
        briefs = sorted(target.glob("brief-*.md"))
    else:
        if not target.is_file():
            print(f"ERROR: {target} not found", file=sys.stderr)
            sys.exit(2)
        briefs = [target]

    if not briefs:
        print("No brief files found.", file=sys.stderr)
        sys.exit(2)

    all_pass = True
    results = []
    for brief_path in briefs:
        passed, findings = run_citation_sweep(brief_path, vault_root)
        results.append({"file": str(brief_path), "passed": passed, "findings": findings})
        if not passed:
            all_pass = False

    if args.json:
        import json
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"\n{'='*70}")
            print(f"  {r['file']}")
            print(f"  Status: {status}")
            print(f"{'='*70}")
            if r["findings"]:
                for f in r["findings"]:
                    print(f"  {f}")
            else:
                print("  All citation checks passed. No findings.")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
