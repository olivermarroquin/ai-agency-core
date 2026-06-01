#!/usr/bin/env python3
"""
audit-published-links.py — corpus-wide dead-link auditor.

Scans every page's draft HTML for internal links (href starting with `/`),
compares each destination against the set of pages that actually exist in
the corpus, and flags any link whose destination doesn't exist. Read-only;
never modifies HTML. Pairs with insert-internal-links.py:

  - insert-internal-links.py PROPOSES new links and refuses to add ones
    pointing at non-existent destinations.
  - audit-published-links.py REPORTS the dead links already sitting in
    HTML from earlier hand-edits, scaffolder runs, or manual paste.

USAGE
-----
Single page:

    python audit-published-links.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va

Whole corpus:

    python audit-published-links.py \\
        --corpus-root /path/to/core-30

Include non-Core-30 destinations from a known-good allowlist (e.g. /contact/,
/about/, /reviews/ are valid even though they're not in the corpus):

    python audit-published-links.py \\
        --corpus-root /path/to/core-30 \\
        --allowlist /contact /about /reviews /services /service-areas

OUTPUTS
-------
- stdout: per-page summary + corpus-wide totals
- `<corpus-root>/_dead-link-audit-YYYY-MM-DD.md` (batch mode only) — full
  report grouped by source page AND by destination (the "which missing
  page would close the most dead links" view, which is build-next signal)
- `<page-folder>/_dead-link-audit-YYYY-MM-DD.md` (single-page mode) —
  per-page report

EDGE CASES HANDLED
------------------
- Anchor fragments (`/services/#power-panels`) — strips `#` and compares path
- Trailing slashes — normalizes
- Phone `tel:` and `mailto:` links — skipped
- Absolute URLs (`https://...`) — skipped (out of scope; this is internal audit only)
- Hash-only links (`#section`) — skipped
- Query strings (`?foo=bar`) — stripped before comparison
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


# ----------------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------------


def discover_corpus_destinations(corpus_root: Path) -> set[str]:
    """Return the set of page slugs that exist as `NN-<slug>/` folders."""
    if not corpus_root or not corpus_root.is_dir():
        return set()
    slugs: set[str] = set()
    for entry in corpus_root.iterdir():
        if not entry.is_dir():
            continue
        m = re.match(r"^\d{2}-(.+)$", entry.name)
        if m:
            slugs.add(m.group(1))
    return slugs


def find_latest_draft(folder: Path) -> Path | None:
    """Highest-numbered draft-vN-WP-WRAPPED.html, numeric sort."""
    def _ver(p: Path) -> int:
        m = re.match(r"draft-v(\d+)-WP-WRAPPED\.html$", p.name)
        return int(m.group(1)) if m else -1
    drafts = sorted(folder.glob("draft-v*-WP-WRAPPED.html"), key=_ver)
    return drafts[-1] if drafts else None


# ----------------------------------------------------------------------------
# Link extraction
# ----------------------------------------------------------------------------


HREF_PATTERN = re.compile(r'href\s*=\s*"([^"]+)"', re.IGNORECASE)


def normalize_href(href: str) -> str | None:
    """Reduce an href to a comparable internal path, or return None if it's
    not an internal-link candidate worth checking.
    """
    # Strip query string
    if "?" in href:
        href = href.split("?", 1)[0]
    # Strip fragment
    if "#" in href:
        href = href.split("#", 1)[0]
    href = href.strip()
    if not href:
        return None
    # Skip non-internal protocols
    lowered = href.lower()
    if lowered.startswith(("tel:", "mailto:", "javascript:", "data:")):
        return None
    if lowered.startswith(("http://", "https://", "//")):
        return None
    # Skip hash-only links
    if href.startswith("#"):
        return None
    # Skip relative links (we want absolute internal paths only)
    if not href.startswith("/"):
        return None
    # Normalize: ensure leading and trailing slash for path-style hrefs
    if not href.endswith("/"):
        href = href + "/"
    return href


def extract_internal_links(html: str) -> list[str]:
    """Return every internal href on the page, normalized. Duplicates preserved
    (occurrences matter for the per-page report)."""
    hrefs: list[str] = []
    for m in HREF_PATTERN.finditer(html):
        normalized = normalize_href(m.group(1))
        if normalized:
            hrefs.append(normalized)
    return hrefs


# ----------------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------------


@dataclass
class DeadLink:
    source_page_slug: str
    destination_href: str
    occurrences: int


def audit_page(
    folder: Path,
    corpus_destinations: set[str],
    allowlist: set[str],
) -> tuple[str | None, list[DeadLink], Path | None]:
    """Return (page_slug, list of dead links, html path) for one page folder."""
    m = re.match(r"^\d{2}-(.+)$", folder.name)
    if not m:
        return None, [], None
    page_slug = m.group(1)
    html_path = find_latest_draft(folder)
    if not html_path:
        return page_slug, [], None
    html = html_path.read_text(encoding="utf-8")
    hrefs = extract_internal_links(html)

    # Group by destination + count occurrences
    counts: dict[str, int] = {}
    for href in hrefs:
        counts[href] = counts.get(href, 0) + 1

    dead: list[DeadLink] = []
    for href, count in counts.items():
        # Allowlist check — known-good destinations outside the corpus
        if href in allowlist:
            continue
        # Self-link check — link to the page's own slug is fine
        if href == f"/{page_slug}/":
            continue
        # Strip leading/trailing slashes for slug comparison
        slug = href.strip("/")
        if slug in corpus_destinations:
            continue
        # Anything left is a dead link
        dead.append(
            DeadLink(
                source_page_slug=page_slug,
                destination_href=href,
                occurrences=count,
            )
        )
    return page_slug, dead, html_path


# ----------------------------------------------------------------------------
# Report writers
# ----------------------------------------------------------------------------


def write_per_page_report(folder: Path, page_slug: str, html_path: Path, dead: list[DeadLink]) -> Path:
    today = date.today().isoformat()
    path = folder / f"_dead-link-audit-{today}.md"
    lines = [
        f"# Dead-link audit — {page_slug}",
        "",
        f"**Generated:** {today}",
        f"**HTML source:** `{html_path.name}`",
        f"**Dead links found:** {len(dead)}",
        "",
    ]
    if not dead:
        lines.append("_No dead links found on this page._")
    else:
        lines.append("Internal `<a href>` links pointing to destinations that don't exist")
        lines.append("as a `NN-<slug>/` folder in the corpus. Could be: pre-built")
        lines.append("forward-references the operator added by hand, scaffolder leftovers,")
        lines.append("or copy-paste from another page.")
        lines.append("")
        lines.append("| Destination | Occurrences |")
        lines.append("|---|---|")
        for d in sorted(dead, key=lambda x: (-x.occurrences, x.destination_href)):
            lines.append(f"| `{d.destination_href}` | {d.occurrences} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_corpus_report(
    corpus_root: Path,
    pages_scanned: int,
    all_dead: list[DeadLink],
) -> Path:
    today = date.today().isoformat()
    path = corpus_root / f"_dead-link-audit-{today}.md"

    # Aggregate by destination (the "which missing page closes the most dead
    # links" view — build-next prioritization signal).
    by_destination: dict[str, list[DeadLink]] = {}
    for d in all_dead:
        by_destination.setdefault(d.destination_href, []).append(d)

    # Aggregate by source page
    by_source: dict[str, list[DeadLink]] = {}
    for d in all_dead:
        by_source.setdefault(d.source_page_slug, []).append(d)

    lines = [
        "# Dead-link audit — corpus-wide",
        "",
        f"**Generated:** {today}",
        f"**Pages scanned:** {pages_scanned}",
        f"**Total dead links:** {len(all_dead)}",
        f"**Distinct dead destinations:** {len(by_destination)}",
        f"**Pages with at least one dead link:** {len(by_source)}",
        "",
        "Internal `<a href>` links currently shipping in the corpus that point at",
        "destinations not represented by a `NN-<slug>/` folder. These are either",
        "(a) pre-built forward-references — the operator added the link assuming",
        "the destination page would be built soon, or (b) leftovers from scaffold",
        "templates, or (c) genuine mistakes. The auditor doesn't distinguish; you",
        "decide.",
        "",
        "---",
        "",
        "## By destination — build-next prioritization",
        "",
        "Destinations sorted by total dead-link refs across the corpus. If you",
        "build the page at the top of this list, you close the most dead links",
        "with the least work.",
        "",
        "| Destination | Total refs | Affecting pages |",
        "|---|---|---|",
    ]
    sorted_destinations = sorted(
        by_destination.items(),
        key=lambda kv: -sum(d.occurrences for d in kv[1]),
    )
    for dest, items in sorted_destinations:
        total_refs = sum(d.occurrences for d in items)
        affecting = ", ".join(sorted({d.source_page_slug for d in items}))
        lines.append(f"| `{dest}` | {total_refs} | {affecting} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## By source page — what each page needs fixed")
    lines.append("")
    for source in sorted(by_source):
        items = by_source[source]
        total = sum(d.occurrences for d in items)
        lines.append(f"### {source}  ({len(items)} distinct dead destinations, {total} occurrences)")
        lines.append("")
        for d in sorted(items, key=lambda x: (-x.occurrences, x.destination_href)):
            lines.append(f"- `{d.destination_href}` — {d.occurrences} occurrence(s)")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


# Sensible default allowlist for EV Electric — standard utility pages the
# Core 30 corpus links to that aren't themselves Core 30 pages. Operator can
# extend via --allowlist on the command line.
DEFAULT_ALLOWLIST = {
    "/",
    "/contact/",
    "/about/",
    "/reviews/",
    "/services/",
    "/service-areas/",
    "/blog/",
    "/faq/",
    "/privacy/",
    "/terms/",
    "/sitemap/",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a Core 30 corpus for internal dead links (read-only).",
    )
    parser.add_argument(
        "--page-folder",
        type=Path,
        help="A single Core 30 page folder.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        help="Batch mode — root containing many page folders.",
    )
    parser.add_argument(
        "--allowlist",
        nargs="*",
        default=[],
        help="Extra hrefs to treat as valid (e.g. /contact /about). Combined with the built-in defaults.",
    )
    args = parser.parse_args()

    if not args.page_folder and not args.corpus_root:
        parser.error("Provide --page-folder or --corpus-root.")

    # Build allowlist — defaults + user-supplied. Normalize each entry.
    allowlist: set[str] = set(DEFAULT_ALLOWLIST)
    for entry in args.allowlist:
        n = normalize_href(entry)
        if n:
            allowlist.add(n)

    corpus_root = args.corpus_root or (args.page_folder.parent if args.page_folder else None)
    corpus_destinations = discover_corpus_destinations(corpus_root) if corpus_root else set()

    targets: list[Path] = []
    if args.page_folder:
        targets = [args.page_folder]
    else:
        targets = sorted(
            p for p in args.corpus_root.iterdir()
            if p.is_dir() and re.match(r"^\d{2}-", p.name)
        )

    all_dead: list[DeadLink] = []
    pages_scanned = 0
    for folder in targets:
        page_slug, dead, html_path = audit_page(folder, corpus_destinations, allowlist)
        if not page_slug or not html_path:
            print(f"[skip] {folder.name}: could not find a draft HTML to scan")
            continue
        pages_scanned += 1
        all_dead.extend(dead)
        if args.page_folder:
            report_path = write_per_page_report(folder, page_slug, html_path, dead)
            print(
                f"[audit] {page_slug}: {len(dead)} dead destination(s) "
                f"({sum(d.occurrences for d in dead)} occurrences) "
                f"→ {report_path.name}"
            )
        else:
            print(
                f"[audit] {page_slug}: {len(dead)} dead destination(s) "
                f"({sum(d.occurrences for d in dead)} occurrences)"
            )

    if args.corpus_root and all_dead:
        report_path = write_corpus_report(args.corpus_root, pages_scanned, all_dead)
        print(f"\n[report] corpus-wide audit: {report_path.name}")

    print(
        f"\nDone. Pages scanned: {pages_scanned}. "
        f"Total dead links: {len(all_dead)} across "
        f"{len({d.destination_href for d in all_dead})} distinct destinations."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
