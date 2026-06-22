#!/usr/bin/env python3
"""
insert-internal-links.py — Phase 4b internal-linking inserter.

Reads a published Core 30 page corpus, reads a reference-architecture
recommendation (the link-map synthesis written by the competitor-deep-research
skill in link-map mode), and proposes or inserts cross-links into each page
along four axes:

  - Axis A: same service across nearby cities ("Panel upgrades in nearby cities" block)
  - Axis B: same city across other services (the existing `related_cards` block)
  - Axis C: category hub (Phase 2 hubs C1-C6 — skipped when the hub doesn't exist)
  - Axis D: sentence-embedded contextual links in body prose (semantic mode)

NON-DESTRUCTIVE DEFAULT. The script ALWAYS writes a diff first. HTML mutation only
happens when the operator runs the second pass with `--apply`. Even then, the
inserter writes alongside the existing draft (draft-vN+1-*.html) — never edits
the existing draft in place.

USAGE
-----
Propose links for one page (data-driven mode):

    python insert-internal-links.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --reference-architecture /path/to/_synthesis-ev-electric.md \\
        --mode data-driven

Add semantic-mode pass on top (AI-proposed contextual links):

    python insert-internal-links.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --reference-architecture /path/to/_synthesis-ev-electric.md \\
        --mode both

Batch — every page in a corpus root:

    python insert-internal-links.py \\
        --corpus-root /path/to/core-30 \\
        --reference-architecture /path/to/_synthesis-ev-electric.md \\
        --mode data-driven

Apply approved diffs (writes draft-vN+1-WP-WRAPPED.html alongside the source):

    python insert-internal-links.py \\
        --page-folder /path/to/02-panel-upgrade-vienna-va \\
        --reference-architecture /path/to/_synthesis-ev-electric.md \\
        --mode data-driven \\
        --apply \\
        --diff-file /path/to/diff-2026-05-31.json

OUTPUTS
-------
Default (no --apply):

  - <page-folder>/_internal-link-proposals-YYYY-MM-DD.json    machine-readable diff
  - <page-folder>/_internal-link-proposals-YYYY-MM-DD.md       operator-readable diff
  - stdout: per-page summary (proposals added per axis)

With --apply:

  - <page-folder>/draft-vN+1-WP-WRAPPED.html                   the new draft (alongside the old one)
  - <page-folder>/_internal-link-apply-YYYY-MM-DD.md           record of which proposals were applied

The script never modifies an existing draft-vN-WP-WRAPPED.html file. New drafts always
bump the version number.

DESIGN
------
- Pure stdlib. No third-party HTML parser. Regex + targeted string operations on
  the WP-WRAPPED HTML. Why: the existing scaffold-page.py is stdlib-only,
  and the Core 30 page structure is stable and class-named (`evp-related-grid`,
  `evp-section`, etc.) — surgical edits are safer than a full DOM rewrite.
- Modes:
  - data-driven: deterministic. Reads the page's slug + service + city, derives
    the expected Axis A and Axis B link sets from the reference architecture
    and the build-order file, compares to what's already in the HTML, proposes
    the diff.
  - semantic: heuristic. Scans body paragraphs for natural mentions of related
    services/cities, proposes sentence-embedded link wraps. Capped at 5 per
    page. Currently uses regex + keyword-matching (no LLM call) — the operator
    reviews every semantic proposal.
- Diff format: a list of "proposal" objects. Each carries axis, type
  (insert-block / wrap-anchor / replace-block), target location (CSS-selector-ish
  or line-anchor), before, after, rationale. Operator can accept-all or pick.

VOCAB
-----
- "Service slug" = the service identifier in data/services/<slug>.json
  (panel-upgrade, troubleshooting, ev-charger, etc.)
- "Page slug" = the URL slug of the published page
  (panel-upgrade-vienna-va, electrical-troubleshooting-fairfax-va, etc.)
  Note: services/troubleshooting.json renders to URL slug
  electrical-troubleshooting-{city_slug} via page_slug_template.
- "City slug" = the city identifier in data/cities/<slug>.json (vienna-va, fairfax-va, etc.)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
CITIES_COORDS_PATH = DATA_DIR / "cities-coordinates.json"


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------


@dataclass
class Proposal:
    """A single proposed change to a page."""

    axis: str  # "A" | "B" | "C" | "D"
    type: str  # "insert-block" | "replace-block" | "wrap-anchor"
    page_slug: str
    rationale: str
    anchor_text: str
    destination_href: str
    location_hint: str = ""  # "after evp-related-grid" / "para containing X"
    before: str = ""
    after: str = ""
    # "actionable" = destination page exists, safe to apply.
    # "future-page" = destination doesn't exist yet; surface as build-next signal,
    #                  do not apply (would ship a 404).
    status: str = "actionable"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageContext:
    """The parsed identity of a page, derived from its folder + draft files."""

    folder: Path
    page_slug: str
    service_slug: str
    city_slug: str
    html_path: Path
    html_content: str

    @property
    def page_url(self) -> str:
        return f"/{self.page_slug}/"


# ----------------------------------------------------------------------------
# Reference architecture parsing
# ----------------------------------------------------------------------------


# A minimal mapping of service slugs (as used in data/services/<slug>.json)
# to the URL-slug template they render to. Sourced from the troubleshooting
# JSON's `page_slug_template` field and the build-order file. If a new service
# is added, extend this map. The script falls back to "<service-slug>-{city}"
# when the template isn't here.
SERVICE_TO_URL_TEMPLATE = {
    "troubleshooting": "electrical-troubleshooting-{city_slug}",
    "panel-upgrade": "panel-upgrade-{city_slug}",
    "ev-charger": "ev-charger-{city_slug}",
    "light-fixture-installation": "light-fixture-installation-{city_slug}",
    "smoke-alarm": "smoke-alarm-{city_slug}",
    "outlet-installation": "outlet-installation-{city_slug}",
    "generator-installation": "generator-installation-{city_slug}",
    "whole-house-rewire": "whole-house-rewire-{city_slug}",
    "smart-home-installation": "smart-home-installation-{city_slug}",
}

SERVICE_LABEL = {
    "troubleshooting": "Electrical Troubleshooting",
    "panel-upgrade": "Electrical Panel Upgrades",
    "ev-charger": "EV Charger Installation",
    "light-fixture-installation": "Light Fixtures & Chandeliers",
    "smoke-alarm": "Smoke Alarm Installation",
    "outlet-installation": "Outlet & Switch Installation",
    "generator-installation": "Generator Installation",
    "whole-house-rewire": "Whole-House Rewiring",
    "smart-home-installation": "Smart Home Installation",
}

# Anchor-text variants for rotation across the corpus. When proposing an Axis A
# (same-service-different-city) or Axis B (same-city-different-service) link,
# the inserter picks the variant that's been used the LEAST times pointing at
# that destination across the whole corpus. Goal: avoid having 30 pages all
# link to /panel-upgrade-vienna-va/ with the exact same 2-word anchor — that
# reads templated to Google and triggers spam heuristics. Synthesis §3 rule 5.
#
# Format for AXIS_A: {service_slug: [pattern1, pattern2, pattern3]} where each
# pattern can use {city_label} placeholder. Caller substitutes.
# Format for AXIS_B: {service_slug: [label1, label2, label3]} — flat list of
# label variants for the related-cards grid.
ANCHOR_VARIANTS_AXIS_A = {
    "troubleshooting":            ["{city_label}", "{city_label} troubleshooting", "electrical troubleshooting in {city_label}"],
    "panel-upgrade":              ["{city_label}", "{city_label} panel upgrades", "panel upgrade in {city_label}"],
    "ev-charger":                 ["{city_label}", "EV charger installation in {city_label}", "{city_label} EV charging"],
    "light-fixture-installation": ["{city_label}", "{city_label} light fixtures", "lighting work in {city_label}"],
    "smoke-alarm":                ["{city_label}", "{city_label} smoke alarms", "smoke alarm work in {city_label}"],
    "outlet-installation":        ["{city_label}", "{city_label} outlets", "outlet work in {city_label}"],
    "generator-installation":     ["{city_label}", "{city_label} generators", "generator installation in {city_label}"],
    "whole-house-rewire":         ["{city_label}", "{city_label} rewiring", "whole-house rewire in {city_label}"],
    "smart-home-installation":    ["{city_label}", "{city_label} smart home work", "smart home installation in {city_label}"],
}

ANCHOR_VARIANTS_AXIS_B = {
    "troubleshooting":            ["Electrical Troubleshooting", "Diagnostic & Troubleshooting", "Electrical Diagnostics"],
    "panel-upgrade":              ["Electrical Panel Upgrades", "Panel Replacement", "Service Panel Upgrades"],
    "ev-charger":                 ["EV Charger Installation", "EV Charging Setup", "Level 2 Charger Install"],
    "light-fixture-installation": ["Light Fixtures & Chandeliers", "Lighting Installation", "Fixture & Chandelier Install"],
    "smoke-alarm":                ["Smoke Alarm Installation", "Smoke & CO Alarms", "Smoke Detector Install"],
    "outlet-installation":        ["Outlet & Switch Installation", "Outlet, Switch & GFCI Work", "Receptacles & Switches"],
    "generator-installation":     ["Standby Generator Installation", "Whole-Home Generators", "Generator Setup"],
    "whole-house-rewire":         ["Whole-House Rewiring", "Complete Home Rewire", "Full-Home Rewire Service"],
    "smart-home-installation":    ["Smart Home Installation", "Smart Home Wiring", "Smart Devices & Wiring"],
}


def load_city_coordinates() -> dict[str, tuple[float, float]]:
    """Load the city -> (lat, lon) map. Returns empty dict if file missing."""
    if not CITIES_COORDS_PATH.is_file():
        return {}
    try:
        raw = json.loads(CITIES_COORDS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, tuple[float, float]] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and len(v) == 2:
            out[k] = (float(v[0]), float(v[1]))
    return out


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1 = a
    lat2, lon2 = b
    r_miles = 3958.756  # earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r_miles * math.asin(math.sqrt(h))


HREF_PATTERN = re.compile(r'<a\s+[^>]*href\s*=\s*"([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def scan_corpus_anchor_usage(corpus_root: Path) -> dict[tuple[str, str], int]:
    """Walk every page's latest draft, count (anchor_text, destination_href) pairs.

    Used by check_axis_a + check_axis_b to pick the LEAST-used variant when
    proposing a new link, so anchor text varies across the corpus.
    """
    counter: dict[tuple[str, str], int] = {}
    if not corpus_root or not corpus_root.is_dir():
        return counter
    for entry in corpus_root.iterdir():
        if not entry.is_dir() or not re.match(r"^\d{2}-", entry.name):
            continue
        def _ver(p: Path) -> int:
            m = re.match(r"draft-v(\d+)-WP-WRAPPED\.html$", p.name)
            return int(m.group(1)) if m else -1
        drafts = sorted(entry.glob("draft-v*-WP-WRAPPED.html"), key=_ver)
        if not drafts:
            continue
        html = drafts[-1].read_text(encoding="utf-8")
        for m in HREF_PATTERN.finditer(html):
            href = m.group(1).split("#")[0].split("?")[0].strip()
            if not href.startswith("/") or not href.endswith("/"):
                continue
            # Strip HTML tags from anchor text to get plain text
            anchor = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not anchor:
                continue
            counter[(anchor, href)] = counter.get((anchor, href), 0) + 1
    return counter


def pick_least_used_variant(
    variants: list[str],
    destination_href: str,
    anchor_usage: dict[tuple[str, str], int],
    substitutions: dict[str, str] | None = None,
    source_page_slug: str = "",
) -> str:
    """Return the variant pointing at this destination that's been used the
    fewest times across the corpus. Ties broken by a stable hash of
    (source_page_slug, destination_href) — guarantees variance even on a
    cold start (when no anchors have been used yet) so the first batch run
    doesn't propose the same variant on every page."""
    if not variants:
        return ""
    subs = substitutions or {}
    expanded = [v.format(**subs) for v in variants]
    # Per-page deterministic offset so cold-start ties spread across variants
    offset = hash((source_page_slug, destination_href)) % len(expanded) if expanded else 0
    counted = [
        (anchor_usage.get((v, destination_href), 0), (i - offset) % len(expanded), v)
        for i, v in enumerate(expanded)
    ]
    counted.sort()  # least-used first, then by (offset-adjusted) list-order
    return counted[0][2]


