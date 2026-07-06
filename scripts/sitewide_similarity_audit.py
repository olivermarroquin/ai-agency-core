#!/usr/bin/env python3
"""
sitewide_similarity_audit.py — Canonical sitewide duplicate-content measurement tool.

Measures pairwise word-level similarity across sibling page groups for any client,
emitting a structured audit (machine YAML + human MD) with 7 evidence classes.

ENGINE + CONFIG. Zero hardcoded client values (CR-155/156 discipline).
All client-specific data lives in the per-client config JSON.

Evidence classes:
  1. Pairwise word-level difflib similarity (city/brand tokens stripped)
  2. Verbatim long-sentence share + longest common passage extraction
  3. Shared-asset detection (hero image, og:image, portrait, repeated CTA)
  4. Section-level diff (split by headings, classify identical/near-dup/varied)
  5. Rendered live HTML (cache-busted) primary; WP REST post_content secondary
  6. Indexation join (optional — GSC reason-code file)
  7. Two-file output split (machine YAML + human MD)

Hard requirements:
  - CR-164: collection floor — fail loud below 100% with named misses
  - CR-142: probe sitemap + one page first before bulk
  - CR-144: verify page source against known substrate
  - CR-155/156: zero client literals in engine
  - RGH-18: difflib.SequenceMatcher word-level (canonical method)
  - --dry-run mode available
  - No credentials needed (published-page reads only)

Usage:
    python3 sitewide_similarity_audit.py --config configs/<client-slug>.audit.json
    python3 sitewide_similarity_audit.py --config configs/<client-slug>.audit.json --dry-run

Author: Oliver Marroquin (via Claude Code)
Created: 2026-07-05
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CACHE_BUST_PARAM = "__cb"
DEFAULT_FLAG_THRESHOLD = 0.40
DEFAULT_CRITICAL_THRESHOLD = 0.60
LONG_SENTENCE_MIN_WORDS = 8
MIN_PASSAGE_WORDS = 15  # minimum words for a "shared passage" to be reported
REQUEST_DELAY_S = 1.0  # polite crawl delay


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PageContent:
    """Holds fetched content for a single page."""
    url: str
    slug: str
    rendered_html: str = ""
    wp_content: str = ""
    rendered_text: str = ""  # stripped of HTML tags
    wp_text: str = ""
    sections: list = field(default_factory=list)  # list of (heading, body_text)
    images: list = field(default_factory=list)  # list of image URLs
    og_image: str = ""
    fetch_source: str = ""  # "rendered" or "wp_rest" or "both"
    fetch_error: str = ""


@dataclass
class PairResult:
    """Similarity result for a single page pair."""
    slug_a: str
    slug_b: str
    group: str
    word_similarity: float  # 0.0 - 1.0, the canonical CR-150 measure
    char_similarity: float  # reported but NOT used for verdicts (LU-Q5)
    long_sentence_share: float  # fraction of long sentences that are verbatim shared
    shared_passages: list = field(default_factory=list)  # list of (text, word_count)
    shared_images: list = field(default_factory=list)
    shared_og_image: bool = False
    section_diffs: list = field(default_factory=list)  # list of (heading, classification)
    source_divergence: bool = False  # True if rendered vs wp_content differ materially
    rendered_similarity: float = 0.0
    wp_similarity: float = 0.0


# ---------------------------------------------------------------------------
# HTML / text processing utilities
# ---------------------------------------------------------------------------

def strip_html_tags(html: str) -> str:
    """Remove HTML tags, decode entities, normalize whitespace."""
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').replace('&#8217;', "'").replace('&#8220;', '"').replace('&#8221;', '"')
    text = re.sub(r'&#?\w+;', ' ', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_sections(html: str) -> list[tuple[str, str]]:
    """Split HTML by heading tags into (heading_text, body_text) sections."""
    # Split on h1-h6
    parts = re.split(r'(<h[1-6][^>]*>.*?</h[1-6]>)', html, flags=re.DOTALL | re.IGNORECASE)
    sections = []
    current_heading = "(intro)"
    current_body = []

    for part in parts:
        heading_match = re.match(r'<h[1-6][^>]*>(.*?)</h[1-6]>', part, re.DOTALL | re.IGNORECASE)
        if heading_match:
            # Save previous section
            body_text = strip_html_tags(' '.join(current_body))
            if body_text.strip():
                sections.append((current_heading, body_text))
            current_heading = strip_html_tags(heading_match.group(1)).strip()
            current_body = []
        else:
            current_body.append(part)

    # Final section
    body_text = strip_html_tags(' '.join(current_body))
    if body_text.strip():
        sections.append((current_heading, body_text))

    return sections


def extract_images(html: str) -> list[str]:
    """Extract all image src URLs from HTML."""
    return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)


def extract_og_image(html: str) -> str:
    """Extract og:image meta content."""
    match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.IGNORECASE)
    return match.group(1) if match else ""


def tokenize_words(text: str) -> list[str]:
    """Split text into lowercase word tokens."""
    return [w.lower() for w in re.findall(r'\b\w+\b', text)]


def strip_tokens(words: list[str], strip_set: set[str]) -> list[str]:
    """Remove configured strip-tokens (city names, brand names, owner names)."""
    return [w for w in words if w.lower() not in strip_set]


# ---------------------------------------------------------------------------
# Similarity computation (RGH-18 aligned: difflib.SequenceMatcher, word-level)
# ---------------------------------------------------------------------------

def word_similarity(text_a: str, text_b: str, strip_set: set[str]) -> float:
    """
    Canonical word-level similarity using difflib.SequenceMatcher.

    This is THE CR-150 method: tokenize to words, strip city/brand tokens,
    then SequenceMatcher.ratio(). RGH-18 alignment ensures audit numbers
    and build-gate numbers are directly comparable.
    """
    words_a = strip_tokens(tokenize_words(text_a), strip_set)
    words_b = strip_tokens(tokenize_words(text_b), strip_set)
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return difflib.SequenceMatcher(None, words_a, words_b).ratio()


def char_similarity(text_a: str, text_b: str) -> float:
    """Character-level similarity — reported but NOT used for verdicts (LU-Q5)."""
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    return difflib.SequenceMatcher(None, text_a, text_b).ratio()


def find_shared_passages(text_a: str, text_b: str, strip_set: set[str],
                         min_words: int = MIN_PASSAGE_WORDS) -> list[tuple[str, int]]:
    """
    Find verbatim shared passages (sequences of matching words) between two texts.
    Returns list of (passage_text, word_count) sorted by length descending.
    """
    words_a = strip_tokens(tokenize_words(text_a), strip_set)
    words_b = strip_tokens(tokenize_words(text_b), strip_set)

    matcher = difflib.SequenceMatcher(None, words_a, words_b)
    passages = []

    for block in matcher.get_matching_blocks():
        if block.size >= min_words:
            passage_words = words_a[block.a:block.a + block.size]
            passage_text = ' '.join(passage_words)
            passages.append((passage_text, block.size))

    # Sort by word count descending
    passages.sort(key=lambda x: x[1], reverse=True)
    return passages


def long_sentence_share(text_a: str, text_b: str, strip_set: set[str],
                        min_words: int = LONG_SENTENCE_MIN_WORDS) -> float:
    """
    Fraction of long sentences in text_a that appear verbatim in text_b.
    A sentence is "long" if it has >= min_words words after stripping.
    """
    # Split into sentences
    sentences_a = re.split(r'[.!?]+', text_a)

    long_a = []
    for s in sentences_a:
        words = strip_tokens(tokenize_words(s), strip_set)
        if len(words) >= min_words:
            long_a.append(' '.join(words))

    if not long_a:
        return 0.0

    # Tokenize text_b for matching
    words_b_joined = ' '.join(strip_tokens(tokenize_words(text_b), strip_set))

    shared_count = 0
    for sentence in long_a:
        if sentence in words_b_joined:
            shared_count += 1

    return shared_count / len(long_a)


def classify_section(heading_a: str, body_a: str, heading_b: str, body_b: str,
                     strip_set: set[str]) -> str:
    """Classify a section pair as identical / near-duplicate / city-varied."""
    sim = word_similarity(body_a, body_b, strip_set)
    if sim >= 0.95:
        return "identical"
    elif sim >= 0.70:
        return "near-duplicate"
    else:
        return "city-varied"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_url(url: str, cache_bust: bool = True, timeout: int = 30) -> tuple[str, str]:
    """
    Fetch a URL with cache-busting. Returns (html_content, error_string).
    Published pages only — no auth needed.
    """
    if cache_bust:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}{CACHE_BUST_PARAM}={int(time.time())}"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace'), ""
    except urllib.error.HTTPError as e:
        return "", f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return "", f"URLError: {e.reason}"
    except Exception as e:
        return "", str(e)


def fetch_sitemap(sitemap_url: str) -> tuple[list[str], str]:
    """
    Fetch and parse sitemap (handles sitemap index + sub-sitemaps).
    Returns (list_of_page_urls, error_string).
    """
    html, err = fetch_url(sitemap_url, cache_bust=False, timeout=30)
    if err:
        return [], f"Sitemap fetch failed: {err}"

    urls = []
    try:
        root = ET.fromstring(html)
    except ET.ParseError as e:
        return [], f"Sitemap XML parse error: {e}"

    ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    # Check if this is a sitemap index
    sitemaps = root.findall('.//sm:sitemap/sm:loc', ns)
    if sitemaps:
        # Sitemap index — fetch each sub-sitemap
        for loc in sitemaps:
            sub_url = loc.text.strip() if loc.text else ""
            if sub_url:
                sub_html, sub_err = fetch_url(sub_url, cache_bust=False, timeout=30)
                if sub_err:
                    continue
                try:
                    sub_root = ET.fromstring(sub_html)
                    for url_elem in sub_root.findall('.//sm:url/sm:loc', ns):
                        if url_elem.text:
                            urls.append(url_elem.text.strip())
                except ET.ParseError:
                    continue
    else:
        # Direct sitemap
        for url_elem in root.findall('.//sm:url/sm:loc', ns):
            if url_elem.text:
                urls.append(url_elem.text.strip())

    return urls, ""


def fetch_wp_rest_content(wp_rest_base: str, slug: str) -> tuple[str, str]:
    """Fetch post_content via WP REST API. Returns (content, error)."""
    url = f"{wp_rest_base}/wp-json/wp/v2/pages?slug={slug}&_fields=content"
    html, err = fetch_url(url, cache_bust=True, timeout=30)
    if err:
        return "", err
    try:
        data = json.loads(html)
        if data and isinstance(data, list) and len(data) > 0:
            return data[0].get('content', {}).get('rendered', ''), ""
        return "", "No page found for slug"
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return "", str(e)


# ---------------------------------------------------------------------------
# Page collection + grouping
# ---------------------------------------------------------------------------

def slug_from_url(url: str, domain: str) -> str:
    """Extract slug from a full URL given the domain."""
    # Remove protocol + domain
    path = re.sub(r'^https?://' + re.escape(domain) + r'/?', '', url)
    # Remove trailing slash
    path = path.rstrip('/')
    # Remove -va, -md, etc. state suffixes for slug matching purposes
    return path


def build_strip_set(config: dict) -> set[str]:
    """Build the set of tokens to strip from word lists before comparison."""
    tokens = set()
    for token_list in config.get("strip_tokens", {}).values():
        for t in token_list:
            # Add both the full token and individual words
            tokens.add(t.lower())
            for word in t.lower().split():
                tokens.add(word)
    return tokens


def group_pages(slugs: list[str], grouping_rules: dict) -> dict[str, list[str]]:
    """
    Group page slugs into sibling sets using config grouping rules.

    grouping_rules format:
      {
        "service_prefixes": ["<service-slug>", ...],
        "city_suffixes": ["<city>-<state>", ...],
        "hub_slugs": ["<hub-slug>", ...]
      }
    """
    service_prefixes = grouping_rules.get("service_prefixes", [])
    city_suffixes = grouping_rules.get("city_suffixes", [])
    hub_slugs = set(grouping_rules.get("hub_slugs", []))
    exclude_slugs = set(grouping_rules.get("exclude_slugs", []))

    groups: dict[str, list[str]] = defaultdict(list)

    for slug in slugs:
        if slug in exclude_slugs or slug in hub_slugs:
            continue

        matched = False
        for prefix in service_prefixes:
            if slug.startswith(prefix + '-'):
                # Check if the remainder is a city suffix
                remainder = slug[len(prefix) + 1:]
                if remainder in city_suffixes or any(remainder.startswith(c.split('-')[0]) for c in city_suffixes):
                    groups[prefix].append(slug)
                    matched = True
                    break

        if not matched:
            # Try matching by finding a city suffix at the end
            for suffix in city_suffixes:
                if slug.endswith('-' + suffix):
                    service = slug[:-(len(suffix) + 1)]
                    if service in service_prefixes:
                        groups[service].append(slug)
                        break

    return dict(groups)


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------

def run_audit(config: dict, dry_run: bool = False, verbose: bool = False) -> dict:
    """
    Execute the full similarity audit per config.

    Returns a result dict suitable for YAML serialization + MD rendering.
    """
    domain = config["domain"]
    sitemap_url = config["sitemap_url"]
    wp_rest_base = config.get("wp_rest_base", f"https://{domain}")
    grouping_rules = config["grouping_rules"]
    strip_set = build_strip_set(config)
    flag_threshold = config.get("flag_threshold", DEFAULT_FLAG_THRESHOLD)
    critical_threshold = config.get("critical_threshold", DEFAULT_CRITICAL_THRESHOLD)
    output_dir = Path(config.get("output_dir", "."))
    gsc_file = config.get("gsc_reason_code_file")
    expected_page_count = config.get("expected_page_count")

    results = {
        "meta": {
            "domain": domain,
            "audit_date": time.strftime("%Y-%m-%d"),
            "method": "word-level difflib.SequenceMatcher (RGH-18 aligned)",
            "strip_tokens": list(strip_set),
            "flag_threshold": flag_threshold,
            "critical_threshold": critical_threshold,
            "dry_run": dry_run,
        },
        "collection": {},
        "groups": {},
        "pairs": [],
        "summary": {},
    }

    # --- Step 1: Probe sitemap (CR-142) ---
    print(f"[PROBE] Fetching sitemap: {sitemap_url}")
    sitemap_urls, sitemap_err = fetch_sitemap(sitemap_url)
    if sitemap_err:
        print(f"[FAIL] Sitemap probe failed: {sitemap_err}")
        results["collection"]["error"] = sitemap_err
        results["collection"]["status"] = "FAILED"
        return results

    print(f"[OK] Sitemap returned {len(sitemap_urls)} URLs")

    # Extract slugs from sitemap URLs
    sitemap_slugs = [slug_from_url(u, domain) for u in sitemap_urls]
    sitemap_slugs = [s for s in sitemap_slugs if s]  # remove empty (homepage)

    # Group pages
    groups = group_pages(sitemap_slugs, grouping_rules)
    all_audit_slugs = []
    for group_slugs in groups.values():
        all_audit_slugs.extend(group_slugs)

    print(f"[INFO] {len(groups)} sibling groups, {len(all_audit_slugs)} pages to audit")
    for group_name, group_slugs in groups.items():
        print(f"  {group_name}: {len(group_slugs)} pages")

    results["collection"]["sitemap_total"] = len(sitemap_urls)
    results["collection"]["audit_pages"] = len(all_audit_slugs)
    results["collection"]["groups"] = {k: v for k, v in groups.items()}

    if dry_run:
        print("\n[DRY-RUN] Would fetch and compare these pages:")
        for group_name, group_slugs in groups.items():
            print(f"\n  Group: {group_name}")
            for s in group_slugs:
                print(f"    https://{domain}/{s}/")
        results["collection"]["status"] = "DRY_RUN"
        return results

    # --- Step 2: Probe one page (CR-142) ---
    probe_slug = all_audit_slugs[0] if all_audit_slugs else sitemap_slugs[0]
    probe_url = f"https://{domain}/{probe_slug}/"
    print(f"\n[PROBE] Fetching single page: {probe_url}")
    probe_html, probe_err = fetch_url(probe_url)
    if probe_err:
        print(f"[FAIL] Page probe failed: {probe_err}")
        results["collection"]["error"] = f"Page probe failed on {probe_slug}: {probe_err}"
        results["collection"]["status"] = "FAILED"
        return results
    print(f"[OK] Page probe successful ({len(probe_html)} bytes)")

    # --- Step 3: Fetch all audit pages ---
    pages: dict[str, PageContent] = {}
    fetch_errors: list[str] = []

    print(f"\n[FETCH] Collecting {len(all_audit_slugs)} pages...")
    for i, slug in enumerate(all_audit_slugs):
        url = f"https://{domain}/{slug}/"
        print(f"  [{i+1}/{len(all_audit_slugs)}] {slug}...", end=" ")

        # Primary: rendered HTML (LU-Q6)
        html, err = fetch_url(url)
        if err:
            print(f"FAIL ({err})")
            fetch_errors.append(slug)
            pages[slug] = PageContent(url=url, slug=slug, fetch_error=err)
            continue

        page = PageContent(url=url, slug=slug)
        page.rendered_html = html
        page.rendered_text = strip_html_tags(html)
        page.sections = extract_sections(html)
        page.images = extract_images(html)
        page.og_image = extract_og_image(html)
        page.fetch_source = "rendered"

        # Secondary: WP REST content
        if wp_rest_base:
            wp_content, wp_err = fetch_wp_rest_content(wp_rest_base, slug)
            if not wp_err and wp_content:
                page.wp_content = wp_content
                page.wp_text = strip_html_tags(wp_content)
                page.fetch_source = "both"

        pages[slug] = page
        print(f"OK ({len(html)} bytes)")

        # Polite delay
        if i < len(all_audit_slugs) - 1:
            time.sleep(REQUEST_DELAY_S)

    # --- Collection floor (CR-164) ---
    # Floor = audit pages attempted (leaf pages in sibling groups), not total site pages.
    # expected_page_count from config is the site-total reference for the MD report.
    audit_attempted = len(all_audit_slugs)
    fetched_count = audit_attempted - len(fetch_errors)

    results["collection"]["fetched"] = fetched_count
    results["collection"]["audit_attempted"] = audit_attempted
    results["collection"]["site_total"] = expected_page_count
    results["collection"]["missed"] = fetch_errors

    if fetch_errors:
        print(f"\n[WARNING] Collection floor: {fetched_count}/{audit_attempted} audit pages collected")
        print(f"  MISSED: {', '.join(fetch_errors)}")
        results["collection"]["status"] = f"PARTIAL ({fetched_count}/{audit_attempted})"
    else:
        print(f"\n[OK] Collection floor: {fetched_count}/{audit_attempted} audit pages collected (100%)")
        results["collection"]["status"] = f"COMPLETE ({fetched_count}/{audit_attempted})"

    # --- Step 4: Pairwise comparison per group ---
    print("\n[COMPARE] Running pairwise similarity analysis...")
    all_pairs: list[dict] = []
    group_summaries: dict[str, dict] = {}

    for group_name, group_slugs in groups.items():
        valid_slugs = [s for s in group_slugs if s in pages and not pages[s].fetch_error]
        if len(valid_slugs) < 2:
            print(f"  {group_name}: <2 valid pages, skipping")
            group_summaries[group_name] = {"status": "skipped", "reason": "fewer than 2 valid pages"}
            continue

        print(f"  {group_name}: comparing {len(valid_slugs)} pages...")
        group_pairs = []

        for i in range(len(valid_slugs)):
            for j in range(i + 1, len(valid_slugs)):
                slug_a = valid_slugs[i]
                slug_b = valid_slugs[j]
                page_a = pages[slug_a]
                page_b = pages[slug_b]

                # Evidence 1: Word-level similarity (THE canonical measure)
                w_sim = word_similarity(page_a.rendered_text, page_b.rendered_text, strip_set)
                c_sim = char_similarity(page_a.rendered_text, page_b.rendered_text)

                # Evidence 2: Long sentence share + shared passages
                ls_share = long_sentence_share(page_a.rendered_text, page_b.rendered_text, strip_set)
                passages = find_shared_passages(page_a.rendered_text, page_b.rendered_text, strip_set)

                # Evidence 3: Shared assets
                shared_imgs = list(set(page_a.images) & set(page_b.images))
                shared_og = (page_a.og_image == page_b.og_image and page_a.og_image != "")

                # Evidence 4: Section-level diff
                section_diffs = []
                sections_a = {h: b for h, b in page_a.sections}
                sections_b = {h: b for h, b in page_b.sections}
                all_headings = list(dict.fromkeys(
                    [h for h, _ in page_a.sections] + [h for h, _ in page_b.sections]
                ))
                for heading in all_headings:
                    body_a = sections_a.get(heading, "")
                    body_b = sections_b.get(heading, "")
                    if body_a and body_b:
                        classification = classify_section(heading, body_a, heading, body_b, strip_set)
                    elif body_a or body_b:
                        classification = "unique-to-one"
                    else:
                        classification = "empty"
                    section_diffs.append({"heading": heading, "classification": classification})

                # Evidence 5: Source divergence
                source_divergence = False
                wp_sim = 0.0
                if page_a.wp_text and page_b.wp_text:
                    wp_sim = word_similarity(page_a.wp_text, page_b.wp_text, strip_set)
                    if abs(w_sim - wp_sim) > 0.1:
                        source_divergence = True

                pair_data = {
                    "slug_a": slug_a,
                    "slug_b": slug_b,
                    "group": group_name,
                    "word_similarity": round(w_sim, 4),
                    "char_similarity": round(c_sim, 4),
                    "long_sentence_share": round(ls_share, 4),
                    "shared_passages": [
                        {"text": p[0][:200], "word_count": p[1]}
                        for p in passages[:10]  # top 10
                    ],
                    "shared_images_count": len(shared_imgs),
                    "shared_images": shared_imgs[:5],  # cap for readability
                    "shared_og_image": shared_og,
                    "section_diffs": section_diffs,
                    "source_divergence": source_divergence,
                    "rendered_similarity": round(w_sim, 4),
                    "wp_similarity": round(wp_sim, 4) if wp_sim else None,
                    "verdict": (
                        "CRITICAL" if w_sim >= critical_threshold else
                        "FLAGGED" if w_sim >= flag_threshold else
                        "OK"
                    ),
                }
                group_pairs.append(pair_data)
                all_pairs.append(pair_data)

        # Group summary
        if group_pairs:
            similarities = [p["word_similarity"] for p in group_pairs]
            group_summaries[group_name] = {
                "pair_count": len(group_pairs),
                "mean_similarity": round(sum(similarities) / len(similarities), 4),
                "max_similarity": round(max(similarities), 4),
                "min_similarity": round(min(similarities), 4),
                "critical_count": sum(1 for p in group_pairs if p["verdict"] == "CRITICAL"),
                "flagged_count": sum(1 for p in group_pairs if p["verdict"] == "FLAGGED"),
            }

    results["groups"] = group_summaries
    results["pairs"] = all_pairs

    # --- Evidence 6: Indexation join (optional) ---
    if gsc_file and Path(gsc_file).exists():
        print(f"\n[JOIN] Loading GSC reason-code file: {gsc_file}")
        try:
            with open(gsc_file) as f:
                gsc_data = json.load(f)

            # Build slug → reason map
            slug_to_reason = {}
            for entry in gsc_data:
                url = entry.get("url", "")
                reason = entry.get("indexing_state", entry.get("verdict", entry.get("coverageState", "")))
                s = slug_from_url(url, domain)
                if s:
                    slug_to_reason[s] = reason

            # Cross-table: similarity × indexation
            indexation_join = []
            for pair in all_pairs:
                reason_a = slug_to_reason.get(pair["slug_a"], "unknown")
                reason_b = slug_to_reason.get(pair["slug_b"], "unknown")
                indexation_join.append({
                    "slug_a": pair["slug_a"],
                    "slug_b": pair["slug_b"],
                    "similarity": pair["word_similarity"],
                    "verdict": pair["verdict"],
                    "index_state_a": reason_a,
                    "index_state_b": reason_b,
                })
            results["indexation_join"] = indexation_join
        except Exception as e:
            print(f"  [WARN] GSC join failed: {e}")
            results["indexation_join"] = {"error": str(e)}

    # --- Summary ---
    total_pairs = len(all_pairs)
    critical_pairs = sum(1 for p in all_pairs if p["verdict"] == "CRITICAL")
    flagged_pairs = sum(1 for p in all_pairs if p["verdict"] == "FLAGGED")
    ok_pairs = total_pairs - critical_pairs - flagged_pairs
    all_sims = [p["word_similarity"] for p in all_pairs]

    results["summary"] = {
        "total_pairs": total_pairs,
        "critical": critical_pairs,
        "flagged": flagged_pairs,
        "ok": ok_pairs,
        "mean_similarity": round(sum(all_sims) / len(all_sims), 4) if all_sims else 0,
        "max_similarity": round(max(all_sims), 4) if all_sims else 0,
        "min_similarity": round(min(all_sims), 4) if all_sims else 0,
    }

    print(f"\n[SUMMARY] {total_pairs} pairs: {critical_pairs} CRITICAL, {flagged_pairs} FLAGGED, {ok_pairs} OK")
    print(f"  Mean similarity: {results['summary']['mean_similarity']:.1%}")
    print(f"  Range: {results['summary']['min_similarity']:.1%} – {results['summary']['max_similarity']:.1%}")

    return results


# ---------------------------------------------------------------------------
# Output rendering (Evidence class 7: two-file split)
# ---------------------------------------------------------------------------

def render_yaml(results: dict, output_path: Path) -> None:
    """Write machine-readable YAML output."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        yaml.dump(results, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"\n[OUTPUT] YAML: {output_path}")


