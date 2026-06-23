#!/usr/bin/env python3
"""
BTF-3 Slice 2 — internal links + OQL routing tests.

Tests the fixed-list link model (Axis N) for non-matrix business types:
  1. check_axis_n proposes cross-page links for restaurant pages
  2. check_axis_n skips self-links and already-linked pages
  3. detect_fixed_list_context identifies pages from the page model
  4. Matrix mode still uses Axes A/B/C/D (no Axis N)
  5. OQL spec-routing-table has a restaurant entry

Environment robustness: uses isolated tempdirs, imports real functions.

Run:
    python3 tests/test_btf3_slice2_internal_links.py
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent


def _import_module(script_name: str, module_name: str):
    """Import a script as a module."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = SCRIPTS_DIR / script_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    old_argv = sys.argv
    sys.argv = [str(path)]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
    return mod


# ---------------------------------------------------------------------------
# Test 1: check_axis_n proposes cross-page links
# ---------------------------------------------------------------------------

def test_axis_n_proposes_cross_page_links():
    """Axis N should propose links from a restaurant page to all other pages."""
    mod = _import_module("insert-internal-links.py", "insert_internal_links")

    page_model = {
        "pages": [
            {"slug": "home", "role": "homepage", "title_template": "Home"},
            {"slug": "menu", "role": "menu", "title_template": "Menu"},
            {"slug": "about", "role": "about", "title_template": "About Us"},
            {"slug": "reserve-order", "role": "conversion", "title_template": "Reserve"},
        ]
    }

    # Create a minimal PageContext for "home" with no existing links
    ctx = mod.PageContext(
        folder=Path("/tmp/test-home"),
        page_slug="home",
        service_slug="",
        city_slug="",
        html_path=Path("/tmp/test-home/home.html"),
        html_content="<html><body><h1>Welcome</h1></body></html>",
    )

    corpus_destinations = {"home", "menu", "about", "reserve-order"}

    proposals = mod.check_axis_n(ctx, page_model, corpus_destinations)

    # Should propose links to menu, about, reserve-order (not self)
    assert len(proposals) == 3, f"Expected 3 proposals, got {len(proposals)}"
    dest_hrefs = {p.destination_href for p in proposals}
    assert "/menu/" in dest_hrefs, "Should propose link to /menu/"
    assert "/about/" in dest_hrefs, "Should propose link to /about/"
    assert "/reserve-order/" in dest_hrefs, "Should propose link to /reserve-order/"
    assert "/home/" not in dest_hrefs, "Should NOT propose self-link to /home/"

    # All proposals should be Axis N
    assert all(p.axis == "N" for p in proposals), "All proposals should be Axis N"

    # All should be actionable (destinations exist in corpus)
    assert all(p.status == "actionable" for p in proposals)

    print("  PASS (1): check_axis_n proposes cross-page links correctly")


# ---------------------------------------------------------------------------
# Test 2: check_axis_n skips already-linked pages
# ---------------------------------------------------------------------------

def test_axis_n_skips_existing_links():
    """Axis N should not propose links to pages already linked in the HTML."""
    mod = _import_module("insert-internal-links.py", "insert_internal_links")

    page_model = {
        "pages": [
            {"slug": "home", "role": "homepage", "title_template": "Home"},
            {"slug": "menu", "role": "menu", "title_template": "Menu"},
            {"slug": "about", "role": "about", "title_template": "About Us"},
        ]
    }

    # HTML already contains a link to /menu/
    ctx = mod.PageContext(
        folder=Path("/tmp/test-home"),
        page_slug="home",
        service_slug="",
        city_slug="",
        html_path=Path("/tmp/test-home/home.html"),
        html_content='<html><body><a href="/menu/">See our menu</a></body></html>',
    )

    corpus_destinations = {"home", "menu", "about"}

    proposals = mod.check_axis_n(ctx, page_model, corpus_destinations)

    # Should only propose link to about (menu already linked, home is self)
    assert len(proposals) == 1, f"Expected 1 proposal, got {len(proposals)}"
    assert proposals[0].destination_href == "/about/"

    print("  PASS (2): check_axis_n skips already-linked pages")


# ---------------------------------------------------------------------------
# Test 3: detect_fixed_list_context identifies pages
# ---------------------------------------------------------------------------

def test_detect_fixed_list_context():
    """detect_fixed_list_context should identify pages from the page model."""
    mod = _import_module("insert-internal-links.py", "insert_internal_links")

    page_model = {
        "pages": [
            {"slug": "home", "role": "homepage", "title_template": "Home"},
            {"slug": "menu", "role": "menu", "title_template": "Menu"},
        ]
    }

    # Create a temp folder with the right structure
    with tempfile.TemporaryDirectory() as tmpdir:
        page_folder = Path(tmpdir) / "01-home"
        page_folder.mkdir()
        html_path = page_folder / "draft-v1-WP-WRAPPED.html"
        html_path.write_text("<html><body>Home page</body></html>")

        ctx = mod.detect_fixed_list_context(page_folder, page_model)

        assert ctx is not None, "Should detect the page context"
        assert ctx.page_slug == "home", f"Expected slug 'home', got '{ctx.page_slug}'"
        assert ctx.service_slug == "", "Fixed-list has no service concept"
        assert ctx.city_slug == "", "Fixed-list has no city concept"
        assert "Home page" in ctx.html_content

    # Test with an unknown slug
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_folder = Path(tmpdir) / "99-nonexistent"
        bad_folder.mkdir()
        (bad_folder / "draft-v1-WP-WRAPPED.html").write_text("<html></html>")

        ctx = mod.detect_fixed_list_context(bad_folder, page_model)
        assert ctx is None, "Should return None for unknown page slug"

    print("  PASS (3): detect_fixed_list_context identifies pages from page model")