def render_page_slug(service_slug: str, city_slug: str) -> str:
    """Apply the URL-slug template for a (service, city) pair."""
    tmpl = SERVICE_TO_URL_TEMPLATE.get(service_slug, f"{service_slug}-{{city_slug}}")
    return tmpl.format(city_slug=city_slug)


def parse_build_order(build_order_path: Path) -> list[tuple[str, str]]:
    """Extract (service-slug, city-slug) pairs from the build-order markdown table.

    The table has a row per page with the page slug in the second column. We
    map each page slug back to (service-slug, city-slug) by inverse-matching
    against SERVICE_TO_URL_TEMPLATE.
    """
    if not build_order_path.is_file():
        return []
    text = build_order_path.read_text(encoding="utf-8")
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"\|\s*\d+\s*\|\s*([a-z0-9\-]+)\s*\|", line)
        if not m:
            continue
        page_slug = m.group(1)
        # Inverse-lookup: which service template would produce this page slug?
        for service_slug, tmpl in SERVICE_TO_URL_TEMPLATE.items():
            prefix = tmpl.replace("{city_slug}", "")
            if page_slug.startswith(prefix.rstrip("-")):
                city_slug = page_slug[len(prefix.rstrip("-")) + 1 :]
                # Skip if the suffix doesn't look like a city slug.
                if city_slug and re.match(r"^[a-z]+(-[a-z]+)*$", city_slug):
                    pairs.append((service_slug, city_slug))
                    break
    return pairs


