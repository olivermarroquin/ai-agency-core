#!/usr/bin/env python3
"""
client-schema-sync engine — agnostic, config-driven WordPress JSON-LD schema
synchronization.

Given a client slug + schema-sync profile, this engine:
  1. Detects the render store per page (content.raw vs Elementor _kw_jsonld vs SEO-plugin head)
  2. Verifies the three core entities (LocalBusiness/subtype + Service + FAQPage)
  3. Syncs AggregateRating to a provided live count (human-in-loop read)
  4. Adds LocalBusiness to non-scaffolded / Elementor pages via keelworks-jsonld-head plugin
  5. Detects duplicate SEO-plugin org schema
  6. Injects HowTo (only on pages with genuinely visible process steps)
  7. Injects SpeakableSpecification
  8. Verifies live, cache-busted, reports "X of Y", emits a gate verdict

All client-specific values come from:
  - repos/ai-agency-core/scripts/data/client-<slug>.json  (business facts)
  - skills/client-schema-sync/references/profiles/<slug>.json  (schema-sync config)

Zero hardcoded client values, slugs, NAP, counts, or domains in this engine.

Safety rules baked in:
  - safe_json_for_script(): replaces </ with <\\/ in all JSON placed inside <script>
    tags, preventing </script> breakout (the standard web-safe JSON-in-HTML approach,
    equivalent to PHP's JSON_HEX_TAG)
  - Live-not-vault cache-busted verification
  - skip_widget_patterns: content sweeps skip elements matching configured patterns
    (e.g. ti-review-* customer-review widgets)
  - WAF workaround: curl subprocess for all WP REST calls (not urllib/requests)

Lifted + generalized from:
  - repos/ev-electric-services/.kos/scripts/ev_schema_inject_v2.py
  - repos/ev-electric-services/.kos/scripts/ev_schema_audit.py
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent  # ~/workspace
DATA_DIR = Path(__file__).resolve().parent / "data"
PROFILES_DIR = WORKSPACE / "skills" / "client-schema-sync" / "references" / "profiles"

# Schema types we track in audits
AUDIT_TYPES = [
    "LocalBusiness", "Electrician", "Restaurant", "Service", "FAQPage",
    "AggregateRating", "BreadcrumbList", "HowTo", "SpeakableSpecification",
    "Review", "GeoCoordinates", "OpeningHoursSpecification", "WebSite",
    "WebPage", "Organization", "Person",
]


# ---------------------------------------------------------------------------
# Safety: JSON-in-HTML escaping (BLOCKING-1 fix)
# ---------------------------------------------------------------------------
def safe_json_for_script(json_str):
    """
    Make a JSON string safe for embedding inside <script> tags.
    Replaces </ with <\\/ to prevent </script> breakout — the standard
    web-safe JSON-in-HTML approach (equivalent to PHP's JSON_HEX_TAG).
    """
    return json_str.replace("</", "<\\/")


# ---------------------------------------------------------------------------
# Safety: skip-widget filtering (BLOCKING-2 fix)
# ---------------------------------------------------------------------------
def filter_skip_widgets(html_content, skip_patterns):
    """
    Remove HTML elements whose class matches any skip_widget_patterns glob.
    E.g., skip_patterns=["ti-review-*"] removes <div class="ti-review-widget">...</div>.
    Uses a nesting-aware approach to avoid eating sibling elements.
    Returns filtered HTML content.
    """
    if not skip_patterns:
        return html_content
    for pattern in skip_patterns:
        # Convert glob to class-safe regex: ti-review-* -> ti\-review\-[^"]*
        # Manual glob-to-regex (fnmatch.translate's (?s:.*) eats past the quote boundary)
        regex_pattern = re.escape(pattern).replace(r"\*", r'[^"]*').replace(r"\?", r'[^"]')
        # Find opening tags with matching class and remove the element
        # by tracking nesting depth of the same tag type
        open_re = re.compile(
            r'<(div|section|aside|span)([^>]*class="[^"]*' + regex_pattern + r'[^"]*"[^>]*)>',
            re.IGNORECASE,
        )
        while True:
            m = open_re.search(html_content)
            if not m:
                break
            tag = m.group(1).lower()
            start = m.start()
            # Walk forward, tracking nesting depth of same tag type
            depth = 1
            pos = m.end()
            open_tag_re = re.compile(r'<' + tag + r'[\s>]', re.IGNORECASE)
            close_tag_re = re.compile(r'</' + tag + r'>', re.IGNORECASE)
            end = len(html_content)
            while pos < len(html_content) and depth > 0:
                next_open = open_tag_re.search(html_content, pos)
                next_close = close_tag_re.search(html_content, pos)
                if next_close is None:
                    break
                if next_open and next_open.start() < next_close.start():
                    depth += 1
                    pos = next_open.end()
                else:
                    depth -= 1
                    if depth == 0:
                        end = next_close.end()
                    pos = next_close.end()
            html_content = html_content[:start] + html_content[end:]
    return html_content


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_client_config(client_slug):
    """Load client facts from data/client-<slug>.json."""
    path = DATA_DIR / f"client-{client_slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Client config not found: {path}")
    with open(path) as f:
        return json.load(f)


def load_sync_profile(client_slug):
    """Load schema-sync profile from skills/client-schema-sync/references/profiles/<slug>.json."""
    path = PROFILES_DIR / f"{client_slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema-sync profile not found: {path}")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# WP REST via curl (WAF-safe)
# ---------------------------------------------------------------------------
def _curl_auth_args(profile):
    """Build curl auth args from profile."""
    wp_user = profile["wordpress"]["api_user"]
    wp_pass = os.environ.get(profile["wordpress"]["api_password_env"], "")
    if not wp_pass:
        raise EnvironmentError(
            f"WP app password env var '{profile['wordpress']['api_password_env']}' is not set"
        )
    return ["-u", f"{wp_user}:{wp_pass}"]


def wp_get_page(page_id, profile, fields="id,slug,content,meta"):
    """GET a WP page via curl subprocess (avoids WAF 403)."""
    domain = profile["wordpress"]["domain"]
    auth = _curl_auth_args(profile)
    url = f"https://{domain}/wp-json/wp/v2/pages/{page_id}?context=edit&_fields={fields}"
    r = subprocess.run(
        ["curl", "-s", *auth, url],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def wp_put_page(page_id, payload_dict, profile):
    """POST (update) a WP page via curl subprocess."""
    domain = profile["wordpress"]["domain"]
    auth = _curl_auth_args(profile)
    url = f"https://{domain}/wp-json/wp/v2/pages/{page_id}"
    payload = json.dumps(payload_dict)
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", *auth,
         "-H", "Content-Type: application/json",
         "--data-binary", "@-", url],
        input=payload, capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    try:
        resp = json.loads(r.stdout)
        return resp.get("id")
    except json.JSONDecodeError:
        return None


def wp_get_meta(page_id, meta_key, profile):
    """GET a specific post meta value."""
    page = wp_get_page(page_id, profile, fields="id,meta")
    if not page:
        return None
    meta = page.get("meta", {})
    return meta.get(meta_key)


def wp_put_meta(page_id, meta_key, meta_value, profile):
    """Update a specific post meta value via WP REST."""
    return wp_put_page(page_id, {"meta": {meta_key: meta_value}}, profile)


# ---------------------------------------------------------------------------
# JSON-LD extraction + manipulation
# ---------------------------------------------------------------------------
def extract_jsonld_blocks(html_content):
    """Extract all JSON-LD blocks from HTML string."""
    pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)
    blocks = []
    for m in matches:
        try:
            blocks.append(json.loads(m))
        except json.JSONDecodeError:
            blocks.append({"_parse_error": m[:200]})
    return blocks


def collect_types(obj, found=None):
    """Recursively collect all @type values from a JSON-LD object tree."""
    if found is None:
        found = {}
    if isinstance(obj, dict):
        if "@type" in obj:
            t = obj["@type"]
            types = t if isinstance(t, list) else [t]
            for tp in types:
                if tp not in found:
                    found[tp] = []
                info = {}
                for key in ("ratingValue", "reviewCount", "ratingCount", "name", "@id"):
                    if key in obj:
                        info[key] = str(obj[key])[:80]
                found[tp].append(info)
        if "@graph" in obj:
            for item in obj["@graph"]:
                collect_types(item, found)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                collect_types(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_types(item, found)
    return found


def count_jsonld_tags(html_content):
    """Count ld+json script tags accurately (grep -o equivalent, not grep -c)."""
    return len(re.findall(r'application/ld\+json', html_content, re.IGNORECASE))


def get_graph(jsonld_data):
    """Extract @graph array from a JSON-LD object, creating one if needed."""
    if isinstance(jsonld_data, dict) and "@graph" in jsonld_data:
        return jsonld_data["@graph"]
    return []


def has_type_in_graph(graph, type_name):
    """Check if a @type exists in a flat @graph array."""
    for node in graph:
        t = node.get("@type", "")
        if isinstance(t, list):
            if type_name in t:
                return True
        elif t == type_name:
            return True
    return False


def ensure_graph_wrapper(jsonld_data):
    """Ensure JSON-LD data has an @graph wrapper. Handles flat Elementor JSON-LD."""
    if isinstance(jsonld_data, dict) and "@graph" in jsonld_data:
        return jsonld_data
    if isinstance(jsonld_data, list):
        return {"@context": "https://schema.org", "@graph": jsonld_data}
    if isinstance(jsonld_data, dict):
        return {"@context": "https://schema.org", "@graph": [jsonld_data]}
    return {"@context": "https://schema.org", "@graph": []}


# ---------------------------------------------------------------------------
# Render store detection
# ---------------------------------------------------------------------------
def detect_render_store(page_data):
    """
    Detect where schema lives for this page.
    Returns: 'content_raw' | 'elementor_kw_jsonld' | 'unknown'
    """
    meta = page_data.get("meta", {})
    elementor_mode = meta.get("_elementor_edit_mode", "")
    if elementor_mode == "builder":
        return "elementor_kw_jsonld"
    return "content_raw"


# ---------------------------------------------------------------------------
# Step 1: Audit — detect render store + count entities per page
# ---------------------------------------------------------------------------
def audit_page_live(url, nocache_ts=None):
    """Fetch a live page via curl and audit its JSON-LD."""
    if nocache_ts is None:
        nocache_ts = str(int(time.time()))
    cache_busted_url = f"{url}?nocache={nocache_ts}"
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "15", cache_busted_url],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        return {"url": url, "status": "FETCH_ERROR", "error": r.stderr[:200]}

    html_content = r.stdout
    ld_count = count_jsonld_tags(html_content)
    blocks = extract_jsonld_blocks(html_content)
    all_types = {}
    for b in blocks:
        collect_types(b, all_types)

    return {
        "url": url,
        "status": "OK",
        "ld_block_count": ld_count,
        "parsed_blocks": len(blocks),
        "types_found": all_types,
    }


def audit_all_pages(profile):
    """Audit all pages defined in the profile."""
    pages = profile.get("pages", [])
    nocache_ts = str(int(time.time()))
    results = []
    for page in pages:
        url = page["url"]
        page_class = page.get("class", "unknown")
        print(f"  Auditing {page_class}: {url}...", end=" ", flush=True)
        result = audit_page_live(url, nocache_ts)
        result["page_class"] = page_class
        result["page_id"] = page.get("wp_id")
        results.append(result)
        if result["status"] == "OK":
            types = list(result["types_found"].keys())
            print(f"OK — {len(types)} types: {', '.join(types[:5])}")
        else:
            print(f"ERROR: {result.get('error', result['status'])}")
    return results


# ---------------------------------------------------------------------------
# Step 2: Verify three core entities
# ---------------------------------------------------------------------------
def verify_core_entities(audit_result, profile):
    """
    Check that LocalBusiness (or subtype), Service, and FAQPage are present.
    Returns a list of findings (missing entities).
    """
    findings = []
    types = audit_result.get("types_found", {})
    page_class = audit_result.get("page_class", "")

    # Determine the expected business type from profile
    business_type = profile.get("schema_config", {}).get("business_type_schema", "LocalBusiness")

    # LocalBusiness / subtype check
    has_business = business_type in types or "LocalBusiness" in types
    if not has_business:
        findings.append({
            "type": "MISSING_ENTITY",
            "entity": business_type,
            "severity": "BLOCKING",
            "page": audit_result["url"],
        })

    # Service check (only on service pages, not core pages)
    if page_class not in ("core-home", "core-about", "core-contact", "core-services"):
        if "Service" not in types:
            findings.append({
                "type": "MISSING_ENTITY",
                "entity": "Service",
                "severity": "BLOCKING",
                "page": audit_result["url"],
            })

    # FAQPage check (only where expected — leaf and hub pages)
    if page_class in ("leaf", "hub") or page_class.endswith("-hub"):
        if "FAQPage" not in types:
            findings.append({
                "type": "MISSING_ENTITY",
                "entity": "FAQPage",
                "severity": "ADVISORY",
                "page": audit_result["url"],
            })

    return findings


# ---------------------------------------------------------------------------
# Step 3: AggregateRating sync
# ---------------------------------------------------------------------------
def sync_aggregate_rating(page_id, live_rating, live_count, profile, dry_run=True):
    """
    Sync AggregateRating to the live Google review count on a single page.
    live_rating and live_count come from a human-in-loop GBP read.

    Returns a result dict.
    """
    page = wp_get_page(page_id, profile)
    if not page:
        return {"page_id": page_id, "status": "FETCH_ERROR"}

    store = detect_render_store(page)
    slug = page.get("slug", "?")

    if store == "content_raw":
        content = page["content"]["raw"]
        return _sync_rating_in_content(
            page_id, slug, content, live_rating, live_count, profile, dry_run
        )
    elif store == "elementor_kw_jsonld":
        return _sync_rating_in_kw_jsonld(
            page_id, slug, live_rating, live_count, profile, dry_run
        )
    else:
        return {"page_id": page_id, "slug": slug, "status": "UNKNOWN_STORE"}


def _sync_rating_in_content(page_id, slug, content, rating, count, profile, dry_run):
    """Update AggregateRating in content.raw JSON-LD."""
    ld_match = re.search(
        r'(<script[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
        content, re.DOTALL,
    )
    if not ld_match:
        return {"page_id": page_id, "slug": slug, "status": "NO_JSONLD"}

    try:
        graph_data = json.loads(ld_match.group(2))
    except json.JSONDecodeError:
        return {"page_id": page_id, "slug": slug, "status": "JSON_PARSE_ERROR"}

    graph = get_graph(graph_data)
    updated = False
    for node in graph:
        ar = node.get("aggregateRating")
        if ar and ar.get("@type") == "AggregateRating":
            old_rating = ar.get("ratingValue")
            old_count = ar.get("reviewCount")
            if str(old_rating) != str(rating) or str(old_count) != str(count):
                ar["ratingValue"] = str(rating)
                ar["reviewCount"] = str(count)
                updated = True

    if not updated:
        return {"page_id": page_id, "slug": slug, "status": "ALREADY_CURRENT",
                "rating": rating, "count": count}

    graph_data["@graph"] = graph
    new_jsonld = safe_json_for_script(json.dumps(graph_data, ensure_ascii=False))
    new_script = f'{ld_match.group(1)}{new_jsonld}{ld_match.group(3)}'
    new_content = content[:ld_match.start()] + new_script + content[ld_match.end():]

    if dry_run:
        return {"page_id": page_id, "slug": slug, "status": "DRY_RUN_WOULD_UPDATE",
                "rating": rating, "count": count}

    result_id = wp_put_page(page_id, {"content": new_content}, profile)
    if result_id:
        return {"page_id": page_id, "slug": slug, "status": "UPDATED",
                "rating": rating, "count": count}
    return {"page_id": page_id, "slug": slug, "status": "UPDATE_FAILED"}


def _sync_rating_in_kw_jsonld(page_id, slug, rating, count, profile, dry_run):
    """Update AggregateRating in _kw_jsonld meta (Elementor pages)."""
    raw_meta = wp_get_meta(page_id, "_kw_jsonld", profile)
    if not raw_meta:
        return {"page_id": page_id, "slug": slug, "status": "NO_KW_JSONLD"}

    try:
        jsonld_data = json.loads(raw_meta)
    except (json.JSONDecodeError, TypeError):
        return {"page_id": page_id, "slug": slug, "status": "KW_JSONLD_PARSE_ERROR"}

    # Deep copy to avoid circular ref with Elementor flat JSON-LD
    jsonld_data = copy.deepcopy(jsonld_data)
    wrapped = ensure_graph_wrapper(jsonld_data)
    graph = wrapped.get("@graph", [])

    updated = False
    for node in graph:
        ar = node.get("aggregateRating")
        if ar and ar.get("@type") == "AggregateRating":
            old_rating = ar.get("ratingValue")
            old_count = ar.get("reviewCount")
            if str(old_rating) != str(rating) or str(old_count) != str(count):
                ar["ratingValue"] = str(rating)
                ar["reviewCount"] = str(count)
                updated = True

    if not updated:
        return {"page_id": page_id, "slug": slug, "status": "ALREADY_CURRENT",
                "rating": rating, "count": count}

    # Re-serialize — keep original shape (flat or @graph) for _kw_jsonld
    new_meta = safe_json_for_script(json.dumps(wrapped, ensure_ascii=False))

    if dry_run:
        return {"page_id": page_id, "slug": slug, "status": "DRY_RUN_WOULD_UPDATE",
                "rating": rating, "count": count}

    result_id = wp_put_meta(page_id, "_kw_jsonld", new_meta, profile)
    if result_id:
        return {"page_id": page_id, "slug": slug, "status": "UPDATED",
                "rating": rating, "count": count}
    return {"page_id": page_id, "slug": slug, "status": "UPDATE_FAILED"}


# ---------------------------------------------------------------------------
# Step 4: Add LocalBusiness via _kw_jsonld (Elementor pages)
# ---------------------------------------------------------------------------
def build_local_business_node(client_config, profile):
    """Build a LocalBusiness JSON-LD node from client config."""
    schema_cfg = profile.get("schema_config", {})
    biz_type = schema_cfg.get("business_type_schema", "LocalBusiness")
    domain = profile["wordpress"]["domain"]
    biz_id = f"https://{domain}/#business"

    node = {
        "@type": biz_type,
        "@id": biz_id,
        "name": client_config["name"],
        "alternateName": client_config.get("alternate_name", ""),
        "description": client_config.get("business_description_schema", ""),
        "url": client_config["website_url"],
        "telephone": client_config["phone_e164"],
        "email": client_config.get("email", ""),
        "image": client_config.get("brand_image_url", ""),
        "logo": client_config.get("brand_logo_url", ""),
        "priceRange": client_config.get("price_range_token", ""),
        "address": {
            "@type": "PostalAddress",
            "streetAddress": client_config["address"]["street"],
            "addressLocality": client_config["address"]["locality"],
            "addressRegion": client_config["address"]["region"],
            "postalCode": client_config["address"]["postal_code"],
            "addressCountry": client_config["address"]["country"],
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": client_config["geo"]["latitude"],
            "longitude": client_config["geo"]["longitude"],
        },
    }

    # Opening hours
    hours_spec = []
    for h in client_config.get("hours", []):
        hours_spec.append({
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": h["days"],
            "opens": h["opens"],
            "closes": h["closes"],
        })
    if hours_spec:
        node["openingHoursSpecification"] = hours_spec

    # Area served
    areas = client_config.get("brand_areas_served", [])
    if areas:
        node["areaServed"] = [{"@type": "City", "name": a} for a in areas]

    # AggregateRating (only if real reviews exist)
    review_count = client_config.get("review_count", 0)
    review_rating = client_config.get("review_rating", "")
    if review_count and review_rating:
        node["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(review_rating),
            "reviewCount": str(review_count),
            "bestRating": "5",
            "worstRating": "1",
        }

    # Business-type-specific fields
    biz_type_key = client_config.get("business_type", "")
    type_data = client_config.get(biz_type_key, {})

    # License / credential (electrician)
    license_data = type_data.get("license") or client_config.get("license")
    if license_data:
        node["hasCredential"] = {
            "@type": "EducationalOccupationalCredential",
            "credentialCategory": license_data.get("category", ""),
            "recognizedBy": {
                "@type": "Organization",
                "name": license_data.get("issuer", ""),
            },
        }

    # Founder
    if client_config.get("owner_name"):
        node["founder"] = {
            "@type": "Person",
            "name": client_config["owner_name"],
            "jobTitle": client_config.get("owner_title", ""),
            "worksFor": {"@id": biz_id},
        }

    # Restaurant-specific
    if biz_type_key == "restaurant":
        rest_data = type_data
        if rest_data.get("cuisine_types"):
            node["servesCuisine"] = rest_data["cuisine_types"]
        if rest_data.get("menu_url"):
            node["hasMenu"] = rest_data["menu_url"]
        if rest_data.get("accepts_reservations"):
            node["acceptsReservations"] = "True"

    return node


def inject_local_business_kw_jsonld(page_id, client_config, profile, dry_run=True):
    """Add LocalBusiness to an Elementor page via _kw_jsonld meta."""
    raw_meta = wp_get_meta(page_id, "_kw_jsonld", profile)

    lb_node = build_local_business_node(client_config, profile)

    if raw_meta:
        try:
            existing = json.loads(raw_meta)
            existing = copy.deepcopy(existing)
        except (json.JSONDecodeError, TypeError):
            existing = None

        if existing:
            wrapped = ensure_graph_wrapper(existing)
            graph = wrapped.get("@graph", [])
            biz_type = profile.get("schema_config", {}).get(
                "business_type_schema", "LocalBusiness"
            )
            if has_type_in_graph(graph, biz_type) or has_type_in_graph(graph, "LocalBusiness"):
                return {"page_id": page_id, "status": "ALREADY_HAS_LOCALBUSINESS"}
            graph.append(lb_node)
            wrapped["@graph"] = graph
            new_meta = safe_json_for_script(json.dumps(wrapped, ensure_ascii=False))
        else:
            wrapped = ensure_graph_wrapper(lb_node)
            new_meta = safe_json_for_script(json.dumps(wrapped, ensure_ascii=False))
    else:
        wrapped = ensure_graph_wrapper(lb_node)
        new_meta = safe_json_for_script(json.dumps(wrapped, ensure_ascii=False))

    if dry_run:
        return {"page_id": page_id, "status": "DRY_RUN_WOULD_INJECT"}

    result_id = wp_put_meta(page_id, "_kw_jsonld", new_meta, profile)
    if result_id:
        return {"page_id": page_id, "status": "INJECTED"}
    return {"page_id": page_id, "status": "INJECT_FAILED"}


# ---------------------------------------------------------------------------
# Step 5: Detect duplicate org schema
# ---------------------------------------------------------------------------
def detect_duplicate_org(audit_result):
    """Check for duplicate LocalBusiness/Organization nodes."""
    types = audit_result.get("types_found", {})
    findings = []

    # Count business-type nodes
    biz_count = 0
    biz_ids = []
    for t in ("LocalBusiness", "Electrician", "Restaurant"):
        entries = types.get(t, [])
        biz_count += len(entries)
        for e in entries:
            biz_ids.append(f"{t}: @id={e.get('@id', '?')}")

    if biz_count > 1:
        findings.append({
            "type": "DUPLICATE_BUSINESS",
            "severity": "BLOCKING",
            "page": audit_result["url"],
            "detail": f"{biz_count} business nodes: {'; '.join(biz_ids)}",
        })

    # Organization node alongside a LocalBusiness
    org_entries = types.get("Organization", [])
    if org_entries and biz_count > 0:
        org_ids = [f"Organization: @id={e.get('@id', '?')}" for e in org_entries]
        findings.append({
            "type": "DUPLICATE_ORG_ALONGSIDE_BUSINESS",
            "severity": "ADVISORY",
            "page": audit_result["url"],
            "detail": f"Organization node(s) alongside {biz_count} business node(s): {'; '.join(org_ids)}",
        })

    return findings


# ---------------------------------------------------------------------------
# Step 6: HowTo injection (only genuinely visible steps)
# ---------------------------------------------------------------------------
def extract_process_steps(html_content, step_selectors, skip_patterns=None):
    """
    Extract visible process steps from page HTML using configured selectors.
    Filters out elements matching skip_patterns (e.g. ti-review-* widgets) before scanning.
    step_selectors: list of dicts with 'combined_pattern' regex key.
    skip_patterns: list of glob patterns for widget classes to skip.
    """
    html_content = filter_skip_widgets(html_content, skip_patterns or [])
    all_steps = []
    for sel in step_selectors:
        pattern = sel.get("combined_pattern", "")
        if not pattern:
            continue
        matches = re.findall(pattern, html_content, re.DOTALL)
        for match in matches:
            if len(match) >= 3:
                num, title, desc = match[0], match[1], match[2]
            elif len(match) >= 2:
                num = str(len(all_steps) + 1)
                title, desc = match[0], match[1]
            else:
                continue
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            desc_clean = re.sub(r'<[^>]+>', '', desc).strip()
            if title_clean:
                all_steps.append({"num": num, "title": title_clean, "desc": desc_clean})
    return all_steps


def build_howto_node(steps, service_name, page_url):
    """Build a HowTo JSON-LD node from extracted steps."""
    if not steps:
        return None
    howto_steps = []
    for s in steps:
        howto_steps.append({
            "@type": "HowToStep",
            "position": int(s["num"]),
            "name": s["title"],
            "text": s["desc"],
        })
    return {
        "@type": "HowTo",
        "@id": f"{page_url}#howto",
        "name": f"How {service_name} Works" if service_name else "Our Service Process",
        "description": f"Step-by-step process for {service_name}" if service_name else "Our service process",
        "step": howto_steps,
        "totalTime": "PT2H",
    }


# ---------------------------------------------------------------------------
# Step 7: SpeakableSpecification injection
# ---------------------------------------------------------------------------
def build_speakable_node(page_url, css_selectors):
    """Build a WebPage node with SpeakableSpecification."""
    return {
        "@type": "WebPage",
        "@id": f"{page_url}#webpage-speakable",
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": css_selectors,
        },
    }


# ---------------------------------------------------------------------------
# Step 6+7 combined: Inject HowTo + Speakable into a page
# ---------------------------------------------------------------------------
def inject_howto_speakable(page_id, profile, dry_run=True):
    """Inject HowTo + SpeakableSpecification into a page's JSON-LD."""
    schema_cfg = profile.get("schema_config", {})
    step_selectors = schema_cfg.get("howto_step_selectors", [])
    speakable_selectors = schema_cfg.get("speakable_css_selectors", [])
    skip_patterns = schema_cfg.get("skip_widget_patterns", [])
    domain = profile["wordpress"]["domain"]

    page = wp_get_page(page_id, profile)
    if not page:
        return {"page_id": page_id, "status": "FETCH_ERROR"}

    slug = page.get("slug", "?")
    store = detect_render_store(page)

    if store == "content_raw":
        content = page["content"]["raw"]
        return _inject_hs_content_raw(
            page_id, slug, content, domain, step_selectors, speakable_selectors, skip_patterns, profile, dry_run
        )
    elif store == "elementor_kw_jsonld":
        return _inject_hs_kw_jsonld(
            page_id, slug, domain, step_selectors, speakable_selectors, skip_patterns, profile, dry_run
        )
    return {"page_id": page_id, "slug": slug, "status": "UNKNOWN_STORE"}


def _inject_hs_content_raw(page_id, slug, content, domain, step_selectors, speakable_selectors, skip_patterns, profile, dry_run):
    """Inject HowTo + Speakable into content.raw JSON-LD."""
    ld_match = re.search(
        r'(<script[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
        content, re.DOTALL,
    )
    if not ld_match:
        return {"page_id": page_id, "slug": slug, "status": "NO_JSONLD"}

    try:
        graph_data = json.loads(ld_match.group(2))
    except json.JSONDecodeError:
        return {"page_id": page_id, "slug": slug, "status": "JSON_PARSE_ERROR"}

    graph = get_graph(graph_data)

    # Collect existing types
    existing_types = set()
    for node in graph:
        t = node.get("@type", "")
        if isinstance(t, list):
            existing_types.update(t)
        else:
            existing_types.add(t)

    # Get page URL + service name from graph
    page_url = f"https://{domain}/{slug}/"
    service_name = ""
    for node in graph:
        if node.get("@type") == "Service":
            service_name = node.get("name", "")
            if node.get("@id"):
                page_url = node["@id"].rsplit("#", 1)[0]
            break

    added = []

    # HowTo — only if steps are genuinely visible on the page
    if "HowTo" not in existing_types and step_selectors:
        steps = extract_process_steps(content, step_selectors, skip_patterns)
        if steps:
            howto = build_howto_node(steps, service_name, page_url)
            if howto:
                graph.append(howto)
                added.append("HowTo")

    # SpeakableSpecification
    speakable_exists = any("speakable" in json.dumps(node) for node in graph)
    if not speakable_exists and speakable_selectors:
        graph.append(build_speakable_node(page_url, speakable_selectors))
        added.append("SpeakableSpecification")

    if not added:
        return {"page_id": page_id, "slug": slug, "status": "ALREADY_COMPLETE"}

    graph_data["@graph"] = graph
    new_jsonld = safe_json_for_script(json.dumps(graph_data, ensure_ascii=False))
    new_script = f'{ld_match.group(1)}{new_jsonld}{ld_match.group(3)}'
    new_content = content[:ld_match.start()] + new_script + content[ld_match.end():]

    if dry_run:
        return {"page_id": page_id, "slug": slug, "status": "DRY_RUN", "added": added}

    result_id = wp_put_page(page_id, {"content": new_content}, profile)
    if result_id:
        return {"page_id": page_id, "slug": slug, "status": "UPDATED", "added": added}
    return {"page_id": page_id, "slug": slug, "status": "UPDATE_FAILED", "added": added}


def _inject_hs_kw_jsonld(page_id, slug, domain, step_selectors, speakable_selectors, skip_patterns, profile, dry_run):
    """Inject HowTo + Speakable into _kw_jsonld meta (Elementor pages)."""
    raw_meta = wp_get_meta(page_id, "_kw_jsonld", profile)
    if not raw_meta:
        return {"page_id": page_id, "slug": slug, "status": "NO_KW_JSONLD"}

    try:
        jsonld_data = json.loads(raw_meta)
        jsonld_data = copy.deepcopy(jsonld_data)
    except (json.JSONDecodeError, TypeError):
        return {"page_id": page_id, "slug": slug, "status": "KW_JSONLD_PARSE_ERROR"}

    wrapped = ensure_graph_wrapper(jsonld_data)
    graph = wrapped.get("@graph", [])

    existing_types = set()
    for node in graph:
        t = node.get("@type", "")
        if isinstance(t, list):
            existing_types.update(t)
        else:
            existing_types.add(t)

    page_url = f"https://{domain}/{slug}/"
    added = []

    # HowTo — for Elementor pages, we need to fetch rendered HTML to find steps
    if "HowTo" not in existing_types and step_selectors:
        live_html = _fetch_live_html(f"https://{domain}/{slug}/")
        if live_html:
            steps = extract_process_steps(live_html, step_selectors, skip_patterns)
            if steps:
                service_name = ""
                for node in graph:
                    if node.get("@type") == "Service":
                        service_name = node.get("name", "")
                        break
                howto = build_howto_node(steps, service_name, page_url)
                if howto:
                    graph.append(howto)
                    added.append("HowTo")

    # Speakable
    speakable_exists = any("speakable" in json.dumps(node) for node in graph)
    if not speakable_exists and speakable_selectors:
        graph.append(build_speakable_node(page_url, speakable_selectors))
        added.append("SpeakableSpecification")

    if not added:
        return {"page_id": page_id, "slug": slug, "status": "ALREADY_COMPLETE"}

    wrapped["@graph"] = graph
    new_meta = safe_json_for_script(json.dumps(wrapped, ensure_ascii=False))

    if dry_run:
        return {"page_id": page_id, "slug": slug, "status": "DRY_RUN", "added": added}

    result_id = wp_put_meta(page_id, "_kw_jsonld", new_meta, profile)
    if result_id:
        return {"page_id": page_id, "slug": slug, "status": "UPDATED", "added": added}
    return {"page_id": page_id, "slug": slug, "status": "UPDATE_FAILED", "added": added}


def _fetch_live_html(url):
    """Fetch live rendered HTML via curl."""
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "15", url],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            return r.stdout
    except subprocess.TimeoutExpired:
        pass
    return None


