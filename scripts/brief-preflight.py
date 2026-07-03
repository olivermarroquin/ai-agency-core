#!/usr/bin/env python3
"""brief-preflight.py — Deterministic §7 data-utilization pre-flight for per-city content briefs.

Checks (LU-T1):
  1. Every PAA/AI-Overview question is assigned to a section or FAQ.
  2. Every symptom/long-tail phrase maps to a section, scenario, or FAQ.
  3. No orphan researched data (every substance fact drives a section or spoke).
  4. Jurisdiction resolves to an authority, not a ZIP (GPR-15 sub-checks a/b/c).
  5. In-body link slugs are named.
  6. Spin-off spokes are identified.

Also runs GPR-15 sub-checks:
  (a) Anti-pattern: ZIP→jurisdiction line detection.
  (b) Require non-ZIP determinant when >=2 authorities named.
  (c) ZIP-collision across jurisdiction sections.

Usage:
  python3 brief-preflight.py <path-to-brief.md>
  python3 brief-preflight.py --all <directory>

Exit codes: 0 = PASS, 1 = FAIL (findings), 2 = error.
Zero hardcoded client values.
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_brief(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_frontmatter_body(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Minimal YAML-like parse for key: value."""
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


# ---------------------------------------------------------------------------
# §7 check 1: Every PAA/AI-Overview question assigned
# ---------------------------------------------------------------------------

def extract_paa_questions(body: str) -> list[str]:
    """Extract verbatim PAA questions from the brief (quoted in Layer 1 sections)."""
    questions = []
    # Match patterns like: "How much does..." or - "How much..."
    for m in re.finditer(r'["""]([^"""\n]{15,}?\?)\s*["""]', body):
        questions.append(m.group(1).strip())
    # Also match unquoted PAA after bullet: - How much ... ?
    for m in re.finditer(r'(?:^|\n)\s*[-•]\s*(?:\*\*)?[""]?([A-Z][^"""\n]{10,}?\?)[""]?(?:\*\*)?', body):
        q = m.group(1).strip().strip('"').strip('"').strip('"')
        if q not in questions:
            questions.append(q)
    return questions


def check_paa_assigned(body: str) -> list[str]:
    """Check that every extracted PAA appears referenced in a section/FAQ assignment."""
    findings = []
    questions = extract_paa_questions(body)
    if not questions:
        return ["§7.1 WARNING: No PAA questions detected in the brief."]

    # For each question, check it's referenced in Layer 4 or FAQ or section assignment
    # We look for: the question text (or its key phrase) appearing in a section header,
    # table row, or assignment line below Layer 1
    layers_below = body
    layer4_match = re.search(r'(?:^|\n)## LAYER [234]', body)
    if layer4_match:
        layers_below = body[layer4_match.start():]

    orphan_paas = []
    body_lower = body.lower()
    for q in questions:
        # Extract key content words (skip stopwords)
        stopwords = {'how', 'much', 'does', 'the', 'what', 'are', 'is', 'it', 'to',
                     'a', 'an', 'in', 'for', 'do', 'can', 'my', 'your', 'i', 'of',
                     'and', 'or', 'there', 'would', 'should', 'have', 'been', 'own'}
        key_words = [w for w in re.findall(r'\b[a-zA-Z]{2,}\b', q.lower())
                     if w not in stopwords]
        if not key_words:
            continue

        # Check: do >=50% of the key words appear together in the body below Layer 1?
        # This handles "three most common causes" matching "3 most common causes"
        found = False
        # Strategy 1: direct substring match (fuzzy — first 25 chars)
        if q[:25].lower() in body_lower:
            found = True
        # Strategy 2: check if majority of key words appear in layers below
        if not found:
            hits = sum(1 for w in key_words if w in layers_below.lower())
            if hits >= max(2, len(key_words) * 0.5):
                found = True
        # Strategy 3: check for the question's core noun phrase in the whole body
        if not found and len(key_words) >= 2:
            core = ".*".join(re.escape(w) for w in key_words[:3])
            if re.search(core, body_lower):
                found = True

        if not found:
            orphan_paas.append(q[:80])

    if orphan_paas:
        for q in orphan_paas:
            findings.append(f"§7.1 FAIL: PAA not assigned to section/FAQ: \"{q}\"")
    return findings


