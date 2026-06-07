#!/usr/bin/env python3
"""
hardcode-scanner.py — detect template defaults that leaked into scaffolded output.

Catches the D-10/D-11/D-12 class: a field that SHOULD be per-subject but wasn't
parameterized, so the scaffolder silently substituted a template default.

DESIGN: Engine-level / project-agnostic. This script does NOT know about Core 30,
SEO, cities, or services. It reads a declarative scan profile (JSON) that tells it:
  1. Which defaults to watch for (field name + default value + severity)
  2. Which files to scan (glob pattern relative to output dir)
  3. What to report when a default is found

Core 30 is ONE registered profile. Any template+data pipeline can use this scanner
by providing its own profile.

USAGE
-----
Scan against a profile:

    python hardcode-scanner.py --profile profiles/core-30-defaults.json \
                               --output-dir /path/to/scaffolded/page/

Scan with inline overrides (for throwaway/test scaffolds):

    python hardcode-scanner.py --profile profiles/core-30-defaults.json \
                               --output-dir /path/to/output/ \
                               --subject-id "test-city-throwaway"

Dry-run (show what would be scanned, don't scan):

    python hardcode-scanner.py --profile profiles/core-30-defaults.json \
                               --output-dir /path/to/output/ \
                               --dry-run

PROFILE SHAPE
-------------
{
  "profile_id": "core-30-defaults",
  "description": "Detects scaffolder defaults in Core 30 page output",
  "scan_globs": ["*.html", "*.md"],
  "defaults": [
    {
      "field": "dispatch_time",
      "default_values": ["45-minute", "45-min"],
      "severity": "blocking",
      "rationale": "D-12: silent 45-min default when city JSON omits dispatch_time"
    },
    ...
  ]
}

EXIT CODES
----------
  0 — no defaults found (clean)
  1 — at least one blocking default found
  2 — configuration / file error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def load_profile(path: Path) -> dict[str, Any]:
    """Load and validate a scan profile."""
    if not path.is_file():
        sys.stderr.write(f"ERROR: scan profile not found: {path}\n")
        sys.exit(2)
    profile = json.loads(path.read_text(encoding="utf-8"))
    required = {"profile_id", "defaults", "scan_globs"}
    missing = required - set(profile.keys())
    if missing:
        sys.stderr.write(f"ERROR: profile missing required keys: {missing}\n")
        sys.exit(2)
    return profile


def collect_files(output_dir: Path, globs: list[str]) -> list[Path]:
    """Collect all files matching any of the glob patterns."""
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in globs:
        for f in output_dir.rglob(pattern):
            if f.is_file() and f not in seen:
                files.append(f)
                seen.add(f)
    return sorted(files)


def scan_file_for_defaults(
    file_path: Path,
    defaults: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Scan a single file for default values. Returns list of findings."""
    content = file_path.read_text(encoding="utf-8")
    findings: list[dict[str, Any]] = []

    for default_spec in defaults:
        field = default_spec["field"]
        default_values = default_spec["default_values"]
        severity = default_spec.get("severity", "warning")

        for default_val in default_values:
            # Skip empty or trivially short defaults — they match everywhere
            if len(default_val) < 3:
                continue
            # Case-insensitive search — defaults may appear in different casing
            pattern = re.compile(re.escape(default_val), re.IGNORECASE)
            for match in pattern.finditer(content):
                # Find line number
                line_num = content[:match.start()].count("\n") + 1
                # Extract surrounding context (the line containing the match)
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                context_line = content[line_start:line_end].strip()

                findings.append({
                    "field": field,
                    "default_value": default_val,
                    "found_value": match.group(),
                    "file": str(file_path),
                    "line": line_num,
                    "context": context_line[:200],  # truncate long lines
                    "severity": severity,
                    "rationale": default_spec.get("rationale", ""),
                })

    return findings


