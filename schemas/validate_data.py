#!/usr/bin/env python3
"""
Validate all Core-30 data files against the website-factory JSON Schemas.

Usage:
    python3 repos/ai-agency-core/schemas/validate_data.py

Reports per-file PASS/FAIL with specific error paths.
"""

import json
import os
import sys
from pathlib import Path

try:
    import jsonschema
    # Draft202012Validator requires jsonschema >= 4.0; older versions (e.g., 3.2)
    # have the package but lack the class.
    if not hasattr(jsonschema, "Draft202012Validator"):
        print(
            f"ERROR: jsonschema {jsonschema.__version__} is too old (need >= 4.0). "
            "Run: pip3 install --break-system-packages 'jsonschema>=4.0'"
        )
        sys.exit(1)
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema package not installed. Run: pip3 install --break-system-packages jsonschema")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "scripts" / "data"

# Schema file mapping
SCHEMAS = {
    "client": SCRIPT_DIR / "client.schema.json",
    "city": SCRIPT_DIR / "city.schema.json",
    "city-overlay": SCRIPT_DIR / "city-overlay.schema.json",
    "service": SCRIPT_DIR / "service.schema.json",
}


def load_schema(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def classify_file(rel_path: str) -> str | None:
    """Classify a data file path to determine which schema applies."""
    parts = Path(rel_path).parts

    # client-*.json at top level
    if len(parts) == 1 and parts[0].startswith("client-") and parts[0].endswith(".json"):
        return "client"

    # cities/<client-slug>/<city>.json = overlay
    if len(parts) == 3 and parts[0] == "cities" and parts[2].endswith(".json"):
        return "city-overlay"

    # cities/<city>.json = shared city
    if len(parts) == 2 and parts[0] == "cities" and parts[1].endswith(".json"):
        return "city"

    # services/<client-slug>/<service>.json = client-specific service (same schema)
    if len(parts) == 3 and parts[0] == "services" and parts[2].endswith(".json"):
        return "service"

    # services/<service>.json = shared service
    if len(parts) == 2 and parts[0] == "services" and parts[1].endswith(".json"):
        return "service"

    # cities-coordinates.json — no schema (lookup table)
    if len(parts) == 1 and parts[0] == "cities-coordinates.json":
        return None

    return None


def collect_data_files(data_dir: Path) -> list[tuple[Path, str]]:
    """Collect all JSON files with their relative paths."""
    results = []
    for root, _dirs, files in os.walk(data_dir):
        for f in sorted(files):
            if f.endswith(".json"):
                full_path = Path(root) / f
                rel_path = full_path.relative_to(data_dir)
                results.append((full_path, str(rel_path)))
    return results


def main():
    if not DATA_DIR.exists():
        print(f"ERROR: Data directory not found: {DATA_DIR}")
        sys.exit(1)

    # Load schemas
    validators = {}
    for name, schema_path in SCHEMAS.items():
        schema = load_schema(schema_path)
        validators[name] = Draft202012Validator(schema)

    # Collect and validate
    files = collect_data_files(DATA_DIR)
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    errors_detail = []

    print(f"Validating {len(files)} files in {DATA_DIR}\n")
    print(f"{'File':<60} {'Schema':<15} {'Result'}")
    print("-" * 90)

    for full_path, rel_path in files:
        # Skip test fixtures (*-test.json) — surface to operator for archival
        if full_path.stem.endswith("-test"):
            print(f"{rel_path:<60} {'(test fixture)':<15} SKIP")
            skipped += 1
            continue

        schema_type = classify_file(rel_path)

        if schema_type is None:
            print(f"{rel_path:<60} {'(no schema)':<15} SKIP")
            skipped += 1
            continue

        total += 1
        try:
            with open(full_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"{rel_path:<60} {schema_type:<15} FAIL (invalid JSON: {e})")
            failed += 1
            errors_detail.append((rel_path, schema_type, f"Invalid JSON: {e}"))
            continue

        validator = validators[schema_type]
        file_errors = list(validator.iter_errors(data))

        if not file_errors:
            print(f"{rel_path:<60} {schema_type:<15} PASS")
            passed += 1
        else:
            print(f"{rel_path:<60} {schema_type:<15} FAIL ({len(file_errors)} errors)")
            failed += 1
            for err in file_errors:
                path_str = " > ".join(str(p) for p in err.absolute_path) if err.absolute_path else "(root)"
                msg = err.message[:120]
                errors_detail.append((rel_path, schema_type, f"  [{path_str}] {msg}"))

    print("-" * 90)
    print(f"\nTotal: {total} validated, {passed} PASS, {failed} FAIL, {skipped} SKIP")

    if errors_detail:
        print(f"\n{'='*90}")
        print("DETAILED ERRORS:")
        print(f"{'='*90}")
        current_file = None
        for rel_path, schema_type, detail in errors_detail:
            if rel_path != current_file:
                current_file = rel_path
                print(f"\n{rel_path} (schema: {schema_type}):")
            print(f"  {detail}")

    # Migration notes for files that need changes
    print(f"\n{'='*90}")
    print("MIGRATION NOTES:")
    print(f"{'='*90}")
    print("""
Files requiring migration to match the scaled schema:

1. CITY FILES — add population + zip_codes:
   All city files currently lack 'population' and 'zip_codes' (needed for the
   Service Area Information card, blueprint §6b). These fields are currently
   OPTIONAL in city.schema.json to avoid breaking validation of existing files.
   They flip to REQUIRED at [WF-9] close, once all cities are populated.
   Migration: populate via census.gov / ACS data during [WF-9] research fan-out.
   (Bethesda + Rockville already have zip_codes.)

2. CITY FILES — split client-relative fields into overlays:
   Most shared city files still contain 'distance_from_hq_phrase',
   'dispatch_time_phrase', 'dispatch_time_short', 'no_trip_charge_cities',
   and 'other_areas_paragraph' (client-relative fields that belong in
   per-client overlay files). Only burke-va.json has been split so far.
   Migration: [DA5] implementation handles the backfill/split.

3. CITY FILES — name_with_state missing from DC:
   washington-dc.json should add name_with_state: "Washington, DC".

4. SERVICE FILES — pricing_closing_note may be missing:
   Some service files lack this field (schema marks it optional).

5. PRICE LAYER — new file:
   data/price_layer.json does not exist yet. Create it during the EV Phase 2
   build or [WF-9] research fan-out, extracting prices currently embedded
   in service file pricing_items arrays.

6. SERVICE CITY FACTS — new directory:
   data/service_city_facts/ does not exist yet. Populated during [WF-9].

7. LINK GRAPH — generated file:
   data/link_graph.json is derived at build time. The existing
   insert-internal-links.py generates equivalent data in a different format.
   Migration: adapt the script output to match link-graph.schema.json during
   [WF-8] or [WF-10].
""")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