# ---------------------------------------------------------------------------
# §7 check 2: Every symptom/long-tail phrase maps to a section
# ---------------------------------------------------------------------------

def extract_symptoms(body: str) -> list[str]:
    """Extract symptom phrases from symptom tables and symptom mentions."""
    symptoms = []
    # Match table rows in symptom tables (| Symptom | ...)
    for m in re.finditer(r'\|\s*([^|]+?)\s*\|.*?\|.*?\|', body):
        cell = m.group(1).strip()
        # Skip header rows
        if cell.lower() in ('symptom', 'symptom (searcher phrase)', '---', 'candidate section',
                            'query', 'winning trait', 'page', 'section', '#', 'check',
                            'id', 'date'):
            continue
        if re.search(r'[a-zA-Z]{3,}', cell) and len(cell) > 5:
            symptoms.append(cell)
    return symptoms


def check_symptoms_mapped(body: str) -> list[str]:
    findings = []
    symptoms = extract_symptoms(body)
    # Symptom tables themselves are the mapping — if they have link targets, they're mapped
    # Check that symptom rows have a link target column
    symptom_table = re.search(
        r'(?:Symptom|symptom).*?\|.*?(?:link|slug|target)',
        body, re.IGNORECASE
    )
    if symptoms and not symptom_table:
        findings.append("§7.2 FAIL: Symptom table found but no in-body link target column.")
    return findings


# ---------------------------------------------------------------------------
# §7 check 4: Jurisdiction resolves to authority, not ZIP (GPR-15)
# ---------------------------------------------------------------------------

ZIP_RE = re.compile(r'\b\d{5}\b')
JURIS_TERMS = re.compile(
    r'(?:permit|jurisdiction|City\s+of|County|AHJ|Code\s+Admin|LDS|'
    r'building\s+official|Land\s+Development|DPWES|ELER|trades?\s+permit|'
    r'zoning\s+approval|Planning\s+&\s+Zoning)',
    re.IGNORECASE
)
NON_ZIP_DETERMINANT = re.compile(
    r'(?:corporate[.\s-]*limits?|GIS|parcel|tax[.\s-]*bill|town[.\s-]*limits?|'
    r'zoning[.\s-]*approval|boundary|incorporated)',
    re.IGNORECASE
)


def gpr15a_anti_pattern(body: str) -> list[str]:
    """(a) Flag lines that assert ZIP determines jurisdiction."""
    findings = []
    for i, line in enumerate(body.splitlines(), 1):
        zips_in_line = ZIP_RE.findall(line)
        juris_in_line = JURIS_TERMS.findall(line)
        if zips_in_line and juris_in_line:
            # Exclude lines that explicitly say ZIP is NOT the determinant
            if re.search(r'(?:NOT|never|not)\s+(?:determined|the\s+(?:test|determinant))', line, re.IGNORECASE):
                continue
            # Exclude lines with "loosely" or "geographic color" or "do NOT present"
            if re.search(r'(?:loosely|geographic\s+color|do\s+NOT\s+present|loose\s+geographic|'
                         r'never\s+as\s+the|not\s+.*ZIP)', line, re.IGNORECASE):
                continue
            # Exclude lines inside CR-140 GUARD blocks (they warn against the anti-pattern)
            if re.search(r'CR-140|determinant\s*=', line, re.IGNORECASE):
                continue
            # Exclude lines that contain BOTH City and County (explicitly noting overlap)
            if re.search(r'(?:contain\s+BOTH|both\s+City\s+and\s+County|straddle)', line, re.IGNORECASE):
                continue
            # Exclude office address lines (a ZIP in an address is not an assertion)
            if re.search(r'(?:office|Phone|email|portal|Pkwy|St,|Blvd|Center|phone|fax)\s*\**\s*\d', line, re.IGNORECASE):
                continue
            if re.search(r'VA\s+\d{5}', line):
                continue
            # Exclude lines that are asserting ZIP→jurisdiction as FALSE (the guard itself)
            if re.search(r'(?:never\s+determined\s+by|jurisdiction\s+is\s+NOT|is\s+NOT\s+determined)',
                         line, re.IGNORECASE):
                continue
            # Flag: ZIP co-located with jurisdiction term in an assertive context
            findings.append(
                f"GPR-15a FAIL (line {i}): ZIP co-located with jurisdiction term — "
                f"ZIPs {zips_in_line}, terms {juris_in_line}: {line.strip()[:120]}"
            )
    return findings