def load_related_cards(service_slug: str) -> list[dict]:
    """Read the related_cards array from the service JSON file."""
    path = DATA_DIR / "services" / f"{service_slug}.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("related_cards", [])
    except json.JSONDecodeError:
        return []


def discover_corpus_destinations(corpus_root: Path) -> set[str]:
    """Return the set of page slugs that exist as folders in the corpus root.

    A page is "live" enough to link to if it has a `NN-<page-slug>` folder.
    Build-order presence isn't required (a page can be in build-order but
    not yet scaffolded — those return 404 from the live site).
    """
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


# ----------------------------------------------------------------------------
# Page identity parsing
# ----------------------------------------------------------------------------


def detect_page_context(folder: Path) -> Optional[PageContext]:
    """Derive (service_slug, city_slug) from the folder name + draft HTML.

    Folder name convention: `<NN>-<page-slug>` where `<page-slug>` matches
    a SERVICE_TO_URL_TEMPLATE expansion. The draft HTML is whichever
    draft-vN-WP-WRAPPED.html has the highest N.
    """
    folder_name = folder.name
    m = re.match(r"^\d{2}-(.+)$", folder_name)
    if not m:
        return None
    page_slug = m.group(1)

    # Inverse-lookup service + city
    service_slug: Optional[str] = None
    city_slug: Optional[str] = None
    for s_slug, tmpl in SERVICE_TO_URL_TEMPLATE.items():
        prefix = tmpl.replace("{city_slug}", "")
        if page_slug.startswith(prefix.rstrip("-") + "-"):
            service_slug = s_slug
            city_slug = page_slug[len(prefix.rstrip("-")) + 1 :]
            break
    if not service_slug or not city_slug:
        return None

    # Find highest-numbered WP-WRAPPED draft. Sort NUMERICALLY by version,
    # not lexicographically — otherwise draft-v9 sorts after draft-v14 because
    # '9' > '1' as a character.
    def _draft_version(p: Path) -> int:
        m = re.match(r"draft-v(\d+)-WP-WRAPPED\.html$", p.name)
        return int(m.group(1)) if m else -1

    drafts = sorted(folder.glob("draft-v*-WP-WRAPPED.html"), key=_draft_version)
    if not drafts:
        return None
    html_path = drafts[-1]
    html = html_path.read_text(encoding="utf-8")

    return PageContext(
        folder=folder,
        page_slug=page_slug,
        service_slug=service_slug,
        city_slug=city_slug,
        html_path=html_path,
        html_content=html,
    )