def run_scan(
    profile: dict[str, Any],
    output_dir: Path,
    subject_id: str | None = None,
) -> dict[str, Any]:
    """Run the full scan. Returns structured results."""
    files = collect_files(output_dir, profile["scan_globs"])

    if not files:
        return {
            "profile_id": profile["profile_id"],
            "output_dir": str(output_dir),
            "subject_id": subject_id,
            "files_scanned": 0,
            "files_list": [],
            "total_findings": 0,
            "blocking_findings": 0,
            "findings": [],
            "verdict": "NO_FILES",
            "message": f"No files matching {profile['scan_globs']} in {output_dir}",
        }

    all_findings: list[dict[str, Any]] = []
    for f in files:
        all_findings.extend(scan_file_for_defaults(f, profile["defaults"]))

    blocking = [f for f in all_findings if f["severity"] == "blocking"]

    return {
        "profile_id": profile["profile_id"],
        "output_dir": str(output_dir),
        "subject_id": subject_id,
        "files_scanned": len(files),
        "files_list": [str(f) for f in files],
        "total_findings": len(all_findings),
        "blocking_findings": len(blocking),
        "findings": all_findings,
        "verdict": "FAIL" if blocking else ("WARN" if all_findings else "PASS"),
    }


def print_report(results: dict[str, Any]) -> None:
    """Print a human-readable report."""
    verdict = results["verdict"]
    print(f"\n{'='*60}")
    print(f"HARDCODE SCANNER — {results['profile_id']}")
    print(f"{'='*60}")
    print(f"Output dir:   {results['output_dir']}")
    print(f"Subject:      {results.get('subject_id', '(not specified)')}")
    print(f"Files scanned: {results['files_scanned']}")
    print(f"Total findings: {results['total_findings']}")
    print(f"Blocking:      {results['blocking_findings']}")
    print(f"Verdict:       {verdict}")
    print(f"{'='*60}")

    if not results["findings"]:
        print("\nNo template defaults found in output. Clean.")
        return

    for i, f in enumerate(results["findings"], 1):
        severity_marker = "BLOCKING" if f["severity"] == "blocking" else "WARNING"
        print(f"\n[{i}] {severity_marker} — field: {f['field']}")
        print(f"    Default value: {f['default_value']!r}")
        print(f"    Found:         {f['found_value']!r}")
        print(f"    File:          {f['file']}:{f['line']}")
        print(f"    Context:       {f['context']}")
        if f["rationale"]:
            print(f"    Rationale:     {f['rationale']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan scaffolded output for template default values that should have been parameterized."
    )
    parser.add_argument(
        "--profile",
        required=True,
        type=Path,
        help="Path to the scan profile JSON",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory containing scaffolded output to scan",
    )
    parser.add_argument(
        "--subject-id",
        default=None,
        help="Subject identifier (for reporting; e.g., city slug)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable report",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be scanned without scanning",
    )
    args = parser.parse_args()

    profile = load_profile(args.profile)

    if args.dry_run:
        files = collect_files(args.output_dir, profile["scan_globs"])
        print(f"Profile: {profile['profile_id']}")
        print(f"Defaults to scan for: {len(profile['defaults'])}")
        for d in profile["defaults"]:
            print(f"  - {d['field']}: {d['default_values']} ({d.get('severity', 'warning')})")
        print(f"Files that would be scanned: {len(files)}")
        for f in files:
            print(f"  - {f}")
        return

    results = run_scan(profile, args.output_dir, args.subject_id)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_report(results)

    if results["verdict"] == "FAIL":
        sys.exit(1)


# -- Importable API for composition with other scripts -----------------------

def scan_from_code(
    profile_path: Path | str,
    output_dir: Path | str,
    subject_id: str | None = None,
) -> dict[str, Any]:
    """Run the scanner programmatically. Returns structured results dict.

    Use this when composing the hardcode-scanner into another script
    (e.g., bulk-scaffold-pages.py post-scaffold validation).
    """
    profile = load_profile(Path(profile_path))
    return run_scan(profile, Path(output_dir), subject_id)


if __name__ == "__main__":
    main()