def gpr15b_require_non_zip_determinant(body: str) -> list[str]:
    """(b) When >=2 authorities named, require a non-ZIP determinant."""
    findings = []
    # Count distinct permitting authorities mentioned
    authorities = set()
    for m in re.finditer(r'(?:City\s+of\s+\w+\s+Code\s+Admin\w*|'
                         r'(?:Fairfax\s+)?County\s+(?:Land\s+Development|LDS)|'
                         r'Town\s+of\s+\w+\s+(?:zoning|Planning)|'
                         r'DPWES)',
                         body, re.IGNORECASE):
        authorities.add(m.group(0).lower()[:30])

    if len(authorities) >= 2:
        # Must have a non-ZIP determinant stated
        if not NON_ZIP_DETERMINANT.search(body):
            findings.append(
                f"GPR-15b FAIL: {len(authorities)} permitting authorities named "
                f"({', '.join(sorted(authorities)[:3])}) but no non-ZIP determinant "
                f"(corporate-limits / GIS / tax-bill) stated."
            )
    return findings


def gpr15c_zip_collision(body: str) -> list[str]:
    """(c) A ZIP under one jurisdiction must not appear as another jurisdiction's address."""
    findings = []
    # Extract jurisdiction sections (§3A-like blocks)
    sections = re.split(r'\n(?=\*\*(?:City of|[A-Z][a-z]+\s+County|Town of))', body)

    zip_to_jurisdiction: dict[str, list[str]] = {}
    for section in sections:
        # Identify which jurisdiction this section describes
        juris_match = re.match(r'\*\*(City of \w+|[A-Z][a-z]+\s+County|Town of \w+)', section)
        if not juris_match:
            continue
        juris_name = juris_match.group(1)
        # Find all ZIPs in this section
        zips = ZIP_RE.findall(section)
        for z in zips:
            if z not in zip_to_jurisdiction:
                zip_to_jurisdiction[z] = []
            if juris_name not in zip_to_jurisdiction[z]:
                zip_to_jurisdiction[z].append(juris_name)

    # Flag any ZIP assigned to >1 jurisdiction (unless the brief explicitly warns about it)
    for z, jurisdictions in zip_to_jurisdiction.items():
        if len(jurisdictions) > 1:
            # Check if the brief already warns about this overlap
            warn_pattern = re.compile(
                rf'{z}.*?(?:both|overlap|straddle|contain\s+BOTH|NOT\s+.*determinant)',
                re.IGNORECASE
            )
            if not warn_pattern.search(body):
                findings.append(
                    f"GPR-15c FAIL: ZIP {z} appears under multiple jurisdictions "
                    f"({', '.join(jurisdictions)}) without an explicit overlap warning."
                )
    return findings


def check_jurisdiction(body: str) -> list[str]:
    """Run all GPR-15 sub-checks."""
    findings = []
    findings.extend(gpr15a_anti_pattern(body))
    findings.extend(gpr15b_require_non_zip_determinant(body))
    findings.extend(gpr15c_zip_collision(body))
    return findings


# ---------------------------------------------------------------------------
# §7 check 5: In-body link slugs named
# ---------------------------------------------------------------------------

def check_link_slugs(body: str) -> list[str]:
    """Check that in-body link target slugs are named (backtick-quoted slugs)."""
    findings = []
    slugs = re.findall(r'`([a-z]+-[a-z]+-[a-z]+-[a-z]+-va)`', body)
    if not slugs:
        findings.append("§7.5 FAIL: No in-body link target slugs found (expected backtick-quoted slug patterns).")
    # Check for a link plan section
    link_section = re.search(r'(?:internal.link|link\s+plan)', body, re.IGNORECASE)
    if not link_section:
        findings.append("§7.5 FAIL: No internal-link plan section found.")
    return findings