# ----------------------------------------------------------------------------
# Axis B — data-driven: related_cards in same city, other services
# ----------------------------------------------------------------------------


def check_axis_b(
    ctx: PageContext,
    corpus_destinations: set[str],
    anchor_usage: dict[tuple[str, str], int] | None = None,
    rotate_anchors: bool = True,
) -> list[Proposal]:
    """Verify the page's HTML contains the Axis-B related_cards block per the
    service JSON. Propose insertions for any missing cards.

    Destinations that don't exist in the corpus are still surfaced — they
    become "future-page" proposals (build-next signal) instead of being
    silently dropped, so the synthesis's recommended related_cards stay
    visible even when the destination page hasn't been built yet.

    Anchor rotation: when rotate_anchors=True and the destination's service
    has variants defined in ANCHOR_VARIANTS_AXIS_B, picks the variant least-
    used across the corpus. Goal: 30 pages each linking to a destination
    don't all use the exact same 2-word anchor.
    """

    related = load_related_cards(ctx.service_slug)
    if not related:
        return []

    anchor_usage = anchor_usage or {}
    proposals: list[Proposal] = []
    for card in related:
        slug = card["href_slug"].replace("{city_slug}", ctx.city_slug)
        href = f"/{slug}/"
        default_label = card.get("label", slug)
        # Skip self-link
        if slug == ctx.page_slug:
            continue
        # Check if already present
        if href in ctx.html_content:
            continue

        # Pick anchor text — rotation aware.
        # Look up which service the related-card destination is; derive from
        # the href_slug template (everything before -{city_slug}).
        target_service_slug = card["href_slug"].replace("-{city_slug}", "")
        label = default_label
        if rotate_anchors and target_service_slug in ANCHOR_VARIANTS_AXIS_B:
            label = pick_least_used_variant(
                ANCHOR_VARIANTS_AXIS_B[target_service_slug],
                href,
                anchor_usage,
                source_page_slug=ctx.page_slug,
            )

        # Destination exists in corpus? If not, mark as future-page candidate
        status = "actionable" if slug in corpus_destinations else "future-page"
        rationale = (
            f"Axis B (same city, other service): {default_label} not yet linked from {ctx.page_slug}"
            if status == "actionable"
            else f"Axis B FUTURE-PAGE: {default_label} in {ctx.city_slug} referenced by "
                 f"data/services/{ctx.service_slug}.json related_cards, but page not built yet "
                 f"— signals build-next candidate"
        )
        proposals.append(
            Proposal(
                axis="B",
                type="insert-block",
                page_slug=ctx.page_slug,
                rationale=rationale,
                anchor_text=label,
                destination_href=href,
                location_hint="inside evp-related-grid (related-services block)",
                after=f'<div class="evp-related-card"><a href="{href}">{label}</a></div>',
                status=status,
            )
        )
    return proposals


# ----------------------------------------------------------------------------
# Axis A — data-driven: same service, nearby cities
# ----------------------------------------------------------------------------


def check_axis_a(
    ctx: PageContext,
    all_pairs: list[tuple[str, str]],
    corpus_destinations: set[str],
    anchor_usage: dict[tuple[str, str], int] | None = None,
    rotate_anchors: bool = True,
    city_coords: dict[str, tuple[float, float]] | None = None,
) -> list[Proposal]:
    """For each other city that has this service in the build-order, propose
    a link if it's not already present.

    Caps at 5 nearby cities (the synthesis's Axis-A target).

    Geographic adjacency: if both the source city and a sibling city have
    coordinates in data/cities-coordinates.json, sibling_pairs is sorted by
    real great-circle distance from the source city. Falls back to
    build-order ordering for cities missing coordinates. This makes "nearby
    cities" actually mean nearby, not just "earlier in the build-order."

    Anchor rotation: when rotate_anchors=True and the service has variants in
    ANCHOR_VARIANTS_AXIS_A, picks the variant pointing at this destination
    least-used across the corpus.

    Destinations missing from the corpus are routed to "future-page" status
    so they don't ship a 404 if --apply is run blindly. They still surface
    in the diff as build-next signal.
    """
    anchor_usage = anchor_usage or {}
    city_coords = city_coords or {}

    sibling_pairs = [
        (s, c) for (s, c) in all_pairs
        if s == ctx.service_slug and c != ctx.city_slug
    ]

    # Geographic-distance sort if source city has coordinates. Siblings without
    # coordinates fall back to a large sentinel distance, preserving build-order
    # tail behind the geographically-ordered head.
    source_coord = city_coords.get(ctx.city_slug)
    if source_coord:
        def _dist(pair: tuple[str, str]) -> float:
            sibling_city = pair[1]
            target = city_coords.get(sibling_city)
            return haversine_miles(source_coord, target) if target else 1e9
        sibling_pairs = sorted(sibling_pairs, key=_dist)

    service_label = SERVICE_LABEL.get(ctx.service_slug, ctx.service_slug.replace("-", " ").title())
    proposals: list[Proposal] = []
    count = 0
    for sibling_service, sibling_city in sibling_pairs:
        if count >= 5:
            break
        sibling_page_slug = render_page_slug(sibling_service, sibling_city)
        href = f"/{sibling_page_slug}/"
        # If already linked anywhere in the page, skip
        if href in ctx.html_content:
            count += 1
            continue
        # Friendly city label (replace -va/-md/-dc suffix)
        city_label = sibling_city.replace("-va", "").replace("-md", "").replace("-", " ").title()

        # Pick anchor text — rotation aware. Default is bare city label.
        anchor_text = city_label
        if rotate_anchors and sibling_service in ANCHOR_VARIANTS_AXIS_A:
            anchor_text = pick_least_used_variant(
                ANCHOR_VARIANTS_AXIS_A[sibling_service],
                href,
                anchor_usage,
                substitutions={"city_label": city_label},
                source_page_slug=ctx.page_slug,
            )

        # Destination exists in corpus? If not, mark as future-page candidate
        status = "actionable" if sibling_page_slug in corpus_destinations else "future-page"

        # Distance note for the rationale (helps the operator sanity-check geography)
        target_coord = city_coords.get(sibling_city)
        distance_note = ""
        if source_coord and target_coord:
            miles = haversine_miles(source_coord, target_coord)
            distance_note = f" (~{miles:.1f} mi from {ctx.city_slug})"

        rationale = (
            f"Axis A (same service, nearby city): {SERVICE_LABEL.get(sibling_service, sibling_service)} in {city_label}{distance_note} not linked from {ctx.page_slug}"
            if status == "actionable"
            else f"Axis A FUTURE-PAGE: {SERVICE_LABEL.get(sibling_service, sibling_service)} in {city_label}{distance_note} "
                 f"referenced by build-order but page not built yet — signals build-next candidate"
        )
        proposals.append(
            Proposal(
                axis="A",
                type="insert-block",
                page_slug=ctx.page_slug,
                rationale=rationale,
                anchor_text=anchor_text,
                destination_href=href,
                location_hint=f"proposed new block: '{service_label} in nearby cities'",
                after=f'<a href="{href}">{anchor_text}</a>',
                status=status,
            )
        )
        count += 1
    return proposals