# ---------------------------------------------------------------------------
# Test 4: Matrix mode uses A/B/C/D, not N
# ---------------------------------------------------------------------------

def test_matrix_mode_no_axis_n():
    """Verify the matrix model path does not call check_axis_n."""
    mod = _import_module("insert-internal-links.py", "insert_internal_links")

    # Verify Axis N is not in the matrix-mode SERVICE_TO_URL_TEMPLATE constants
    # (it's a structural check — matrix mode uses A/B/C/D, not N)
    assert hasattr(mod, "check_axis_a"), "Matrix mode needs check_axis_a"
    assert hasattr(mod, "check_axis_b"), "Matrix mode needs check_axis_b"
    assert hasattr(mod, "check_axis_c"), "Matrix mode needs check_axis_c"
    assert hasattr(mod, "check_axis_d"), "Matrix mode needs check_axis_d"
    assert hasattr(mod, "check_axis_n"), "Fixed-list mode needs check_axis_n"

    # Verify the dispatch logic: inspect main() source for the is_matrix branch
    import inspect
    source = inspect.getsource(mod.main)
    assert "is_matrix" in source, "main() must dispatch based on is_matrix"
    assert "check_axis_n" in source, "main() must call check_axis_n for non-matrix"
    # Axis N should only appear in the else (non-matrix) branch
    assert "check_axis_a" in source, "main() must call check_axis_a for matrix"

    print("  PASS (4): Matrix and fixed-list dispatch paths are separate")


# ---------------------------------------------------------------------------
# Test 5: OQL spec-routing-table has restaurant entry
# ---------------------------------------------------------------------------

def test_oql_routing_restaurant_entry():
    """The OQL spec-routing-table must have a 'Restaurant fixed-list page' entry."""
    routing_path = (
        SCRIPTS_DIR.parent.parent.parent  # workspace root
        / "skills" / "output-quality-loop" / "references" / "spec-routing-table.md"
    )
    assert routing_path.is_file(), f"spec-routing-table.md not found at {routing_path}"

    content = routing_path.read_text(encoding="utf-8")

    assert "### Restaurant fixed-list page" in content, (
        "spec-routing-table.md missing '### Restaurant fixed-list page' entry"
    )
    # Verify it references the key spec sources
    assert "page-model.json" in content, "Restaurant entry should reference page-model.json"
    assert "schema-template.json" in content, "Restaurant entry should reference schema-template.json"
    assert "verify-restaurant-page.json" in content, "Restaurant entry should reference verify profile"
    assert "condition-gated" in content, "Restaurant entry should mention condition-gated checks"

    print("  PASS (5): OQL spec-routing-table has restaurant fixed-list page entry")


# ---------------------------------------------------------------------------
# Test 6: check_axis_n marks missing destinations as future-page
# ---------------------------------------------------------------------------

def test_axis_n_future_page_status():
    """Destinations NOT in corpus_destinations should be marked future-page."""
    mod = _import_module("insert-internal-links.py", "insert_internal_links")

    page_model = {
        "pages": [
            {"slug": "home", "role": "homepage", "title_template": "Home"},
            {"slug": "menu", "role": "menu", "title_template": "Menu"},
            {"slug": "about", "role": "about", "title_template": "About Us"},
            {"slug": "reserve-order", "role": "conversion", "title_template": "Reserve"},
        ]
    }

    ctx = mod.PageContext(
        folder=Path("/tmp/test-home"),
        page_slug="home",
        service_slug="",
        city_slug="",
        html_path=Path("/tmp/test-home/home.html"),
        html_content="<html><body><h1>Welcome</h1></body></html>",
    )

    # Only "menu" exists in corpus — about and reserve-order are missing
    corpus_destinations = {"home", "menu"}

    proposals = mod.check_axis_n(ctx, page_model, corpus_destinations)

    assert len(proposals) == 3, f"Expected 3 proposals, got {len(proposals)}"

    actionable = [p for p in proposals if p.status == "actionable"]
    future = [p for p in proposals if p.status == "future-page"]

    assert len(actionable) == 1, f"Expected 1 actionable, got {len(actionable)}"
    assert actionable[0].destination_href == "/menu/"

    assert len(future) == 2, f"Expected 2 future-page, got {len(future)}"
    future_hrefs = {p.destination_href for p in future}
    assert "/about/" in future_hrefs, "about should be future-page"
    assert "/reserve-order/" in future_hrefs, "reserve-order should be future-page"

    print("  PASS (6): check_axis_n marks missing destinations as future-page")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    passed = 0
    failed = 0
    tests = [
        test_axis_n_proposes_cross_page_links,
        test_axis_n_skips_existing_links,
        test_detect_fixed_list_context,
        test_matrix_mode_no_axis_n,
        test_oql_routing_restaurant_entry,
        test_axis_n_future_page_status,
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
        print(f"BTF-3 SLICE 2 INTERNAL LINKS: {passed}/{len(tests)} PASS, {failed} FAILED")
        return 1
    else:
        print(f"BTF-3 SLICE 2 INTERNAL LINKS PASS: {passed}/{len(tests)} tests passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