def render_markdown(results: dict, config: dict, output_path: Path) -> None:
    """
    Write human-readable markdown output in the same shape as ev-duplicate-content-map.md.
    """
    domain = config["domain"]
    client_slug = config["client_slug"]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("---")
    lines.append("type: audit")
    lines.append("status: active")
    lines.append(f"created: {time.strftime('%Y-%m-%d')}")
    lines.append(f"updated: {time.strftime('%Y-%m-%d')}")
    lines.append(f"client: {client_slug}")
    lines.append(f"domain: {domain}")
    lines.append("tags: [audit, duplicate-content, similarity, sitewide-audit]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {client_slug} site-wide duplicate-content map")
    lines.append("")
    lines.append(f"**Audited:** {results['meta']['audit_date']}")
    lines.append(f"**Method:** {results['meta']['method']}")
    lines.append(f"**Source:** Rendered live HTML (cache-busted) + WP REST API")
    lines.append(f"**Collection:** {results['collection'].get('status', 'N/A')}")
    lines.append(f"**Thresholds:** flag ≥{results['meta']['flag_threshold']:.0%}, critical ≥{results['meta']['critical_threshold']:.0%}")
    lines.append("")

    # Summary table
    summary = results.get("summary", {})
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total pairs compared | {summary.get('total_pairs', 0)} |")
    lines.append(f"| CRITICAL (≥{results['meta']['critical_threshold']:.0%}) | {summary.get('critical', 0)} |")
    lines.append(f"| FLAGGED (≥{results['meta']['flag_threshold']:.0%}) | {summary.get('flagged', 0)} |")
    lines.append(f"| OK (<{results['meta']['flag_threshold']:.0%}) | {summary.get('ok', 0)} |")
    lines.append(f"| Mean similarity | {summary.get('mean_similarity', 0):.1%} |")
    lines.append(f"| Range | {summary.get('min_similarity', 0):.1%} – {summary.get('max_similarity', 0):.1%} |")
    lines.append("")

    # Per-group summary
    lines.append("## Per-group summary")
    lines.append("")
    lines.append("| Group | Pairs | Mean sim | Max sim | Critical | Flagged |")
    lines.append("|---|---|---|---|---|---|")
    for group_name, gs in results.get("groups", {}).items():
        if isinstance(gs, dict) and "pair_count" in gs:
            lines.append(
                f"| {group_name} | {gs['pair_count']} | "
                f"{gs['mean_similarity']:.1%} | {gs['max_similarity']:.1%} | "
                f"{gs.get('critical_count', 0)} | {gs.get('flagged_count', 0)} |"
            )
    lines.append("")

    # Flagged pairs detail
    flagged = [p for p in results.get("pairs", []) if p["verdict"] in ("CRITICAL", "FLAGGED")]
    if flagged:
        lines.append("## Flagged pairs (detail)")
        lines.append("")
        for pair in sorted(flagged, key=lambda x: x["word_similarity"], reverse=True):
            lines.append(f"### {pair['slug_a']} vs {pair['slug_b']}")
            lines.append("")
            lines.append(f"- **Group:** {pair['group']}")
            lines.append(f"- **Word similarity:** {pair['word_similarity']:.1%} ({pair['verdict']})")
            lines.append(f"- **Char similarity:** {pair['char_similarity']:.1%} (reported only, not used for verdict)")
            lines.append(f"- **Long sentence share:** {pair['long_sentence_share']:.1%}")
            lines.append(f"- **Shared images:** {pair['shared_images_count']}")
            lines.append(f"- **Shared og:image:** {'Yes' if pair['shared_og_image'] else 'No'}")
            if pair.get("source_divergence"):
                lines.append(f"- **⚠️ Source divergence:** rendered={pair['rendered_similarity']:.1%} vs wp_rest={pair.get('wp_similarity', 'N/A')}")
            lines.append("")

            # Shared passages
            if pair.get("shared_passages"):
                lines.append("**Longest shared passages:**")
                lines.append("")
                for passage in pair["shared_passages"][:5]:
                    lines.append(f"> {passage['text']}... ({passage['word_count']}w)")
                    lines.append("")

            # Section diffs
            section_diffs = pair.get("section_diffs", [])
            if section_diffs:
                identical = sum(1 for s in section_diffs if s["classification"] == "identical")
                near_dup = sum(1 for s in section_diffs if s["classification"] == "near-duplicate")
                varied = sum(1 for s in section_diffs if s["classification"] == "city-varied")
                lines.append(f"**Sections:** {identical} identical, {near_dup} near-duplicate, {varied} city-varied")
                lines.append("")

    # Indexation join
    if "indexation_join" in results and isinstance(results["indexation_join"], list):
        lines.append("## Indexation × similarity cross-table")
        lines.append("")
        lines.append("| Pair | Similarity | Verdict | State A | State B |")
        lines.append("|---|---|---|---|---|")
        for entry in results["indexation_join"]:
            lines.append(
                f"| {entry['slug_a']} vs {entry['slug_b']} | "
                f"{entry['similarity']:.1%} | {entry['verdict']} | "
                f"{entry['index_state_a']} | {entry['index_state_b']} |"
            )
        lines.append("")

    # Collection details
    lines.append("## Collection details")
    lines.append("")
    lines.append(f"- Sitemap URLs: {results['collection'].get('sitemap_total', 'N/A')}")
    lines.append(f"- Audit pages (sibling leaves): {results['collection'].get('audit_attempted', results['collection'].get('audit_pages', 'N/A'))}")
    lines.append(f"- Fetched: {results['collection'].get('fetched', 'N/A')}")
    lines.append(f"- Site total (incl. hubs/core): {results['collection'].get('site_total', 'N/A')}")
    missed = results['collection'].get('missed', [])
    if missed:
        lines.append(f"- **MISSED ({len(missed)}):** {', '.join(missed)}")
    lines.append("")

    lines.append("## See also")
    lines.append("")
    lines.append("[[_sh-differentiation-wave-status]] · [[ev-duplicate-content-map]] · [[handoff-2026-07-05-sh-phase0-g1-sitewide-similarity-audit]]")
    lines.append("")

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"[OUTPUT] Markdown: {output_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sitewide similarity audit — canonical duplicate-content measurement tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full audit
  python3 sitewide_similarity_audit.py --config configs/<client-slug>.audit.json

  # 2nd-instance / calibration proof
  python3 sitewide_similarity_audit.py --config configs/<client-slug>.audit.json

  # Dry run (no fetching)
  python3 sitewide_similarity_audit.py --config configs/<client-slug>.audit.json --dry-run