# ---------------------------------------------------------------------------
# Step 8: Full verification — cache-busted "X of Y" report
# ---------------------------------------------------------------------------
def verify_all_pages(profile, expected_types=None):
    """
    Verify all pages live + cache-busted. Report "X of Y" for each entity type.
    Returns a verification report dict suitable for gate verdict.
    """
    if expected_types is None:
        schema_cfg = profile.get("schema_config", {})
        biz_type = schema_cfg.get("business_type_schema", "LocalBusiness")
        expected_types = [biz_type, "Service", "FAQPage", "AggregateRating",
                          "HowTo", "SpeakableSpecification"]

    pages = profile.get("pages", [])
    nocache_ts = str(int(time.time()))
    results = []
    all_findings = []

    for page in pages:
        url = page["url"]
        page_class = page.get("class", "unknown")
        audit = audit_page_live(url, nocache_ts)
        audit["page_class"] = page_class
        results.append(audit)

        # Entity verification
        entity_findings = verify_core_entities(audit, profile)
        all_findings.extend(entity_findings)

        # Duplicate org detection
        dup_findings = detect_duplicate_org(audit)
        all_findings.extend(dup_findings)

    # Build X of Y report
    total_pages = len(pages)
    type_coverage = {}
    for t in expected_types:
        count = sum(
            1 for r in results
            if r.get("status") == "OK" and t in r.get("types_found", {})
        )
        type_coverage[t] = {"present": count, "total": total_pages}

    # Build per-page-class breakdown
    class_breakdown = {}
    for r in results:
        pc = r.get("page_class", "unknown")
        if pc not in class_breakdown:
            class_breakdown[pc] = {"pages": 0, "ok": 0, "errors": 0}
        class_breakdown[pc]["pages"] += 1
        if r.get("status") == "OK":
            class_breakdown[pc]["ok"] += 1
        else:
            class_breakdown[pc]["errors"] += 1

    blocking = [f for f in all_findings if f.get("severity") == "BLOCKING"]
    advisory = [f for f in all_findings if f.get("severity") == "ADVISORY"]

    report = {
        "total_pages": total_pages,
        "pages_fetched_ok": sum(1 for r in results if r.get("status") == "OK"),
        "type_coverage": type_coverage,
        "class_breakdown": class_breakdown,
        "blocking_findings": blocking,
        "advisory_findings": advisory,
        "all_findings": all_findings,
        "verdict": "PASS" if not blocking else "BLOCKING",
    }

    return report


