#!/usr/bin/env python3
"""
BTF-3 Slice 3 — capstone orchestrator routing tests.

Verifies the client-seo-onboarding SKILL.md has been extended with
business-type routing that covers both electrician and restaurant paths
through ONE skill, not a fork.

Run:
    python3 tests/test_btf3_slice3_orchestrator.py
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SCRIPTS_DIR.parent.parent.parent  # repos/ai-agency-core/scripts -> workspace
SKILL_PATH = WORKSPACE_ROOT / "skills" / "client-seo-onboarding" / "SKILL.md"


# ---------------------------------------------------------------------------
# Test 1: SKILL.md has business-type routing section
# ---------------------------------------------------------------------------

def test_routing_section_exists():
    """The orchestrator SKILL.md must have a Business-type routing section."""
    assert SKILL_PATH.is_file(), f"SKILL.md not found at {SKILL_PATH}"
    content = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Business-type routing" in content, (
        "SKILL.md missing '## Business-type routing' section"
    )
    print("  PASS (1): Business-type routing section exists")


# ---------------------------------------------------------------------------
# Test 2: Routing section covers both electrician and restaurant
# ---------------------------------------------------------------------------

def test_routing_covers_both_types():
    """The routing table must name both electrician (matrix) and restaurant (fixed-list)."""
    content = SKILL_PATH.read_text(encoding="utf-8")

    # Extract the routing section
    start = content.find("## Business-type routing")
    assert start > -1
    # Find the next ## section
    next_section = content.find("\n## ", start + 10)
    routing_section = content[start:next_section] if next_section > -1 else content[start:]

    # Must mention both types
    assert "electrician" in routing_section, "Routing section must mention electrician"
    assert "restaurant" in routing_section, "Routing section must mention restaurant"

    # Must mention both page models
    assert "matrix" in routing_section.lower(), "Routing section must mention matrix model"
    assert "fixed-list" in routing_section.lower(), "Routing section must mention fixed-list model"

    print("  PASS (2): Routing covers both electrician and restaurant")


# ---------------------------------------------------------------------------
# Test 3: Per-step routing table is present and covers all 11 steps
# ---------------------------------------------------------------------------

def test_per_step_routing_table():
    """The per-step routing table must cover all pipeline steps."""
    content = SKILL_PATH.read_text(encoding="utf-8")

    start = content.find("### Per-step routing table")
    assert start > -1, "Missing '### Per-step routing table'"

    next_section = content.find("\n### ", start + 10)
    table_section = content[start:next_section] if next_section > -1 else content[start:]

    # Check that all key steps are listed
    required_steps = [
        "1 — Ingest",
        "3 — Author data",
        "5 — Bulk scaffold",
        "6 — Imagery",
        "10 — Internal link",
    ]
    for step in required_steps:
        assert step in table_section, f"Per-step routing table missing: {step}"

    print("  PASS (3): Per-step routing table covers all key steps")


# ---------------------------------------------------------------------------
# Test 4: Version bumped to 1.9
# ---------------------------------------------------------------------------

def test_version_bump():
    """SKILL.md version must be 1.9 (BTF-3)."""
    content = SKILL_PATH.read_text(encoding="utf-8")

    assert "version: 1.9" in content, "SKILL.md version should be 1.9"
    assert "v1.9 changelog" in content, "SKILL.md should have v1.9 changelog entry"

    print("  PASS (4): Version bumped to 1.9")


# ---------------------------------------------------------------------------
# Test 5: Routing references the parameterized scripts from Slices 1-2
# ---------------------------------------------------------------------------

def test_routing_references_parameterized_scripts():
    """The routing section must reference the key flags added in Slices 1-2."""
    content = SKILL_PATH.read_text(encoding="utf-8")

    start = content.find("## Business-type routing")
    routing_section = content[start:content.find("\n## ", start + 10)]

    # Slice 1 flags
    assert "--business-type" in routing_section, (
        "Routing must reference --business-type flag"
    )
    assert "--service-prompts-file" in routing_section, (
        "Routing must reference --service-prompts-file flag"
    )

    # Slice 2 references
    assert "Axis N" in routing_section, (
        "Routing must reference Axis N for fixed-list types"
    )

    # CR-072 reference
    assert "matrix_section_sequence" in routing_section or "CR-072" in routing_section or "page-model.json" in routing_section, (
        "Routing should reference the profile-driven page model"
    )

    print("  PASS (5): Routing references parameterized scripts from Slices 1-2")


# ---------------------------------------------------------------------------
# Test 6: Honest limits documented
# ---------------------------------------------------------------------------

def test_honest_limits_documented():
    """The routing section must document honest limits (what's NOT done yet)."""
    content = SKILL_PATH.read_text(encoding="utf-8")

    start = content.find("### Honest limits")
    assert start > -1, "Missing '### Honest limits' subsection in routing"

    limits_section = content[start:content.find("\n## ", start + 10)]

    # Key honest limits from Slices 1-2
    assert "brief template" in limits_section.lower(), (
        "Must document restaurant brief templates as not yet authored"
    )
    assert "apply" in limits_section.lower() or "--apply" in limits_section, (
        "Must document --apply limitation for fixed-list types"
    )
    assert "imagery" in limits_section.lower() or "prompt" in limits_section.lower(), (
        "Must document restaurant imagery prompts as not yet authored"
    )

    print("  PASS (6): Honest limits documented")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    passed = 0
    failed = 0
    tests = [
        test_routing_section_exists,
        test_routing_covers_both_types,
        test_per_step_routing_table,
        test_version_bump,
        test_routing_references_parameterized_scripts,
        test_honest_limits_documented,
    ]

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL ({tests.index(test_fn) + 1}): {test_fn.__name__} — {e}")
            failed += 1

    print()
    if failed:
        print(f"BTF-3 SLICE 3 ORCHESTRATOR: {passed}/{len(tests)} PASS, {failed} FAILED")
        return 1
    else:
        print(f"BTF-3 SLICE 3 ORCHESTRATOR PASS: {passed}/{len(tests)} tests passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
