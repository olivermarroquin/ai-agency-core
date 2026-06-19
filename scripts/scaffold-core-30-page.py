#!/usr/bin/env python3
"""
scaffold-core-30-page.py — generate a finished Core 30 page draft from data.

Replaces the chat-side authoring of `draft-vN-WP-WRAPPED.html` for new pages.
Takes a (service, city, client) tuple, loads three data files, calls
generate-maps-iframe.py for the Section 8 map, and writes:

  - draft-v1.md             (frontmatter + AIOSEO metadata + checklist)
  - draft-v1-WP-WRAPPED.html (the publish-ready HTML)
  - _VERSION-LOG.md          (audit trail for this folder)

into a freshly-created page folder under the client's `website-archive/new/core-30/`
directory.

USAGE
-----
Basic:

    export GOOGLE_MAPS_EMBED_API_KEY="AIzaSyD…"
    python scaffold-core-30-page.py \\
        --service troubleshooting \\
        --city    vienna-va \\
        --position 6

Override the client (default: ev-electric-services):

    python scaffold-core-30-page.py \\
        --service troubleshooting \\
        --city    vienna-va \\
        --client  ev-electric-services \\
        --position 6

Verify-only (don't write any files):

    python scaffold-core-30-page.py \\
        --service troubleshooting \\
        --city    vienna-va \\
        --position 1 \\
        --dry-run

DATA MODEL
----------
Three JSON files combine into one rendered page:

  - data/client-<slug>.json — brand facts, address, phone, hours, founder, review count
  - data/services/<slug>.json — pricing, problem cards, FAQs, process, "what this means"
  - data/cities/<slug>.json — neighborhoods, housing-stock patterns, geographic anchors

Templates live in templates/:

  - core-30-page.html.tmpl — the v17 HTML structure with {placeholders}
  - draft-v1.md.tmpl       — the markdown source with frontmatter + AIOSEO

The script substitutes data into the templates and writes the result. No
third-party deps — pure stdlib + str.format_map.

ADDING NEW DATA
---------------
- New city: copy data/cities/vienna-va.json → data/cities/<slug>.json, fill in
  neighborhoods, housing patterns, geographic anchor, distance phrase.
- New service: copy data/services/troubleshooting.json → data/services/<slug>.json,
  fill in pricing, problems, FAQs, process. Service files share many
  city-substituted templates so the same file works for every city.
- New client: copy data/client-ev-electric.json → data/client-<slug>.json,
  fill in brand/address/founder. Future Keelworks clients each get their own.

RELATED SCRIPTS
---------------
- generate-maps-iframe.py — called for Section 8 map. Must have run for the
  requested city at least once (or `GOOGLE_MAPS_EMBED_API_KEY` must be set
  so the scaffolder can generate on-the-fly).
- publish-core-30-page.py — runs downstream. Takes the draft this script
  produces and publishes to WordPress.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
TEMPLATES_DIR = SCRIPTS_DIR / "templates"


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    if not path.is_file():
        sys.stderr.write(f"ERROR: data file not found: {path}\n")
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def load_client(slug: str) -> dict:
    return load_json(DATA_DIR / f"client-{slug}.json")


def load_service(slug: str, client_slug: str | None = None) -> dict:
    # Check for a client-specific override first (e.g. data/services/s-and-h-contracting/panel-upgrade.json).
    # Falls back to the shared file (e.g. data/services/panel-upgrade.json).
    if client_slug:
        client_path = DATA_DIR / "services" / client_slug / f"{slug}.json"
        if client_path.is_file():
            return load_json(client_path)
    return load_json(DATA_DIR / "services" / f"{slug}.json")


def load_city(slug: str, client_slug: str | None = None) -> dict:
    # Load the shared city file, then overlay client-specific HQ-relative fields
    # if a client override exists (e.g. data/cities/ev-electric-services/burke-va.json).
    # Unlike load_service (full replacement), city overrides MERGE — shared facts
    # (county, neighborhoods, housing_patterns) stay in the base file; only
    # client-relative fields (dispatch, distance_from_hq, route) get overridden.
    base = load_json(DATA_DIR / "cities" / f"{slug}.json")
    if client_slug:
        override_path = DATA_DIR / "cities" / client_slug / f"{slug}.json"
        if override_path.is_file():
            override = load_json(override_path)
            base.update(override)
    return base


def load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if not path.is_file():
        sys.stderr.write(f"ERROR: template not found: {path}\n")
        sys.exit(2)
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
# Substitution context (one big dict the template format_map's against)
# ----------------------------------------------------------------------------


def require_variant_field(city: dict, field_name: str, service_slug: str, city_slug: str) -> Any:
    """Get a per-service variant field from a city JSON dict, failing loud if missing.

    DA2: every output variant must get equal source depth. A missing variant
    must error, never silent-degrade to empty string or <!-- MISSING -->.
    """
    container = city.get(field_name, {})
    if not isinstance(container, dict):
        sys.stderr.write(
            f"ERROR: city data '{city_slug}' field '{field_name}' is not a dict "
            f"(got {type(container).__name__}). Cannot look up service key.\n"
        )
        sys.exit(3)
    if service_slug not in container:
        sys.stderr.write(
            f"ERROR: city data '{city_slug}' is missing "
            f"'{field_name}[\"{service_slug}\"]'.\n"
            f"  → This is a DA2 depth-parity failure: every service in the build "
            f"set must have localized depth in the city JSON.\n"
            f"  → Fix: run the intersection-research skill for "
            f"{service_slug}--{city_slug}, then scaffold-city-data.py to "
            f"populate the field.\n"
            f"  → Or: run facts-completeness-gate.py first (SC-2) to catch all "
            f"missing fields before scaffolding.\n"
        )
        sys.exit(3)
    value = container[service_slug]
    if value is None or value == "" or value == []:
        sys.stderr.write(
            f"ERROR: city data '{city_slug}' has "
            f"'{field_name}[\"{service_slug}\"]' but it is empty.\n"
        )
        sys.exit(3)
    return value


def build_context(client: dict, service: dict, city: dict, position: int) -> dict:
    """Combine the three data sources into a single substitution dict.
    The .format() / format_map() ready-to-render context.
    """
    city_slug = city["slug"]                  # e.g. "vienna-va"
    city_name = city["name"]                  # e.g. "Vienna"
    city_state = city["state"]                # e.g. "VA"
    city_full = city["name_with_state"]       # e.g. "Vienna, VA"
    page_slug = service["page_slug_template"].format(city_slug=city_slug)
    page_url = f"{client['website_url']}{page_slug}/"

    sub = {
        # Client-derived
        "client_slug": client["client_slug"],
        "client_name": client["name"],
        "client_alt_name": client["alternate_name"],
        "owner_name": client["owner_name"],
        "owner_first_name": client["owner_first_name"],
        "owner_title": client["owner_title"],
        "phone_display": client["phone_display"],
        "phone_tel": client["phone_tel"],
        "phone_e164": client["phone_e164"],
        "email": client["email"],
        "website_url": client["website_url"],
        "website_url_no_slash": client["website_url_no_slash"],
        "contact_page_path": client["contact_page_path"],
        "secondary_cta_label": client["secondary_cta_label"],
        "response_promise": client["final_cta_response_promise"],
        "review_count": client["review_count"],
        "review_count_phrase": client["review_count_phrase"],
        "review_pitch": client["review_pitch"],
        "review_rating": client["review_rating"],
        "license_state": client["license"]["state"],
        "license_state_full": client["license"]["state_full"],
        "brand_image_url": client["brand_image_url"],
        "brand_logo_url": client["brand_logo_url"],
        # Brand colors (CSS substitutions)
        "primary_color": client["primary_color"],
        "navy": client["navy"],
        "accent_yellow": client["accent_yellow"],
        "heading_color": client["heading_color"],
        "hero_gradient_dark": client["hero_gradient_dark"],
        "hero_gradient_mid": client["hero_gradient_mid"],
        "hero_gradient_bright": client["hero_gradient_bright"],

        # Service-derived (slug + name only — the templated strings get rendered below)
        "service_slug": service["slug"],
        "service_name": service["name"],
        "service_tag": service["tag_short"],
        "service_lowercase": service["lowercase_phrase"],

        # City-derived
        "city_slug": city_slug,
        "city_name": city_name,
        "city_state": city_state,
        "city_name_with_state": city_full,
        "city_county": city["county"],
        "city_county_full": city["county_full"],
        "city_tag": city_name.lower().replace(" ", "-"),
        "county_short": city["county"].split(",")[0],
        "city_distance_from_hq_phrase": city["distance_from_hq_phrase"],
        "geographic_anchor_paragraph": city["geographic_anchor_paragraph"],
        "audience_descriptor": city["audience_descriptor"],
        "other_areas_paragraph": city["other_areas_paragraph"],
        "city_ev_homes_phrase": city["ev_charger_homes_phrase"],
        "city_specific_problems_neighborhood_phrase": require_variant_field(city, "specific_problems_neighborhood_phrase", service["slug"], city_slug),
        "city_ev_neighborhood_phrase": require_variant_field(city, "ev_neighborhood_phrase", service["slug"], city_slug),
        "city_most_common_problem_paragraph": require_variant_field(city, "most_common_problem_paragraph", service["slug"], city_slug),
        "city_distance_phrase": require_variant_field(city, "distance_phrase", service["slug"], city_slug),

        # D-11 fix: utility coordination phrase — cities with multiple utilities
        # (e.g. Stafford: Dominion/NOVEC/REC) get a city-specific phrase; single-
        # utility cities fall back to "Dominion Energy coordination"
        "utility_coordination_phrase": city.get("utility_coordination_phrase", "Dominion Energy coordination"),

        # D-12 fix: per-city dispatch time — max-reach cities (Stafford ~25mi)
        # get longer times; adjacent cities (Springfield ~15mi) get shorter.
        # Default 45-minute for cities without explicit data.
        "dispatch_time_phrase": city.get("dispatch_time_phrase", "45-minute"),
        "dispatch_time_short": city.get("dispatch_time_short", "45-min"),

        # Page-level derived
        "page_slug": page_slug,
        "page_url": page_url,
        "core_30_position": position,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    # Compose the no-trip-charge cities phrase
    cities = city["no_trip_charge_cities"]
    if len(cities) >= 2:
        no_trip_phrase = ", ".join(cities[:-1]) + ", or " + cities[-1]
    else:
        no_trip_phrase = cities[0] if cities else ""
    sub["no_trip_charge_cities_phrase"] = no_trip_phrase

    # Render the service's templated strings with the rest of the context
    sub["hero_eyebrow"] = service["hero_eyebrow_template"].format_map(sub)
    sub["hero_heading"] = service["hero_heading"]
    sub["hero_subheading"] = service["hero_subheading_template"].format_map(sub)
    sub["hero_image_alt"] = service["hero_image_alt_template"].format_map(sub)
    sub["hero_image_url"] = service["hero_image_url_template"].format_map(sub)
    sub["what_it_means_heading"] = service["what_it_means_heading"]
    sub["quick_ref_heading"] = service["quick_ref_heading_template"].format_map(sub)
    sub["quick_ref_intro"] = service["quick_ref_intro"]
    sub["quick_ref_footer_html"] = service["quick_ref_footer_html_template"].format_map(sub)
    sub["why_city_heading"] = service["why_city_heading_template"].format_map(sub)
    sub["why_city_closing_note"] = service["why_city_closing_note_template"].format_map(sub)
    sub["problems_heading"] = service["problems_heading_template"].format_map(sub)
    sub["problems_intro"] = service["problems_intro_template"].format_map(sub)
    sub["process_heading"] = service["process_heading_template"].format_map(sub)
    sub["process_intro"] = service["process_intro_template"].format_map(sub)
    sub["pricing_heading"] = service["pricing_heading"]
    sub["pricing_intro"] = service["pricing_intro_template"].format_map(sub)
    sub["pricing_note"] = service["pricing_note_template"].format_map(sub)
    sub["pricing_closing_note"] = service["pricing_closing_note"]
    sub["about_heading"] = service["about_heading_template"].format_map(sub)
    sub["about_portrait_url"] = service["about_portrait_url_template"].format_map(sub)
    sub["about_portrait_alt"] = service["about_portrait_alt_template"].format_map(sub)
    sub["neighborhoods_heading"] = service["neighborhoods_heading_template"].format_map(sub)
    sub["neighborhoods_intro"] = service["neighborhoods_intro_template"].format_map(sub)
    sub["related_heading"] = service["related_heading_template"].format_map(sub)
    sub["related_intro"] = service["related_intro_template"].format_map(sub)
    sub["final_cta_heading"] = service["final_cta_heading"]
    sub["final_cta_paragraph"] = service["final_cta_paragraph_template"].format_map(sub)
    sub["faq_heading"] = service["faq_heading"]

    # AIOSEO metadata templates
    sub["aioseo_page_title"] = service["aioseo_page_title_template"].format_map(sub)
    sub["aioseo_meta_description"] = service["aioseo_meta_description_template"].format_map(sub)
    sub["wordpress_page_title"] = service["wordpress_page_title_template"].format_map(sub)
    sub["focus_keyword"] = service["aioseo_focus_keyword_template"].format_map(sub)
    sub["aioseo_additional_keywords"] = [
        kw.format_map(sub) for kw in service["aioseo_additional_keywords_template"]
    ]

    # Stash the raw data structures for the section-renderers
    sub["_client"] = client
    sub["_service"] = service
    sub["_city"] = city
    return sub


# ----------------------------------------------------------------------------
# Section renderers (each returns an HTML fragment string)
# ----------------------------------------------------------------------------


def render_what_it_means_paragraphs(ctx: dict) -> str:
    out: list[str] = []
    for para in ctx["_service"]["what_it_means_paragraphs"]:
        out.append("        <p>" + para.format_map(ctx) + "</p>")
    return "\n".join(out) + "\n"


def render_quick_ref_items(ctx: dict) -> str:
    items = require_variant_field(
        ctx["_city"], "quick_ref_localized_items",
        ctx["service_slug"], ctx["city_slug"],
    )
    out: list[str] = []
    for it in items:
        out.append(
            "        <details>\n"
            f"          <summary>{it['summary']}</summary>\n"
            f"          <p>{it['body']}</p>\n"
            "        </details>"
        )
    return "\n\n".join(out) + "\n"


def render_pattern_cards(ctx: dict) -> str:
    cards = ctx["_city"]["housing_patterns"]
    out: list[str] = []
    for c in cards:
        out.append(
            '        <div class="evp-pattern-card">\n'
            f"          <h3>{c['title']}</h3>\n"
            f"          <em>{c['neighborhood_examples']}</em>\n"
            f"          <p>{c['context_paragraph']}</p>\n"
            f"          <p><strong>Symptoms:</strong> {c['symptoms']}</p>\n"
            "        </div>"
        )
    return "\n".join(out) + "\n"


def render_problem_cards(ctx: dict) -> str:
    cards = ctx["_service"]["problem_cards"]
    out: list[str] = []
    for c in cards:
        body = c["body"].format_map(ctx)
        out.append(
            '        <div class="evp-problem">\n'
            f"          <h3>{c['title']}</h3>\n"
            f"          <p>{body}</p>\n"
            "        </div>"
        )
    return "\n".join(out) + "\n"


def render_process_steps(ctx: dict) -> str:
    steps = ctx["_service"]["process_steps"]
    out: list[str] = []
    for i, s in enumerate(steps, start=1):
        body = s["body"].format_map(ctx)
        out.append(
            '        <div class="evp-process-step">\n'
            f'          <div class="evp-step-badge">{i}</div>\n'
            '          <div class="evp-step-body">\n'
            f"            <h3>{s['title']}</h3>\n"
            f"            <p>{body}</p>\n"
            "          </div>\n"
            "        </div>"
        )
    return "\n".join(out) + "\n"


def render_pricing_items(ctx: dict) -> str:
    items = ctx["_service"]["pricing_items"]
    out: list[str] = []
    for item in items:
        out.append(f"          <li>{item}</li>")
    return "\n".join(out) + "\n"


def render_about_paragraphs(ctx: dict) -> str:
    paragraphs = ctx["_service"]["about_text_paragraphs"]
    out: list[str] = []
    for p in paragraphs:
        out.append("          <p>" + p.format_map(ctx) + "</p>")
    return "\n".join(out) + "\n"


def render_neighborhoods_list(ctx: dict) -> str:
    neighborhoods = ctx["_city"]["neighborhoods"]
    out: list[str] = []
    for n in neighborhoods:
        out.append(
            f"            <li><strong>{n['name']}</strong> — {n['blurb']}</li>"
        )
    return "\n".join(out) + "\n"


def _page_exists_on_disk(slug: str, client_slug: str) -> bool:
    """Check if a Core 30 page folder exists for this slug (any position number)."""
    core30_dir = (
        Path.home() / "workspace" / "second-brain" / "04_projects" / "clients"
        / "_active" / client_slug / "website-archive" / "new" / "core-30"
    )
    if not core30_dir.is_dir():
        return False
    for d in core30_dir.iterdir():
        if d.is_dir() and d.name.split("-", 1)[-1:] == [slug]:
            return True
    return False


def render_related_cards(ctx: dict) -> str:
    """Render related-service cards. Only emits links to pages that exist on disk;
    unbuilt siblings render as plain text (no dead <a href>). Skips services not
    in the Core 30 set (whole-house-rewire, generator-installation)."""
    EXCLUDED_SERVICES = {"whole-house-rewire", "generator-installation"}
    cards = ctx["_service"]["related_cards"]
    client_slug = ctx.get("client_slug", "")
    out: list[str] = []
    for c in cards:
        href_slug = c["href_slug"].format_map(ctx)
        # Skip services not in Core 30
        service_part = href_slug.rsplit("-", 2)[0] if "-" in href_slug else href_slug
        if any(excl in href_slug for excl in EXCLUDED_SERVICES):
            continue
        # Only link if the target page exists
        if _page_exists_on_disk(href_slug, client_slug):
            out.append(
                f'        <div class="evp-related-card"><a href="/{href_slug}/">'
                f"{c['label']}</a></div>"
            )
        else:
            out.append(
                f'        <div class="evp-related-card">'
                f"{c['label']}</div>"
            )
    return "\n".join(out) + "\n"


def detect_hero_orientation(image_path: Path) -> str:
    """Return 'portrait' if height > width, else 'landscape'.

    Uses PIL/Pillow if available. Falls back to 'landscape' (the default
    placeholder shape) if PIL is missing or the file can't be read — we
    prefer the visual default over a hard failure, since the same scaffolder
    is used both for placeholder pages (no image yet) and image-wire-in
    runs (real image on disk).
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        sys.stderr.write(
            "WARN: Pillow not installed; can't detect hero orientation. "
            "Defaulting to landscape (Pattern A). "
            "Install with: pip install Pillow --break-system-packages\n"
        )
        return "landscape"
    try:
        with Image.open(image_path) as im:
            w, h = im.size
    except Exception as e:
        sys.stderr.write(
            f"WARN: couldn't read hero image at {image_path}: {e}. "
            "Defaulting to landscape (Pattern A).\n"
        )
        return "landscape"
    return "portrait" if h > w else "landscape"