# ---------------------------------------------------------------------------
# Gate verdict (gate-peer-reviewer return contract)
# ---------------------------------------------------------------------------
def build_gate_verdict(verification_report, profile, run_id=""):
    """Build a gate-peer-reviewer return contract JSON from a verification report."""
    client_slug = profile.get("client_slug", "unknown")

    checks_run = []
    catches = []

    # Check 1: core entity verification
    checks_run.append({
        "name": "core-entity-verification",
        "result": "PASS" if not verification_report["blocking_findings"] else "FAIL",
        "detail": f"{verification_report['pages_fetched_ok']}/{verification_report['total_pages']} pages fetched OK",
    })

    # Check 2: type coverage
    for type_name, cov in verification_report["type_coverage"].items():
        checks_run.append({
            "name": f"type-coverage-{type_name}",
            "result": "PASS" if cov["present"] == cov["total"] else "ADVISORY",
            "detail": f"{cov['present']}/{cov['total']} pages have {type_name}",
        })

    # Check 3: duplicate org detection
    dup_findings = [f for f in verification_report["all_findings"]
                    if f["type"].startswith("DUPLICATE")]
    checks_run.append({
        "name": "duplicate-org-detection",
        "result": "PASS" if not dup_findings else "ADVISORY",
        "detail": f"{len(dup_findings)} duplicate findings",
    })

    # Catches from blocking findings
    for f in verification_report["blocking_findings"]:
        catches.append({
            "severity": "BLOCKING",
            "description": f"{f['type']}: {f.get('entity', f.get('detail', ''))} on {f['page']}",
        })
    for f in verification_report["advisory_findings"]:
        catches.append({
            "severity": "ADVISORY",
            "description": f"{f['type']}: {f.get('entity', f.get('detail', ''))} on {f['page']}",
        })

    verdict = {
        "schema_version": "1.0",
        "gate_reviewed": {
            "orchestrator": "client-schema-sync",
            "gate_id": "G-schema",
            "chat_id": run_id,
            "project_slug": client_slug,
        },
        "verdict": "APPROVE" if verification_report["verdict"] == "PASS" else "REJECT-AND-REDO",
        "verdict_rationale": _build_rationale(verification_report),
        "checks_run": checks_run,
        "catches": catches,
        "cost_usd": 0.0,
    }
    return verdict


