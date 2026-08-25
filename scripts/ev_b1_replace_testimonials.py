#!/usr/bin/env python3
"""
ev_b1_replace_testimonials — remove six fabricated testimonials from three EV-Electric
pages and replace them with real, attributable Google reviews.

Reads the backup snapshot (not the live site) to build the replacement, then writes via
the keelworks page-restore route so wp_slash() is applied by the plugin.

Usage:
    cd ~/workspace/repos/ai-agency-core/scripts

    # dry-run (default) — writes proposals to _scratch/b1-preview/
    python3 ev_b1_replace_testimonials.py

    # confirm one page at a time
    python3 ev_b1_replace_testimonials.py --confirm 95
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Reuse the transport + auth layer from the snapshot tool
from wp_page_snapshot import (
    RESTORE_ROUTE,
    SNAPSHOT_ROUTE,
    _auth,
    _client_config,
    _curl,
    _explain,
)

# ---------------------------------------------------------------- constants

CLIENT_SLUG = "ev-electric-services"

GOOGLE_REVIEWS_URL = "https://search.google.com/local/reviews?placeid=ChIJWzPhyC__Xg8Re2qdfAUtPCg"

SNAPSHOT_DIR = Path.home() / "workspace" / "backups" / CLIENT_SLUG / "page-snapshots-2026-08-24T134953Z"

CORPUS_PATH = (
    Path.home() / "workspace" / "second-brain" / "04_projects" / "clients" / "_active"
    / "ev-electric-services" / "admin-extracts" / "google-business-profile"
    / "corpus-gbp-reviews-verbatim-2026-05-19.json"
)

SCRATCH_DIR = Path.home() / "workspace" / "_scratch" / "b1-preview"

# page_id -> (snapshot filename, corpus selected_for key)
PAGES = {
    95: ("page-95-about.json", "about"),
    97: ("page-97-services.json", "services"),
    8:  ("page-8-home.json", "home"),
}

# The six fabricated names — order matters for the assertion
FAKE_NAMES = {"Sarah Johnson", "Jennifer Chen", "David Thompson",
              "Mike Rodriguez", "Lisa Parker", "Robert Kim"}

# The exact assignment order per page, from spec section 2.
# Widget A gets entries 0-2, Widget B gets entries 3-5.
PAGE_ORDER = {
    95: ["Darryl Marshall", "Patricia Maxwell", "Sonia Cool",
         "Jimmy Archange", "Steve Lovell", "Pat Costello"],
    97: ["Safiya Taoufik", "Jawad Siddiqi", "Walter Einhorn",
         "seung baek", "Lisa Mendis", "Christopher Silva"],
    8:  ["Meaghan Duffy", "Matthew Thomas- Wicher", "Jessie Ragsdale",
         "H Cotton", "Mohamedjad Magous", "Andrey Kosarev"],
}


# ---------------------------------------------------------------- corpus

def load_corpus() -> dict[str, list[dict]]:
    """Load corpus, return {selected_for_key: [reviews in corpus order]}."""
    data = json.loads(CORPUS_PATH.read_text())
    by_page: dict[str, list[dict]] = {}
    for r in data["reviews"]:
        sf = r.get("selected_for")
        if sf:
            by_page.setdefault(sf, []).append(r)
    return by_page


def corpus_by_name(reviews: list[dict]) -> dict[str, dict]:
    """Index a page's reviews by name for ordered lookup."""
    return {r["name"]: r for r in reviews}


# ---------------------------------------------------------------- elementor tree walk

def find_testimonial_widgets(elements: list[dict]) -> list[dict]:
    """Walk the Elementor element tree and return widgets with ekit_testimonial_data."""
    found = []
    for el in elements:
        if "settings" in el and "ekit_testimonial_data" in el.get("settings", {}):
            found.append(el)
        for child in el.get("elements", []):
            found.extend(find_testimonial_widgets([child]))
    return found


# ---------------------------------------------------------------- _elementor_data replacement