def render_hero_image_block(ctx: dict, hero_image_path: Path | None) -> str:
    """Render the hero <div class=\"evp-hero-image\"> block as Pattern A or B.

    Pattern A (landscape — default / placeholder): image fills the hero column
    at natural size with rounded corners (the wrapper has overflow:hidden +
    border-radius, and the img also carries border-radius for Imagify
    `<picture>` wrap resilience).

    Pattern B (portrait — only when source image is detected portrait): image
    is a small contained portrait (360px wide) with rounded corners so it
    doesn't dominate the hero column.

    The orientation is *only* swapped to B when a real local image was passed
    via --hero-image-path AND PIL reports height > width. Otherwise we emit
    Pattern A — the safe placeholder shape that matches the live v17
    landscape deploy.
    """
    orientation = "landscape"
    if hero_image_path is not None:
        if not hero_image_path.is_file():
            sys.stderr.write(
                f"WARN: --hero-image-path file not found: {hero_image_path}. "
                "Defaulting to landscape (Pattern A).\n"
            )
        else:
            orientation = detect_hero_orientation(hero_image_path)
            sys.stderr.write(
                f"→ Hero image orientation: {orientation} "
                f"({'Pattern B' if orientation == 'portrait' else 'Pattern A'})\n"
            )

    hero_image_url = ctx["hero_image_url"]
    hero_image_alt = ctx["hero_image_alt"]

    if orientation == "portrait":
        return (
            '      <div class="evp-hero-image" style="background: none; border: none; padding: 0; min-height: 0; display: block;">\n'
            f'        <img src="{hero_image_url}"\n'
            f'             alt="{hero_image_alt}"\n'
            '             loading="eager"\n'
            '             width="896" height="1200"\n'
            '             style="width: 360px; max-width: 100%; height: auto; display: block; border-radius: 20px; -webkit-border-radius: 20px;">\n'
            '      </div>'
        )
    # Pattern A (landscape / placeholder default)
    return (
        '      <div class="evp-hero-image" style="background: none; border: none; padding: 0; overflow: hidden; border-radius: 20px;">\n'
        f'        <img src="{hero_image_url}"\n'
        f'             alt="{hero_image_alt}"\n'
        '             loading="eager"\n'
        '             width="1200" height="896"\n'
        '             style="width: 100%; height: auto; display: block; border-radius: 20px; -webkit-border-radius: 20px;">\n'
        '      </div>'
    )


