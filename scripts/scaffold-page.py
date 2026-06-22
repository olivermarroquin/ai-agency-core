#!/usr/bin/env python3
"""
scaffold-page.py — unified, profile-driven page scaffolder.

Reads a client config (business_type field), loads the matching type profile
from profiles/<business_type>/, and generates pages. Supports two page-generation
modes declared by the profile:

  - matrix: expands service × city slugs (e.g., electrician Core 30)
  - fixed-list: iterates a declared page list (e.g., restaurant multi-page)

ZERO type-specific conditionals. All business-type knowledge lives in profiles/.

USAGE
-----
Matrix mode (electrician — requires --service, --city, --position):

    python3 scaffold-page.py \\
        --client ev-electric-services \\
        --service troubleshooting \\
        --city mclean-va \\
        --position 1

Fixed-list mode (restaurant — generates all pages):

    python3 scaffold-page.py --client asian-delight

Dry-run (render but don't write):

    python3 scaffold-page.py --client asian-delight --dry-run
"""

from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
PROFILES_DIR = SCRIPTS_DIR / "profiles"
TEMPLATES_DIR = SCRIPTS_DIR / "templates"
OUTPUT_DIR = SCRIPTS_DIR / "output"


# ============================================================================
# Loaders
# ============================================================================

def load_json(path: Path) -> dict:
    if not path.is_file():
        sys.stderr.write(f"ERROR: data file not found: {path}\n")
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def load_client(slug: str) -> dict:
    return load_json(DATA_DIR / f"client-{slug}.json")


def load_service(slug: str, client_slug: str | None = None) -> dict:
    if client_slug:
        client_path = DATA_DIR / "services" / client_slug / f"{slug}.json"
        if client_path.is_file():
            return load_json(client_path)
    return load_json(DATA_DIR / "services" / f"{slug}.json")


def load_city(slug: str, client_slug: str | None = None) -> dict:
    base = load_json(DATA_DIR / "cities" / f"{slug}.json")
    if client_slug:
        override_path = DATA_DIR / "cities" / client_slug / f"{slug}.json"
        if override_path.is_file():
            base.update(load_json(override_path))
    return base


def load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if not path.is_file():
        sys.stderr.write(f"ERROR: template not found: {path}\n")
        sys.exit(2)
    return path.read_text(encoding="utf-8")


def load_profile(business_type: str) -> dict:
    profile_dir = PROFILES_DIR / business_type
    if not profile_dir.is_dir():
        available = [d.name for d in PROFILES_DIR.iterdir() if d.is_dir()]
        raise ValueError(f"No profile for business_type={business_type!r}. Available: {available}")
    return {
        "page_model": load_json(profile_dir / "page-model.json"),
        "schema_template": load_json(profile_dir / "schema-template.json"),
        "keyword_research": load_json(profile_dir / "keyword-research.json"),
        "content_sections": load_json(profile_dir / "content-sections.json"),
        "config_schema": load_json(profile_dir / "config-schema.json"),
    }


# ============================================================================
# Token resolution — profile declares, engine resolves
# ============================================================================

def _todays_closing_time(hours: list) -> str:
    today_name = calendar.day_name[datetime.now().weekday()]
    for entry in hours:
        if today_name in entry["days"]:
            raw = entry["closes"]
            h, m = int(raw.split(":")[0]), int(raw.split(":")[1])
            suffix = "AM" if h < 12 else "PM"
            display_h = h if h <= 12 else h - 12
            if display_h == 0:
                display_h = 12
            return f"{display_h} {suffix}" if m == 0 else f"{display_h}:{m:02d} {suffix}"
    return "closed"


def _format_city_list(cities: list) -> str:
    if len(cities) >= 2:
        return ", ".join(cities[:-1]) + ", or " + cities[-1]
    return cities[0] if cities else ""


COMPUTE_FUNCTIONS = {
    "county_short":                 lambda ctx: ctx["_city"]["county"].split(",")[0],
    "city_tag":                     lambda ctx: ctx["_resolved"].get("city_name", "").lower().replace(" ", "-"),
    "no_trip_charge_cities_phrase":  lambda ctx: _format_city_list(ctx["_city"].get("no_trip_charge_cities", [])),
    "page_slug":                    lambda ctx: ctx["_page_slug"],
    "page_url":                     lambda ctx: f"{ctx['_resolved'].get('website_url', '')}{ctx['_page_slug']}/",
    "core_30_position":             lambda ctx: ctx.get("_position", 0),
    "todays_closing_time":          lambda ctx: _todays_closing_time(ctx["_client"]["hours"]),
}


