#!/usr/bin/env python3
"""
elementor_widget_patch — patch a single Elementor widget in a page snapshot.

Takes a wp_page_snapshot.py snapshot file, finds a widget by ID, replaces its
settings, writes a modified snapshot that can be restored with:

    python3 wp_page_snapshot.py restore <modified-snapshot> --confirm

Usage:
    python3 elementor_widget_patch.py <snapshot.json> <widget-id> <settings.json> [--out <path>]

    <settings.json> is a JSON file containing the new settings dict (or the
    subset of keys to overwrite). Use --replace-all to replace the entire
    settings dict instead of merging.

Example — replace a text-editor widget's content:

    echo '{"editor": "New body text here"}' > patch.json
    python3 elementor_widget_patch.py page-861-home.json 537e2e4c patch.json
    python3 wp_page_snapshot.py restore page-861-home-PATCHED.json --confirm

The original snapshot is never modified. A new file is written with "-PATCHED"
suffix (or the path given by --out).

Built 2026-08-28 from a one-off T-10 homepage fix script. Generalized so the
next Elementor widget edit on any client reuses this instead of writing a new
throwaway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def find_widget(elements: list, widget_id: str) -> dict | None:
    """Recursively find a widget by its Elementor ID."""
    for el in elements:
        if el.get("id") == widget_id:
            return el
        for child in el.get("elements", []):
            result = find_widget([child], widget_id)
            if result:
                return result
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Patch a single Elementor widget in a wp_page_snapshot snapshot."
    )
    parser.add_argument("snapshot", type=Path, help="Path to the snapshot JSON file")
    parser.add_argument("widget_id", help="Elementor widget ID to patch (e.g. 537e2e4c)")
    parser.add_argument("settings_file", type=Path, help="JSON file with new settings (merged by default)")
    parser.add_argument("--out", type=Path, default=None, help="Output path (default: <snapshot>-PATCHED.json)")
    parser.add_argument("--replace-all", action="store_true",
                        help="Replace the entire settings dict instead of merging keys")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"ERROR: snapshot not found: {args.snapshot}", file=sys.stderr)
        return 1
    if not args.settings_file.exists():
        print(f"ERROR: settings file not found: {args.settings_file}", file=sys.stderr)
        return 1

    snap = json.loads(args.snapshot.read_text())
    ed = json.loads(snap["meta"]["_elementor_data"])
    new_settings = json.loads(args.settings_file.read_text())

    widget = find_widget(ed, args.widget_id)
    if widget is None:
        print(f"ERROR: widget '{args.widget_id}' not found in Elementor data", file=sys.stderr)
        return 1

    # Show what's changing
    old_settings = widget.get("settings", {})
    print(f"Widget:  {args.widget_id}")
    print(f"Type:    {widget.get('widgetType', '?')}")

    if args.replace_all:
        print(f"Mode:    replace-all ({len(new_settings)} keys replacing {len(old_settings)} keys)")
        widget["settings"] = new_settings
    else:
        changed = []
        for k, v in new_settings.items():
            old_v = old_settings.get(k)
            if old_v != v:
                changed.append(k)
                if isinstance(old_v, str) and isinstance(v, str):
                    print(f"  {k}: {len(old_v)} → {len(v)} chars")
                else:
                    print(f"  {k}: changed")
            widget["settings"][k] = v
        if not changed:
            print("  (no changes detected)")
            return 0
        print(f"Mode:    merge ({len(changed)} key(s) changed)")

    if args.dry_run:
        print("\n--dry-run: no file written")
        return 0

    # Update _elementor_data in the snapshot
    new_ed_str = json.dumps(ed)
    snap["meta"]["_elementor_data"] = new_ed_str

    # Update checksums
    snap["checksums"]["_elementor_data"] = hashlib.md5(new_ed_str.encode()).hexdigest()
    if snap["post"].get("post_content") is not None:
        snap["checksums"]["post_content"] = hashlib.md5(
            snap["post"]["post_content"].encode()
        ).hexdigest()

    # Write
    out_path = args.out or args.snapshot.with_stem(args.snapshot.stem + "-PATCHED")
    out_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"\nWritten: {out_path}")
    print(f"  _elementor_data: {len(new_ed_str):,} bytes")
    print(f"\nRestore with:")
    print(f"  python3 wp_page_snapshot.py restore {out_path} --confirm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