# ----------------------------------------------------------------------------
# Axis C — category hub (skipped if not yet built)
# ----------------------------------------------------------------------------


def check_axis_c(ctx: PageContext, category_hubs_live: set[str]) -> list[Proposal]:
    """Propose a category-hub breadcrumb/intro link if the hub for this
    service is live. Default: hubs not live, so this is a no-op."""
    if ctx.service_slug not in category_hubs_live:
        return []
    hub_slug = {
        "troubleshooting": "electrical-troubleshooting",
        "panel-upgrade": "electrical-panel-upgrade",
        "ev-charger": "ev-charger-installation",
        "light-fixture-installation": "light-fixtures-chandeliers",
        "smoke-alarm": "smoke-alarm-installation",
        "outlet-installation": "outlet-switch-installation",
    }.get(ctx.service_slug)
    if not hub_slug:
        return []
    href = f"/{hub_slug}/"
    if href in ctx.html_content:
        return []
    return [
        Proposal(
            axis="C",
            type="insert-block",
            page_slug=ctx.page_slug,
            rationale=f"Axis C (category hub): link from page to its category hub /{hub_slug}/",
            anchor_text=SERVICE_LABEL.get(ctx.service_slug, ctx.service_slug),
            destination_href=href,
            location_hint="breadcrumb + intro paragraph",
            after=f'<a href="{href}">{SERVICE_LABEL.get(ctx.service_slug, ctx.service_slug)}</a>',
        )
    ]


# ----------------------------------------------------------------------------
# Axis D — semantic: sentence-embedded contextual links
# ----------------------------------------------------------------------------


# Keywords that, when matched inside a body paragraph, suggest a sentence-embedded
# link to the corresponding service-city page. Kept narrow on purpose — false
# positives waste operator review time. Each entry: (regex pattern, target service slug).
SEMANTIC_KEYWORDS = [
    (r"\bEV\s+charger(s)?\b", "ev-charger"),
    (r"\bpanel\s+upgrade(s)?\b", "panel-upgrade"),
    (r"\btroubleshoot(ing)?\b", "troubleshooting"),
    (r"\blight\s+fixture(s)?\b", "light-fixture-installation"),
    (r"\bchandelier(s)?\b", "light-fixture-installation"),
    (r"\bsmoke\s+alarm(s)?\b", "smoke-alarm"),
    (r"\boutlet\s+installation\b", "outlet-installation"),
    (r"\bgenerator(s)?\b", "generator-installation"),
    (r"\bwhole[\-\s]house\s+rewir(e|ing)\b", "whole-house-rewire"),
]