def require_variant_field(city: dict, field_name: str, service_slug: str, city_slug: str) -> Any:
    container = city.get(field_name, {})
    if not isinstance(container, dict):
        sys.stderr.write(f"ERROR: city '{city_slug}' field '{field_name}' is not a dict.\n")
        sys.exit(3)
    if service_slug not in container:
        sys.stderr.write(
            f"ERROR: city '{city_slug}' missing '{field_name}[\"{service_slug}\"]'.\n"
            f"  → DA2 depth-parity failure. Run intersection-research for "
            f"{service_slug}--{city_slug}.\n"
        )
        sys.exit(3)
    value = container[service_slug]
    if value is None or value == "" or value == []:
        sys.stderr.write(f"ERROR: city '{city_slug}' '{field_name}[\"{service_slug}\"]' is empty.\n")
        sys.exit(3)
    return value


def resolve_token(path: str, client: dict, service: dict | None,
                  city: dict | None, computed_ctx: dict) -> Any:
    """Resolve a token_bindings path to a value. No type-specific logic."""
    if path.startswith("literal:"):
        return path[len("literal:"):]

    if path.startswith("default:"):
        parts = path.split(":", 2)
        inner_path, fallback = parts[1], parts[2] if len(parts) > 2 else ""
        try:
            return resolve_token(inner_path, client, service, city, computed_ctx)
        except (KeyError, TypeError):
            return fallback

    if path.startswith("computed."):
        fn_name = path[len("computed."):]
        return COMPUTE_FUNCTIONS[fn_name](computed_ctx)

    # Variant fields: path ending in ._variant resolves via require_variant_field
    if path.endswith("._variant"):
        base_path = path[:-len("._variant")]
        parts = base_path.split(".")
        if parts[0] == "city" and city is not None and service is not None:
            field_name = parts[1]
            return require_variant_field(
                city, field_name, service["slug"], city["slug"]
            )
        raise ValueError(f"Variant path {path!r} requires city+service data")

    parts = path.split(".")
    prefix = parts[0]
    remainder = parts[1:]

    if prefix == "root":
        obj = client
    elif prefix == "service":
        if service is None:
            raise ValueError(f"Token {path!r} requires service data (matrix mode)")
        obj = service
    elif prefix == "city":
        if city is None:
            raise ValueError(f"Token {path!r} requires city data (matrix mode)")
        obj = city
    else:
        obj = client[prefix]  # type-specific section

    for key in remainder:
        obj = obj[key]
    return obj


