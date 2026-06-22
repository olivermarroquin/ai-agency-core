#!/usr/bin/env python3
"""
Electrician regression test — Wave 2 (stored-fixture comparison).

Runs the unified engine (scaffold-page.py) on each service×city pair that has
a stored HTML+JSON-LD fixture, and asserts:
  1. HTML output is byte-identical to the stored fixture (after normalizing
     the Generated: timestamp, which changes every run)
  2. JSON-LD is value-identical to the stored JSON-LD fixture

Environment robustness:
  - The Generated: timestamp is normalized (changes every run)
  - Related-card linking is made deterministic via SCAFFOLD_CORE30_BASE:
    the test creates controlled sibling page stubs so _page_exists_on_disk
    always finds them, matching the fixtures (which were captured the same way).
    Without this, related cards render as plain text on machines lacking the
    operator's website-archive tree — the CR-069 env-dependency class.

Run:
    python3 -m pytest tests/test_electrician_regression.py -v
    # or directly:
    python3 tests/test_electrician_regression.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CLIENT = "ev-electric-services"

# All service×city pairs with stored fixtures
REGRESSION_PAIRS = [
    ("troubleshooting", "mclean-va"),
    ("troubleshooting", "falls-church-va"),
    ("troubleshooting", "tysons-va"),
    ("troubleshooting", "bethesda-md"),
    ("troubleshooting", "potomac-md"),
]

# Regex to normalize the timestamp line in the CSS comment
TIMESTAMP_RE = re.compile(r"Generated: [0-9T:+\-]+")

# Sibling service slugs whose pages the troubleshooting related_cards reference.
# These stubs must exist on disk for related cards to render as linked <a> tags,
# matching the stored fixtures. Derived from data/services/troubleshooting.json
# related_cards[].href_slug (excluding whole-house-rewire + generator-installation).
SIBLING_SERVICES = [
    "panel-upgrade", "ev-charger", "light-fixture-installation",
    "smoke-alarm", "outlet-installation",
]


def _create_sibling_stubs(base_dir: Path, cities: list[str]) -> None:
    """Create stub page folders so _page_exists_on_disk finds linked siblings."""
    core30_dir = base_dir / CLIENT / "core-30"
    core30_dir.mkdir(parents=True, exist_ok=True)
    for city in cities:
        for svc in SIBLING_SERVICES:
            (core30_dir / f"01-{svc}-{city}").mkdir(exist_ok=True)


def run_unified_engine(service: str, city: str, output_dir: Path,
                       env_extra: dict | None = None) -> str:
    """Run scaffold-page.py and return the HTML output."""
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "scaffold-page.py"),
         "--client", CLIENT,
         "--service", service,
         "--city", city,
         "--position", "1",
         "--output-folder", str(output_dir),
         "--skip-validation"],
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR), env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"scaffold-page.py failed (exit {result.returncode}):\n{result.stderr}"
        )
    html_path = output_dir / "draft-v1-WP-WRAPPED.html"
    if not html_path.is_file():
        raise RuntimeError(f"Expected output not found: {html_path}")
    return html_path.read_text(encoding="utf-8")


def extract_jsonld(html: str) -> dict:
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        html, re.DOTALL,
    )
    if not match:
        raise ValueError("No JSON-LD block found in HTML")
    return json.loads(match.group(1))


def normalize_html(html: str) -> str:
    """Normalize environment-dependent content for comparison."""
    return TIMESTAMP_RE.sub("Generated: NORMALIZED", html)


def test_electrician_regression():
    """Regression test: unified engine output must match stored Wave 1 fixtures.

    Creates controlled sibling page stubs so related-card linking is
    deterministic (matching how the fixtures were captured).
    """
    cities = [city for _, city in REGRESSION_PAIRS]
    passed = 0

    with tempfile.TemporaryDirectory(prefix="btf2-stubs-") as stub_base:
        _create_sibling_stubs(Path(stub_base), cities)
        env_extra = {"SCAFFOLD_CORE30_BASE": stub_base}

        for service, city in REGRESSION_PAIRS:
            html_fixture = FIXTURES_DIR / f"{city}-{service}-html.fixture"
            jsonld_fixture = FIXTURES_DIR / f"{city}-{service}-jsonld.json"

            if not html_fixture.is_file():
                raise FileNotFoundError(f"HTML fixture missing: {html_fixture}")
            if not jsonld_fixture.is_file():
                raise FileNotFoundError(f"JSON-LD fixture missing: {jsonld_fixture}")

            expected_html = html_fixture.read_text(encoding="utf-8")
            expected_jsonld = json.loads(jsonld_fixture.read_text(encoding="utf-8"))

            with tempfile.TemporaryDirectory(prefix=f"btf2-reg-{city}-") as out_dir:
                actual_html = run_unified_engine(
                    service, city, Path(out_dir), env_extra=env_extra)

                # 1. Non-empty output
                assert len(actual_html) > 0, f"{city}: engine produced empty HTML"

                # 2. HTML byte-identical (after timestamp normalization)
                norm_actual = normalize_html(actual_html)
                norm_expected = normalize_html(expected_html)
                assert norm_actual == norm_expected, (
                    f"{city} HTML MISMATCH: "
                    f"expected={len(expected_html)} chars, "
                    f"actual={len(actual_html)} chars. "
                    f"The unified engine must reproduce the stored fixture."
                )

                # 3. JSON-LD value-identical to stored fixture
                actual_jsonld = extract_jsonld(actual_html)
                assert actual_jsonld == expected_jsonld, (
                    f"{city} JSON-LD structure mismatch vs stored fixture."
                )

                passed += 1
                print(f"  PASS: {city}/{service} — "
                      f"HTML byte-identical + JSON-LD value-identical")

    print(f"\nREGRESSION PASS: {passed}/{len(REGRESSION_PAIRS)} pairs "
          f"verified against stored fixtures (with controlled sibling stubs)")


if __name__ == "__main__":
    test_electrician_regression()