def replace_elementor_data(elementor_data: list[dict], page_id: int,
                           reviews_by_name: dict[str, dict]) -> list[dict]:
    """Replace testimonial entries in the parsed _elementor_data tree. Returns the modified tree."""
    tree = copy.deepcopy(elementor_data)
    widgets = find_testimonial_widgets(tree)

    # ASSERT exactly 2 widgets
    if len(widgets) != 2:
        raise SystemExit(f"ABORT: page {page_id} has {len(widgets)} testimonial widgets, expected 2")

    # Collect all entries across both widgets
    all_entries = []
    for w in widgets:
        all_entries.extend(w["settings"]["ekit_testimonial_data"])

    # ASSERT exactly 6 entries total
    if len(all_entries) != 6:
        raise SystemExit(f"ABORT: page {page_id} has {len(all_entries)} testimonial entries, expected 6")

    # ASSERT the 6 names are exactly the known fakes
    found_names = {e["client_name"] for e in all_entries}
    if found_names != FAKE_NAMES:
        raise SystemExit(
            f"ABORT: page {page_id} names mismatch.\n"
            f"  Expected: {sorted(FAKE_NAMES)}\n"
            f"  Found:    {sorted(found_names)}"
        )

    # Replace in spec order: widget A entries 0-2, widget B entries 3-5
    order = PAGE_ORDER[page_id]
    for idx, name in enumerate(order):
        review = reviews_by_name[name]
        widget_idx = 0 if idx < 3 else 1
        entry_idx = idx if idx < 3 else idx - 3
        entry = widgets[widget_idx]["settings"]["ekit_testimonial_data"][entry_idx]

        entry["client_name"] = review["name"]
        entry["designation"] = review["designation"]
        entry["review"] = review["display_text"]
        entry["client_photo"] = {"url": "", "id": "", "size": ""}
        entry["link"]["url"] = ""

    # Retarget the "View All" heading widget: hirenimbus -> Google reviews
    _retarget_view_all(tree)

    return tree


def _retarget_view_all(elements: list[dict]) -> None:
    """Walk the tree and retarget any heading widget whose link points to hirenimbus."""
    for el in elements:
        s = el.get("settings", {})
        if not isinstance(s, dict):
            continue
        if "ekit_testimonial_data" not in s:
            link = s.get("link")
            if isinstance(link, dict) and "hirenimbus" in link.get("url", ""):
                link["url"] = GOOGLE_REVIEWS_URL
        for child in el.get("elements", []):
            _retarget_view_all([child])


# ---------------------------------------------------------------- post_content replacement

def replace_post_content(post_content: str, page_id: int,
                         reviews_by_name: dict[str, dict],
                         old_elementor_data: list[dict]) -> str:
    """Replace the six fabricated testimonials in post_content HTML.

    Each fake appears as:
        <a href="...">
            <p>REVIEW TEXT</p>
            <strong>NAME</strong>
            DESIGNATION
        </a>

    We match by the <strong>NAME</strong> pattern, then replace the preceding <p>...</p>
    review text, the name, and the designation.
    """
    # Build a map of fake name -> (fake_review_text, fake_designation, new_name, new_designation, new_review)
    # We need the old review text and designation from _elementor_data to find them in post_content
    old_widgets = find_testimonial_widgets(old_elementor_data)
    old_entries = []
    for w in old_widgets:
        old_entries.extend(w["settings"]["ekit_testimonial_data"])
    old_by_name = {e["client_name"]: e for e in old_entries}

    order = PAGE_ORDER[page_id]
    result = post_content
    replaced_count = 0

    for idx, new_reviewer_name in enumerate(order):
        # Which fake name is at this position?
        # Widget A has fakes at indices 0-2, Widget B at 3-5
        # The fakes are always: Sarah Johnson, Jennifer Chen, David Thompson, Mike Rodriguez, Lisa Parker, Robert Kim
        # in the order they appear in the widgets
        fake_name_list = list(old_by_name.keys())  # preserves insertion order
        fake_name = fake_name_list[idx]
        old_entry = old_by_name[fake_name]
        new_review = reviews_by_name[new_reviewer_name]

        # Pattern: <p>OLD_REVIEW</p>\n\t...\t<strong>FAKE_NAME</strong>\n\t...\tOLD_DESIGNATION
        # Build a regex that matches the review paragraph + name + designation block
        old_review_escaped = re.escape(old_entry["review"])
        old_name_escaped = re.escape(fake_name)
        old_desg_escaped = re.escape(old_entry["designation"])

        pattern = (
            r"(<p>)" + old_review_escaped + r"(</p>\s+)"
            + r"<strong>" + old_name_escaped + r"</strong>"
            + r"(\s+)" + old_desg_escaped
        )

        replacement = (
            r"\g<1>" + new_review["display_text"].replace("\\", "\\\\") + r"\g<2>"
            + "<strong>" + new_review["name"] + "</strong>"
            + r"\g<3>" + new_review["designation"]
        )

        new_result, n = re.subn(pattern, replacement, result, count=1)
        if n != 1:
            raise SystemExit(
                f"ABORT: page {page_id} post_content — could not find testimonial block for "
                f"'{fake_name}' (designation: '{old_entry['designation']}')"
            )
        result = new_result
        replaced_count += 1

    if replaced_count != 6:
        raise SystemExit(
            f"ABORT: page {page_id} post_content — replaced {replaced_count}/6 testimonials"
        )

    # UNWRAP the <a href="hirenimbus"> anchors that wrap each testimonial card.
    # Each card is:   ...tabs...<a href="hirenimbus">\n...inner...\n...tabs...</a>
    # The anchors sit on their own lines (block-level), so a regex anchored to
    # line-start tabs + <a ...> won't hit the inline <a> inside the <h6>View All</h6>.
    unwrap_pattern = re.compile(
        r'(\t*)<a href="https://hirenimbus\.com/pro/ahmad-shaban">\n'
        r'([\s\S]*?)\n'
        r'\t*</a>',
    )
    unwrap_count = 0
    for _ in range(6):
        m = unwrap_pattern.search(result)
        if not m:
            break
        # Keep the inner content exactly as-is
        result = result[:m.start()] + m.group(2) + result[m.end():]
        unwrap_count += 1

    if unwrap_count != 6:
        raise SystemExit(
            f"ABORT: page {page_id} post_content — unwrapped {unwrap_count}/6 testimonial anchors"
        )

    # Retarget the "View All" link: hirenimbus -> Google reviews
    result = result.replace(
        'href="https://hirenimbus.com/pro/ahmad-shaban"',
        f'href="{GOOGLE_REVIEWS_URL}"',
    )

    return result