def render_faq_items(ctx: dict) -> str:
    items = ctx["_service"]["faq_items"]
    out: list[str] = []
    for it in items:
        q = it["question"].format_map(ctx)
        a = it["answer_html"].format_map(ctx)
        out.append(
            "        <details>\n"
            f"          <summary>{q}</summary>\n"
            f"          <p>{a}</p>\n"
            "        </details>"
        )
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# Map iframe — call generate-maps-iframe.py as an importable module
# ----------------------------------------------------------------------------


def get_map_iframe_html(ctx: dict) -> str:
    """Load generate-maps-iframe.py as a module and call its core function."""
    map_script_path = SCRIPTS_DIR / "generate-maps-iframe.py"
    if not map_script_path.is_file():
        sys.stderr.write(f"ERROR: generate-maps-iframe.py not found at {map_script_path}\n")
        sys.exit(2)

    spec = importlib.util.spec_from_file_location("maps_iframe", map_script_path)
    if spec is None or spec.loader is None:
        sys.stderr.write("ERROR: failed to load generate-maps-iframe.py as a module.\n")
        sys.exit(2)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cfg = mod.load_config(None)
    # Inject client_name from the caller's context so the maps iframe title
    # uses the correct client brand — not a hardcoded default (PR-35 EV-leak fix).
    cfg["client_name"] = ctx["client_name"]
    cache_path = mod.resolve_cache_path(cfg["cache_path"])
    cache = mod.load_cache(cache_path)

    city_name = ctx["city_name"]
    city_state = ctx["city_state"]

    key, wrapped, hit = mod.generate_for_city(
        city_name, city_state, cfg, cache, force_refresh=False
    )
    if not hit:
        mod.save_cache(cache_path, cache)
        sys.stderr.write(f"[maps:fresh] {key} (added to cache)\n")
    else:
        sys.stderr.write(f"[maps:cache] {key}\n")

    # Indent the wrapped HTML to match the template's expected indentation
    indented = "\n".join("        " + line if line.strip() else line for line in wrapped.splitlines())
    return indented + "\n"


