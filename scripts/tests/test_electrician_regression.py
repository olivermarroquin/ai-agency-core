#!/usr/bin/env python3
"""
Electrician regression smoke test.

Runs BOTH engines (legacy scaffold-core-30-page.py and unified scaffold-page.py)
on the same service×city pair in the same environment and asserts:
  1. Both produce non-empty HTML
  2. HTML output is byte-identical (old == new — the actual regression invariant)
  3. JSON-LD is value-identical to the stored fixture (portable across envs)

The old==new HTML comparison is environment-robust: both engines run in the
same process with the same Maps cache, so env-dependent content (iframe size,
cache state) cancels out. This catches real legacy-renderer breakage without
false-failing on a different machine.

Run:
    python3 -m pytest tests/test_electrician_regression.py -v
    # or directly:
    python3 tests/test_electrician_regression.py
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CLIENT = "ev-electric-services"
SERVICE = "troubleshooting"
CITY = "mclean-va"
POSITION = "1"


def run_engine(script_name: str, output_dir: Path) -> str:
    """Run a scaffolder script and return the HTML output."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name),
         "--client", CLIENT,
         "--service", SERVICE,
         "--city", CITY,
         "--position", POSITION,
         "--output-folder", str(output_dir)]
        + (["--skip-validation"] if script_name == "scaffold-page.py" else []),
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} failed (exit {result.returncode}):\n{result.stderr}")
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


def test_electrician_regression():
    jsonld_path = FIXTURES_DIR / "mclean-va-troubleshooting-jsonld.json"
    if not jsonld_path.is_file():
        raise FileNotFoundError(f"Fixture missing: {jsonld_path}")
    expected_jsonld = json.loads(jsonld_path.read_text())

    with tempfile.TemporaryDirectory(prefix="btf1-reg-old-") as old_dir, \
         tempfile.TemporaryDirectory(prefix="btf1-reg-new-") as new_dir:

        # Run both engines in the same environment
        old_html = run_engine("scaffold-core-30-page.py", Path(old_dir))
        new_html = run_engine("scaffold-page.py", Path(new_dir))

        # 1. Both produce non-empty HTML
        assert len(old_html) > 0, "Legacy engine produced empty HTML"
        assert len(new_html) > 0, "Unified engine produced empty HTML"

        # 2. HTML byte-identical (the actual regression invariant)
        assert old_html == new_html, (
            f"HTML MISMATCH: legacy={len(old_html)} chars, unified={len(new_html)} chars. "
            f"The unified engine's electrician path must produce identical HTML to the legacy engine."
        )

        # 3. JSON-LD value-identical to stored fixture (portable)
        new_jsonld = extract_jsonld(new_html)
        assert new_jsonld == expected_jsonld, (
            "JSON-LD structure mismatch vs stored fixture."
        )

    print(f"REGRESSION PASS: old==new HTML byte-identical ({len(old_html)} chars) + JSON-LD value-identical")


if __name__ == "__main__":
    test_electrician_regression()