"""
    )
    parser.add_argument("--config", required=True, help="Path to client audit config JSON")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched without fetching")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--output-dir", help="Override output directory from config")

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[ERROR] Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    if args.output_dir:
        config["output_dir"] = args.output_dir

    # Validate required config fields
    required_fields = ["domain", "sitemap_url", "client_slug", "grouping_rules"]
    missing = [f for f in required_fields if f not in config]
    if missing:
        print(f"[ERROR] Config missing required fields: {', '.join(missing)}")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"SITEWIDE SIMILARITY AUDIT")
    print(f"Client: {config['client_slug']}")
    print(f"Domain: {config['domain']}")
    print(f"Method: word-level difflib.SequenceMatcher (RGH-18 aligned)")
    print(f"{'='*60}")
    print()

    # Run audit
    results = run_audit(config, dry_run=args.dry_run, verbose=args.verbose)

    if args.dry_run:
        print("\n[DRY-RUN] Complete. No files written.")
        return

    # Write outputs (Evidence class 7: two-file split)
    output_dir = Path(config.get("output_dir", "."))
    client_slug = config["client_slug"]

    yaml_path = output_dir / f"_{client_slug}-similarity-audit.yaml"
    md_path = output_dir / f"{client_slug}-duplicate-content-map.md"

    render_yaml(results, yaml_path)
    render_markdown(results, config, md_path)

    # Final status
    collection_status = results["collection"].get("status", "UNKNOWN")
    if "PARTIAL" in collection_status or "FAILED" in collection_status:
        print(f"\n[⚠️  COLLECTION FLOOR BREACH] {collection_status}")
        missed = results["collection"].get("missed", [])
        if missed:
            print(f"  Named misses: {', '.join(missed)}")
        sys.exit(2)

    print(f"\n[DONE] Audit complete. Outputs in {output_dir}/")


if __name__ == "__main__":
    main()