def _build_rationale(report):
    """Build a one-paragraph rationale from the verification report."""
    parts = []
    parts.append(f"{report['pages_fetched_ok']}/{report['total_pages']} pages verified live.")
    for t, cov in report["type_coverage"].items():
        if cov["present"] < cov["total"]:
            parts.append(f"{t}: {cov['present']}/{cov['total']}.")
    if report["blocking_findings"]:
        parts.append(f"{len(report['blocking_findings'])} BLOCKING finding(s).")
    if report["advisory_findings"]:
        parts.append(f"{len(report['advisory_findings'])} advisory finding(s).")
    if not report["blocking_findings"]:
        parts.append("All core entities present. Schema sync verified.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------
def cmd_audit(args, profile):
    """Run a full audit of all pages in the profile."""
    print(f"{'='*80}")
    print(f"[client-schema-sync] AUDIT — {profile['client_slug']}")
    print(f"{'='*80}")
    results = audit_all_pages(profile)

    # Print summary table
    print(f"\n{'='*80}")
    print(f"{'Page URL':<60} {'Class':<12} {'LD#':>3}")
    print(f"{'-'*80}")
    for r in results:
        if r.get("status") != "OK":
            print(f"{r['url']:<60} {r.get('page_class','?'):<12} ERR")
            continue
        print(f"{r['url']:<60} {r.get('page_class','?'):<12} {r['ld_block_count']:>3}")
        types = r.get("types_found", {})
        type_list = ", ".join(types.keys())
        print(f"  Types: {type_list}")

    # Duplicate detection
    all_dup_findings = []
    for r in results:
        if r.get("status") == "OK":
            all_dup_findings.extend(detect_duplicate_org(r))
    if all_dup_findings:
        print(f"\nDUPLICATE FINDINGS ({len(all_dup_findings)}):")
        for f in all_dup_findings:
            print(f"  [{f['severity']}] {f['page']}: {f['detail']}")

    return results


def cmd_verify(args, profile):
    """Run full verification and emit a gate verdict."""
    print(f"{'='*80}")
    print(f"[client-schema-sync] VERIFY — {profile['client_slug']}")
    print(f"{'='*80}")
    report = verify_all_pages(profile)

    # Print coverage table
    print(f"\nType coverage:")
    for t, cov in report["type_coverage"].items():
        status = "OK" if cov["present"] == cov["total"] else "GAP"
        print(f"  {t:<30} {cov['present']}/{cov['total']}  [{status}]")

    print(f"\nVerdict: {report['verdict']}")
    if report["blocking_findings"]:
        print(f"BLOCKING findings ({len(report['blocking_findings'])}):")
        for f in report["blocking_findings"]:
            print(f"  {f['type']}: {f.get('entity', f.get('detail', ''))} — {f['page']}")
    if report["advisory_findings"]:
        print(f"Advisory findings ({len(report['advisory_findings'])}):")
        for f in report["advisory_findings"]:
            print(f"  {f['type']}: {f.get('entity', f.get('detail', ''))} — {f['page']}")

    # Emit gate verdict JSON
    if args.verdict_file:
        verdict = build_gate_verdict(report, profile, run_id=args.run_id or "")
        verdict_path = Path(args.verdict_file)
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        with open(verdict_path, "w") as f:
            json.dump(verdict, f, indent=2)
        print(f"\nGate verdict written to: {verdict_path}")

    return report


def cmd_sync_rating(args, profile):
    """Sync AggregateRating across all pages."""
    if not args.rating or not args.count:
        print("ERROR: --rating and --count are required for sync-rating.")
        print("HUMAN-IN-LOOP: Read the live Google review count from the client's")
        print("Google Business Profile listing (GBP isn't headless-fetchable).")
        print("Then re-run with: --rating 5.0 --count 91")
        sys.exit(1)

    pages = profile.get("pages", [])
    dry_run = not args.live

    print(f"{'='*80}")
    print(f"[client-schema-sync] SYNC RATING — {profile['client_slug']}")
    print(f"  Rating: {args.rating} | Count: {args.count} | {'LIVE' if not dry_run else 'DRY RUN'}")
    print(f"{'='*80}")

    results = []
    for page in pages:
        wp_id = page.get("wp_id")
        if not wp_id:
            print(f"  SKIP {page['url']} — no wp_id in profile")
            continue
        print(f"  [{page.get('class', '?')}] ID {wp_id}...", end=" ", flush=True)
        r = sync_aggregate_rating(wp_id, args.rating, args.count, profile, dry_run=dry_run)
        results.append(r)
        print(r["status"])
        if not dry_run and r["status"] == "UPDATED":
            time.sleep(0.5)

    updated = sum(1 for r in results if r["status"] in ("UPDATED", "DRY_RUN_WOULD_UPDATE"))
    current = sum(1 for r in results if r["status"] == "ALREADY_CURRENT")
    failed = sum(1 for r in results if "ERROR" in r["status"] or "FAILED" in r["status"])
    print(f"\nSummary: {updated} updated | {current} already current | {failed} failed")
    return results


def cmd_inject(args, profile, client_config):
    """Inject HowTo + SpeakableSpecification into pages."""
    pages = profile.get("pages", [])
    dry_run = not args.live

    print(f"{'='*80}")
    print(f"[client-schema-sync] INJECT HowTo + Speakable — {profile['client_slug']}")
    print(f"  Mode: {'LIVE' if not dry_run else 'DRY RUN'}")
    print(f"{'='*80}")

    results = []
    for page in pages:
        wp_id = page.get("wp_id")
        if not wp_id:
            print(f"  SKIP {page['url']} — no wp_id in profile")
            continue
        print(f"  [{page.get('class', '?')}] ID {wp_id}...", end=" ", flush=True)
        r = inject_howto_speakable(wp_id, profile, dry_run=dry_run)
        results.append(r)
        added = r.get("added", [])
        print(f"{r['status']} — added: {added}")
        if not dry_run and r["status"] == "UPDATED":
            time.sleep(0.5)

    updated = sum(1 for r in results if r["status"] in ("UPDATED", "DRY_RUN"))
    complete = sum(1 for r in results if r["status"] == "ALREADY_COMPLETE")
    failed = sum(1 for r in results if "ERROR" in r["status"] or "FAILED" in r["status"])
    print(f"\nSummary: {updated} updated | {complete} already complete | {failed} failed")
    return results


def cmd_inject_lb(args, profile, client_config):
    """Inject LocalBusiness into Elementor pages via _kw_jsonld."""
    dry_run = not args.live

    print(f"{'='*80}")
    print(f"[client-schema-sync] INJECT LocalBusiness (Elementor) — {profile['client_slug']}")
    print(f"  Mode: {'LIVE' if not dry_run else 'DRY RUN'}")
    print(f"{'='*80}")

    # Filter to Elementor pages only
    elementor_pages = [p for p in profile.get("pages", [])
                       if p.get("store") == "elementor_kw_jsonld"]

    if not elementor_pages:
        print("  No Elementor pages defined in profile. Nothing to inject.")
        return []

    results = []
    for page in elementor_pages:
        wp_id = page.get("wp_id")
        if not wp_id:
            continue
        print(f"  [{page.get('class', '?')}] ID {wp_id}...", end=" ", flush=True)
        r = inject_local_business_kw_jsonld(wp_id, client_config, profile, dry_run=dry_run)
        results.append(r)
        print(r["status"])
        if not dry_run and r["status"] == "INJECTED":
            time.sleep(0.5)

    injected = sum(1 for r in results if r["status"] in ("INJECTED", "DRY_RUN_WOULD_INJECT"))
    exists = sum(1 for r in results if r["status"] == "ALREADY_HAS_LOCALBUSINESS")
    print(f"\nSummary: {injected} injected | {exists} already had LocalBusiness")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="client-schema-sync engine — agnostic WordPress JSON-LD schema sync"
    )
    parser.add_argument("command", choices=["audit", "verify", "sync-rating",
                                             "inject", "inject-lb"],
                        help="Command to run")
    parser.add_argument("--client", required=True, help="Client slug (e.g. ev-electric-services)")
    parser.add_argument("--live", action="store_true",
                        help="Execute live changes (default is dry-run)")
    parser.add_argument("--rating", help="Live Google rating (e.g. 5.0) — for sync-rating")
    parser.add_argument("--count", help="Live Google review count (e.g. 91) — for sync-rating")
    parser.add_argument("--verdict-file", help="Path to write gate verdict JSON — for verify")
    parser.add_argument("--run-id", help="Run/chat ID for gate verdict")

    args = parser.parse_args()

    # Load configs
    client_config = load_client_config(args.client)
    profile = load_sync_profile(args.client)

    if args.command == "audit":
        cmd_audit(args, profile)
    elif args.command == "verify":
        cmd_verify(args, profile)
    elif args.command == "sync-rating":
        cmd_sync_rating(args, profile)
    elif args.command == "inject":
        cmd_inject(args, profile, client_config)
    elif args.command == "inject-lb":
        cmd_inject_lb(args, profile, client_config)


if __name__ == "__main__":
    main()
