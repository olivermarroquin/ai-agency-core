#!/usr/bin/env python3
"""
Restaurant regression test — BTF-2b.

Mirrors test_electrician_regression.py for the restaurant tooling pipeline.
Asserts four things:

  (a) Populated config → all 3 gated schema checks PASS
  (b) No-frills config (no reservations/orders/menu_url) → all 3 gated SKIP
  (c) Populated config with gated schema surgically removed → gated FAIL
  (d) Electrician validate_jsonld + verify-core-30-page still PASS under the recursive extractor

Environment robustness (CR-069/071 discipline):
  - NEVER symlinks into or mutates the shared data/ directory
  - Copies test fixtures into an isolated tempdir and sets SCAFFOLD_CLIENT_DATA_DIR
    to point scaffold-page.py at it (same pattern as SCAFFOLD_CORE30_BASE)
  - All tempdirs are cleaned up even on failure (context managers)
  - Idempotent: no pre-existing state required, no leftover artifacts

Run:
    python3 -m pytest tests/test_restaurant_regression.py -v
    # or directly:
    python3 tests/test_restaurant_regression.py
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = SCRIPTS_DIR / "test-fixtures"
PROFILES_DIR = SCRIPTS_DIR / "profiles"

POPULATED_FIXTURE = FIXTURES_DIR / "client-asian-delight-populated.json"
NOFRILLS_FIXTURE = FIXTURES_DIR / "client-asian-delight-nofrills.json"

VERIFY_PROFILE = PROFILES_DIR / "verify-restaurant-page.json"
WORKSPACE_ROOT = SCRIPTS_DIR.parent.parent.parent  # repos/ai-agency-core/scripts -> workspace


# ============================================================================
# Helpers
# ============================================================================


def _load_verify_module():
    """Load verify-artifact.py as a module (handles hyphenated filename)."""
    spec = importlib.util.spec_from_file_location(
        "verify_artifact", SCRIPTS_DIR / "verify-artifact.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_real_validate_jsonld():
    """Import the REAL validate_jsonld from publish-core-30-page.py via AST extraction.

    Extracts the function source + its dependencies from the real file so we test
    the actual recursive implementation, not a reimplementation.
    """
    import ast

    source = (SCRIPTS_DIR / "publish-core-30-page.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Collect the source of the constants and function we need
    needed_names = {
        "_JSON_LD_BLOCK_PATTERN",
        "_REQUIRED_SCHEMA_TYPES_ELECTRICIAN",
        "_REQUIRED_SCHEMA_TYPES_RESTAURANT",
        "_REQUIRED_SCHEMA_TYPES",
        "validate_jsonld",
    }
    fragments = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            if any(t in needed_names for t in targets):
                fragments.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.FunctionDef) and node.name in needed_names:
            fragments.append(ast.get_source_segment(source, node))

    # Build an executable module from the fragments
    exec_source = "import json, re\nfrom typing import Any\n\n" + "\n\n".join(
        f for f in fragments if f is not None
    )
    exec_globals: dict[str, Any] = {}
    exec(exec_source, exec_globals)
    return exec_globals["validate_jsonld"]


def _prepare_data_dir(fixture_path: Path, data_dir: Path) -> str:
    """Copy a test fixture into an isolated data dir. Returns the client slug."""
    data_dir.mkdir(parents=True, exist_ok=True)
    # Copy the fixture
    shutil.copy2(fixture_path, data_dir / fixture_path.name)
    # Also copy the real asian-delight config (needed for identity-leak ground truth)
    real_config = SCRIPTS_DIR / "data" / "client-asian-delight.json"
    if real_config.is_file():
        shutil.copy2(real_config, data_dir / real_config.name)
    # Extract slug from filename: client-asian-delight-populated.json -> asian-delight-populated
    return fixture_path.stem.replace("client-", "")


def _scaffold_restaurant(client_slug: str, output_dir: Path, data_dir: Path) -> None:
    """Run scaffold-page.py with SCAFFOLD_CLIENT_DATA_DIR pointing to isolated data."""
    env = {**os.environ, "SCAFFOLD_CLIENT_DATA_DIR": str(data_dir)}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "scaffold-page.py"),
         "--client", client_slug,
         "--output-folder", str(output_dir)],
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR), env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"scaffold-page.py failed (exit {result.returncode}):\n{result.stderr}"
        )


def _run_verify(artifact_dir: Path, client_slug: str, data_dir: Path) -> dict[str, Any]:
    """Run verify-artifact programmatically with ground truth from isolated data dir.

    Patches the profile's ground_truth.base_path to point at the isolated dir
    so the verify engine reads the test fixture, not the shared data/ tree.
    """
    mod = _load_verify_module()

    # Load the profile and override base_path to point at isolated dir
    profile = mod.load_profile(Path(VERIFY_PROFILE))
    if "ground_truth" in profile:
        profile["ground_truth"]["base_path"] = str(data_dir)

    return mod.run_verification(
        profile=profile,
        artifact_dir=artifact_dir,
        variables={"client_slug": client_slug},
        workspace_root=WORKSPACE_ROOT,
        skip_http=True,
    )


def _extract_schema_checks(results: dict) -> dict[str, dict]:
    """Extract the 4 schema check results by ID."""
    checks = {}
    for cr in results.get("check_results", []):
        if "schema" in cr["check_id"]:
            checks[cr["check_id"]] = cr
    return checks


def _remove_nested_key(html: str, key: str) -> str:
    """Surgically remove a key from the Restaurant JSON-LD node (valid JSON edit)."""
    pattern = re.compile(
        r'(<script type="application/ld\+json">)\s*(.*?)\s*(</script>)',
        re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return html
    data = json.loads(match.group(2))
    for node in data.get("@graph", [data]):
        if isinstance(node, dict) and node.get("@type") == "Restaurant":
            if key in node:
                del node[key]
    new_jsonld = json.dumps(data, indent=2)
    return html[:match.start(2)] + new_jsonld + html[match.end(2):]


# ============================================================================
# Test cases
# ============================================================================


def test_a_populated_gated_pass():
    """(a) Populated config → all 3 gated schema checks PASS."""
    assert POPULATED_FIXTURE.is_file(), f"Missing fixture: {POPULATED_FIXTURE}"

    with tempfile.TemporaryDirectory(prefix="rst-reg-a-") as tmpdir:
        data_dir = Path(tmpdir) / "data"
        out_dir = Path(tmpdir) / "output"
        out_dir.mkdir()
        slug = _prepare_data_dir(POPULATED_FIXTURE, data_dir)

        _scaffold_restaurant(slug, out_dir, data_dir)
        results = _run_verify(out_dir, slug, data_dir)
        checks = _extract_schema_checks(results)

        assert checks["schema-base-restaurant"]["verdict"] == "PASS", \
            f"schema-base-restaurant: {checks['schema-base-restaurant']}"
        assert checks["schema-menu-page-gated"]["verdict"] == "PASS", \
            f"schema-menu-page-gated: {checks['schema-menu-page-gated']}"
        assert checks["schema-reserve-page-gated"]["verdict"] == "PASS", \
            f"schema-reserve-page-gated: {checks['schema-reserve-page-gated']}"
        assert checks["schema-order-page-gated"]["verdict"] == "PASS", \
            f"schema-order-page-gated: {checks['schema-order-page-gated']}"

        print("  PASS (a): populated config — all 4 schema checks PASS")


def test_b_nofrills_gated_skip():
    """(b) No-frills config → all 3 gated checks SKIPPED (not false-FAIL)."""
    assert NOFRILLS_FIXTURE.is_file(), f"Missing fixture: {NOFRILLS_FIXTURE}"

    with tempfile.TemporaryDirectory(prefix="rst-reg-b-") as tmpdir:
        data_dir = Path(tmpdir) / "data"
        out_dir = Path(tmpdir) / "output"
        out_dir.mkdir()
        slug = _prepare_data_dir(NOFRILLS_FIXTURE, data_dir)

        _scaffold_restaurant(slug, out_dir, data_dir)
        results = _run_verify(out_dir, slug, data_dir)
        checks = _extract_schema_checks(results)

        assert checks["schema-menu-page-gated"]["verdict"] == "SKIPPED", \
            f"schema-menu-page-gated should SKIP: {checks['schema-menu-page-gated']}"
        assert checks["schema-reserve-page-gated"]["verdict"] == "SKIPPED", \
            f"schema-reserve-page-gated should SKIP: {checks['schema-reserve-page-gated']}"
        assert checks["schema-order-page-gated"]["verdict"] == "SKIPPED", \
            f"schema-order-page-gated should SKIP: {checks['schema-order-page-gated']}"

        print("  PASS (b): no-frills config — all 3 gated checks correctly SKIPPED")


def test_c_populated_tampered_gated_fail():
    """(c) Populated config with gated schema removed → gated FAIL."""
    assert POPULATED_FIXTURE.is_file(), f"Missing fixture: {POPULATED_FIXTURE}"

    with tempfile.TemporaryDirectory(prefix="rst-reg-c-") as tmpdir:
        data_dir = Path(tmpdir) / "data"
        out_dir = Path(tmpdir) / "output"
        out_dir.mkdir()
        slug = _prepare_data_dir(POPULATED_FIXTURE, data_dir)

        _scaffold_restaurant(slug, out_dir, data_dir)

        # Tamper: remove hasMenu from menu.html
        menu_path = out_dir / "menu.html"
        menu_path.write_text(
            _remove_nested_key(menu_path.read_text(encoding="utf-8"), "hasMenu"),
            encoding="utf-8",
        )

        # Tamper: remove potentialAction from reserve-order.html
        reserve_path = out_dir / "reserve-order.html"
        reserve_path.write_text(
            _remove_nested_key(reserve_path.read_text(encoding="utf-8"), "potentialAction"),
            encoding="utf-8",
        )

        results = _run_verify(out_dir, slug, data_dir)
        checks = _extract_schema_checks(results)

        assert checks["schema-menu-page-gated"]["verdict"] == "FAIL", \
            f"schema-menu-page-gated should FAIL after removal: {checks['schema-menu-page-gated']}"
        assert checks["schema-reserve-page-gated"]["verdict"] == "FAIL", \
            f"schema-reserve-page-gated should FAIL after removal: {checks['schema-reserve-page-gated']}"
        assert checks["schema-order-page-gated"]["verdict"] == "FAIL", \
            f"schema-order-page-gated should FAIL after removal: {checks['schema-order-page-gated']}"

        # Base restaurant should still PASS (only gated blocks removed)
        assert checks["schema-base-restaurant"]["verdict"] == "PASS", \
            f"schema-base-restaurant should still PASS: {checks['schema-base-restaurant']}"

        print("  PASS (c): tampered output — all 3 gated checks correctly FAIL, base still PASS")


def test_d_electrician_backward_compat():
    """(d) Electrician validate_jsonld + verify still work under the recursive extractor."""
    validate_jsonld = _load_real_validate_jsonld()

    # d1: Electrician fixture passes auto-detect
    fixture_dir = Path(__file__).resolve().parent / "fixtures"
    electrician_html = None
    for f in sorted(fixture_dir.glob("*-html.fixture")):
        electrician_html = f.read_text(encoding="utf-8")
        break

    if electrician_html:
        valid, errors = validate_jsonld(electrician_html)
        assert valid, f"Electrician validate_jsonld should PASS: {errors}"
        print("  PASS (d1): electrician validate_jsonld auto-detect PASS")
    else:
        print("  SKIP (d1): no electrician HTML fixture found")

    # d2: Restaurant HTML correctly fails electrician validation
    if POPULATED_FIXTURE.is_file():
        with tempfile.TemporaryDirectory(prefix="rst-reg-d2-") as tmpdir:
            data_dir = Path(tmpdir) / "data"
            out_dir = Path(tmpdir) / "output"
            out_dir.mkdir()
            slug = _prepare_data_dir(POPULATED_FIXTURE, data_dir)
            _scaffold_restaurant(slug, out_dir, data_dir)
            rst_html = (out_dir / "home.html").read_text(encoding="utf-8")
            valid, errors = validate_jsonld(rst_html, business_type="electrician")
            assert not valid, "Restaurant HTML forced as electrician should FAIL"
            print("  PASS (d2): restaurant HTML correctly fails electrician validation")

    # d3: Electrician regression (delegates to existing test)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "test_electrician_regression.py")],
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
    )
    assert result.returncode == 0, \
        f"Electrician regression failed:\n{result.stdout}\n{result.stderr}"
    print("  PASS (d3): electrician regression 5/5 PASS (recursive extractor backward-compatible)")


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    tests = [
        ("(a) populated → gated PASS", test_a_populated_gated_pass),
        ("(b) no-frills → gated SKIP", test_b_nofrills_gated_skip),
        ("(c) tampered → gated FAIL", test_c_populated_tampered_gated_fail),
        ("(d) electrician backward compat", test_d_electrician_backward_compat),
    ]

    passed = 0
    failed = 0
    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            failed += 1

    total = passed + failed
    if failed:
        print(f"\nREGRESSION FAIL: {passed}/{total} passed, {failed} failed")
        return 1
    print(f"\nRESTAURANT REGRESSION PASS: {passed}/{total} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
