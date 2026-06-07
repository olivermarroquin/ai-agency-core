#!/usr/bin/env python3
"""
facts-completeness-gate.py — verify subject data has every field templates need.

Pre-scaffold gate: runs BEFORE scaffolding. Verifies the data files have every
field the templates reference. Missing field → fail loud, never silent-default.

Catches the D-12 class at the earliest possible point: if a city JSON is missing
dispatch_time_short, this gate fails BEFORE the scaffolder can silently substitute
"45-min". The operator must populate the field (or explicitly acknowledge the
default) before scaffolding proceeds.

DESIGN: Engine-level / project-agnostic. This script does NOT know about Core 30,
SEO, cities, or services. It reads a declarative completeness profile (JSON) that
tells it:
  1. Which data files to check (patterns + how to resolve them)
  2. Which fields are required in each data file
  3. What severity each missing field carries

Core 30 is ONE registered profile. Any template+data pipeline can use this gate
by providing its own profile.

USAGE
-----
Check a specific subject (city + service + client):

    python facts-completeness-gate.py \
        --profile profiles/core-30-completeness.json \
        --data-dir data/ \
        --vars city_slug=springfield-va service_slug=ev-charger-installation client_slug=s-and-h-contracting

Check with JSON output:

    python facts-completeness-gate.py \
        --profile profiles/core-30-completeness.json \
        --data-dir data/ \
        --vars city_slug=springfield-va service_slug=ev-charger-installation client_slug=s-and-h-contracting \
        --json

PROFILE SHAPE
-------------
{
  "profile_id": "core-30-completeness",
  "description": "...",
  "data_files": [
    {
      "pattern": "cities/{city_slug}.json",
      "required_fields": [
        {"field": "dispatch_time_short", "severity": "blocking", "rationale": "..."},
        ...
      ]
    },
    ...
  ]
}

EXIT CODES
----------
  0 — all required fields present (complete)
  1 — at least one blocking field missing
  2 — configuration / file error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_profile(path: Path) -> dict[str, Any]:
    """Load and validate a completeness profile."""
    if not path.is_file():
        sys.stderr.write(f"ERROR: completeness profile not found: {path}\n")
        sys.exit(2)
    profile = json.loads(path.read_text(encoding="utf-8"))
    required = {"profile_id", "data_files"}
    missing = required - set(profile.keys())
    if missing:
        sys.stderr.write(f"ERROR: profile missing required keys: {missing}\n")
        sys.exit(2)
    return profile


def resolve_path(pattern: str, variables: dict[str, str]) -> str:
    """Resolve a path pattern using subject variables."""
    try:
        return pattern.format(**variables)
    except KeyError as e:
        sys.stderr.write(
            f"ERROR: path pattern '{pattern}' requires variable {e} "
            f"not provided in --vars. Available: {list(variables.keys())}\n"
        )
        sys.exit(2)


def get_nested(data: dict, field_path: str) -> tuple[bool, Any]:
    """Get a possibly-nested field from a dict using dot notation.

    Returns (found, value). Supports:
      - "dispatch_time_short" → data["dispatch_time_short"]
      - "address.street" → data["address"]["street"]
      - "specific_problems_neighborhood_phrase.{service_slug}" → dynamic key
    """
    parts = field_path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return False, None
        if part not in current:
            return False, None
        current = current[part]
    return True, current


def check_data_file(
    data_dir: Path,
    file_spec: dict[str, Any],
    variables: dict[str, str],
) -> dict[str, Any]:
    """Check a single data file for required fields. Returns findings."""
    pattern = file_spec["pattern"]
    resolved = resolve_path(pattern, variables)
    file_path = data_dir / resolved

    result: dict[str, Any] = {
        "pattern": pattern,
        "resolved_path": str(file_path),
        "file_exists": file_path.is_file(),
        "missing_fields": [],
        "present_fields": [],
    }

    if not file_path.is_file():
        # Entire file missing — all required fields are implicitly missing
        for field_spec in file_spec["required_fields"]:
            result["missing_fields"].append({
                "field": field_spec["field"],
                "severity": field_spec.get("severity", "blocking"),
                "rationale": field_spec.get("rationale", ""),
                "reason": "data file not found",
            })
        return result

    data = json.loads(file_path.read_text(encoding="utf-8"))

    for field_spec in file_spec["required_fields"]:
        field_path = field_spec["field"]

        # Support dynamic field paths (e.g., "specific_problems_neighborhood_phrase.{service_slug}")
        resolved_field = field_path.format(**variables) if "{" in field_path else field_path

        found, value = get_nested(data, resolved_field)

        if not found:
            result["missing_fields"].append({
                "field": field_path,
                "resolved_field": resolved_field,
                "severity": field_spec.get("severity", "blocking"),
                "rationale": field_spec.get("rationale", ""),
                "reason": "field not present in data file",
            })
        elif value is None or (isinstance(value, str) and value.strip() == ""):
            result["missing_fields"].append({
                "field": field_path,
                "resolved_field": resolved_field,
                "severity": field_spec.get("severity", "blocking"),
                "rationale": field_spec.get("rationale", ""),
                "reason": "field present but empty",
            })
        else:
            result["present_fields"].append({
                "field": field_path,
                "resolved_field": resolved_field,
                "value_preview": str(value)[:80],
            })

    return result


def run_gate(
    profile: dict[str, Any],
    data_dir: Path,
    variables: dict[str, str],
) -> dict[str, Any]:
    """Run the full completeness gate. Returns structured results."""
    file_results: list[dict[str, Any]] = []
    all_missing: list[dict[str, Any]] = []

    for file_spec in profile["data_files"]:
        result = check_data_file(data_dir, file_spec, variables)
        file_results.append(result)
        all_missing.extend(result["missing_fields"])

    blocking = [m for m in all_missing if m["severity"] == "blocking"]

    return {
        "profile_id": profile["profile_id"],
        "data_dir": str(data_dir),
        "variables": variables,
        "files_checked": len(file_results),
        "file_results": file_results,
        "total_missing": len(all_missing),
        "blocking_missing": len(blocking),
        "all_missing": all_missing,
        "verdict": "FAIL" if blocking else ("WARN" if all_missing else "PASS"),
    }


def print_report(results: dict[str, Any]) -> None:
    """Print a human-readable report."""
    verdict = results["verdict"]
    print(f"\n{'='*60}")
    print(f"FACTS-COMPLETENESS GATE — {results['profile_id']}")
    print(f"{'='*60}")
    print(f"Data dir:       {results['data_dir']}")
    print(f"Variables:      {results['variables']}")
    print(f"Files checked:  {results['files_checked']}")
    print(f"Total missing:  {results['total_missing']}")
    print(f"Blocking:       {results['blocking_missing']}")
    print(f"Verdict:        {verdict}")
    print(f"{'='*60}")

    for fr in results["file_results"]:
        status = "FOUND" if fr["file_exists"] else "MISSING"
        print(f"\n  [{status}] {fr['resolved_path']}")
        if fr["present_fields"]:
            for pf in fr["present_fields"]:
                print(f"    OK  {pf['field']}: {pf['value_preview']}")
        if fr["missing_fields"]:
            for mf in fr["missing_fields"]:
                sev = "BLOCKING" if mf["severity"] == "blocking" else "WARNING"
                print(f"    {sev}  {mf['field']}: {mf['reason']}")
                if mf.get("rationale"):
                    print(f"            Rationale: {mf['rationale']}")

    if verdict == "PASS":
        print(f"\nAll required fields present. Safe to scaffold.")
    elif verdict == "FAIL":
        print(f"\n{results['blocking_missing']} blocking field(s) missing. "
              f"Populate data files before scaffolding.")
    else:
        print(f"\n{results['total_missing']} non-blocking field(s) missing. "
              f"Scaffolding will proceed but output may have gaps.")


def parse_vars(var_strings: list[str]) -> dict[str, str]:
    """Parse key=value variable arguments into a dict."""
    variables: dict[str, str] = {}
    for v in var_strings:
        if "=" not in v:
            sys.stderr.write(f"ERROR: --vars entry must be key=value, got: {v!r}\n")
            sys.exit(2)
        key, value = v.split("=", 1)
        variables[key.strip()] = value.strip()
    return variables


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-scaffold gate: verify subject data has every field templates need."
    )
    parser.add_argument(
        "--profile",
        required=True,
        type=Path,
        help="Path to the completeness profile JSON",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="Root directory of data files",
    )
    parser.add_argument(
        "--vars",
        nargs="+",
        default=[],
        help="Subject variables as key=value pairs (e.g., city_slug=vienna-va)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable report",
    )
    args = parser.parse_args()

    profile = load_profile(args.profile)
    variables = parse_vars(args.vars)

    results = run_gate(profile, args.data_dir, variables)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_report(results)

    if results["verdict"] == "FAIL":
        sys.exit(1)


# -- Importable API for composition with other scripts -----------------------

def check_from_code(
    profile_path: Path | str,
    data_dir: Path | str,
    variables: dict[str, str],
) -> dict[str, Any]:
    """Run the completeness gate programmatically. Returns structured results dict.

    Use this when composing the gate into another script
    (e.g., scaffold-core-30-page.py pre-scaffold validation,
    or bulk-scaffold-pages.py batch pre-flight).
    """
    profile = load_profile(Path(profile_path))
    return run_gate(profile, Path(data_dir), variables)


if __name__ == "__main__":
    main()