def build_context(client: dict, profile: dict,
                  service: dict | None = None, city: dict | None = None,
                  page_slug: str = "", position: int = 0) -> dict:
    """Build the substitution dict by walking the profile's token_bindings.
    NO per-type conditionals."""
    bindings = profile["content_sections"]["token_bindings"]
    computed_ctx = {
        "_client": client, "_city": city or {},
        "_resolved": {}, "_page_slug": page_slug, "_position": position,
    }

    sub = {}
    for token_name, config_path in bindings.items():
        try:
            sub[token_name] = resolve_token(config_path, client, service, city, computed_ctx)
            computed_ctx["_resolved"][token_name] = sub[token_name]
        except (KeyError, TypeError, ValueError):
            sub[token_name] = ""

    sub["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sub["today"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Pass 2: template_renders
    for render_spec in profile["content_sections"].get("template_renders", []):
        token_name = render_spec["token"]
        source = render_spec["source"]
        is_template = render_spec.get("is_template", True)

        if source.startswith("literal:"):
            raw_value = source[len("literal:"):]
        else:
            try:
                raw_value = resolve_token(source, client, service, city, computed_ctx)
            except (KeyError, TypeError, ValueError):
                raw_value = ""

        if is_template and isinstance(raw_value, str):
            try:
                sub[token_name] = raw_value.format_map(sub)
            except KeyError:
                sub[token_name] = raw_value
        elif is_template and isinstance(raw_value, list):
            sub[token_name] = [
                item.format_map(sub) if isinstance(item, str) else item
                for item in raw_value
            ]
        else:
            sub[token_name] = raw_value

    sub["_client"] = client
    sub["_service"] = service
    sub["_city"] = city
    return sub


# ============================================================================
# JSON-LD builder — profile-driven, branch-free
# ============================================================================

def _resolve_jsonld_value(val: Any, client: dict, service: dict | None,
                          city: dict | None, ctx: dict) -> Any:
    """Resolve a single value in a field_mappings tree."""
    if isinstance(val, str):
        if val == "_from_hours_array":
            return _build_hours_spec(client["hours"])
        if val == "_from_area_served":
            return _build_area_served(client, city)
        if val == "_from_menu_sections":
            btype = client["business_type"]
            sections = client.get(btype, {}).get("menu_sections", [])
            return _build_menu_section_nodes(sections)
        if val == "_from_faq_items":
            if service is not None:
                return _build_faq_nodes(service, ctx)
            return []
        if val == "_business_id_ref":
            return f"{client['website_url']}#business"
        computed_ctx = {"_client": client, "_city": city or {}, "_resolved": ctx, "_page_slug": ctx.get("page_slug", ctx.get("_page_slug", ""))}
        return resolve_token(val, client, service, city, computed_ctx)
    if isinstance(val, dict):
        return _resolve_jsonld_node(val, client, service, city, ctx)
    return val


def _resolve_jsonld_node(mapping: dict, client: dict, service: dict | None,
                         city: dict | None, ctx: dict) -> dict:
    """Recursively resolve a JSON-LD node from a field_mappings dict."""
    node = {}
    for key, val in mapping.items():
        if key in ("note", "@id_suffix"):
            continue
        # @type is always a literal value, not a token path
        if key == "@type":
            node["@type"] = val
            continue
        # @id with special directives
        if key == "@id" and isinstance(val, str) and val == "_business_id_ref":
            node["@id"] = f"{client['website_url']}#business"
            continue
        try:
            resolved = _resolve_jsonld_value(val, client, service, city, ctx)
            # Apply format_map for template strings (e.g., service description templates)
            if isinstance(resolved, str) and "{" in resolved:
                try:
                    resolved = resolved.format_map(ctx)
                except KeyError:
                    pass
            if resolved is not None:
                # Convert review_count to string for schema compliance
                if key == "reviewCount" and isinstance(resolved, int):
                    resolved = str(resolved)
                node[key] = resolved
        except (KeyError, TypeError, ValueError):
            pass
    return node


def _build_hours_spec(hours: list) -> list:
    return [
        {"@type": "OpeningHoursSpecification",
         "dayOfWeek": h["days"], "opens": h["opens"], "closes": h["closes"]}
        for h in hours
    ]


def _build_area_served(client: dict, city: dict | None) -> list:
    if city and "area_served_schema" in city:
        return city["area_served_schema"]
    return [
        {"@type": "City", "name": name,
         "containedInPlace": {"@type": "AdministrativeArea",
                              "name": client.get("brand_area_county", "")}}
        for name in client.get("brand_areas_served", [])
    ]


def _build_menu_section_nodes(sections: list) -> list:
    result = []
    for sec in sections:
        menu_sec = {"@type": "MenuSection", "name": sec["name"], "hasMenuItem": []}
        for item in sec.get("items", []):
            mi = {"@type": "MenuItem", "name": item["name"]}
            if item.get("description"):
                mi["description"] = item["description"]
            if item.get("price"):
                mi["offers"] = {
                    "@type": "Offer",
                    "price": item["price"].replace("$", ""),
                    "priceCurrency": "USD",
                }
            menu_sec["hasMenuItem"].append(mi)
        result.append(menu_sec)
    return result


def _build_faq_nodes(service: dict, ctx: dict) -> list:
    items = service.get("faq_items", [])
    result = []
    for it in items:
        q = it.get("question_schema", it["question"])
        a = it["answer_schema"]
        if isinstance(q, str):
            try: q = q.format_map(ctx)
            except KeyError: pass
        if isinstance(a, str):
            try: a = a.format_map(ctx)
            except KeyError: pass
        result.append({
            "@type": "Question", "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    return result


def build_jsonld(client: dict, profile: dict, ctx: dict,
                 page_slug: str, service: dict | None = None,
                 city: dict | None = None) -> str:
    """Build JSON-LD from the profile's schema-template. ZERO type-specific code.

    The profile declares:
      - business_node.field_mappings → the main business entity
      - conditional_blocks → blocks attached to business node if condition is truthy
      - page_conditional_blocks → per-page conditional blocks to also evaluate
      - page_graph_nodes → additional @graph nodes for specific pages
    """
    schema = profile["schema_template"]
    page_url = ctx.get("page_url", f"{client['website_url']}{page_slug}/")

    # Build business node from field_mappings
    business = {"@type": schema["primary_type"],
                "@id": f"{client['website_url']}#business"}

    for field_name, mapping in schema["business_node"]["field_mappings"].items():
        try:
            resolved = _resolve_jsonld_value(mapping, client, service, city, ctx)
            if isinstance(resolved, str) and "{" in resolved:
                try:
                    resolved = resolved.format_map(ctx)
                except KeyError:
                    pass
            if field_name == "reviewCount" and isinstance(resolved, int):
                resolved = str(resolved)
            if resolved is not None:
                business[field_name] = resolved
        except (KeyError, TypeError, ValueError):
            pass

    # Collect all block keys that are page-gated (only emit on their declared page)
    all_page_gated_keys = set()
    for keys_list in schema.get("page_conditional_blocks", {}).values():
        all_page_gated_keys.update(keys_list)

    # Evaluate conditional_blocks (global — SKIP page-gated blocks)
    for block_key, block_def in schema.get("conditional_blocks", {}).items():
        if block_key in all_page_gated_keys:
            continue  # This block only fires on its declared page
        condition = block_def.get("condition")
        if condition is None:
            continue
        try:
            computed_ctx = {"_client": client, "_city": city or {}, "_resolved": ctx, "_page_slug": ctx.get("page_slug", ctx.get("_page_slug", ""))}
            val = resolve_token(condition, client, service, city, computed_ctx)
            if val:
                prop_name = block_key.split("_")[0] if "_" in block_key else block_key
                block_node = _resolve_jsonld_node(block_def["block"], client, service, city, ctx)
                if block_node:
                    business[prop_name] = block_node
        except (KeyError, TypeError, ValueError):
            pass

    # Evaluate page-specific conditional_blocks (only for THIS page)
    page_cond_keys = schema.get("page_conditional_blocks", {}).get(page_slug, [])
    for block_key in page_cond_keys:
        block_def = schema["conditional_blocks"].get(block_key, {})
        condition = block_def.get("condition")
        if condition is None:
            continue
        try:
            computed_ctx = {"_client": client, "_city": city or {}, "_resolved": ctx, "_page_slug": ctx.get("page_slug", ctx.get("_page_slug", ""))}
            val = resolve_token(condition, client, service, city, computed_ctx)
            if val:
                prop_name = block_key.split("_")[0] if "_" in block_key else block_key
                block_node = _resolve_jsonld_node(block_def["block"], client, service, city, ctx)
                if block_node:
                    # Append to list if property already exists (e.g., multiple potentialAction)
                    if prop_name in business and isinstance(business[prop_name], list):
                        business[prop_name].append(block_node)
                    elif prop_name in business:
                        business[prop_name] = [business[prop_name], block_node]
                    else:
                        business[prop_name] = block_node
        except (KeyError, TypeError, ValueError):
            pass

    graph = [business]

    # Page-specific additional graph nodes
    page_key = page_slug if page_slug in schema.get("page_graph_nodes", {}) else "_matrix_page"
    for node_def in schema.get("page_graph_nodes", {}).get(page_key, []):
        node = {"@type": node_def["@type"]}
        id_suffix = node_def.get("@id_suffix", "")
        if id_suffix:
            node["@id"] = f"{page_url}{id_suffix}"
        for field_name, mapping in node_def.get("field_mappings", {}).items():
            try:
                resolved = _resolve_jsonld_value(mapping, client, service, city, ctx)
                # Apply format_map for template strings
                if isinstance(resolved, str) and "{" in resolved:
                    try:
                        resolved = resolved.format_map(ctx)
                    except KeyError:
                        pass
                if field_name == "reviewCount" and isinstance(resolved, int):
                    resolved = str(resolved)
                if resolved is not None:
                    node[field_name] = resolved
            except (KeyError, TypeError, ValueError):
                pass
        graph.append(node)

    result = json.dumps({"@context": "https://schema.org", "@graph": graph},
                        indent=2, ensure_ascii=False)
    # CR-055: escape </ to prevent </script> breakout
    return result.replace("</", "\\u003c/")


# ============================================================================
# Electrician legacy HTML renderer — delegates to existing section renderers
# ============================================================================

def _import_legacy_engine():
    """Import scaffold-core-30-page.py as a module for its section renderers."""
    legacy_path = SCRIPTS_DIR / "scaffold-core-30-page.py"
    if not legacy_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("legacy_engine", legacy_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_matrix_page(client: dict, service: dict, city: dict,
                       profile: dict, position: int,
                       hero_image_path: Path | None = None) -> tuple[str, str, str]:
    """Render an electrician-style matrix page using the legacy template + renderers.

    This preserves byte-for-byte HTML output for regression. The JSON-LD is
    built by the profile-driven builder (not the legacy one).
    """
    legacy = _import_legacy_engine()
    if legacy is None:
        sys.stderr.write("ERROR: scaffold-core-30-page.py not found for legacy rendering.\n")
        sys.exit(2)

    # Use legacy build_context for the HTML substitution dict (regression)
    ctx = legacy.build_context(client, service, city, position)

    # OVERRIDE: use the profile-driven JSON-LD builder
    profile_ctx = build_context(client, profile, service=service, city=city,
                                page_slug=ctx["page_slug"], position=position)
    ctx["jsonld"] = build_jsonld(client, profile, profile_ctx,
                                 page_slug="_matrix_page",
                                 service=service, city=city)

    # Render HTML using legacy renderers
    html = legacy.render_html.__wrapped__(ctx, hero_image_path) if hasattr(legacy.render_html, '__wrapped__') else _render_legacy_html(legacy, ctx, hero_image_path)
    md = legacy.render_markdown(ctx)
    version_log = legacy.render_version_log(ctx)
    return html, md, version_log


def _render_legacy_html(legacy, ctx: dict, hero_image_path: Path | None) -> str:
    """Call the legacy render pipeline, but inject our profile-driven JSON-LD."""
    template = load_template("core-30-page.html.tmpl")
    ctx["hero_image_block"] = legacy.render_hero_image_block(ctx, hero_image_path)
    ctx["what_it_means_paragraphs_html"] = legacy.render_what_it_means_paragraphs(ctx)
    ctx["quick_ref_items_html"] = legacy.render_quick_ref_items(ctx)
    ctx["pattern_cards_html"] = legacy.render_pattern_cards(ctx)
    ctx["problem_cards_html"] = legacy.render_problem_cards(ctx)
    ctx["process_steps_html"] = legacy.render_process_steps(ctx)
    ctx["pricing_items_html"] = legacy.render_pricing_items(ctx)
    ctx["about_text_paragraphs_html"] = legacy.render_about_paragraphs(ctx)
    ctx["neighborhoods_list_html"] = legacy.render_neighborhoods_list(ctx)
    ctx["related_cards_html"] = legacy.render_related_cards(ctx)
    ctx["faq_items_html"] = legacy.render_faq_items(ctx)
    ctx["map_iframe_html"] = legacy.get_map_iframe_html(ctx)
    # jsonld already set by caller from profile-driven builder
    html = template.format_map(ctx)
    legacy._validate_internal_links(html, ctx.get("client_slug", ""))
    return html


# ============================================================================
# Fixed-list HTML renderer — profile-driven, layout-based
# ============================================================================

def render_fixed_list_page(page: dict, ctx: dict, jsonld: str,
                           profile: dict, client: dict) -> str:
    """Render a fixed-list page from profile-declared sections.

    Sections are rendered by LAYOUT TYPE, not by section ID. The profile
    declares each section's layout + data_source; the engine renders the
    layout generically. No business-type literals.
    """
    title = page["title_template"].format_map(ctx)
    sections_html = []
    section_defs = profile["content_sections"]["sections"]
    btype = client["business_type"]
    type_section = client.get(btype, {})
    prefix = ctx.get("css_prefix", "pg")

    for section_id in page.get("sections", []):
        section_def = section_defs.get(section_id, {})
        html = _render_section_by_layout(section_id, section_def, ctx, client,
                                         type_section, prefix)
        sections_html.append(html)

    meta_desc = ctx.get("meta_description", title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <meta name="description" content="{meta_desc}">
    <!-- PLACEHOLDER: All content on this page is researched/placeholder. NOT real client-confirmed data. -->
    <!-- ACCESS-GATED: Live publish requires domain + hosting + client sign-off -->
    <script type="application/ld+json">
{jsonld}
    </script>
    <style>
        :root {{
            --primary: {ctx.get('primary_color', '#333')};
            --dark: {ctx.get('navy', '#111')};
            --accent: {ctx.get('accent_yellow', '#f5b400')};
            --heading: {ctx.get('heading_color', '#111')};
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; color: #333; line-height: 1.6; }}
        .{prefix}-section {{ padding: 3rem 1.5rem; max-width: 900px; margin: 0 auto; }}
        .{prefix}-hero {{ background: linear-gradient(135deg, {ctx.get('hero_gradient_dark', '#111')}, {ctx.get('hero_gradient_mid', '#333')}); color: white; padding: 4rem 1.5rem; text-align: center; }}
        .{prefix}-hero h1 {{ font-size: 2.5rem; margin-bottom: 0.5rem; }}
        .{prefix}-hero p {{ font-size: 1.2rem; opacity: 0.9; }}
        .{prefix}-cta {{ display: inline-block; padding: 0.75rem 1.5rem; margin: 0.5rem; border-radius: 6px; text-decoration: none; font-weight: 600; }}
        .{prefix}-cta-primary {{ background: var(--accent); color: var(--dark); }}
        .{prefix}-cta-secondary {{ background: transparent; color: white; border: 2px solid white; }}
        h2 {{ color: var(--heading); margin-bottom: 1rem; }}
        .{prefix}-card {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 1.5rem; margin: 1rem 0; }}
        .{prefix}-data-row {{ display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px dotted #ccc; }}
        .{prefix}-hours-table {{ width: 100%; border-collapse: collapse; }}
        .{prefix}-hours-table td {{ padding: 0.5rem; border-bottom: 1px solid #eee; }}
        .{prefix}-placeholder {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; margin: 1rem 0; font-style: italic; }}
    </style>
</head>
<body>
    <div class="{prefix}-placeholder">
        PLACEHOLDER CONTENT — scaffolded from config + profile for dry-run validation.
        All content is researched/placeholder, NOT confirmed by client. Do not publish.
    </div>

{''.join(sections_html)}

    <footer class="{prefix}-section" style="text-align:center; color:#666; font-size:0.9rem;">
        <p>&copy; {ctx.get('today', '')[:4]} {ctx.get('client_name', '')} &middot; {ctx.get('city_name', '')}, {ctx.get('city_state', '')}</p>
        <!-- ACCESS-GATED: Real footer needs domain, legal pages, social links from client -->
    </footer>
</body>
</html>"""


def _render_section_by_layout(section_id: str, section_def: dict, ctx: dict,
                               client: dict, type_section: dict, prefix: str) -> str:
    """Render a section by its declared layout/type. No business-type literals.

    Layout dispatch is on structural patterns (hero, card-grid, data-rows,
    hours-table, content-block, cta-block, contact-block), not on section IDs.
    """
    # Determine the layout from section definition
    layout = section_def.get("layout", "")
    has_cta = "cta" in section_def or "cta_primary" in section_def
    data_source = section_def.get("data_source", "")
    heading_key = section_def.get("heading_template", "")
    body_type = section_def.get("body", "")
    includes = section_def.get("includes", [])

    # Hero sections: detected by section_id ending in "-hero" or role
    if section_id == "hero" or section_id.endswith("-hero"):
        heading = ctx.get(section_id.replace("-", "_") + "_heading",
                          ctx.get("hero_heading", ctx.get("client_name", "")))
        # Try to find a matching rendered heading token
        for key_candidate in [section_id.replace("-", "_") + "_heading",
                              section_id.split("-")[0] + "_heading",
                              "hero_heading"]:
            if key_candidate in ctx and ctx[key_candidate]:
                heading = ctx[key_candidate]
                break
        sub = ctx.get("hero_subheading", "")
        ctas = _render_cta_buttons(section_def, ctx, prefix)
        return f"""
    <section class="{prefix}-hero">
        <h1>{heading}</h1>
        {"<p>" + sub + "</p>" if sub and section_id == "hero" else ""}
        {f'<div style="margin-top:1.5rem">{ctas}</div>' if ctas else ""}
    </section>
"""

    # Data-row layout (e.g., structured items with label + value)
    if layout in ("accordion-or-table", "data-rows"):
        heading = _resolve_heading(section_def, ctx)
        items_data = _get_data_source(data_source, type_section, client)
        rows = ""
        if isinstance(items_data, list):
            for group in items_data:
                if isinstance(group, dict) and "items" in group:
                    rows += f'<h3>{group.get("name", "")}</h3>\n'
                    rows += f'<div class="{prefix}-placeholder">PLACEHOLDER data — real content needed from client</div>\n'
                    for item in group.get("items", []):
                        desc = item.get("description", "")
                        price = item.get("price", "")
                        rows += f'<div class="{prefix}-data-row"><span><strong>{item.get("name", "")}</strong> — {desc}</span><span>{price}</span></div>\n'
        return f"""
    <section class="{prefix}-section">
        {"<h2>" + heading + "</h2>" if heading else ""}
        {rows}
    </section>
"""

    # Hours table
    if layout == "day-by-day-table" or data_source == "hours":
        hours = client.get("hours", [])
        rows = ""
        for h in hours:
            days = ", ".join(h["days"])
            rows += f'<tr><td>{days}</td><td>{h["opens"]} - {h["closes"]}</td></tr>\n'
        return f"""
    <section class="{prefix}-section">
        <h2>{_resolve_heading(section_def, ctx) or "Hours"}</h2>
        <div class="{prefix}-placeholder">PLACEHOLDER hours — verify with client</div>
        <table class="{prefix}-hours-table">{rows}</table>
    </section>
"""

    # Card grid
    if layout == "card-grid":
        heading = _resolve_heading(section_def, ctx)
        items = _get_data_source(data_source, type_section, client)
        cards = ""
        if isinstance(items, list):
            for item in items:
                label = item if isinstance(item, str) else item.get("name", str(item))
                cards += f'<div class="{prefix}-card"><strong>{label}</strong><br><!-- PLACEHOLDER: details --></div>\n'
        return f"""
    <section class="{prefix}-section">
        {"<h2>" + heading + "</h2>" if heading else ""}
        {cards if cards else f'<div class="{prefix}-placeholder">PLACEHOLDER: data for card grid</div>'}
    </section>
"""

    # CTA block
    if has_cta and not layout:
        heading = _resolve_heading(section_def, ctx)
        cta_html = _render_cta_buttons(section_def, ctx, prefix)
        if not cta_html:
            return f"""
    <section class="{prefix}-section">
        <div class="{prefix}-placeholder">PLACEHOLDER: CTA not yet configured. ACCESS-GATED.</div>
    </section>
"""
        return f"""
    <section class="{prefix}-section">
        {"<h2>" + heading + "</h2>" if heading else ""}
        {cta_html}
    </section>
"""

    # Contact block
    if includes and any(x in includes for x in ("phone", "email", "address", "map-embed", "hours-summary")):
        parts = []
        addr = client.get("address", {})
        if "address" in includes or "map-embed" in includes:
            parts.append(f'<p>{addr.get("street", "")}, {addr.get("locality", "")}, {addr.get("region", "")} {addr.get("postal_code", "")}</p>')
        if "phone" in includes:
            parts.append(f'<p>Phone: <a href="tel:{ctx.get("phone_tel", "")}">{ctx.get("phone_display", "")}</a></p>')
        if "email" in includes:
            parts.append(f'<p>Email: <a href="mailto:{ctx.get("email", "")}">{ctx.get("email", "")}</a></p>')
        if "map-embed" in includes:
            parts.append(f'<div class="{prefix}-placeholder">PLACEHOLDER: Map embed — ACCESS-GATED on API key</div>')
        if "hours-summary" in includes:
            hours = client.get("hours", [])
            for h in hours:
                parts.append(f'<p>{", ".join(h["days"])}: {h["opens"]} - {h["closes"]}</p>')
        return f"""
    <section class="{prefix}-section">
        <h2>{_resolve_heading(section_def, ctx) or "Contact"}</h2>
        {"".join(parts)}
    </section>
"""

    # Content block (authored content placeholder)
    if body_type == "content-block":
        heading = _resolve_heading(section_def, ctx)
        # Check if there's a data_source with actual content
        content = ""
        if data_source:
            data = _get_data_source(data_source, type_section, client)
            if isinstance(data, list):
                content = "\n".join(f"<p>{p}</p>" for p in data if isinstance(p, str))
            elif isinstance(data, str):
                content = f"<p>{data}</p>"
        return f"""
    <section class="{prefix}-section">
        {"<h2>" + heading + "</h2>" if heading else ""}
        {content if content else ""}
        <div class="{prefix}-placeholder">PLACEHOLDER: authored content — to be written with client input. ACCESS-GATED.</div>
    </section>
"""

    # Fallback: generic section
    heading = _resolve_heading(section_def, ctx)
    return f"""
    <section class="{prefix}-section">
        {"<h2>" + heading + "</h2>" if heading else ""}
        <div class="{prefix}-placeholder">PLACEHOLDER: section content TBD</div>
    </section>
"""


def _resolve_heading(section_def: dict, ctx: dict) -> str:
    """Resolve a section heading from the definition."""
    tmpl = section_def.get("heading_template", "")
    if tmpl:
        try:
            return tmpl.format_map(ctx)
        except KeyError:
            return tmpl
    return ""


def _get_data_source(data_source: str, type_section: dict, client: dict) -> Any:
    """Resolve a data_source path to actual data."""
    if not data_source:
        return []
    # Strip annotations like "[*].items (flagged as featured)"
    clean = re.sub(r'\[.*?\]|\(.*?\)', '', data_source).strip()
    parts = clean.split(".")
    if len(parts) >= 2:
        prefix = parts[0]
        remainder = parts[1:]
        if prefix == "root":
            obj = client
        elif prefix == client.get("business_type", ""):
            obj = type_section
        else:
            obj = type_section if parts[0] in type_section else client
            remainder = parts if parts[0] in type_section else parts
        for key in remainder:
            if isinstance(obj, dict) and key in obj:
                obj = obj[key]
            else:
                return []
        return obj
    if data_source in type_section:
        return type_section[data_source]
    if data_source in client:
        return client[data_source]
    return []


def _render_cta_buttons(section_def: dict, ctx: dict, prefix: str) -> str:
    """Render CTA buttons from section definition."""
    parts = []
    for cta_key in ("cta_primary", "cta", "cta_secondary"):
        cta = section_def.get(cta_key, {})
        if not cta:
            continue
        label = cta.get("label", "")
        target_path = cta.get("target", "")
        url = ""
        if target_path:
            url = ctx.get(target_path.split(".")[-1], "")
        if url:
            cls = f"{prefix}-cta-primary" if "primary" in cta_key or cta_key == "cta" else f"{prefix}-cta-secondary"
            parts.append(f'<a href="{url}" class="{prefix}-cta {cls}">{label}</a>')
    if not parts:
        phone = ctx.get("phone_tel", "")
        display = ctx.get("phone_display", "")
        if phone:
            parts.append(f'<a href="tel:{phone}" class="{prefix}-cta {prefix}-cta-primary">Call {display}</a>')
    return "\n".join(parts)


# ============================================================================
# Main — unified entry point
# ============================================================================

def main() -> int:
    p = argparse.ArgumentParser(description="Unified profile-driven page scaffolder.")
    p.add_argument("--client", required=True, help='Client slug (e.g. "ev-electric-services" or "asian-delight")')
    p.add_argument("--service", default=None, help='Service slug (matrix mode only)')
    p.add_argument("--city", default=None, help='City slug (matrix mode only)')
    p.add_argument("--position", type=int, default=0, help="Build-order position (matrix mode)")
    p.add_argument("--dry-run", action="store_true", help="Render but don't write files")
    p.add_argument("--output-folder", type=Path, default=None, help="Override output folder")
    p.add_argument("--hero-image-path", type=Path, default=None, help="Local hero image for orientation detection")
    p.add_argument("--skip-validation", action="store_true", help="Skip config validation for WIP configs")
    args = p.parse_args()

    client = load_client(args.client)
    btype = client.get("business_type")
    if not btype:
        sys.stderr.write(f"ERROR: client config missing 'business_type' field.\n")
        sys.exit(2)

    profile = load_profile(btype)
    page_model = profile["page_model"]

    sys.stderr.write(f"-> Client:   {args.client} ({client['name']})\n")
    sys.stderr.write(f"-> Type:     {btype}\n")
    sys.stderr.write(f"-> Profile:  {page_model['page_model_name']}\n")

    # Validate config against profile schema
    if not args.skip_validation:
        config_schema = profile["config_schema"]
        missing = []
        for field_path in config_schema.get("required_type_fields", {}):
            parts = field_path.split(".")
            obj = client
            try:
                for pt in parts:
                    obj = obj[pt]
            except (KeyError, TypeError):
                missing.append(field_path)
        if missing:
            sys.stderr.write(f"WARNING: Missing required fields: {missing}\n")
            sys.stderr.write("  Use --skip-validation for WIP configs.\n")

    # Dispatch based on page generation mode
    if page_model.get("page_generation") == "matrix":
        return _run_matrix_mode(args, client, profile, page_model)
    else:
        return _run_fixed_list_mode(args, client, profile, page_model)


def _run_matrix_mode(args, client: dict, profile: dict, page_model: dict) -> int:
    """Matrix mode: requires --service, --city, --position. Renders one page."""
    if not args.service or not args.city:
        sys.stderr.write("ERROR: matrix mode requires --service and --city.\n")
        sys.exit(2)

    service = load_service(args.service, client_slug=args.client)
    city = load_city(args.city, client_slug=args.client)

    sys.stderr.write(f"-> Service:  {args.service} ({service['name']})\n")
    sys.stderr.write(f"-> City:     {args.city} ({city['name_with_state']})\n")
    sys.stderr.write(f"-> Position: {args.position}\n")

    # Build context using legacy build_context for HTML regression
    legacy = _import_legacy_engine()
    if legacy is None:
        sys.stderr.write("ERROR: legacy engine (scaffold-core-30-page.py) required for matrix mode.\n")
        sys.exit(2)

    ctx = legacy.build_context(client, service, city, args.position)

    # Build JSON-LD from profile (the unified path)
    profile_ctx = build_context(client, profile, service=service, city=city,
                                page_slug=ctx["page_slug"], position=args.position)
    ctx["jsonld"] = build_jsonld(client, profile, profile_ctx,
                                 page_slug="_matrix_page",
                                 service=service, city=city)

    sys.stderr.write(f"-> Page URL: {ctx['page_url']}\n")

    # Render HTML using legacy renderers (regression)
    html = _render_legacy_html(legacy, ctx, args.hero_image_path)
    md = legacy.render_markdown(ctx)
    version_log = legacy.render_version_log(ctx)

    if args.dry_run:
        sys.stderr.write(f"\nDRY RUN — no files written.\n")
        sys.stderr.write(f"  HTML size: {len(html):,} chars\n")
        sys.stderr.write(f"  MD size:   {len(md):,} chars\n")
        return 0

    folder = args.output_folder or legacy.find_page_folder(client["client_slug"], ctx["page_slug"], args.position)
    if folder.exists() and any(folder.iterdir()):
        sys.stderr.write(f"\nWARNING: folder already has contents: {folder}\nRefusing to overwrite.\n")
        return 3

    legacy.write_outputs(folder, html, md, version_log)
    sys.stderr.write(f"\n-> Wrote: {folder}\n")
    legacy.emit_quality_loop_invoke_block(folder)
    return 0


def _run_fixed_list_mode(args, client: dict, profile: dict, page_model: dict) -> int:
    """Fixed-list mode: generates all pages declared in the profile."""
    pages = page_model["pages"]
    out_dir = args.output_folder or OUTPUT_DIR / args.client
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "client": args.client,
        "business_type": client["business_type"],
        "profile": page_model["page_model_name"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_status": "placeholder",
        "pages": [],
    }

    for page in pages:
        slug = page["slug"]
        ctx = build_context(client, profile, page_slug=slug)
        jsonld = build_jsonld(client, profile, ctx, page_slug=slug)
        html = render_fixed_list_page(page, ctx, jsonld, profile, client)

        if not args.dry_run:
            (out_dir / f"{slug}.html").write_text(html, encoding="utf-8")
            (out_dir / f"{slug}-schema.json").write_text(jsonld, encoding="utf-8")

        manifest["pages"].append({
            "slug": slug, "role": page["role"],
            "title": page["title_template"].format_map(ctx),
        })
        sys.stderr.write(f"  -> {slug}.html + {slug}-schema.json\n")

    if args.dry_run:
        sys.stderr.write(f"\nDRY RUN — {len(pages)} pages rendered, no files written.\n")
    else:
        (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        sys.stderr.write(f"\n{len(pages)} pages scaffolded to {out_dir}/\n")

    sys.stderr.write("Content status: PLACEHOLDER — not client-confirmed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