# ---------------------------------------------------------------- diff

def unified_diff(old: str, new: str, label: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{label}", tofile=f"b/{label}"))


# ---------------------------------------------------------------- dry-run

def dry_run_page(page_id: int, corpus_by_page: dict[str, list[dict]]) -> bool:
    snap_path = SNAPSHOT_DIR / PAGES[page_id][0]
    page_key = PAGES[page_id][1]

    print(f"\n{'='*60}")
    print(f"Page {page_id} ({page_key}) — dry run")
    print(f"{'='*60}")

    snap = json.loads(snap_path.read_text())
    old_ed = json.loads(snap["meta"]["_elementor_data"])
    old_pc = snap["post"]["post_content"]

    # Load reviews for this page
    page_reviews = corpus_by_page.get(page_key)
    if page_reviews is None:
        raise SystemExit(f"ABORT: no reviews in corpus with selected_for='{page_key}'")
    if len(page_reviews) != 6:
        raise SystemExit(f"ABORT: corpus has {len(page_reviews)} reviews for '{page_key}', expected 6")

    rbn = corpus_by_name(page_reviews)

    # Verify all expected names are in the corpus
    for name in PAGE_ORDER[page_id]:
        if name not in rbn:
            raise SystemExit(f"ABORT: reviewer '{name}' not found in corpus for page '{page_key}'")

    # Replace _elementor_data
    new_ed = replace_elementor_data(old_ed, page_id, rbn)

    # ASSERT round-trip
    new_ed_json = json.dumps(new_ed)
    try:
        json.loads(new_ed_json)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ABORT: page {page_id} rebuilt _elementor_data fails JSON parse: {e}")

    # Replace post_content
    new_pc = replace_post_content(old_pc, page_id, rbn, old_ed)

    # Diff
    ed_diff = unified_diff(
        json.dumps(old_ed, indent=2), json.dumps(new_ed, indent=2),
        f"page-{page_id}._elementor_data"
    )
    pc_diff = unified_diff(old_pc, new_pc, f"page-{page_id}.post_content")

    if ed_diff:
        print(f"\n_elementor_data diff:")
        print(ed_diff)
    if pc_diff:
        print(f"\npost_content diff:")
        print(pc_diff)

    # Write preview files
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    (SCRATCH_DIR / f"page-{page_id}.json").write_text(json.dumps(new_ed, indent=2))
    (SCRATCH_DIR / f"page-{page_id}.html").write_text(new_pc)
    print(f"\nPreview written to {SCRATCH_DIR}/page-{page_id}.{{json,html}}")

    # Post-replacement assertions
    new_widgets = find_testimonial_widgets(new_ed)
    assert len(new_widgets) == 2, f"Widget count changed: {len(new_widgets)}"
    new_entries = []
    for w in new_widgets:
        new_entries.extend(w["settings"]["ekit_testimonial_data"])
    assert len(new_entries) == 6, f"Entry count changed: {len(new_entries)}"

    # Assert zero fake names, all real names present
    new_ed_str = json.dumps(new_ed)
    for fake in FAKE_NAMES:
        if fake in new_ed_str:
            raise SystemExit(f"ABORT: fake name '{fake}' still in _elementor_data after replacement")
        if fake in new_pc:
            raise SystemExit(f"ABORT: fake name '{fake}' still in post_content after replacement")

    for real_name in PAGE_ORDER[page_id]:
        if real_name not in new_ed_str:
            raise SystemExit(f"ABORT: real name '{real_name}' missing from _elementor_data")
        if real_name not in new_pc:
            raise SystemExit(f"ABORT: real name '{real_name}' missing from post_content")

    # Assert zero dead anchors in post_content
    if '<a href="">' in new_pc:
        raise SystemExit(f"ABORT: page {page_id} post_content still contains <a href=\"\">")

    # Assert zero hirenimbus anywhere in either surface
    if "hirenimbus" in new_ed_str:
        raise SystemExit(f"ABORT: page {page_id} _elementor_data still contains 'hirenimbus'")
    if "hirenimbus" in new_pc:
        raise SystemExit(f"ABORT: page {page_id} post_content still contains 'hirenimbus'")

    print(f"\nAll assertions passed for page {page_id}.")
    return True


# ---------------------------------------------------------------- confirm (write one page)

def confirm_page(page_id: int, corpus_by_page: dict[str, list[dict]]) -> bool:
    page_key = PAGES[page_id][1]
    snap_path = SNAPSHOT_DIR / PAGES[page_id][0]

    print(f"\n{'='*60}")
    print(f"Page {page_id} ({page_key}) — CONFIRM (live write)")
    print(f"{'='*60}")

    cfg = _client_config(CLIENT_SLUG)
    base, auth = _auth(cfg)

    # --- Step 1: Build the replacement (same as dry run) ---
    snap = json.loads(snap_path.read_text())
    old_ed = json.loads(snap["meta"]["_elementor_data"])
    old_pc = snap["post"]["post_content"]

    page_reviews = corpus_by_page.get(page_key)
    if not page_reviews or len(page_reviews) != 6:
        raise SystemExit(f"ABORT: corpus review count wrong for '{page_key}'")
    rbn = corpus_by_name(page_reviews)

    new_ed = replace_elementor_data(old_ed, page_id, rbn)
    new_ed_json = json.dumps(new_ed)
    json.loads(new_ed_json)  # round-trip assert

    new_pc = replace_post_content(old_pc, page_id, rbn, old_ed)

    # --- Step 2: Pre-write snapshot (capture current live state for meta diff) ---
    print("\n1/5  Pre-write snapshot ...")
    pre = _curl(SNAPSHOT_ROUTE.format(base=base, id=page_id), auth)
    if pre.status_code != 200:
        raise SystemExit(f"ABORT: pre-write snapshot failed: {_explain(pre, 'snapshot')}")
    pre_snap = pre.json()
    pre_meta_keys = set(pre_snap.get("meta", {}).keys())

    # --- Step 3: Write via restore route ---
    # Build a synthetic snapshot body with the new data
    write_body = copy.deepcopy(snap)
    write_body["meta"]["_elementor_data"] = new_ed_json
    write_body["post"]["post_content"] = new_pc
    write_body["confirm"] = str(page_id)

    print("2/5  Writing via page-restore route ...")
    w = _curl(RESTORE_ROUTE.format(base=base, id=page_id), auth, "POST", json.dumps(write_body))
    if w.status_code != 200:
        raise SystemExit(f"ABORT: write failed: {_explain(w, 'restore')}")
    write_result = w.json()
    print(f"   Restore response: {json.dumps(write_result.get('verify', {}))}")

    # --- Step 4: Post-write snapshot and meta-key diff ---
    print("3/5  Post-write snapshot ...")
    post = _curl(SNAPSHOT_ROUTE.format(base=base, id=page_id), auth)
    if post.status_code != 200:
        raise SystemExit(f"ABORT: post-write snapshot failed: {_explain(post, 'snapshot')}")
    post_snap = post.json()
    post_meta_keys = set(post_snap.get("meta", {}).keys())

    # Diff ALL meta keys
    print("\n4/5  Meta-key diff (⚠️ known double-wrap defect check) ...")
    expected_changed = {"_elementor_data"}
    actually_changed = set()
    for key in pre_meta_keys | post_meta_keys:
        pre_val = pre_snap.get("meta", {}).get(key)
        post_val = post_snap.get("meta", {}).get(key)
        if pre_val != post_val:
            actually_changed.add(key)
            if key not in expected_changed:
                print(f"   ⚠️  UNEXPECTED meta key changed: {key}")
                print(f"       pre:  {repr(str(pre_val)[:200])}")
                print(f"       post: {repr(str(post_val)[:200])}")

    added_keys = post_meta_keys - pre_meta_keys
    removed_keys = pre_meta_keys - post_meta_keys
    if added_keys:
        print(f"   ⚠️  Meta keys ADDED: {added_keys}")
    if removed_keys:
        print(f"   ⚠️  Meta keys REMOVED: {removed_keys}")

    unexpected = actually_changed - expected_changed
    if unexpected:
        print(f"\n   ⚠️  UNEXPECTED CHANGED KEYS: {unexpected}")
        print("   The restore route may have double-wrapped serialized meta.")
        print("   Investigate before proceeding to the next page.")
    else:
        print(f"   Only expected keys changed: {actually_changed}")

    # --- Step 5: Post-write assertions ---
    print("\n5/5  Post-write assertions ...")
    post_ed_raw = post_snap["meta"]["_elementor_data"]
    post_pc = post_snap["post"]["post_content"]

    # Parse check
    try:
        post_ed = json.loads(post_ed_raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"FATAL: post-write _elementor_data does not parse as JSON: {e}")

    # Zero fake names
    for fake in FAKE_NAMES:
        if fake in post_ed_raw:
            raise SystemExit(f"FATAL: fake name '{fake}' still in live _elementor_data")
        if fake in post_pc:
            raise SystemExit(f"FATAL: fake name '{fake}' still in live post_content")

    # All real names present
    for real_name in PAGE_ORDER[page_id]:
        if real_name not in post_ed_raw:
            raise SystemExit(f"FATAL: real name '{real_name}' missing from live _elementor_data")
        if real_name not in post_pc:
            raise SystemExit(f"FATAL: real name '{real_name}' missing from live post_content")

    # Widget/entry counts
    post_widgets = find_testimonial_widgets(post_ed)
    if len(post_widgets) != 2:
        raise SystemExit(f"FATAL: widget count is {len(post_widgets)}, expected 2")
    post_entries = []
    for w in post_widgets:
        post_entries.extend(w["settings"]["ekit_testimonial_data"])
    if len(post_entries) != 6:
        raise SystemExit(f"FATAL: entry count is {len(post_entries)}, expected 6")

    # Zero dead anchors in post_content
    if '<a href="">' in post_pc:
        raise SystemExit(f"FATAL: page {page_id} live post_content contains <a href=\"\">")

    # Zero hirenimbus anywhere
    if "hirenimbus" in post_ed_raw:
        raise SystemExit(f"FATAL: page {page_id} live _elementor_data still contains 'hirenimbus'")
    if "hirenimbus" in post_pc:
        raise SystemExit(f"FATAL: page {page_id} live post_content still contains 'hirenimbus'")

    print(f"\n   All post-write assertions PASSED for page {page_id}.")

    # Save the post-write snapshot
    post_snap_path = SCRATCH_DIR / f"page-{page_id}-post-write-snapshot.json"
    post_snap_path.write_text(json.dumps(post_snap, indent=2))
    print(f"   Post-write snapshot saved: {post_snap_path}")

    return True


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="B1: replace fabricated testimonials with real Google reviews")
    parser.add_argument("--confirm", type=int, metavar="POST_ID",
                        help="Write ONE page (95, 97, or 8). Without this flag, dry-run only.")
    args = parser.parse_args()

    # Validate snapshot dir
    if not SNAPSHOT_DIR.exists():
        raise SystemExit(f"Snapshot dir not found: {SNAPSHOT_DIR}")
    for pid, (fname, _) in PAGES.items():
        if not (SNAPSHOT_DIR / fname).exists():
            raise SystemExit(f"Missing snapshot: {SNAPSHOT_DIR / fname}")

    # Load corpus
    corpus_by_page = load_corpus()

    if args.confirm:
        if args.confirm not in PAGES:
            raise SystemExit(f"Invalid page ID {args.confirm}. Must be one of: {list(PAGES.keys())}")
        confirm_page(args.confirm, corpus_by_page)
    else:
        # Dry run all three pages
        for pid in [95, 97, 8]:
            dry_run_page(pid, corpus_by_page)

        print(f"\n{'='*60}")
        print("DRY RUN COMPLETE")
        print(f"Preview files in {SCRATCH_DIR}/")
        print("To apply: python3 ev_b1_replace_testimonials.py --confirm 95")
        print("Order: 95 first, then 97, then 8. Purge cache between each.")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