def check_axis_d(
    ctx: PageContext,
    corpus_destinations: set[str],
    max_proposals: int = 5,
    max_per_destination: int = 2,
) -> list[Proposal]:
    """Scan body paragraphs for keyword mentions of related services. For each
    match outside an existing anchor, propose a wrap-anchor link to the
    corresponding service-in-current-city page.

    Caps:
      - max_proposals per page (default 5) — total Axis D budget
      - max_per_destination per page (default 2) — prevents 5 identical-anchor
        links to the same URL. Per the synthesis §3 rule 5: multiple
        same-anchor links to one destination on a single page read templated
        to Google (only the first link's anchor counts under "first link
        priority" anyway) and dilute editorial quality. Two is the natural
        max before the page reads spammy.

    Skips matches inside an existing <a> (no nested links).
    Skips matches that would point to the current page.
    Skips destinations missing from the corpus (Axis D wraps only fire on
    pages we know exist — unlike Axis A/B, an Axis D 404 link is
    embarrassing because it's embedded in body prose).
    """
    proposals: list[Proposal] = []
    per_destination_count: dict[str, int] = {}
    # Find <p>...</p> blocks
    para_pattern = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
    paragraphs = list(para_pattern.finditer(ctx.html_content))

    for para_match in paragraphs:
        if len(proposals) >= max_proposals:
            break
        para_text = para_match.group(1)
        # Skip paragraphs that already contain an anchor — don't nest links
        if "<a " in para_text:
            continue
        for pattern, target_service in SEMANTIC_KEYWORDS:
            if target_service == ctx.service_slug:
                continue  # don't link to self-service
            m = re.search(pattern, para_text, flags=re.IGNORECASE)
            if not m:
                continue
            target_page_slug = render_page_slug(target_service, ctx.city_slug)
            if target_page_slug == ctx.page_slug:
                continue
            # Skip if destination page doesn't exist in the corpus
            if target_page_slug not in corpus_destinations:
                continue
            href = f"/{target_page_slug}/"
            # Per-destination cap — no more than max_per_destination wraps
            # to the same URL on one page.
            if per_destination_count.get(href, 0) >= max_per_destination:
                continue
            matched_phrase = m.group(0)
            wrapped = f'<a href="{href}">{matched_phrase}</a>'
            # Build a short snippet for the operator's diff
            snippet_start = max(0, m.start() - 30)
            snippet_end = min(len(para_text), m.end() + 30)
            snippet = para_text[snippet_start:snippet_end]
            proposals.append(
                Proposal(
                    axis="D",
                    type="wrap-anchor",
                    page_slug=ctx.page_slug,
                    rationale=f"Axis D (sentence-embedded): wrap '{matched_phrase}' to link to {target_page_slug}",
                    anchor_text=matched_phrase,
                    destination_href=href,
                    location_hint=f"paragraph snippet: …{snippet}…",
                    before=matched_phrase,
                    after=wrapped,
                )
            )
            per_destination_count[href] = per_destination_count.get(href, 0) + 1
            if len(proposals) >= max_proposals:
                break
    return proposals


# ----------------------------------------------------------------------------
# Diff writing
# ----------------------------------------------------------------------------


def write_diff(ctx: PageContext, proposals: list[Proposal]) -> tuple[Path, Path]:
    """Write the machine-readable JSON diff and the operator-readable markdown diff."""
    today = date.today().isoformat()
    json_path = ctx.folder / f"_internal-link-proposals-{today}.json"
    md_path = ctx.folder / f"_internal-link-proposals-{today}.md"

    actionable = [p for p in proposals if p.status == "actionable"]
    future_page = [p for p in proposals if p.status == "future-page"]

    json_data = {
        "page_slug": ctx.page_slug,
        "service_slug": ctx.service_slug,
        "city_slug": ctx.city_slug,
        "html_source": ctx.html_path.name,
        "generated": today,
        "proposal_count": len(proposals),
        "actionable_count": len(actionable),
        "future_page_count": len(future_page),
        "proposals": [p.to_dict() for p in proposals],
    }
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

    # Markdown — group by axis for operator review
    md = [
        f"# Internal-link proposals — {ctx.page_slug}",
        "",
        f"**Generated:** {today}",
        f"**HTML source:** `{ctx.html_path.name}`",
        f"**Service:** {ctx.service_slug} | **City:** {ctx.city_slug}",
        f"**Actionable proposals:** {len(actionable)}",
        f"**Future-page candidates:** {len(future_page)}",
        "",
        "Operator review: accept or reject each actionable proposal. Future-page",
        "candidates are surfaced as build-next signal — they're skipped on",
        "`--apply` to prevent shipping 404 links. To apply approved",
        "proposals, edit this file (delete rejected entries) and re-run with",
        f"`--apply --diff-file {json_path.name}`.",
        "",
        "---",
        "",
    ]
    by_axis: dict[str, list[Proposal]] = {"A": [], "B": [], "C": [], "D": []}
    for p in actionable:
        by_axis[p.axis].append(p)
    axis_titles = {
        "A": "Axis A — same service, nearby cities",
        "B": "Axis B — same city, other services (`related_cards`)",
        "C": "Axis C — category hub link",
        "D": "Axis D — sentence-embedded contextual links",
    }
    for axis in ["A", "B", "C", "D"]:
        items = by_axis[axis]
        md.append(f"## {axis_titles[axis]}  ({len(items)} proposed)")
        md.append("")
        if not items:
            md.append("_None proposed for this page._")
            md.append("")
            continue
        for i, p in enumerate(items, 1):
            md.append(f"### {axis}.{i}  →  `{p.destination_href}`")
            md.append("")
            md.append(f"**Rationale:** {p.rationale}")
            md.append(f"**Anchor text:** `{p.anchor_text}`")
            md.append(f"**Location:** {p.location_hint}")
            if p.before:
                md.append(f"**Before:** `{p.before}`")
            md.append(f"**After:** `{p.after}`")
            md.append("")

    # Future-page candidates — separate section, not applied even with --apply.
    if future_page:
        md.append("---")
        md.append("")
        md.append("## 🟡 Future-page candidates (destinations referenced but not yet built)")
        md.append("")
        md.append("These came up as Axis A or Axis B proposals but the destination page doesn't")
        md.append("exist in the corpus. Each row is also a signal to build the page — if many")
        md.append("Core 30 pages reference the same destination, prioritize it in the build order.")
        md.append("")
        md.append("| Axis | Destination | Anchor | Source |")
        md.append("|---|---|---|---|")
        for p in future_page:
            source = "service JSON related_cards" if p.axis == "B" else "build-order"
            md.append(f"| {p.axis} | `{p.destination_href}` | {p.anchor_text} | {source} |")
        md.append("")

    md_path.write_text("\n".join(md), encoding="utf-8")

    return json_path, md_path


