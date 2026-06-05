#!/usr/bin/env python3
"""
bulk-scaffold-pages.py — scaffold many Core 30 pages in one run.

Reads a client's `_build-order.md` and loops `scaffold-core-30-page.py`
for every row in the selected range. Skips publish, skips imagery — just
produces HTML + markdown drafts so the operator can do a batch authoring
session and then move on to per-page imagery + publish.

USAGE
-----
Scaffold everything from position 6 onward, skipping pages that already
have output folders:

    python3 bulk-scaffold-pages.py \\
        --client ev-electric-services \\
        --positions 6-30 \\
        --skip-existing

Dry run — see what would be scaffolded without writing anything:

    python3 bulk-scaffold-pages.py \\
        --client ev-electric-services \\
        --positions 6-15 \\
        --dry-run

Pick specific positions:

    python3 bulk-scaffold-pages.py \\
        --client ev-electric-services \\
        --positions 6,8,11

Use a build-order file outside the canonical location:

    python3 bulk-scaffold-pages.py \\
        --client ev-electric-services \\
        --build-order /path/to/_build-order.md \\
        --positions 6-15

WHAT THE SCRIPT DOES
--------------------
1. Locates the client's build-order file (default path, or `--build-order`).
2. Parses the markdown table — pulls (position, page-slug, service-label,
   city-label) for every row that has a real slug. Skips reserved rows.
3. Filters by `--positions` if given.
4. For each row, derives (service-slug, city-slug) from the page slug using
   data files already on disk:
   - Loads every `data/cities/*.json` to know valid city slugs.
   - Loads every `data/services/*.json` to know how page-slug templates
     map to service slugs.
   - Strips the longest matching city-slug suffix from the page slug;
     matches the remainder against `page_slug_template` prefixes.
5. Pre-flight check for existing output folders:
   - With `--skip-existing` — flags existing folders as "will skip".
   - Without it — aborts the whole batch and lists the conflicts so the
     operator can decide what to do.
6. Invokes `scaffold-core-30-page.py` for each remaining page as a
   subprocess. A failure on one page doesn't stop the batch — it gets
   recorded with its reason and the batch continues.
7. Prints a summary at the end: scaffolded successfully, skipped (already
   existed), failed (with reason per failure).

OUT OF SCOPE
------------
- Publishing — `publish-core-30-page.py` handles that per-page.
- Imagery — Phase 1 image pipeline runs per-page after scaffold.
- Internal linking — Phase 4b.
- Indexing — Phase 4a.

The bulk scaffolder only produces the paste-ready HTML + markdown drafts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
SCAFFOLD_SCRIPT = SCRIPTS_DIR / "scaffold-core-30-page.py"


# ----------------------------------------------------------------------------
# Workspace discovery (mirror scaffold-core-30-page.py)
# ----------------------------------------------------------------------------


def workspace_root() -> Path:
    here = SCRIPTS_DIR
    for parent in here.parents:
        if (parent / "second-brain").is_dir() and (parent / "repos").is_dir():
            return parent
    sys.stderr.write("ERROR: couldn't locate workspace root.\n")
    sys.exit(2)


def default_build_order(client: str) -> Path:
    return (
        workspace_root()
        / "second-brain"
        / "04_projects"
        / "clients"
        / "_active"
        / client
        / "website-archive"
        / "new"
        / "core-30"
        / "_build-order.md"
    )


def page_folder(client: str, position: int, page_slug: str) -> Path:
    return (
        workspace_root()
        / "second-brain"
        / "04_projects"
        / "clients"
        / "_active"
        / client
        / "website-archive"
        / "new"
        / "core-30"
        / f"{position:02d}-{page_slug}"
    )


# ----------------------------------------------------------------------------
# Build-order parsing
# ----------------------------------------------------------------------------


# Table row example (5-col EV):
# | 06 | light-fixture-installation-vienna-va | Light fixtures + chandeliers | Vienna | uncontested... |
# Table row example (6-col S&H):
# | 01 | panel-upgrade-woodbridge-va | Panel upgrade | Woodbridge | High | **Geographic exclusive** ... |
ROW_RE = re.compile(
    r"^\|\s*(?P<pos>\d+)\s*\|"             # position
    r"\s*(?P<slug>[A-Za-z0-9_\-]+)\s*\|"   # page slug (skips "(reserved)" rows)
    r"\s*(?P<service>[^|]+?)\s*\|"         # service label
    r"\s*(?P<city>[^|]+?)\s*\|"            # city label
    r"\s*(?P<status>[^|]*?)\s*\|"          # status / priority tier
    r"(?:\s*[^|]*\s*\|)*"                  # consume any extra columns (e.g. positioning notes)
    r"\s*$"
)


def parse_build_order(path: Path) -> list[dict]:
    """Return a list of rows: {position, page_slug, service_label, city_label, status, line}."""
    if not path.is_file():
        sys.stderr.write(f"ERROR: build-order file not found: {path}\n")
        sys.exit(2)

    rows: list[dict] = []
    seen_table = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        # Skip the header separator line (|---|---|...)
        if re.match(r"^\|\s*-+", line):
            seen_table = True
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        # Only count rows that look numeric in position AND have a real slug.
        slug = m.group("slug")
        if slug.lower() in {"reserved", "tbd"}:
            continue
        rows.append({
            "position": int(m.group("pos")),
            "page_slug": slug,
            "service_label": m.group("service").strip(),
            "city_label": m.group("city").strip(),
            "status": m.group("status").strip(),
            "line": line,
        })
    if not rows:
        sys.stderr.write(f"ERROR: couldn't find any parseable rows in {path}\n")
        sys.exit(2)
    return rows


# ----------------------------------------------------------------------------
# Positions filter
# ----------------------------------------------------------------------------


def parse_positions(spec: str | None) -> set[int] | None:
    """`6-15` → {6..15}; `6,8,11` → {6,8,11}; mixed `1,4-6,9` allowed.
    Returns None when spec is None (meaning "all positions in build-order").
    """
    if spec is None:
        return None
    out: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(chunk))
    if not out:
        sys.stderr.write(f"ERROR: --positions '{spec}' parsed to an empty set\n")
        sys.exit(2)
    return out


# ----------------------------------------------------------------------------
# Service / city slug derivation
# ----------------------------------------------------------------------------


def load_city_slugs() -> list[str]:
    """Return all city slugs known on disk (longest first for suffix matching)."""
    cities_dir = DATA_DIR / "cities"
    if not cities_dir.is_dir():
        return []
    slugs: list[str] = []
    for jf in sorted(cities_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            slug = data.get("slug") or jf.stem
        except Exception:
            slug = jf.stem
        slugs.append(slug)
    # Longest first so vienna-va-test never wins over vienna-va by accident.
    slugs.sort(key=len, reverse=True)
    return slugs


def load_service_prefix_map() -> dict[str, str]:
    """Return {page_slug_template_prefix: service_slug} for all on-disk service files.

    page_slug_template is something like 'electrical-troubleshooting-{city_slug}'
    or 'panel-upgrade-{city_slug}'. The prefix is the template with
    '-{city_slug}' stripped.
    """
    services_dir = DATA_DIR / "services"
    out: dict[str, str] = {}
    if not services_dir.is_dir():
        return out
    for jf in sorted(services_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = data.get("slug") or jf.stem
        tmpl = data.get("page_slug_template", "")
        if "{city_slug}" not in tmpl:
            continue
        # Common shape: "<prefix>-{city_slug}". Strip the placeholder + dash.
        prefix = tmpl.replace("-{city_slug}", "").replace("{city_slug}", "")
        prefix = prefix.rstrip("-")
        if prefix:
            out[prefix] = slug
    return out


def derive_service_and_city(
    page_slug: str,
    city_slugs: list[str],
    service_prefix_map: dict[str, str],
) -> tuple[str | None, str | None, str | None]:
    """Returns (service_slug, city_slug, error_reason). Two of the three are None at any time."""
    # Try city suffixes longest-first.
    matched_city: str | None = None
    for city in city_slugs:
        if page_slug == city:
            continue  # impossible for our slugs, but defensive
        if page_slug.endswith("-" + city):
            matched_city = city
            break
    if matched_city is None:
        return None, None, (
            f"no city data file matches the suffix of page slug '{page_slug}'. "
            f"Create data/cities/<slug>.json for the city this page targets."
        )

    service_prefix = page_slug[: -(len(matched_city) + 1)]  # strip "-<city>"
    service_slug = service_prefix_map.get(service_prefix)
    if service_slug is None:
        return None, matched_city, (
            f"no service data file has page_slug_template prefix '{service_prefix}'. "
            f"Create data/services/<slug>.json with "
            f"page_slug_template='{service_prefix}-{{city_slug}}'."
        )

    return service_slug, matched_city, None


# ----------------------------------------------------------------------------
# Subprocess invocation
# ----------------------------------------------------------------------------


def invoke_scaffold(
    client: str,
    service: str,
    city: str,
    position: int,
) -> tuple[bool, str]:
    """Returns (ok, stderr_tail). On failure, stderr_tail is the last ~15 lines for the summary."""
    if not SCAFFOLD_SCRIPT.is_file():
        return False, f"scaffold-core-30-page.py not found at {SCAFFOLD_SCRIPT}"

    cmd = [
        sys.executable,
        str(SCAFFOLD_SCRIPT),
        "--client", client,
        "--service", service,
        "--city", city,
        "--position", str(position),
    ]
    # Stream stderr to terminal AND capture for the summary
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # Replay stderr to the operator so they see per-page progress.
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.returncode == 0:
        return True, ""
    # Trim to the last few lines for the summary
    tail = "\n".join(proc.stderr.strip().splitlines()[-15:]) or f"exit code {proc.returncode}"
    return False, tail


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description="Scaffold many Core 30 pages in one run.")
    p.add_argument("--client", required=True, help="Client slug (e.g. ev-electric-services)")
    p.add_argument(
        "--build-order",
        type=Path,
        default=None,
        help="Path to the build-order markdown file (default: client's canonical location).",
    )
    p.add_argument(
        "--positions",
        default=None,
        help='Which positions to scaffold. Range "6-15", comma list "6,8,11", or both: "1,4-6,9".'
             " Omit to scaffold every parseable row.",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip pages whose output folder already exists. Without this flag, the batch aborts"
             " when any conflict is detected (refuse-to-overwrite default).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be scaffolded. No subprocess calls, no files written.",
    )

    args = p.parse_args()

    build_order_path = args.build_order or default_build_order(args.client)
    rows = parse_build_order(build_order_path)
    sys.stderr.write(f"→ Loaded {len(rows)} build-order rows from {build_order_path}\n")

    positions_filter = parse_positions(args.positions)
    if positions_filter is not None:
        rows = [r for r in rows if r["position"] in positions_filter]
        if not rows:
            sys.stderr.write(f"ERROR: no rows match --positions '{args.positions}'\n")
            return 2
        sys.stderr.write(f"→ Filtered to {len(rows)} rows by --positions\n")

    city_slugs = load_city_slugs()
    service_map = load_service_prefix_map()
    sys.stderr.write(
        f"→ Known city slugs: {len(city_slugs)} | "
        f"known service prefixes: {len(service_map)}\n"
    )

    # Resolve service/city for each row up-front so the pre-flight summary is complete.
    plan: list[dict] = []
    for row in rows:
        service, city, err = derive_service_and_city(row["page_slug"], city_slugs, service_map)
        target = page_folder(args.client, row["position"], row["page_slug"])
        plan.append({
            **row,
            "service_slug": service,
            "city_slug": city,
            "derive_error": err,
            "output_folder": target,
            "folder_exists": target.exists() and any(target.iterdir()) if target.exists() else False,
        })

    # Pre-flight: existing folders
    existing = [p for p in plan if p["folder_exists"]]
    if existing and not args.skip_existing:
        sys.stderr.write("\nABORTED: these output folders already exist and --skip-existing was not passed:\n")
        for p_ in existing:
            sys.stderr.write(f"  - {p_['output_folder']}\n")
        sys.stderr.write(
            "\nRe-run with --skip-existing to skip them, or delete the conflicts first.\n"
        )
        return 3

    # Pre-flight printout. Folder-exists wins over derive errors — if we're
    # going to skip the page anyway, the operator doesn't need to see a
    # derive complaint about it.
    sys.stderr.write("\nPre-flight plan:\n")
    for p_ in plan:
        if p_["folder_exists"]:
            tag = "SKIP (exists)"
        elif p_["derive_error"]:
            tag = "ERROR (derive)"
        else:
            tag = "WILL SCAFFOLD"
        sys.stderr.write(
            f"  [{p_['position']:02d}] {p_['page_slug']:<45s} "
            f"→ {tag}"
        )
        if p_["service_slug"] and p_["city_slug"]:
            sys.stderr.write(f"  (service={p_['service_slug']}, city={p_['city_slug']})")
        sys.stderr.write("\n")
        # Only surface derive errors for pages we'd actually try to scaffold.
        if p_["derive_error"] and not p_["folder_exists"]:
            sys.stderr.write(f"        reason: {p_['derive_error']}\n")

    if args.dry_run:
        sys.stderr.write("\nDRY RUN — no scaffolding performed.\n")
        sys.stderr.write(_summarize_dryrun(plan) + "\n")
        return 0

    # Execute
    scaffolded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for p_ in plan:
        header = f"\n=== [{p_['position']:02d}] {p_['page_slug']} ===\n"

        # Folder-exists wins over derive errors — we're skipping anyway.
        if p_["folder_exists"]:
            sys.stderr.write(header)
            sys.stderr.write(f"Skipping — output folder already exists: {p_['output_folder']}\n")
            skipped.append(p_)
            continue

        if p_["derive_error"]:
            sys.stderr.write(header)
            sys.stderr.write(f"Skipping — couldn't derive service/city: {p_['derive_error']}\n")
            failed.append({**p_, "reason": p_["derive_error"]})
            continue

        sys.stderr.write(header)
        ok, tail = invoke_scaffold(
            client=args.client,
            service=p_["service_slug"],
            city=p_["city_slug"],
            position=p_["position"],
        )
        if ok:
            scaffolded.append(p_)
        else:
            failed.append({**p_, "reason": tail})

    # Summary
    sys.stderr.write("\n" + "=" * 60 + "\n")
    sys.stderr.write("Summary\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(f"  Scaffolded:        {len(scaffolded)}\n")
    sys.stderr.write(f"  Skipped (exists):  {len(skipped)}\n")
    sys.stderr.write(f"  Failed:            {len(failed)}\n\n")

    if scaffolded:
        sys.stderr.write("Scaffolded pages:\n")
        for p_ in scaffolded:
            sys.stderr.write(f"  ✓ [{p_['position']:02d}] {p_['page_slug']}\n")
        sys.stderr.write("\n")

    if skipped:
        sys.stderr.write("Skipped (already had a folder):\n")
        for p_ in skipped:
            sys.stderr.write(f"  - [{p_['position']:02d}] {p_['page_slug']}\n")
        sys.stderr.write("\n")

    if failed:
        sys.stderr.write("Failed pages:\n")
        for p_ in failed:
            sys.stderr.write(f"  ✗ [{p_['position']:02d}] {p_['page_slug']}\n")
            reason_lines = (p_.get("reason") or "").splitlines() or ["(no reason captured)"]
            for line in reason_lines:
                sys.stderr.write(f"        {line}\n")
        sys.stderr.write("\n")

    # Consolidated output-quality-loop auto-invoke block for the whole batch.
    # Each scaffold subprocess already emits a per-page block via the replayed
    # stderr stream above (so the orchestrating chat can act on individual
    # pages mid-batch if it wants). The consolidated block below is the
    # cleaner handoff for chats that prefer one batch-level evaluation pass.
    if scaffolded:
        emit_quality_loop_invoke_block_batch(scaffolded, args.client)

    if failed:
        return 1
    return 0


# ----------------------------------------------------------------------------
# output-quality-loop auto-invoke recommendation (batch-level)
# ----------------------------------------------------------------------------


def emit_quality_loop_invoke_block_batch(scaffolded: list[dict], client: str) -> None:
    """Emit one consolidated auto-invoke block listing every successful page.

    The orchestrating Cowork chat reads this block from stderr and runs
    output-quality-loop in EVALUATE mode against each artifact triple, then
    feeds revision prompts back into the same chat (or a per-page subchat) as
    NEEDS REVISION / FAIL verdicts return.

    Per-page blocks also emit from the scaffold subprocesses (replayed via
    proc.stderr above); this batch block is the one the chat acts on for the
    whole run.

    Spec: ~/workspace/skills/output-quality-loop/references/auto-invoke-convention.md
    """
    sys.stderr.write("\n## Auto-invoke output-quality-loop (batch)\n\n")
    sys.stderr.write(
        f"The bulk scaffolder produced the following artifact(s) for client "
        f"`{client}`:\n\n"
    )
    for p_ in scaffolded:
        folder = p_["output_folder"]
        sys.stderr.write(f"### Page {p_['position']:02d} — `{p_['page_slug']}`\n")
        sys.stderr.write(f"- `{folder / 'draft-v1.md'}`\n")
        sys.stderr.write(f"- `{folder / 'draft-v1-WP-WRAPPED.html'}`\n")
        sys.stderr.write(f"- `{folder / '_VERSION-LOG.md'}`\n\n")
    sys.stderr.write(
        "[output-quality-loop:eval] — for each artifact above, run "
        "output-quality-loop in EVALUATE mode. If PASS, mark the page "
        "ready-to-publish. If NEEDS REVISION or FAIL, ingest the revision "
        "prompt as if it were operator input and regenerate the page, then "
        "re-invoke output-quality-loop. Cap at 3 iterations per page; on the "
        "3rd FAIL, escalate to the operator with the evaluation report. The "
        "publish-core-30-page.py quality-gate refuses to publish any page "
        "whose `last-verdict:` frontmatter is not PASS.\n"
    )


def _summarize_dryrun(plan: list[dict]) -> str:
    skip = sum(1 for p in plan if p["folder_exists"])
    err = sum(1 for p in plan if p["derive_error"] and not p["folder_exists"])
    will = sum(1 for p in plan if not p["folder_exists"] and not p["derive_error"])
    return (
        f"  Would scaffold:    {will}\n"
        f"  Would skip:        {skip}\n"
        f"  Would error early: {err}"
    )


if __name__ == "__main__":
    sys.exit(main())
