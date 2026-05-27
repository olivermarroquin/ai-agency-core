#!/usr/bin/env python3
"""
scaffold-client-data.py — Phase 3c client scaffolder.

Reads a Phase 2d client-fact brief (markdown) plus optional meeting notes and
produces every per-client artifact the Core 30 pipeline needs:

1. `data/client-<slug>.json` — the data file `scaffold-core-30-page.py` consumes
   for brand, owner, address, contact, review, area, and license fields.

2. `<client-slug>.config.example.json` — the WP-publish config skeleton with
   wp_base_url + client_slug pre-filled and wp_username left empty for the
   operator to fill in.

3. A tier-3 credentials checklist TEMPLATE (markdown) — one row per credential
   the Core 30 pipeline needs. Written to `--tier3-template-out` if supplied;
   otherwise printed to stdout. The script NEVER writes silently to the tier-3
   vault — that's an operator-driven copy/move step.

4. A stdout credentials checklist Oliver can paste into an email to the client
   or into a working doc.

5. Optional `--test-wp-auth` flag — given a config file and `$WP_APP_PASSWORD`
   (or tier-3 lookup), hits `GET /wp-json/wp/v2/users/me` and reports whether
   auth works, what role the user has, and whether the role can upload to the
   Media Library.

USAGE
-----
Scaffold a fresh client from a brief:

    python scaffold-client-data.py \\
        --brief    /path/to/research-briefs/clients/s-and-h-contracting/brief.md \\
        --client-slug s-and-h-contracting

Scaffold + write the tier-3 template directly to the air-gapped vault path
(operator runs this — the script just writes the file; it doesn't try to read
back the existing tier-3 vault):

    python scaffold-client-data.py \\
        --brief    .../brief.md \\
        --client-slug s-and-h-contracting \\
        --tier3-template-out ~/workspace/second-brain-tier3/clients/s-and-h-contracting/credentials.md

Test WordPress REST API connectivity for an existing client config:

    export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
    python scaffold-client-data.py \\
        --client-slug ev-electric-services \\
        --test-wp-auth \\
        --config /path/to/ev-electric.config.json

Both — scaffold and then test auth (useful when you already have credentials):

    python scaffold-client-data.py \\
        --brief .../brief.md \\
        --client-slug s-and-h-contracting \\
        --test-wp-auth

When --test-wp-auth runs without --config, the script uses the freshly
scaffolded config file. The WP password is loaded via _load_secrets.py — same
lookup hierarchy as publish-core-30-page.py.

DESIGN NOTES
------------
- The brief is the source of truth. Every JSON field maps back to a section
  (§1-§8) per the contract documented in `_template-client-brief.md`.
- The parser is intentionally simple: it walks numbered sections, finds
  markdown tables, and extracts rows. Citations in `[source: ...]` brackets are
  stripped. "TBD", "NOT captured", "⚠️ NOT", "(pending)", or blank values are
  treated as missing and emitted as `null` with a parallel `_needs_confirmation`
  array surfaced at the top of the JSON.
- The script is NON-DESTRUCTIVE: if `data/client-<slug>.json` already exists,
  the script writes `data/client-<slug>.scaffolded.json` next to it and prints
  a diff command for the operator to review. The original file is never
  overwritten without explicit `--overwrite`.
- The tier-3 path is printed but not written unless `--tier3-template-out` is
  supplied. This matches the standing convention that Cowork never silently
  writes to the air-gapped vault.

REFERENCES
----------
- Brief template: second-brain/05_shared-intelligence/research-briefs/_template-client-brief.md
- Blueprint: second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md (Phase 3c)
- Reference JSON: repos/ai-agency-core/scripts/data/client-ev-electric-services.json
- WP auth pattern: publish-core-30-page.py (WordPressClient class)
- Secret loading: _load_secrets.py (env var → tier-3 markdown fallback)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ----------------------------------------------------------------------------
# Brief parsing
# ----------------------------------------------------------------------------


# Tokens that mean "this value was not captured in the brief"
_MISSING_TOKENS = (
    "TBD",
    "NOT captured",
    "NOT YET CAPTURED",
    "Not captured",
    "not captured",
    "(pending",
    "(not captured",
    "Pending",
    "unconfirmed",
    "unknown",
    "NOT confirmed",
    "(TBD",
    "not yet",
    "Not yet",
)

# Citation pattern — `[source: ...]` blocks are stripped before value extraction
_CITATION_PATTERN = re.compile(r"\[source:[^\]]*\]")

# Hex color pattern
_HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\b")

# Frontmatter YAML block delimiter
_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def strip_citations(value: str) -> str:
    """Remove inline [source: ...] citations and trim whitespace."""
    cleaned = _CITATION_PATTERN.sub("", value)
    return cleaned.strip()


def is_missing(value: Optional[str]) -> bool:
    """True if the value looks like 'not yet captured' / TBD / etc.

    Detection is intentionally conservative: tokens must appear at the start
    of the value (or be the whole value). A passing mention of 'pending' or
    'TBD' deeper in a long explanatory sentence does NOT mark the field as
    missing, since the brief often pairs a real value with caveats.
    """
    if value is None or value == "":
        return True
    v = value.strip()
    if v in {"—", "-", "–", "N/A", "n/a"}:
        return True
    # Strip leading bold-italic markers / emoji warning indicators
    leading = re.sub(r"^[\*_⚠️🔴🟡✅❓⚠\s]+", "", v)
    leading_lower = leading.lower()
    for token in _MISSING_TOKENS:
        if leading_lower.startswith(token.lower()):
            return True
    return False


@dataclass
class Brief:
    """Parsed structure of a client-fact brief markdown file."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)  # "1", "2", ... -> body
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    raw_text: str = ""


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Tiny YAML-ish parser — handles the simple key: value lines our templates use."""
    m = _FRONTMATTER_PATTERN.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            out[key] = [v.strip() for v in inner.split(",")] if inner else []
        else:
            out[key] = value
    return out


def split_sections(text: str) -> dict[str, str]:
    """Return a dict mapping section-number string ('1', '2', ...) to body text.

    A section starts at `## N. ` headers; the body runs until the next `## N. `
    or a `---` horizontal rule that precedes a top-level closing.
    """
    sections: dict[str, str] = {}
    # Pattern matches `## 1. Business identity` style headers
    header_pattern = re.compile(r"^## (\d+)\. .*$", re.MULTILINE)
    matches = list(header_pattern.finditer(text))
    for i, m in enumerate(matches):
        num = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[start:end].strip()
    return sections


def parse_markdown_tables(section_body: str) -> list[list[dict[str, str]]]:
    """Pull every markdown table out of a section body.

    Returns a list of tables, where each table is a list of row-dicts keyed by
    the table's header columns. Handles tables of any column count >= 2.
    """
    tables: list[list[dict[str, str]]] = []
    lines = section_body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and "|" in line[1:]:
            # Look ahead for separator row `|---|---|`
            if i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|\s*$", lines[i + 1].rstrip()):
                header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
                rows: list[dict[str, str]] = []
                j = i + 2
                while j < len(lines) and lines[j].rstrip().startswith("|"):
                    row_cells = [c.strip() for c in lines[j].rstrip().strip("|").split("|")]
                    if len(row_cells) == len(header_cells):
                        rows.append(dict(zip(header_cells, row_cells)))
                    j += 1
                if rows:
                    tables.append(rows)
                i = j
                continue
        i += 1
    return tables


def parse_brief(brief_path: Path) -> Brief:
    text = brief_path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    sections = split_sections(text)
    tables: dict[str, list[dict[str, str]]] = {}
    for num, body in sections.items():
        # Flatten every table in the section into a single list of rows. Most
        # JSON-field-bearing sections have one main table; secondary tables
        # (NAP audit, ratings by source) also yield rows whose first column
        # is the field label we may want elsewhere.
        flat: list[dict[str, str]] = []
        for table in parse_markdown_tables(body):
            flat.extend(table)
        tables[num] = flat
    return Brief(frontmatter=frontmatter, sections=sections, tables=tables, raw_text=text)


# ----------------------------------------------------------------------------
# Field extraction — label-matching helpers
# ----------------------------------------------------------------------------


def find_value_in_table(
    rows: list[dict[str, str]],
    label_patterns: list[str],
) -> Optional[str]:
    """Find the first row whose first column contains any of the label patterns.

    Returns the value column (assumed to be the SECOND column — the brief
    template's two-column tables) with citations stripped. Returns None if no
    matching row is found.
    """
    for row in rows:
        keys = list(row.keys())
        if not keys:
            continue
        label = row[keys[0]]
        if not label:
            continue
        for pattern in label_patterns:
            if pattern.lower() in label.lower():
                # Value column is usually the second column
                if len(keys) >= 2:
                    raw_value = row[keys[1]]
                    return strip_citations(raw_value)
        # No match
    return None


def extract_hex(value: Optional[str]) -> Optional[str]:
    """Pull the first 6-digit hex code from a string."""
    if not value:
        return None
    m = _HEX_PATTERN.search(value)
    return m.group(0).lower() if m else None


def extract_all_hex(value: Optional[str]) -> list[str]:
    """Pull every 6-digit hex code from a string."""
    if not value:
        return []
    return [h.lower() for h in _HEX_PATTERN.findall(value)]


def extract_first_url(value: Optional[str]) -> Optional[str]:
    """Pull the first http(s):// URL from a value cell.

    Strips trailing markdown link punctuation like `)` or `]`.
    """
    if not value:
        return None
    m = re.search(r"https?://[^\s<>`)\]]+", value)
    if not m:
        return None
    url = m.group(0).rstrip(".,;")
    return url


def extract_url_from_backticks(value: Optional[str]) -> Optional[str]:
    """When a URL is wrapped in backticks like `https://evelectric.pro/`."""
    if not value:
        return None
    m = re.search(r"`(https?://[^`]+)`", value)
    if m:
        return m.group(1)
    return extract_first_url(value)


# ----------------------------------------------------------------------------
# Section-specific extractors
# ----------------------------------------------------------------------------


def extract_business_description(section_body: str) -> Optional[str]:
    """Pull the blockquote after `**Business description (schema).**`."""
    m = re.search(
        r"\*\*Business description \(schema\)\.\*\*.*?\n\n>\s*(.+?)(?:\n\n|\Z)",
        section_body,
        re.DOTALL,
    )
    if not m:
        return None
    # Collapse continued blockquote lines
    raw = m.group(1)
    lines = [ln.lstrip("> ").strip() for ln in raw.splitlines()]
    text = " ".join(ln for ln in lines if ln)
    return strip_citations(text)


def extract_review_pitch(section_body: str) -> Optional[str]:
    """Pull the review-pitch blockquote from §6c.

    Tolerates both 'Current production value' framing (EV brief) and
    'Recommended for hero subheading' framing (S&H brief).
    """
    # Find the §6c subsection
    m = re.search(
        r"###\s*6c\..*?\n\n.*?\n\n>\s*(.+?)(?:\n\n|\[source|\Z)",
        section_body,
        re.DOTALL,
    )
    if not m:
        return None
    raw = m.group(1)
    lines = [ln.lstrip("> ").strip() for ln in raw.splitlines()]
    text = " ".join(ln for ln in lines if ln)
    return strip_citations(text)


def extract_extended_coverage(section_body: str) -> Optional[str]:
    """Pull the extended coverage phrase from §7d or §7e (briefs vary)."""
    for header in ("### 7d", "### 7e"):
        m = re.search(
            rf"{re.escape(header)}\..*?\n\n.*?\n\n>\s*(.+?)(?:\n\n|\[source|\Z)",
            section_body,
            re.DOTALL,
        )
        if m:
            raw = m.group(1)
            lines = [ln.lstrip("> ").strip() for ln in raw.splitlines()]
            text = " ".join(ln for ln in lines if ln)
            cleaned = strip_citations(text)
            if cleaned:
                return cleaned
    return None


def extract_primary_cities(section_body: str) -> list[str]:
    """Pull the 'Primary cities' or 'Top priority cities' list from §7a.

    Briefs sometimes wrap the citation onto a second line — we strip the whole
    section body's citations FIRST so the inline regex doesn't trip on a
    half-captured `[source: ...]`.
    """
    cleaned_body = _CITATION_PATTERN.sub("", section_body)
    # The bold label has the colon INSIDE the markup in most briefs
    # ("**Primary cities (Core 30 build queue):**"), so after the closing
    # `**` we just have whitespace and the comma-separated list. The list
    # itself may wrap across multiple lines — we capture until a blank line
    # or the next bullet/header.
    patterns = [
        r"\*\*Primary cities[^*]*?\*\*\s*:?\s*(.+?)(?:\n\s*\n|\n\s*[-#]|\Z)",
        r"\*\*Top priority cities[^*]*?\*\*\s*:?\s*(.+?)(?:\n\s*\n|\n\s*[-#]|\Z)",
    ]
    for pat in patterns:
        m = re.search(pat, cleaned_body, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            # Collapse internal whitespace (the list wraps mid-comma with newlines)
            raw = re.sub(r"\s+", " ", raw)
            # Strip a leading explanatory parenthetical like '(Core 30 first-wave queue):'
            raw = re.sub(r"^.*?\)\s*", "", raw, count=1)
            # Split on commas, strip
            cities = [c.strip().rstrip(".") for c in raw.split(",")]
            return [c for c in cities if c and not c.startswith("[source")]
    return []


def extract_anchor_county(section_body: str) -> Optional[str]:
    """Pull '**Anchor county:**' bullet from §7a."""
    m = re.search(r"\*\*Anchor county:?\*\*\s*([^\n]+)", section_body)
    if m:
        raw = strip_citations(m.group(1))
        # Drop GBP-tied parenthetical
        raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw)
        return raw.strip()
    return None


def parse_hours(value: Optional[str]) -> Optional[list[dict[str, Any]]]:
    """Parse a string like 'Mon-Fri 08:00-18:00, Sat 09:00-16:00' into the
    structured `hours` list the JSON schema expects.

    Returns None if the value looks missing.
    """
    if is_missing(value):
        return None
    if not value:
        return None

    day_map = {
        "mon": "Monday", "monday": "Monday",
        "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
        "wed": "Wednesday", "weds": "Wednesday", "wednesday": "Wednesday",
        "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
        "fri": "Friday", "friday": "Friday",
        "sat": "Saturday", "saturday": "Saturday",
        "sun": "Sunday", "sunday": "Sunday",
    }

    def expand_range(start: str, end: str) -> list[str]:
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        s = day_map.get(start.lower())
        e = day_map.get(end.lower())
        if not s or not e:
            return []
        si, ei = order.index(s), order.index(e)
        return order[si : ei + 1]

    result: list[dict[str, Any]] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        # Match patterns like "Mon-Fri 08:00-18:00" or "Sat 09:00-16:00"
        m = re.match(
            r"^([A-Za-z]+)(?:\s*[-–]\s*([A-Za-z]+))?\s+(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})$",
            chunk,
        )
        if not m:
            continue
        start_day, end_day, opens, closes = m.groups()
        if end_day:
            days = expand_range(start_day, end_day)
        else:
            day = day_map.get(start_day.lower())
            days = [day] if day else []
        if days:
            result.append({"days": days, "opens": opens, "closes": closes})

    return result or None


def parse_geo(value: Optional[str]) -> Optional[dict[str, float]]:
    """Parse '38.8462, -77.3064' style geo coordinates."""
    if is_missing(value):
        return None
    if not value:
        return None
    m = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", value)
    if not m:
        return None
    return {"latitude": float(m.group(1)), "longitude": float(m.group(2))}


def extract_license_row(rows: list[dict[str, str]], jurisdiction: str = "Virginia") -> dict[str, str]:
    """Find the license-table row for a given jurisdiction.

    The §8 table is wider (7 columns); rows are jurisdiction-keyed. Returns
    {} if not found.
    """
    for row in rows:
        keys = list(row.keys())
        if not keys:
            continue
        if jurisdiction.lower() in row[keys[0]].lower():
            return row
    return {}


# ----------------------------------------------------------------------------
# Build the JSON
# ----------------------------------------------------------------------------


# State-name → full-state-name mapping (only what we need; extend as new
# clients land outside VA)
_STATE_FULL = {
    "Virginia": "Commonwealth of Virginia",
    "VA": "Commonwealth of Virginia",
    "Maryland": "State of Maryland",
    "MD": "State of Maryland",
    "Pennsylvania": "Commonwealth of Pennsylvania",
    "PA": "Commonwealth of Pennsylvania",
    "DC": "District of Columbia",
}


def build_client_data(brief: Brief, client_slug: str) -> tuple[dict[str, Any], list[str]]:
    """Produce the data/client-<slug>.json dict and a parallel list of fields
    that need confirmation from the client (because the brief flagged them TBD).
    """
    needs_confirmation: list[str] = []

    def record_gap(field_name: str) -> None:
        if field_name not in needs_confirmation:
            needs_confirmation.append(field_name)

    s1 = brief.tables.get("1", [])
    s2 = brief.tables.get("2", [])
    s3 = brief.tables.get("3", [])
    s6 = brief.tables.get("6", [])
    s7_body = brief.sections.get("7", "")
    s8 = brief.tables.get("8", [])
    s1_body = brief.sections.get("1", "")
    s2_body = brief.sections.get("2", "")
    s6_body = brief.sections.get("6", "")

    # ---------- §1 Business identity ----------
    name = find_value_in_table(s1, ["`name`"]) or find_value_in_table(s1, ["Doing-business-as"])
    if name and "/" in name:
        # DBA row sometimes reads "EV Electric Services (full form) / EV Electric (short form)"
        name = name.split("/")[0].strip()
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    if is_missing(name):
        record_gap("name")
        name = None

    alternate_name = find_value_in_table(s1, ["`alternate_name`", "alternate_name"])
    if not alternate_name:
        # Fall back to the short-form half of the DBA row
        dba = find_value_in_table(s1, ["Doing-business-as"])
        if dba and "/" in dba:
            short = dba.split("/", 1)[1]
            alternate_name = re.sub(r"\s*\([^)]*\)\s*$", "", short).strip()
    if is_missing(alternate_name):
        record_gap("alternate_name")
        alternate_name = None

    owner_name_raw = find_value_in_table(s1, ["Owner full name (day-to-day)", "Owner full name"])
    owner_first = find_value_in_table(s1, ["Owner first name (for copy)", "Owner first name"])
    owner_title_raw = find_value_in_table(s1, ["Owner title"])

    # Strip trailing parens / "more specific..." annotations from owner_title
    owner_title = owner_title_raw
    if owner_title:
        owner_title = re.sub(r"\s*\([^)]*\).*$", "", owner_title).strip()
    if is_missing(owner_title):
        record_gap("owner_title")
        owner_title = None
    if is_missing(owner_name_raw):
        record_gap("owner_name")
        owner_name = None
    else:
        owner_name = owner_name_raw
    if is_missing(owner_first):
        record_gap("owner_first_name")
        owner_first = None

    business_description = extract_business_description(s1_body)
    if is_missing(business_description):
        record_gap("business_description_schema")
        business_description = None

    # ---------- §2 Brand surface ----------
    website_url = find_value_in_table(s2, ["Primary domain"])
    website_url = extract_url_from_backticks(website_url) if website_url else None
    if website_url and not website_url.endswith("/"):
        website_url = website_url + "/"
    if not website_url:
        record_gap("website_url")
    website_url_no_slash = website_url.rstrip("/") if website_url else None

    logo_raw = find_value_in_table(s2, ["Logo file"])
    brand_logo_url = extract_url_from_backticks(logo_raw) if logo_raw else None
    if is_missing(logo_raw) or not brand_logo_url:
        record_gap("brand_logo_url")
        brand_logo_url = None

    portrait_raw = find_value_in_table(
        s2,
        ["Owner / brand reference photo", "Brand image (hero", "owner / brand reference photo"],
    )
    # Prefer URL extraction first: if a real URL is present, take it even if
    # the cell also carries explanatory caveats ("pending uniform photo", etc.)
    brand_image_url = extract_url_from_backticks(portrait_raw) if portrait_raw else None
    if not brand_image_url:
        record_gap("brand_image_url")
        brand_image_url = None

    primary_color = extract_hex(find_value_in_table(s2, ["Primary brand color"]))
    if not primary_color:
        record_gap("primary_color")

    # Secondary brand colors row often holds multiple hex codes; pick the
    # first (used as the 'navy' / darker stop) as a working default
    secondary_raw = find_value_in_table(s2, ["Secondary brand colors"])
    secondary_hexes = extract_all_hex(secondary_raw)
    navy = secondary_hexes[0] if secondary_hexes else None
    if not navy:
        record_gap("navy")

    accent_yellow = extract_hex(find_value_in_table(s2, ["Accent yellow"]))
    if not accent_yellow:
        record_gap("accent_yellow")

    heading_color = extract_hex(find_value_in_table(s2, ["Heading text color"]))
    if not heading_color:
        record_gap("heading_color")

    gradient_raw = find_value_in_table(s2, ["Hero gradient stops"])
    # Don't pull hex codes out of cells that explicitly say "NOT captured" —
    # those cells often MENTION a brand hex by way of explanation
    # ("...using `#39823c` as primary"), but that's not a real gradient.
    if is_missing(gradient_raw):
        gradient_hexes: list[str] = []
    else:
        gradient_hexes = extract_all_hex(gradient_raw)
    hero_gradient_dark = gradient_hexes[0] if len(gradient_hexes) >= 1 else None
    hero_gradient_mid = gradient_hexes[1] if len(gradient_hexes) >= 2 else None
    hero_gradient_bright = gradient_hexes[2] if len(gradient_hexes) >= 3 else None
    for name_, val in [
        ("hero_gradient_dark", hero_gradient_dark),
        ("hero_gradient_mid", hero_gradient_mid),
        ("hero_gradient_bright", hero_gradient_bright),
    ]:
        if not val:
            record_gap(name_)

    price_range_raw = find_value_in_table(s2, ["Price-range token"])
    # The cell often reads `$$` or `$$ — standard for ...`. Pull the leading
    # currency-symbol cluster.
    price_range_token = None
    if price_range_raw:
        m = re.match(r"^[`\s]*(\$+)", price_range_raw)
        if m:
            price_range_token = m.group(1)
    if not price_range_token:
        # Default for residential contractor positioning per blueprint
        price_range_token = "$$"
        record_gap("price_range_token")

    # ---------- §3 Contact surface ----------
    def _trim_phone(value: Optional[str]) -> Optional[str]:
        """Strip trailing explanatory text after the phone number itself.

        Phone-row values sometimes read '703-972-5571 — single number across
        the site (no fragmentation...)'. We keep only the leading
        digits-and-formatting chunk.
        """
        if value is None:
            return None
        # First whitespace-delimited token usually IS the phone. Fall back to
        # everything before the first em-dash or hyphen-separator phrase.
        m = re.match(r"^([\d+()\-\s.]+)", value)
        if m:
            return m.group(1).strip().rstrip("-").strip()
        return value.split("—")[0].strip()

    phone_display = _trim_phone(find_value_in_table(s3, ["Primary phone (display form)"]))
    phone_tel = _trim_phone(find_value_in_table(s3, ["Primary phone (tel: link form)", "tel: link"]))
    phone_e164 = _trim_phone(find_value_in_table(s3, ["Primary phone (E.164 form)", "E.164"]))
    for name_, val in [
        ("phone_display", phone_display),
        ("phone_tel", phone_tel),
        ("phone_e164", phone_e164),
    ]:
        if is_missing(val):
            record_gap(name_)

    email_raw = find_value_in_table(s3, ["Primary email", "Read email (primary correspondence)"])
    # Strip surrounding backticks like `Contact@evelectric.pro`
    email = None
    if email_raw:
        m = re.search(r"`([^`]+@[^`]+)`", email_raw)
        email = m.group(1) if m else None
        if not email:
            m = re.search(r"[\w.+-]+@[\w.-]+\.[\w.-]+", email_raw)
            email = m.group(0) if m else None
    if not email or is_missing(email):
        record_gap("email")
        email = None

    def _clean_addr_part(value: Optional[str]) -> Optional[str]:
        """Strip trailing parenthetical annotations like '(canonical per GBP)'."""
        if value is None:
            return None
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
        return cleaned or None

    street = _clean_addr_part(find_value_in_table(s3, ["Street address"]))
    locality = _clean_addr_part(find_value_in_table(s3, ["Locality (city)"]))
    region = _clean_addr_part(find_value_in_table(s3, ["Region (state)"]))
    postal_code = _clean_addr_part(find_value_in_table(s3, ["Postal code"]))
    country = _clean_addr_part(find_value_in_table(s3, ["Country"])) or "US"

    address = {
        "street": street if not is_missing(street) else None,
        "locality": locality if not is_missing(locality) else None,
        "region": region if not is_missing(region) else None,
        "postal_code": postal_code if not is_missing(postal_code) else None,
        "country": country if not is_missing(country) else "US",
    }
    for k, v in address.items():
        if v is None:
            record_gap(f"address.{k}")

    geo = parse_geo(find_value_in_table(s3, ["Latitude / longitude"]))
    if geo is None:
        record_gap("geo.latitude")
        record_gap("geo.longitude")

    hours = parse_hours(find_value_in_table(s3, ["Hours"]))
    if hours is None:
        record_gap("hours")

    contact_page_path = find_value_in_table(s3, ["Contact page path"])
    if is_missing(contact_page_path):
        contact_page_path = "/contact/"  # default per brief recommendation
    else:
        # Strip the "(likely `/contact/`); verify on site" cruft sometimes present
        m = re.search(r"`?(/[\w/-]+)`?", contact_page_path)
        contact_page_path = m.group(1) if m else "/contact/"

    primary_cta_raw = find_value_in_table(s3, ["Primary CTA label"])
    primary_cta = "Call {phone}"
    if primary_cta_raw and not is_missing(primary_cta_raw):
        m = re.search(r"`([^`]+)`", primary_cta_raw)
        primary_cta = m.group(1) if m else primary_cta

    secondary_cta_raw = find_value_in_table(s3, ["Secondary CTA label"])
    secondary_cta = "Request a Quote"
    if secondary_cta_raw and not is_missing(secondary_cta_raw):
        secondary_cta = re.sub(r"[`\"]", "", secondary_cta_raw.split("—")[0]).strip()

    final_cta_raw = find_value_in_table(s3, ["Final-CTA response promise"])
    final_cta = "We respond within one business day."
    if final_cta_raw and not is_missing(final_cta_raw):
        # Strip surrounding quotes/leading "Recommend:" prose
        cleaned = re.sub(r"^.*?[:—]\s*", "", final_cta_raw, count=1)
        cleaned = cleaned.strip().strip("\"'")
        if cleaned:
            final_cta = cleaned

    # ---------- §6 Reviews ----------
    # Pull GBP row from §6a (first table)
    review_count = None
    review_rating = None
    for row in s6:
        keys = list(row.keys())
        if len(keys) >= 4 and "Google Business Profile" in row.get(keys[0], ""):
            count_cell = row.get(keys[1], "")
            rating_cell = row.get(keys[2], "")
            cm = re.search(r"\d+", count_cell)
            review_count = int(cm.group(0)) if cm else None
            rm = re.search(r"\d+\.\d", rating_cell)
            review_rating = rm.group(0) if rm else None
            break
    if review_count is None:
        record_gap("review_count")
    if review_rating is None:
        record_gap("review_rating")

    if review_count is not None and review_rating is not None:
        review_count_phrase = f"{review_rating}-star average across {review_count} customer reviews"
    else:
        review_count_phrase = None
        record_gap("review_count_phrase")

    review_pitch = extract_review_pitch(s6_body)
    if is_missing(review_pitch):
        record_gap("review_pitch")
        review_pitch = None

    # ---------- §7 Service area ----------
    brand_areas_served = extract_primary_cities(s7_body)
    if not brand_areas_served:
        record_gap("brand_areas_served")

    brand_area_county = extract_anchor_county(s7_body)
    if is_missing(brand_area_county):
        record_gap("brand_area_county")
        brand_area_county = None

    brand_extended = extract_extended_coverage(s7_body)
    if is_missing(brand_extended):
        record_gap("brand_extended_coverage_phrase")
        brand_extended = None

    # ---------- §8 License ----------
    va_row = extract_license_row(s8, "Virginia")
    license_category = va_row.get(list(va_row.keys())[1]) if va_row else None
    license_issuer_raw = None
    if va_row:
        issuer_key = next((k for k in va_row.keys() if "issuer" in k.lower()), None)
        if issuer_key:
            license_issuer_raw = va_row[issuer_key]

    if is_missing(license_category) or not license_category:
        record_gap("license.category")
        license_category = None
    else:
        license_category = strip_citations(license_category)

    license_issuer = None
    if license_issuer_raw and not is_missing(license_issuer_raw):
        # Strip DPOR gloss text like "(DPOR — Virginia's licensing body)"
        license_issuer = re.sub(r"\s*\([^)]*\)\s*", "", license_issuer_raw).strip()
        # Trim leading/trailing punctuation
        license_issuer = strip_citations(license_issuer)
    if not license_issuer:
        record_gap("license.issuer")

    license_state = "Virginia"
    license_state_full = _STATE_FULL.get(license_state, license_state)

    license_block = {
        "category": license_category,
        "issuer": license_issuer,
        "state": license_state,
        "state_full": license_state_full,
    }

    # ---------- Owner bio paragraphs ----------
    # Generating bio prose from facts produces wooden results, so we leave the
    # field empty for the operator/copywriter to fill. The discovery-call /
    # competitor-deep-research output is the right input for this.
    owner_bio_paragraphs: list[str] = []
    record_gap("owner_bio_paragraphs")

    # ---------- Assemble ----------
    data: dict[str, Any] = {
        "_comment": (
            f"Client-level facts for {name or client_slug}. Used by "
            "scaffold-core-30-page.py to fill the LocalBusiness JSON-LD block, "
            "About section, hero phone CTA, and final CTA across all Core 30 "
            "pages. One file per client."
        ),
        "_scaffolded": {
            "from_brief": True,
            "needs_confirmation": needs_confirmation,
        },
        "client_slug": client_slug,
        "name": name,
        "alternate_name": alternate_name,
        "owner_name": owner_name,
        "owner_first_name": owner_first,
        "owner_title": owner_title,
        "website_url": website_url,
        "website_url_no_slash": website_url_no_slash,
        "phone_display": phone_display if not is_missing(phone_display) else None,
        "phone_tel": phone_tel if not is_missing(phone_tel) else None,
        "phone_e164": phone_e164 if not is_missing(phone_e164) else None,
        "email": email,
        "primary_color": primary_color,
        "navy": navy,
        "accent_yellow": accent_yellow,
        "heading_color": heading_color,
        "hero_gradient_dark": hero_gradient_dark,
        "hero_gradient_mid": hero_gradient_mid,
        "hero_gradient_bright": hero_gradient_bright,
        "address": address,
        "geo": geo if geo else {"latitude": None, "longitude": None},
        "hours": hours if hours else [],
        "license": license_block,
        "business_description_schema": business_description,
        "brand_image_url": brand_image_url,
        "brand_logo_url": brand_logo_url,
        "price_range_token": price_range_token,
        "review_count": review_count,
        "review_rating": review_rating,
        "review_count_phrase": review_count_phrase,
        "review_pitch": review_pitch,
        "brand_areas_served": brand_areas_served,
        "brand_area_county": brand_area_county,
        "brand_extended_coverage_phrase": brand_extended,
        "owner_bio_paragraphs": owner_bio_paragraphs,
        "contact_page_path": contact_page_path,
        "secondary_cta_label": secondary_cta,
        "primary_cta_label_template": primary_cta,
        "final_cta_response_promise": final_cta,
    }

    return data, needs_confirmation


# ----------------------------------------------------------------------------
# Config example writer
# ----------------------------------------------------------------------------


def build_config_example(client_slug: str, website_url_no_slash: Optional[str]) -> dict[str, Any]:
    """Produce the <client-slug>.config.example.json content.

    Mirrors ev-electric.config.example.json. wp_username is left empty for the
    operator. wp_app_password_env stays the same standard name.
    """
    return {
        "_comment": (
            f"Example config for publish-core-30-page.py against {client_slug}. "
            f"Copy to {client_slug}.config.json (gitignored) and fill in "
            "wp_username. The application password lives in $WP_APP_PASSWORD "
            "or the tier-3 vault, not in this file."
        ),
        "wp_base_url": website_url_no_slash or f"https://example.com",
        "wp_username": "",
        "wp_app_password_env": "WP_APP_PASSWORD",
        "default_status": "draft",
        "default_template": "",
        "default_author_id": None,
        "aioseo_defaults": {
            "schema_local_business": False,
        },
        "client_slug": client_slug,
    }


# ----------------------------------------------------------------------------
# Tier-3 credentials template + stdout checklist
# ----------------------------------------------------------------------------


# The canonical list of credentials the Core 30 pipeline + day-to-day SEO work
# needs from any client. Each entry produces a checkbox in the stdout checklist
# AND a section in the tier-3 markdown template.
CREDENTIALS_CHECKLIST = [
    {
        "key": "wp-admin",
        "label": "WordPress admin login",
        "why": "Create a Keelworks-owned admin user (oliver / oliver@keelworks.ai). Avoids lockout when the client rotates their own password.",
        "fields": ["WP login URL (or /wp-login.php)", "Username", "Password"],
    },
    {
        "key": "wp-app-password",
        "label": "WordPress Application Password",
        "why": "Generated at wp-admin → Users → Profile → Application Passwords. Used by publish-core-30-page.py via the WP_APP_PASSWORD env var.",
        "fields": ["Application password value", "Generated date", "WP user it belongs to"],
    },
    {
        "key": "hosting-panel",
        "label": "Hosting control panel (Hostinger / WP Engine / etc.)",
        "why": "Needed for stack-level changes — SSL renewal, PHP version, server-side caching, file restore.",
        "fields": ["Provider", "Login URL", "Username", "Password"],
    },
    {
        "key": "domain-registrar",
        "label": "Domain registrar",
        "why": "Needed for DNS edits (verification records, MX changes, redirects). Often inside the hosting account but sometimes separate.",
        "fields": ["Registrar", "Login URL", "Username", "Password"],
    },
    {
        "key": "dns-provider",
        "label": "DNS provider",
        "why": "Where the nameservers point. Sometimes the registrar, sometimes Cloudflare or hosting-bundled.",
        "fields": ["Provider", "Login URL", "Username", "Password"],
    },
    {
        "key": "gbp-manager",
        "label": "Google Business Profile manager access",
        "why": "Manager-level access to the client's GBP via oliver@keelworks.ai. Owner stays the client. See sop-gbp-add-manager.md.",
        "fields": ["GBP owner gmail", "Whether Manager invite was accepted", "GBP profile URL"],
    },
    {
        "key": "gsc-owner",
        "label": "Google Search Console Owner access",
        "why": "Owner role (not Full user) on the GSC property for oliver@keelworks.ai. Required for sitemap submission, indexing API setup, structured-data monitoring.",
        "fields": ["GSC property URL", "Current Owner gmail", "Whether oliver@keelworks.ai was added as Owner"],
    },
    {
        "key": "ga4-admin",
        "label": "GA4 (Analytics) Administrator access",
        "why": "Admin role on the GA4 property. Required for goal/event setup, audience definitions, integration with Site Kit + GSC.",
        "fields": ["GA4 property ID", "Current Admin gmail", "Whether oliver@keelworks.ai was added as Admin"],
    },
    {
        "key": "imagify-license",
        "label": "Imagify license key (or alternative image-optimization plugin)",
        "why": "Used by optimize-image.py / wire-page-images.py to compress hero + section images before upload. Per-site license, paid yearly.",
        "fields": ["License key", "Account email", "Renewal date"],
    },
    {
        "key": "email-hosting",
        "label": "Business email account (Contact@<domain>)",
        "why": "Account where customer enquiries land. Needed if Keelworks is replying to leads on the client's behalf during the engagement.",
        "fields": ["Email address", "Login URL", "Password"],
    },
    {
        "key": "hirenimbus",
        "label": "HireNimbus / review-platform login (if used)",
        "why": "Some clients use HireNimbus / Birdeye / Podium for review collection. Needed to embed reviews on the client's domain instead of off-site.",
        "fields": ["Platform name", "Login URL", "Username", "Password"],
    },
    {
        "key": "bing-webmaster",
        "label": "Bing Webmaster Tools owner access",
        "why": "Often missed in initial SEO setup. ~5-10% of search traffic. Same verification model as GSC.",
        "fields": ["Whether property is verified", "Owner gmail / Microsoft account"],
    },
    {
        "key": "thumbtack",
        "label": "Thumbtack / lead-aggregator platform logins (if applicable)",
        "why": "Some engagements involve consolidating or retiring aggregator profiles. Need login to manage or close them.",
        "fields": ["Platform name(s)", "Login URLs", "Credentials"],
    },
    {
        "key": "social",
        "label": "Facebook / Instagram / LinkedIn business accounts",
        "why": "Citation consistency (NAP across social profiles is a local-pack ranking signal). Needed to fix bad links, claim unclaimed profiles, replace stale info.",
        "fields": ["Profile URLs", "Admin gmail / login"],
    },
]


def render_tier3_template(client_slug: str, client_name: Optional[str]) -> str:
    """Markdown body for the per-client tier-3 credentials file.

    Per the standing convention (auto-memory `reference_tier3_vault.md`), this
    file goes in `~/workspace/second-brain-tier3/clients/<client-slug>/credentials.md`.
    The script never writes here silently — the operator passes
    `--tier3-template-out` with the path to write to, OR copies this stdout
    output by hand.
    """
    display_name = client_name or client_slug
    lines = [
        f"# Credentials — {display_name}",
        "",
        f"**Client slug:** `{client_slug}`",
        "**Sensitivity:** Tier-3 air-gapped. Never copy values out of this vault.",
        "",
        "This file is the canonical secret store for this client. The main",
        "second-brain vault holds only a pointer file at",
        f"`04_projects/clients/_active/{client_slug}/credentials.reference.md`.",
        "",
        "---",
        "",
        "## Credentials checklist",
        "",
        "Tick each item as it gets collected. `<see tier-3 vault>` in any",
        "main-vault note refers to a specific bullet below.",
        "",
    ]
    for item in CREDENTIALS_CHECKLIST:
        lines.append(f"### {item['label']}")
        lines.append("")
        lines.append(f"_{item['why']}_")
        lines.append("")
        lines.append(f"- [ ] **Status:** not collected")
        for field_name in item["fields"]:
            lines.append(f"  - {field_name}:")
        lines.append("")
    lines.extend([
        "---",
        "",
        "## Cross-Keelworks tools",
        "",
        "Some keys are shared across every Keelworks engagement (Google Maps",
        "Embed API key, GSC service account for indexing). Those live in",
        "`~/workspace/second-brain-tier3/personal/business-keelworks.md`, not",
        "here. This file is per-client values only.",
        "",
        "---",
        "",
        "## Access protocol",
        "",
        "1. Cowork / Claude never reads this vault.",
        "2. Open this file in Obsidian (or any editor) and copy values into the",
        "   destination tool by hand.",
        "3. Never paste a credential into a chat, main-vault note, or commit.",
        "4. If a credential must be referenced elsewhere, write",
        "   `<see tier-3 vault>` plus the bullet name.",
        "",
    ])
    return "\n".join(lines) + "\n"


def render_stdout_checklist(client_slug: str, needs_confirmation: list[str]) -> str:
    """The paste-into-email checklist. One copyable section per credential
    plus a heads-up about the JSON fields that still need client confirmation.
    """
    out: list[str] = []
    out.append("=" * 70)
    out.append(f"CREDENTIALS CHECKLIST — {client_slug}")
    out.append("=" * 70)
    out.append("")
    out.append("Send the following to the client (or work through it on the")
    out.append("kickoff call). Each item maps to a section in the tier-3")
    out.append("credentials template.")
    out.append("")
    for i, item in enumerate(CREDENTIALS_CHECKLIST, start=1):
        out.append(f"[ ] {i:>2}. {item['label']}")
        out.append(f"        Why: {item['why']}")
    out.append("")

    if needs_confirmation:
        out.append("=" * 70)
        out.append("FIELDS IN data/client-<slug>.json THAT STILL NEED CONFIRMATION")
        out.append("=" * 70)
        out.append("")
        out.append("The brief flagged the following fields as TBD or partial. Confirm")
        out.append("with the client (or research separately) before the Core 30 build")
        out.append("goes live:")
        out.append("")
        for f_ in needs_confirmation:
            out.append(f"  • {f_}")
        out.append("")

    return "\n".join(out)


# ----------------------------------------------------------------------------
# Optional: WordPress REST API connectivity check
# ----------------------------------------------------------------------------


def test_wp_auth(config: dict[str, Any]) -> int:
    """Hit GET /wp-json/wp/v2/users/me with Basic Auth and report capabilities.

    Returns shell exit code: 0 on success, 1 on auth failure, 2 on missing
    credentials or network error.
    """
    try:
        import requests  # imported lazily so the rest of the script works without it
    except ImportError:
        sys.stderr.write(
            "ERROR: `requests` is not installed. Run: pip install requests\n"
        )
        return 2

    base_url = config.get("wp_base_url", "").rstrip("/")
    username = config.get("wp_username", "")
    if not base_url or not username:
        sys.stderr.write(
            "ERROR: config must include wp_base_url and wp_username before --test-wp-auth.\n"
            f"  wp_base_url:  {base_url or '(missing)'}\n"
            f"  wp_username:  {username or '(missing)'}\n"
        )
        return 2

    # Resolve password using the same helper publish-core-30-page.py uses
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from _load_secrets import load_wp_app_password  # type: ignore
        app_password = load_wp_app_password(config)
    except Exception as e:
        sys.stderr.write(f"ERROR loading WP app password: {e}\n")
        return 2

    token = b64encode(f"{username}:{app_password}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "scaffold-client-data.py/1.0",
    }

    me_url = f"{base_url}/wp-json/wp/v2/users/me"
    print(f"→ GET {me_url}")
    try:
        resp = requests.get(me_url, headers=headers, timeout=15)
    except requests.RequestException as e:
        sys.stderr.write(f"ERROR: network failure: {e}\n")
        return 2

    if resp.status_code == 401 or resp.status_code == 403:
        sys.stderr.write(
            f"AUTH FAILED ({resp.status_code}). Check that:\n"
            f"  - The application password was generated for user '{username}'\n"
            f"  - The user has Editor or Administrator role\n"
            f"  - WP REST API isn't blocked by a security plugin (Wordfence, AIOS)\n"
            f"  - The wp_base_url '{base_url}' is correct (try with/without www)\n\n"
            f"Response body:\n{resp.text[:500]}\n"
        )
        return 1
    if resp.status_code >= 400:
        sys.stderr.write(
            f"UNEXPECTED RESPONSE ({resp.status_code}):\n{resp.text[:500]}\n"
        )
        return 1

    try:
        data = resp.json()
    except ValueError:
        sys.stderr.write(f"Response was not JSON:\n{resp.text[:500]}\n")
        return 1

    name = data.get("name", "(unknown)")
    user_id = data.get("id", "(unknown)")
    roles = data.get("roles", [])
    capabilities = data.get("capabilities", {}) or {}

    print(f"✓ Auth succeeded.")
    print(f"  user_id:  {user_id}")
    print(f"  name:     {name}")
    print(f"  roles:    {roles or '(empty — public response, not authenticated context)'}")
    # The "context=edit" view exposes capabilities; users/me defaults to view
    # but most WP installs return roles. If roles is empty, try context=edit.
    if not roles:
        print("  (re-querying with context=edit to surface roles + capabilities…)")
        try:
            resp2 = requests.get(
                f"{me_url}?context=edit",
                headers=headers,
                timeout=15,
            )
            if resp2.status_code < 400:
                data = resp2.json()
                roles = data.get("roles", roles)
                capabilities = data.get("capabilities", {}) or capabilities
                print(f"  roles:    {roles}")
        except requests.RequestException:
            pass

    can_upload = bool(capabilities.get("upload_files"))
    can_edit_pages = bool(capabilities.get("edit_pages") or capabilities.get("edit_published_pages"))
    can_publish = bool(capabilities.get("publish_pages"))

    print(f"  upload_files (media library):  {can_upload}")
    print(f"  edit_pages:                    {can_edit_pages}")
    print(f"  publish_pages:                 {can_publish}")

    if can_upload and can_edit_pages and can_publish:
        print("✓ User has full Core-30 publishing capability.")
        return 0

    print("⚠ User CAN authenticate but lacks one or more required capabilities.")
    print("  Required: upload_files + edit_pages + publish_pages")
    print("  Fix: in wp-admin → Users → Edit → set role to Editor or Administrator.")
    return 0  # auth itself worked — capability gap is a warning, not a hard failure


# ----------------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------------


def scaffold(
    brief_path: Path,
    client_slug: str,
    output_dir: Path,
    tier3_template_out: Optional[Path],
    overwrite: bool,
    meeting_notes_path: Optional[Path],
) -> tuple[Path, Path, dict[str, Any]]:
    """Run the scaffolder and return (data_json_path, config_path, data_dict)."""
    brief = parse_brief(brief_path)

    # Pull client display name from frontmatter (more reliable than table parsing)
    client_name = brief.frontmatter.get("client-name")

    data, needs_confirmation = build_client_data(brief, client_slug)
    # If the brief frontmatter has a cleaner client-name than what table parsing
    # found, prefer the frontmatter value
    if client_name and (not data.get("name") or "name" in needs_confirmation):
        data["name"] = client_name
        if "name" in needs_confirmation:
            needs_confirmation.remove("name")
            data["_scaffolded"]["needs_confirmation"] = needs_confirmation

    # Meeting-notes handling: we don't (yet) parse meeting notes for new facts.
    # The brief is the contract. If notes are supplied we append them to the
    # needs_confirmation surface so the operator knows to compare manually.
    if meeting_notes_path and meeting_notes_path.exists():
        print(
            f"→ Meeting notes supplied at {meeting_notes_path}. "
            "v1 of this script does not auto-parse notes — the brief is the "
            "contract. Review notes manually and update the brief if any "
            "new facts surfaced."
        )

    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    data_path = data_dir / f"client-{client_slug}.json"
    if data_path.exists() and not overwrite:
        scaffolded_path = data_dir / f"client-{client_slug}.scaffolded.json"
        scaffolded_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"→ Existing data file detected: {data_path}")
        print(f"→ Wrote scaffolded version to: {scaffolded_path}")
        print(f"→ Diff:  diff {data_path} {scaffolded_path}")
        print("  (Pass --overwrite to replace the existing file instead.)")
        actual_data_path = scaffolded_path
    else:
        data_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"→ Wrote data file:           {data_path}")
        actual_data_path = data_path

    config_path = output_dir / f"{client_slug}.config.example.json"
    if config_path.exists() and not overwrite:
        print(f"→ Config example already exists at {config_path} (use --overwrite to replace).")
    else:
        config = build_config_example(client_slug, data.get("website_url_no_slash"))
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"→ Wrote config example:       {config_path}")

    tier3_body = render_tier3_template(client_slug, data.get("name"))
    if tier3_template_out:
        tier3_template_out.parent.mkdir(parents=True, exist_ok=True)
        if tier3_template_out.exists() and not overwrite:
            print(
                f"→ Tier-3 template path already exists at {tier3_template_out} "
                f"(use --overwrite to replace; otherwise nothing written)."
            )
        else:
            tier3_template_out.write_text(tier3_body, encoding="utf-8")
            print(f"→ Wrote tier-3 template:      {tier3_template_out}")
    else:
        print()
        print("Tier-3 template (paste into ~/workspace/second-brain-tier3/clients/"
              f"{client_slug}/credentials.md, or rerun with --tier3-template-out):")
        print("-" * 70)
        print(tier3_body)
        print("-" * 70)

    # Stdout checklist
    print()
    print(render_stdout_checklist(client_slug, needs_confirmation))

    return actual_data_path, config_path, data


def main() -> int:
    p = argparse.ArgumentParser(
        description="Scaffold per-client data file, config skeleton, and tier-3 "
        "credentials template from a Phase 2d client-fact brief.",
    )
    p.add_argument(
        "--brief",
        type=Path,
        help="Path to a Phase 2d client-fact brief markdown file.",
    )
    p.add_argument(
        "--client-slug",
        type=str,
        required=True,
        help="kebab-case client slug (e.g. 'ev-electric-services'). Must match the "
        "data/client-<slug>.json filename and the 04_projects folder name.",
    )
    p.add_argument(
        "--meeting-notes",
        type=Path,
        default=None,
        help="Optional path to meeting-notes markdown. v1 does not auto-parse "
        "notes; the brief is the contract.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Where to write data/client-<slug>.json and "
        "<slug>.config.example.json. Defaults to this script's directory.",
    )
    p.add_argument(
        "--tier3-template-out",
        type=Path,
        default=None,
        help="If supplied, write the credentials template here (typically "
        "~/workspace/second-brain-tier3/clients/<slug>/credentials.md). "
        "If omitted, the template is printed to stdout and the operator "
        "copies it manually.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing data/config/tier-3 files instead of writing "
        "to a `.scaffolded.json` sibling.",
    )
    p.add_argument(
        "--test-wp-auth",
        action="store_true",
        help="Hit GET /wp-json/wp/v2/users/me to confirm the WP REST API "
        "credentials work. Requires --config OR a freshly scaffolded config.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to an existing client config JSON. Used by --test-wp-auth.",
    )

    args = p.parse_args()

    # --test-wp-auth standalone path: skip scaffolding, just hit the WP API
    if args.test_wp_auth and not args.brief:
        if not args.config or not args.config.exists():
            sys.stderr.write(
                "ERROR: --test-wp-auth without --brief requires --config to point "
                "at an existing client config JSON.\n"
            )
            return 2
        config = json.loads(args.config.read_text(encoding="utf-8"))
        return test_wp_auth(config)

    if not args.brief:
        sys.stderr.write("ERROR: --brief is required (unless --test-wp-auth + --config).\n")
        return 2
    if not args.brief.exists():
        sys.stderr.write(f"ERROR: brief not found: {args.brief}\n")
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        data_path, config_path, _data = scaffold(
            brief_path=args.brief,
            client_slug=args.client_slug,
            output_dir=args.output_dir,
            tier3_template_out=args.tier3_template_out,
            overwrite=args.overwrite,
            meeting_notes_path=args.meeting_notes,
        )
    except Exception as e:
        sys.stderr.write(f"FATAL: {e}\n")
        import traceback
        traceback.print_exc()
        return 1

    # Optional WP auth test after scaffolding
    if args.test_wp_auth:
        config_for_test = args.config or config_path
        if not config_for_test.exists():
            sys.stderr.write(
                f"ERROR: --test-wp-auth requested but no config exists at {config_for_test}.\n"
            )
            return 2
        config = json.loads(config_for_test.read_text(encoding="utf-8"))
        if not config.get("wp_username"):
            print()
            print(
                "→ --test-wp-auth skipped: wp_username is empty in the config. "
                "Fill it in and rerun:\n"
                f"  python scaffold-client-data.py --client-slug {args.client_slug} "
                f"--test-wp-auth --config {config_for_test}"
            )
            return 0
        print()
        print("=" * 70)
        print("TESTING WP REST API CONNECTIVITY")
        print("=" * 70)
        return test_wp_auth(config)

    return 0


if __name__ == "__main__":
    sys.exit(main())