# ----------------------------------------------------------------------------
# JSON-LD schema
# ----------------------------------------------------------------------------


def build_jsonld(ctx: dict) -> str:
    """Build the LocalBusiness + Service + FAQPage @graph for this page."""
    client = ctx["_client"]
    service = ctx["_service"]
    city = ctx["_city"]

    # D-10 fix: prefer city-level area_served_schema (county + adjacent cities)
    # over the client-level brand_areas_served (which is hardcoded to the client's
    # HQ county and doesn't reflect the actual page city). Falls back to client-level
    # for cities that haven't been upgraded yet.
    if "area_served_schema" in city:
        area_served = city["area_served_schema"]
    else:
        area_served = [
            {"@type": "City", "name": name, "containedInPlace": {"@type": "AdministrativeArea", "name": client["brand_area_county"]}}
            for name in client["brand_areas_served"]
        ]

    hours_spec = []
    for h in client["hours"]:
        hours_spec.append({
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": h["days"],
            "opens": h["opens"],
            "closes": h["closes"],
        })

    local_business = {
        "@type": "LocalBusiness",
        "@id": f"{client['website_url']}#business",
        "name": client["name"],
        "alternateName": client["alternate_name"],
        "description": client["business_description_schema"],
        "url": client["website_url"],
        "telephone": client["phone_e164"],
        "email": client["email"],
        "image": client["brand_image_url"],
        "logo": client["brand_logo_url"],
        "priceRange": client["price_range_token"],
        "address": {
            "@type": "PostalAddress",
            "streetAddress": client["address"]["street"],
            "addressLocality": client["address"]["locality"],
            "addressRegion": client["address"]["region"],
            "postalCode": client["address"]["postal_code"],
            "addressCountry": client["address"]["country"],
        },
        "geo": {"@type": "GeoCoordinates", "latitude": client["geo"]["latitude"], "longitude": client["geo"]["longitude"]},
        "areaServed": area_served,
        "openingHoursSpecification": hours_spec,
        "hasCredential": {
            "@type": "EducationalOccupationalCredential",
            "credentialCategory": client["license"]["category"],
            "recognizedBy": {"@type": "Organization", "name": client["license"]["issuer"]},
        },
        "founder": {
            "@type": "Person",
            "name": client["owner_name"],
            "jobTitle": client["owner_title"],
            "worksFor": {"@id": f"{client['website_url']}#business"},
        },
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": client["review_rating"],
            "reviewCount": str(client["review_count"]),
            "bestRating": "5",
            "worstRating": "1",
        },
    }

    service_block = {
        "@type": "Service",
        "@id": f"{ctx['page_url']}#service",
        "name": service["name_with_city"].format_map(ctx),
        "description": service["schema_service_description_template"].format_map(ctx),
        "serviceType": service["service_type_phrase"],
        "provider": {"@id": f"{client['website_url']}#business"},
        "areaServed": {
            "@type": "City",
            "name": city["name"],
            "containedInPlace": {"@type": "AdministrativeArea", "name": city["county_full"]},
        },
        "audience": {"@type": "Audience", "audienceType": ctx["audience_descriptor"]},
        "offers": {
            "@type": "Offer",
            "priceCurrency": "USD",
            "price": service["schema_service_price"],
            "priceSpecification": {
                "@type": "PriceSpecification",
                "price": service["schema_service_price"],
                "priceCurrency": "USD",
                "description": service["schema_service_price_description"],
            },
            "availability": "https://schema.org/InStock",
            "url": ctx["page_url"],
        },
        "termsOfService": service["schema_service_terms"],
    }

    faq_main = []
    for it in service["faq_items"]:
        q = it.get("question_schema", it["question"]).format_map(ctx)
        a = it["answer_schema"].format_map(ctx)
        faq_main.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })

    faq_page = {
        "@type": "FAQPage",
        "@id": f"{ctx['page_url']}#faq",
        "mainEntity": faq_main,
    }

    graph = {
        "@context": "https://schema.org",
        "@graph": [local_business, service_block, faq_page],
    }
    return json.dumps(graph, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------------
# Top-level render
# ----------------------------------------------------------------------------


def render_html(ctx: dict, hero_image_path: Path | None = None) -> str:
    template = load_template("core-30-page.html.tmpl")
    ctx["hero_image_block"] = render_hero_image_block(ctx, hero_image_path)
    ctx["what_it_means_paragraphs_html"] = render_what_it_means_paragraphs(ctx)
    ctx["quick_ref_items_html"] = render_quick_ref_items(ctx)
    ctx["pattern_cards_html"] = render_pattern_cards(ctx)
    ctx["problem_cards_html"] = render_problem_cards(ctx)
    ctx["process_steps_html"] = render_process_steps(ctx)
    ctx["pricing_items_html"] = render_pricing_items(ctx)
    ctx["about_text_paragraphs_html"] = render_about_paragraphs(ctx)
    ctx["neighborhoods_list_html"] = render_neighborhoods_list(ctx)
    ctx["related_cards_html"] = render_related_cards(ctx)
    ctx["faq_items_html"] = render_faq_items(ctx)
    ctx["map_iframe_html"] = get_map_iframe_html(ctx)
    ctx["jsonld"] = build_jsonld(ctx)
    html = template.format_map(ctx)

    # Post-render body-level link validation: check every internal <a href>
    # against page-existence on disk. Warn on any dead links so the operator
    # sees them before publish — catches other_areas_paragraph hrefs, inline
    # cross-references, and any other source of internal links, not just
    # related_cards (which is already existence-checked at render time).
    _validate_internal_links(html, ctx.get("client_slug", ""))

    return html


def _validate_internal_links(html: str, client_slug: str) -> None:
    """Scan rendered HTML for internal <a href="/slug/"> links that point to
    pages that don't exist on disk. Prints warnings to stderr. Does NOT
    block the scaffold — the operator decides whether to fix or accept."""
    import re as _re
    internal_hrefs = _re.findall(r'href="/([a-z0-9][a-z0-9\-]*)/"', html)
    SERVICE_KEYWORDS = [
        'troubleshooting', 'panel-upgrade', 'ev-charger', 'light-fixture',
        'smoke-alarm', 'outlet-installation', 'emergency-electrician',
        'whole-house', 'generator',
    ]
    dead = []
    checked = set()
    for slug in internal_hrefs:
        if slug in checked:
            continue
        checked.add(slug)
        if not any(kw in slug for kw in SERVICE_KEYWORDS):
            continue
        if not _page_exists_on_disk(slug, client_slug):
            dead.append(slug)
    if dead:
        sys.stderr.write(
            f"\n⚠ INTERNAL LINK WARNING: {len(dead)} href(s) point to "
            f"pages that don't exist on disk for client '{client_slug}':\n"
        )
        for slug in dead:
            sys.stderr.write(f"    → /{slug}/\n")
        sys.stderr.write(
            "  These will 404 if published. Fix the source (city JSON "
            "other_areas_paragraph or service JSON related_cards) or "
            "unlink before publishing.\n\n"
        )


def render_markdown(ctx: dict) -> str:
    template = load_template("draft-v1.md.tmpl")
    additional_kw_md = ", ".join(f"`{kw}`" for kw in ctx["aioseo_additional_keywords"])
    EXCLUDED_SERVICES = {"whole-house-rewire", "generator-installation"}
    related_pages = [
        card["href_slug"].format_map(ctx)
        for card in ctx["_service"]["related_cards"]
        if not any(excl in card["href_slug"] for excl in EXCLUDED_SERVICES)
    ]
    ctx["aioseo_additional_keywords_md"] = additional_kw_md
    ctx["related_pages_csv"] = ", ".join(related_pages)
    ctx["page_title_h1"] = ctx["wordpress_page_title"]
    return template.format_map(ctx)


def render_version_log(ctx: dict) -> str:
    return (
        f"# Version log — {ctx['page_slug']}\n\n"
        f"Auto-generated by `scaffold-core-30-page.py` on {ctx['generated_at']}.\n\n"
        "| Version | Date | Notes |\n"
        "|---|---|---|\n"
        f"| draft-v1 | {ctx['today']} | Initial scaffold from "
        f"`data/services/{ctx['service_slug']}.json` + "
        f"`data/cities/{ctx['city_slug']}.json` + "
        f"`data/client-{ctx['client_slug']}.json`. Map iframe sourced from "
        f"`generate-maps-iframe.py` cache for {ctx['city_name_with_state']}. |\n"
    )


# ----------------------------------------------------------------------------
# File output
# ----------------------------------------------------------------------------


def find_page_folder(client_slug: str, page_slug: str, position: int) -> Path:
    """Compute the target folder for the new page.

    For EV Electric, the canonical location is:
      ~/workspace/second-brain/04_projects/clients/_active/<client>/website-archive/new/core-30/<NN>-<slug>/
    """
    # Walk up from SCRIPTS_DIR to find workspace root
    here = SCRIPTS_DIR
    for parent in here.parents:
        if (parent / "second-brain").is_dir() and (parent / "repos").is_dir():
            workspace_root = parent
            break
    else:
        sys.stderr.write("ERROR: couldn't locate workspace root.\n")
        sys.exit(2)

    nn = f"{position:02d}"
    return (
        workspace_root
        / "second-brain"
        / "04_projects"
        / "clients"
        / "_active"
        / client_slug
        / "website-archive"
        / "new"
        / "core-30"
        / f"{nn}-{page_slug}"
    )


def write_outputs(folder: Path, html: str, md: str, version_log: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "draft-v1-WP-WRAPPED.html").write_text(html, encoding="utf-8")
    (folder / "draft-v1.md").write_text(md, encoding="utf-8")
    (folder / "_VERSION-LOG.md").write_text(version_log, encoding="utf-8")


# ----------------------------------------------------------------------------
# output-quality-loop auto-invoke recommendation
# ----------------------------------------------------------------------------


def emit_quality_loop_invoke_block(folder: Path) -> None:
    """Emit the standard output-quality-loop auto-invoke block to stderr.

    The block is a Cowork-chat convention, not a Python convention. The script
    emits it as a recommendation; the Cowork chat that invoked the script reads
    stderr and decides whether to run output-quality-loop's EVALUATE mode on
    the named artifacts. The script does NOT shell out to Cowork — that layer
    doesn't exist.

    Spec: ~/workspace/skills/output-quality-loop/references/auto-invoke-convention.md
    """
    md_path = folder / "draft-v1.md"
    html_path = folder / "draft-v1-WP-WRAPPED.html"
    version_log_path = folder / "_VERSION-LOG.md"
    block = (
        "\n"
        "## Auto-invoke output-quality-loop\n"
        "\n"
        "This skill produced the following artifact(s):\n"
        f"- `{md_path}`\n"
        f"- `{html_path}`\n"
        f"- `{version_log_path}`\n"
        "\n"
        "[output-quality-loop:eval] — for each artifact, run output-quality-loop in "
        "EVALUATE mode. If PASS, declare the chat done. If NEEDS REVISION or FAIL, "
        "ingest the revision prompt as if it were operator input and regenerate the "
        "artifact, then re-invoke output-quality-loop. Cap at 3 iterations; on the 3rd "
        "FAIL, escalate to the operator with the evaluation report.\n"
    )
    sys.stderr.write(block)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Scaffold a finished Core 30 page draft from data.",
    )
    p.add_argument("--service", required=True, help='Service slug (e.g. "troubleshooting")')
    p.add_argument("--city", required=True, help='City slug (e.g. "vienna-va")')
    p.add_argument("--client", default="ev-electric-services", help='Client slug (default: ev-electric-services)')
    p.add_argument("--position", type=int, required=True, help="Core 30 build-order position (e.g. 6)")
    p.add_argument("--dry-run", action="store_true", help="Render but don't write files.")
    p.add_argument(
        "--output-folder", type=Path, default=None,
        help="Override the output folder (defaults to the canonical core-30 path).",
    )
    p.add_argument(
        "--hero-image-path", type=Path, default=None,
        help=(
            "Local path to the actual hero image. If supplied and Pillow is "
            "available, the scaffolder reads the image dimensions and emits "
            "Pattern B (small contained portrait, 360px) when the source is "
            "portrait, or Pattern A (full-width landscape) otherwise. Omit "
            "for placeholder scaffolds — defaults to Pattern A."
        ),
    )

    args = p.parse_args()

    client = load_client(args.client)
    service = load_service(args.service, client_slug=args.client)
    city = load_city(args.city, client_slug=args.client)

    ctx = build_context(client, service, city, args.position)

    sys.stderr.write(f"→ Client:   {args.client} ({client['name']})\n")
    sys.stderr.write(f"→ Service:  {args.service} ({service['name']})\n")
    sys.stderr.write(f"→ City:     {args.city} ({city['name_with_state']})\n")
    sys.stderr.write(f"→ Position: {args.position}\n")
    sys.stderr.write(f"→ Page URL: {ctx['page_url']}\n")

    html = render_html(ctx, hero_image_path=args.hero_image_path)
    md = render_markdown(ctx)
    version_log = render_version_log(ctx)

    if args.dry_run:
        sys.stderr.write("\nDRY RUN — no files written.\n")
        sys.stderr.write(f"  HTML size: {len(html):,} chars\n")
        sys.stderr.write(f"  MD size:   {len(md):,} chars\n")
        return 0

    folder = args.output_folder or find_page_folder(client["client_slug"], ctx["page_slug"], args.position)
    if folder.exists() and any(folder.iterdir()):
        sys.stderr.write(f"\nWARNING: folder already has contents: {folder}\n")
        sys.stderr.write("Refusing to overwrite. Pass --output-folder to a fresh path, or delete the existing folder first.\n")
        return 3

    write_outputs(folder, html, md, version_log)
    sys.stderr.write(f"\n→ Wrote:    {folder / 'draft-v1-WP-WRAPPED.html'}\n")
    sys.stderr.write(f"→ Wrote:    {folder / 'draft-v1.md'}\n")
    sys.stderr.write(f"→ Wrote:    {folder / '_VERSION-LOG.md'}\n")
    sys.stderr.write(
        "\nNext: run output-quality-loop on the new draft (block below), then "
        "publish via publish-core-30-page.py once verdict is PASS.\n"
    )
    emit_quality_loop_invoke_block(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