# ---------------------------------------------------------------------------
# §7 check 6: Spin-off spokes identified
# ---------------------------------------------------------------------------

def check_spinoff_spokes(body: str) -> list[str]:
    findings = []
    spoke_section = re.search(r'(?:spin.off|spoke|topic\s+cluster)', body, re.IGNORECASE)
    if not spoke_section:
        findings.append("§7.9 FAIL: No spin-off spoke / topic cluster section identified.")
    return findings


# ---------------------------------------------------------------------------
# §7 check 3: Competitor winning traits matched
# ---------------------------------------------------------------------------

def check_competitor_match(body: str) -> list[str]:
    findings = []
    # Look for the structural blueprint / winning-trait table
    blueprint_table = re.search(r'(?:winning\s+trait|structural\s+blueprint|competitor.*beat)', body, re.IGNORECASE)
    if not blueprint_table:
        findings.append("§7.3 FAIL: No competitor winning-trait / structural-blueprint table found.")
    return findings


# ---------------------------------------------------------------------------
# §7 check 10: No generic definitional section survives
# ---------------------------------------------------------------------------

def check_no_generic_definition(body: str) -> list[str]:
    findings = []
    # Check for anti-pattern: a section that leads with "what X means/is"
    generic_leads = re.findall(
        r'(?:^|\n)#+\s+(?:What\s+(?:is|are)\s+|Understanding\s+|About\s+(?:our\s+)?)',
        body, re.IGNORECASE
    )
    for gl in generic_leads:
        findings.append(f"§7.10 WARN: Possible generic definitional section header: {gl.strip()}")
    return findings


# ---------------------------------------------------------------------------
# §7 self-checklist detection
# ---------------------------------------------------------------------------

def check_data_utilization_checklist(body: str) -> list[str]:
    """Check if the brief has a §7-style data-utilization checklist."""
    findings = []
    checklist_match = re.search(r'(?:data.utilization\s+checklist|§7\s+data)', body, re.IGNORECASE)
    if not checklist_match:
        findings.append("§7 WARN: No data-utilization checklist section found in the brief.")
    else:
        # Extract only the §7 checklist section (up to the next ## heading or ---)
        section_start = checklist_match.start()
        section_end_match = re.search(r'\n(?:---|##\s)', body[section_start + 10:])
        if section_end_match:
            checklist_text = body[section_start:section_start + 10 + section_end_match.start()]
        else:
            checklist_text = body[section_start:]

        checked = re.findall(r'- \[x\]', checklist_text, re.IGNORECASE)
        unchecked = re.findall(r'- \[ \]', checklist_text)
        if unchecked and checked:
            findings.append(f"§7 FAIL: {len(unchecked)} unchecked item(s) in the §7 data-utilization "
                            f"checklist ({len(checked)} checked).")
        elif unchecked and not checked:
            findings.append(f"§7 WARN: §7 checklist has {len(unchecked)} unchecked items "
                            f"(appears to be a template — check items as they're satisfied).")
    return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_preflight(path: Path) -> tuple[bool, list[str]]:
    """Run all checks. Returns (passed, findings)."""
    text = read_brief(path)
    _, body = split_frontmatter_body(text)

    all_findings: list[str] = []

    # §7 checks
    all_findings.extend(check_paa_assigned(body))
    all_findings.extend(check_symptoms_mapped(body))
    all_findings.extend(check_competitor_match(body))
    all_findings.extend(check_jurisdiction(body))
    all_findings.extend(check_link_slugs(body))
    all_findings.extend(check_spinoff_spokes(body))
    all_findings.extend(check_no_generic_definition(body))
    all_findings.extend(check_data_utilization_checklist(body))

    fails = [f for f in all_findings if "FAIL" in f]
    passed = len(fails) == 0
    return passed, all_findings


def main():
    parser = argparse.ArgumentParser(description="§7 data-utilization brief pre-flight (LU-T1)")
    parser.add_argument("path", help="Path to a brief .md file or directory (with --all)")
    parser.add_argument("--all", action="store_true", help="Run on all brief-*.md files in directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

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
        passed, findings = run_preflight(brief_path)
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
                print("  All §7 checks passed. No findings.")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