# ----------------------------------------------------------------------------
# Apply mode
# ----------------------------------------------------------------------------


def apply_proposals(ctx: PageContext, proposals: list[Proposal]) -> Path:
    """Apply the proposals to a fresh draft (bumps version).

    Future-page proposals are silently skipped — applying them would ship
    404 links. They live in the diff as build-next signal only.
    """
    # Filter out future-page candidates — never apply destinations that don't exist
    proposals = [p for p in proposals if p.status == "actionable"]
    html = ctx.html_content

    # Axis B — append related_cards into the existing evp-related-grid container.
    # The container is roughly:  <div class="evp-related-grid">…cards…</div>
    grid_pattern = re.compile(r'(<div class="evp-related-grid">)(.*?)(</div>)', re.DOTALL)
    for p in proposals:
        if p.axis == "B" and p.type == "insert-block":
            m = grid_pattern.search(html)
            if not m:
                continue
            new_inner = m.group(2).rstrip() + "\n        " + p.after + "\n        "
            html = html[: m.start()] + m.group(1) + new_inner + m.group(3) + html[m.end():]

    # Axis A — append a new "nearby cities" block before the related-services
    # section. Implementation note: we accumulate the Axis-A anchor list and
    # emit one block. If the block already exists in the page, we'd be appending
    # duplicates — guard against that with a sentinel comment.
    axis_a_anchors = [p for p in proposals if p.axis == "A" and p.type == "insert-block"]
    if axis_a_anchors and "<!-- evp-nearby-cities-block -->" not in html:
        service_label = SERVICE_LABEL.get(ctx.service_slug, ctx.service_slug.replace("-", " ").title())
        city_label = ctx.city_slug.replace("-va", "").replace("-md", "").replace("-", " ").title()
        anchors_html = " · ".join(p.after for p in axis_a_anchors)
        nearby_block = (
            "\n    <!-- evp-nearby-cities-block -->\n"
            '    <div class="evp-section evp-nearby-cities">\n'
            '      <div class="evp-section-inner">\n'
            f'        <h2>{service_label} in nearby cities</h2>\n'
            f'        <p>If you\'re outside {city_label}, we also serve:</p>\n'
            f'        <p class="evp-nearby-cities-list">{anchors_html}</p>\n'
            "      </div>\n"
            "    </div>\n"
        )
        # Insert before the related-services section if present, else before the final CTA
        anchor_point = '<div class="evp-section evp-related"'
        idx = html.find(anchor_point)
        if idx > -1:
            html = html[:idx] + nearby_block + html[idx:]

    # Axis D — wrap matched phrases in <a> tags. Only the first occurrence per
    # proposal, to avoid stomping on later text.
    for p in proposals:
        if p.axis == "D" and p.type == "wrap-anchor":
            if p.after in html:
                continue  # already applied
            # Find first <p>…before…</p> match without an enclosing <a> and replace.
            para_pattern = re.compile(
                r"(<p[^>]*>)([^<]*?)(" + re.escape(p.before) + r")([^<]*?)(</p>)",
                re.DOTALL,
            )
            html_new, n = para_pattern.subn(
                lambda m: m.group(1) + m.group(2) + p.after + m.group(4) + m.group(5),
                html,
                count=1,
            )
            if n > 0:
                html = html_new

    # Bump version
    current = ctx.html_path.name  # draft-vN-WP-WRAPPED.html
    m_ver = re.match(r"draft-v(\d+)-WP-WRAPPED\.html", current)
    next_ver = (int(m_ver.group(1)) + 1) if m_ver else 2
    new_path = ctx.folder / f"draft-v{next_ver}-WP-WRAPPED.html"
    new_path.write_text(html, encoding="utf-8")
    return new_path


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 4b internal-linking inserter — non-destructive by default."
    )
    parser.add_argument(
        "--page-folder",
        type=Path,
        help="A single Core 30 page folder (e.g. 02-panel-upgrade-vienna-va).",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        help="Batch mode — root containing many page folders.",
    )
    parser.add_argument(
        "--reference-architecture",
        type=Path,
        required=True,
        help="Path to the link-map synthesis (_synthesis-<client>.md).",
    )
    parser.add_argument(
        "--build-order",
        type=Path,
        default=None,
        help="Path to the Core 30 build-order markdown (drives Axis A).",
    )
    parser.add_argument(
        "--mode",
        choices=["data-driven", "semantic", "both"],
        default="data-driven",
        help="Which axes to run. data-driven = A+B+C. semantic = D. both = all four.",
    )
    parser.add_argument(
        "--category-hubs-live",
        nargs="*",
        default=[],
        help="Service slugs whose category hub is live (enables Axis C).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the diff (writes a new draft-vN+1 file). Default: propose only.",
    )
    parser.add_argument(
        "--diff-file",
        type=Path,
        help="In --apply mode, path to a curated JSON diff (operator-reviewed).",
    )
    parser.add_argument(
        "--no-anchor-rotation",
        action="store_true",
        help="Disable anchor-text rotation (default: rotation ON). Use when you "
             "want UX-consistent labels across the corpus and don't care about "
             "the templated-link SEO signal.",
    )
    parser.add_argument(
        "--no-geo-adjacency",
        action="store_true",
        help="Disable geographic-adjacency sorting on Axis A (default: ON when "
             "data/cities-coordinates.json exists). Falls back to build-order order.",
    )
    args = parser.parse_args()

    if not args.page_folder and not args.corpus_root:
        parser.error("Provide --page-folder or --corpus-root.")
    if args.apply and not args.page_folder:
        parser.error("--apply requires --page-folder (operator confirms per page).")

    if not args.reference_architecture.is_file():
        sys.stderr.write(f"ERROR: reference architecture not found: {args.reference_architecture}\n")
        return 2

    # Build-order drives Axis A
    build_order_pairs: list[tuple[str, str]] = []
    if args.build_order and args.build_order.is_file():
        build_order_pairs = parse_build_order(args.build_order)

    category_hubs_live = set(args.category_hubs_live)

    targets: list[Path] = []
    if args.page_folder:
        targets = [args.page_folder]
    else:
        targets = sorted(
            p for p in args.corpus_root.iterdir()
            if p.is_dir() and re.match(r"^\d{2}-", p.name)
        )

    # Discover corpus destinations once — used by all axes to suppress
    # links to pages that don't exist (would-be 404s) and to route them to
    # the future-page-candidates section instead.
    corpus_root = args.corpus_root or (args.page_folder.parent if args.page_folder else None)
    corpus_destinations = discover_corpus_destinations(corpus_root) if corpus_root else set()

    # Anchor-usage pre-scan — drives anchor-text rotation across the corpus.
    rotate_anchors = not args.no_anchor_rotation
    anchor_usage: dict[tuple[str, str], int] = {}
    if rotate_anchors and corpus_root:
        anchor_usage = scan_corpus_anchor_usage(corpus_root)

    # Geographic-adjacency map for Axis A.
    city_coords: dict[str, tuple[float, float]] = {}
    if not args.no_geo_adjacency:
        city_coords = load_city_coordinates()

    overall = {"pages": 0, "actionable_total": 0, "future_page_total": 0}
    # Aggregated future-page references across all pages — surfaces "build-next"
    # signal in batch mode. Key is destination href; value is list of source page slugs.
    future_page_refs: dict[str, list[str]] = {}

    for folder in targets:
        ctx = detect_page_context(folder)
        if not ctx:
            print(f"[skip] {folder.name}: could not detect (service, city) from folder + drafts")
            continue
        overall["pages"] += 1

        proposals: list[Proposal] = []
        if args.mode in ("data-driven", "both"):
            proposals.extend(check_axis_b(
                ctx, corpus_destinations,
                anchor_usage=anchor_usage, rotate_anchors=rotate_anchors,
            ))
            if build_order_pairs:
                proposals.extend(check_axis_a(
                    ctx, build_order_pairs, corpus_destinations,
                    anchor_usage=anchor_usage, rotate_anchors=rotate_anchors,
                    city_coords=city_coords,
                ))
            proposals.extend(check_axis_c(ctx, category_hubs_live))
        if args.mode in ("semantic", "both"):
            proposals.extend(check_axis_d(ctx, corpus_destinations))

        actionable = [p for p in proposals if p.status == "actionable"]
        future_page = [p for p in proposals if p.status == "future-page"]
        overall["actionable_total"] += len(actionable)
        overall["future_page_total"] += len(future_page)

        # Aggregate future-page refs for batch report
        for p in future_page:
            future_page_refs.setdefault(p.destination_href, []).append(ctx.page_slug)

        if args.apply:
            # Re-load from diff-file if provided (operator-curated subset)
            if args.diff_file and args.diff_file.is_file():
                curated = json.loads(args.diff_file.read_text(encoding="utf-8"))
                proposals = [Proposal(**p) for p in curated.get("proposals", [])]
            new_draft = apply_proposals(ctx, proposals)
            applied_count = sum(1 for p in proposals if p.status == "actionable")
            print(f"[apply] {ctx.page_slug}: wrote {new_draft.name} with {applied_count} applied")
        else:
            json_path, md_path = write_diff(ctx, proposals)
            future_note = f" + {len(future_page)} future-page" if future_page else ""
            print(
                f"[propose] {ctx.page_slug}: {len(actionable)} actionable "
                f"(A={sum(1 for p in actionable if p.axis=='A')}, "
                f"B={sum(1 for p in actionable if p.axis=='B')}, "
                f"C={sum(1 for p in actionable if p.axis=='C')}, "
                f"D={sum(1 for p in actionable if p.axis=='D')})"
                f"{future_note}  → {md_path.name}"
            )

    # Batch-mode future-page candidates report
    if args.corpus_root and future_page_refs:
        today = date.today().isoformat()
        report_path = args.corpus_root / f"_future-page-candidates-{today}.md"
        lines = [
            "# Future-page candidates — corpus-wide aggregation",
            "",
            f"**Generated:** {today}",
            f"**Pages scanned:** {overall['pages']}",
            f"**Distinct future-page destinations referenced:** {len(future_page_refs)}",
            "",
            "These destinations are referenced by Axis A (build-order siblings) or",
            "Axis B (`related_cards` in service JSONs) but don't have a page folder",
            "yet. The reference-count column is a build-next prioritization signal —",
            "destinations referenced by more pages should be built sooner.",
            "",
            "| Destination | Reference count | Referenced by |",
            "|---|---|---|",
        ]
        # Sort by reference count descending — most-wanted first
        for href in sorted(future_page_refs, key=lambda h: -len(future_page_refs[h])):
            refs = future_page_refs[href]
            lines.append(f"| `{href}` | {len(refs)} | {', '.join(refs)} |")
        lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n[future-pages] corpus-wide report: {report_path.name}")

    print(
        f"\nDone. Pages processed: {overall['pages']}. "
        f"Actionable proposals: {overall['actionable_total']}. "
        f"Future-page candidates: {overall['future_page_total']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
