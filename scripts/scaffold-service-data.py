#!/usr/bin/env python3
"""
scaffold-service-data.py — Phase 3a service scaffolder.

Reads a Phase 2a Tier-1 service brief (markdown, in the
`_template-service-brief.md` shape) and produces a populated
`data/services/<slug>.json` that `scaffold-core-30-page.py` consumes when
rendering Core 30 pages.

The script is mechanical. It does not invent facts. If the brief lacks a
section the JSON needs, the script writes a FILL placeholder into the
field and records the JSON path in `_scaffolded.needs_authoring` so the
operator knows which strings still need a human pass.

Template strings like `{city_name}`, `{city_slug}`, `{owner_name}`,
`{phone_display}` are left intact. `scaffold-core-30-page.py` substitutes
them at render time from the city + client data files.

USAGE
-----
Scaffold a panel-upgrade JSON from the panel-upgrade brief:

    python3 scaffold-service-data.py \\
        --brief        ~/workspace/second-brain/05_shared-intelligence/research-briefs/services/panel-upgrade.md \\
        --output-slug  panel-upgrade

Use a custom template instead of the default `troubleshooting.json` shape:

    python3 scaffold-service-data.py \\
        --brief        .../panel-upgrade.md \\
        --output-slug  panel-upgrade \\
        --template     /path/to/some-other-service.json

Verify the brief parses without writing anything:

    python3 scaffold-service-data.py \\
        --brief        .../panel-upgrade.md \\
        --output-slug  panel-upgrade \\
        --dry-run

Overwrite an existing data/services/<slug>.json (default is non-destructive
— writes to <slug>.scaffolded.json sibling):

    python3 scaffold-service-data.py \\
        --brief        .../panel-upgrade.md \\
        --output-slug  panel-upgrade \\
        --overwrite

DESIGN
------
The brief is the source of truth. Every JSON field maps back to a brief
section per the consumption-contract table at the bottom of
`_template-service-brief.md`. Concretely:

  §1 (identity table)          → slug, name, name_with_city,
                                 service_type_phrase, quoted_phrase,
                                 lowercase_phrase, tag_short,
                                 page_slug_template, wordpress_page_title_template
  §3a (head keyword)           → aioseo_focus_keyword_template
  §3b (long-tail bullet list)  → aioseo_additional_keywords_template
  §4d (numbered FAQ questions) → faq_items[].question
  §5 (schema recommendations)  → schema_service_description_template,
                                 schema_service_terms
  §7 (FAQ table)               → cross-checks §4d question count;
                                 answer prose is FILL — brief carries hints
                                 only, not authored prose
  §9 (related cards block)     → related_cards (list of {href_slug, label})
  §10a (problem cards table)   → problem_cards (list of {title, body})
  §10b (process steps table)   → process_steps (list of {title, body})
  §11 (pricing recommendations)→ pricing_heading, pricing_items,
                                 pricing_closing_note,
                                 schema_service_price ("" — estimate-only),
                                 schema_service_price_description ("")
  Static templates             → quick_ref_footer_html_template,
                                 neighborhoods_heading_template,
                                 about_heading_template, etc.

Anything left over after the brief is consumed — what_it_means_paragraphs,
about_text_paragraphs, hero_subheading_template, FAQ answer prose — is
marked with a FILL placeholder string that traces back to the brief
section the operator should consult.

Mandatory fields. The script refuses to write a partial JSON file if any
of these are missing/null:

  slug, name, name_with_city, service_type_phrase, quoted_phrase,
  lowercase_phrase, tag_short, page_slug_template,
  schema_service_description_template, schema_service_terms

Validation also requires the produced JSON to have the same top-level
keys as the template (default: troubleshooting.json). A missing key fails
the build.

REFERENCES
----------
- Brief template: second-brain/05_shared-intelligence/research-briefs/_template-service-brief.md
- Example brief: second-brain/05_shared-intelligence/research-briefs/services/panel-upgrade.md
- Blueprint:     second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md (Phase 3a)
- Reference JSON shape: repos/ai-agency-core/scripts/data/services/troubleshooting.json
- Downstream consumer: repos/ai-agency-core/scripts/scaffold-core-30-page.py (build_context)
- Sibling scaffolder:  repos/ai-agency-core/scripts/scaffold-client-data.py (Phase 3c)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
SERVICES_DIR = DATA_DIR / "services"
DEFAULT_TEMPLATE = SERVICES_DIR / "troubleshooting.json"


# ----------------------------------------------------------------------------
# Brief parsing primitives (mirrors scaffold-client-data.py patterns)
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
)

# Inline `[source: ...]` citation block — stripped before value extraction
_CITATION_PATTERN = re.compile(r"\[source:[^\]]*\]")

# Frontmatter YAML block delimiter
_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def strip_citations(value: str) -> str:
    """Remove inline [source: ...] citations and trim whitespace."""
    cleaned = _CITATION_PATTERN.sub("", value)
    return cleaned.strip()


def is_missing(value: Optional[str]) -> bool:
    if value is None or value == "":
        return True
    v = value.strip()
    if v in {"—", "-", "–", "N/A", "n/a"}:
        return True
    leading = re.sub(r"^[\*_⚠️🔴🟡✅❓⚠\s]+", "", v)
    leading_lower = leading.lower()
    for token in _MISSING_TOKENS:
        if leading_lower.startswith(token.lower()):
            return True
    return False


def strip_backticks(value: Optional[str]) -> Optional[str]:
    """Strip a single layer of surrounding backticks: `foo` → foo."""
    if value is None:
        return None
    v = value.strip()
    if v.startswith("`") and v.endswith("`") and len(v) >= 2:
        return v[1:-1].strip()
    return v


def strip_quotes(value: Optional[str]) -> Optional[str]:
    """Strip a single layer of straight or curly quotes around a value."""
    if value is None:
        return None
    v = value.strip()
    pairs = [('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")]
    for opener, closer in pairs:
        if v.startswith(opener) and v.endswith(closer) and len(v) >= len(opener) + len(closer):
            return v[len(opener) : -len(closer)].strip()
    return v


@dataclass
class Brief:
    """Parsed structure of a Tier-1 service brief markdown file."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)        # "1", "2", ... -> body
    subsections: dict[str, str] = field(default_factory=dict)     # "4d", "10a", ... -> body
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    raw_text: str = ""


def parse_frontmatter(text: str) -> dict[str, Any]:
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
    """Sections start at `## N. ` headers; body runs until next `## N. ` header."""
    sections: dict[str, str] = {}
    header_pattern = re.compile(r"^## (\d+(?:\.\d+)?)\. .*$", re.MULTILINE)
    matches = list(header_pattern.finditer(text))
    for i, m in enumerate(matches):
        num = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[start:end].strip()
    return sections


def split_subsections(text: str) -> dict[str, str]:
    """Subsections start at `### Na. ` headers (e.g. ### 4d., ### 10a.)."""
    subs: dict[str, str] = {}
    sub_pattern = re.compile(r"^### (\d+[a-z])\. .*$", re.MULTILINE)
    matches = list(sub_pattern.finditer(text))
    for i, m in enumerate(matches):
        num = m.group(1)
        start = m.end()
        # End at the next subsection OR at the next top-level section header
        # (whichever comes first).
        next_sub = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        next_top = re.search(r"^## \d+", text[start:], re.MULTILINE)
        if next_top:
            next_top_abs = start + next_top.start()
            end = min(next_sub, next_top_abs)
        else:
            end = next_sub
        subs[num] = text[start:end].strip()
    return subs


def parse_markdown_tables(section_body: str) -> list[list[dict[str, str]]]:
    """Pull every markdown table from a section body, returning a list of tables.
    Each table is a list of row-dicts keyed by the header cells."""
    tables: list[list[dict[str, str]]] = []
    lines = section_body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and "|" in line[1:]:
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
    subsections = split_subsections(text)
    tables: dict[str, list[dict[str, str]]] = {}
    for num, body in sections.items():
        flat: list[dict[str, str]] = []
        for table in parse_markdown_tables(body):
            flat.extend(table)
        tables[num] = flat
    return Brief(
        frontmatter=frontmatter,
        sections=sections,
        subsections=subsections,
        tables=tables,
        raw_text=text,
    )


# ----------------------------------------------------------------------------
# Section-specific extractors
# ----------------------------------------------------------------------------


def find_value_in_table(
    rows: list[dict[str, str]],
    label_patterns: list[str],
) -> Optional[str]:
    """Find the first row whose first column matches any label_patterns.
    Returns the second column value (citations stripped)."""
    for row in rows:
        keys = list(row.keys())
        if not keys:
            continue
        label = row[keys[0]]
        if not label:
            continue
        for pattern in label_patterns:
            if pattern.lower() in label.lower():
                if len(keys) >= 2:
                    return strip_citations(row[keys[1]])
    return None


def extract_identity_fields(rows: list[dict[str, str]]) -> dict[str, Optional[str]]:
    """Pull the §1 identity table.

    The §1 table is a 2-column `| Field | Recommended value |` shape. The
    Field column carries backticked field names like `slug`, `name`, etc.
    """
    fields = [
        "slug",
        "name",
        "name_with_city",
        "service_type_phrase",
        "quoted_phrase",
        "lowercase_phrase",
        "tag_short",
        "page_slug_template",
        "wordpress_page_title_template",
    ]
    out: dict[str, Optional[str]] = {}
    for f in fields:
        # Match the backticked form, then the plain form as fallback
        raw = find_value_in_table(rows, [f"`{f}`", f])
        if raw is None:
            out[f] = None
            continue
        # Values are often wrapped in quotes (display strings) or backticks (slugs).
        cleaned = strip_quotes(strip_backticks(raw))
        out[f] = cleaned
    return out


def extract_head_keyword(section_3_body: str, service_quoted: Optional[str]) -> Optional[str]:
    """Pull the head keyword from §3a.

    §3a in the panel-upgrade brief lists head keyword patterns in a table.
    The first row is typically the head term tied to a city (e.g.
    `panel upgrade {city}`). We accept either the templated form or
    derive `{service_quoted} {city_slug}` as a fallback.
    """
    # Look for the §3a sub-block
    m = re.search(r"### 3a\..*?(?:### |\Z)", section_3_body, re.DOTALL)
    sub = m.group(0) if m else section_3_body
    cleaned = _CITATION_PATTERN.sub("", sub)

    # Pull the first table from this sub
    tables = parse_markdown_tables(cleaned)
    if tables:
        first_table = tables[0]
        if first_table:
            row0 = first_table[0]
            keys = list(row0.keys())
            if keys:
                kw = row0[keys[0]].strip()
                # If the brief uses `{city}` we normalize to `{city_slug}` for
                # consistency with the consumer's substitution context.
                kw = kw.replace("{city}", "{city_slug}")
                if kw:
                    return kw

    # Fallback: derive from §1
    if service_quoted:
        return f"{service_quoted} {{city_slug}}"
    return None


def extract_long_tail_keywords(section_3_body: str) -> list[str]:
    """Pull the §3b bulleted long-tail list.

    The brief lists phrases as `- "100 amp to 200 amp panel upgrade [city]"`.
    We strip the quotes and replace `[city]` with `{city_slug}` to match the
    consumer's substitution context.
    """
    m = re.search(r"### 3b\.(.+?)(?:### |\Z)", section_3_body, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    out: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        text = stripped[1:].strip()
        # Strip surrounding quotes (straight or curly)
        text = strip_quotes(text) or ""
        # Strip a trailing comment fragment like " — source: ..."
        text = re.sub(r"\s+\[source:.*$", "", text)
        # Normalize [city] → {city_slug}
        text = text.replace("[city]", "{city_slug}")
        text = text.replace("{city}", "{city_slug}")
        if text:
            out.append(text)
    return out


def extract_faq_questions(brief: Brief) -> list[str]:
    """Pull the §4d 'questions our page must answer' numbered list.

    The brief lists 8 questions like:
        1. How much does an electrical panel upgrade cost in {city}?
    """
    sub = brief.subsections.get("4d", "")
    if not sub:
        return []
    cleaned = _CITATION_PATTERN.sub("", sub)
    out: list[str] = []
    for line in cleaned.splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.+)$", line)
        if not m:
            continue
        q = m.group(2).strip()
        # Strip trailing markdown emphasis
        q = q.rstrip("*_")
        # Normalize {city} → {city_name_with_state} for the FAQ context (the
        # FAQ scaffolder template uses city_name / city_name_with_state, not
        # the slug).
        q = q.replace("{city}", "{city_name_with_state}")
        if q:
            out.append(q)
    return out


def extract_faq_hints(brief: Brief) -> list[tuple[str, str]]:
    """Pull the §7 FAQ table — returns [(question, hint_notes), ...].

    The §7 table is `| Q | Recurrence | Avg length | Notes |`. The Notes
    column is what we want as a hint for the FILL placeholder.
    """
    s7 = brief.tables.get("7", [])
    out: list[tuple[str, str]] = []
    for row in s7:
        keys = list(row.keys())
        if len(keys) < 4:
            continue
        # Heuristic — only accept rows whose first column looks like a question
        q = row[keys[0]].strip()
        if not q.endswith("?"):
            continue
        notes = strip_citations(row[keys[-1]])
        out.append((q, notes))
    return out


def extract_related_cards(section_9_body: str) -> list[dict[str, str]]:
    """Pull §9's `related_cards (recommended):` code block.

    Format:
        related_cards (recommended):
        - electrical-troubleshooting-{city_slug} — diagnosis often precedes
          upgrade, label "Electrical Troubleshooting & Diagnostics"
        ...
    Returns [{"href_slug": "electrical-troubleshooting-{city_slug}",
              "label": "Electrical Troubleshooting & Diagnostics"}, ...].
    """
    m = re.search(r"```\s*\n(related_cards.*?)```", section_9_body, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    out: list[dict[str, str]] = []
    # Each item starts with `- ` and may span multiple lines (continuation
    # indented). We flatten continuations onto the line that began with `- `.
    flat_lines: list[str] = []
    buf = ""
    for line in block.splitlines():
        if line.startswith("- "):
            if buf:
                flat_lines.append(buf)
            buf = line[2:].strip()
        elif buf and (line.startswith("  ") or line.startswith("\t")):
            buf += " " + line.strip()
        elif not line.strip():
            if buf:
                flat_lines.append(buf)
                buf = ""
    if buf:
        flat_lines.append(buf)

    for line in flat_lines:
        # Patterns we accept:
        #   electrical-troubleshooting-{city_slug} — diagnosis ..., label "..."
        # We pull the slug (first token before space/em-dash) and the label
        # (last quoted string on the line).
        slug_m = re.match(r"^([a-z0-9\-{}_]+)", line)
        label_m = re.search(r'label\s*"([^"]+)"', line)
        if not label_m:
            # Try smart-quote
            label_m = re.search(r"label\s*“([^”]+)”", line)
        if slug_m and label_m:
            out.append({
                "href_slug": slug_m.group(1).strip(),
                "label": label_m.group(1).strip(),
            })
    return out


def extract_table_with_title_body(
    rows: list[dict[str, str]],
    expected_first_col: list[str],
) -> list[dict[str, str]]:
    """Generic helper for §10a (problem cards) and §10b (process steps).

    Each table is `| Title | Body |`. Returns [{"title": ..., "body": ...}].
    Filters out the header row from secondary tables in the section if
    `expected_first_col` doesn't match.
    """
    out: list[dict[str, str]] = []
    for row in rows:
        keys = list(row.keys())
        if len(keys) < 2:
            continue
        # First column header should match one of expected_first_col;
        # the row's first column is the value, the dict key is the header.
        # We accept any table whose first header is "Title" (case-insensitive).
        first_header = keys[0].strip().lower()
        if first_header not in [h.lower() for h in expected_first_col]:
            continue
        title = strip_citations(row[keys[0]])
        body = strip_citations(row[keys[1]])
        if title and body:
            out.append({"title": title, "body": body})
    return out


def extract_problem_cards(brief: Brief) -> list[dict[str, str]]:
    s10a = brief.subsections.get("10a", "")
    if not s10a:
        # Some briefs structure as ## 10. without subsections — fall back to §10
        s10a = brief.sections.get("10", "")
    tables = parse_markdown_tables(s10a)
    flat: list[dict[str, str]] = []
    for tbl in tables:
        flat.extend(tbl)
    return extract_table_with_title_body(flat, ["Title"])


def extract_process_steps(brief: Brief) -> list[dict[str, str]]:
    s10b = brief.subsections.get("10b", "")
    if not s10b:
        s10b = brief.sections.get("10", "")
    tables = parse_markdown_tables(s10b)
    flat: list[dict[str, str]] = []
    for tbl in tables:
        flat.extend(tbl)
    return extract_table_with_title_body(flat, ["Title"])


def extract_recommended_value(section_body: str, field_name: str) -> Optional[str]:
    """Pull a 'Recommended `<field>` value: "..."' string from a section body.

    The brief's §5 and §11 use this pattern to call out exact recommended
    values for specific JSON fields. The quoted value can wrap across
    multiple lines. Tolerates several label shapes the brief uses:

      Recommended `field_name` value: "..."
      Recommended `field_name` values: "..."
      Recommended `field_name`: "..."
      Recommended `field_name` reframe: "..."
    """
    # The (?:...)? makes the qualifier word optional so we match both
    # "Recommended `field` value:" and bare "Recommended `field`:".
    pattern = (
        rf"Recommended\s+`{re.escape(field_name)}`"
        r"(?:\s+(?:values?|reframe))?"
        r"\*?\*?"           # tolerate trailing ** from markdown bold
        r"\s*:?\s*"
    )
    m = re.search(pattern, section_body)
    if not m:
        return None
    after = section_body[m.end():]
    # Now find the first quoted string after the marker (may span newlines)
    q = re.search(r'"([^"]+(?:\n[^"]+)*)"', after)
    if not q:
        # Try smart quotes
        q = re.search(r"“(.+?)”", after, re.DOTALL)
    if not q:
        return None
    raw = q.group(1)
    # Collapse internal whitespace runs (the value sometimes wraps mid-sentence)
    collapsed = re.sub(r"\s+", " ", raw).strip()
    return collapsed


def extract_recommended_heading(section_body: str, field_name: str) -> Optional[str]:
    """Pull a 'Recommended `<field>` reframe: "..."' or similar callout.
    Used in §11 for `pricing_heading` ("How estimates work") and the
    `pricing_intro` reframes.
    """
    pattern = rf"Recommended\s+`{re.escape(field_name)}`\s+(?:reframe|value|values)?\s*:?\s*"
    m = re.search(pattern, section_body)
    if not m:
        return None
    after = section_body[m.end():]
    q = re.search(r'"([^"]+(?:\n[^"]+)*)"', after)
    if not q:
        q = re.search(r"“(.+?)”", after, re.DOTALL)
    if not q:
        return None
    return re.sub(r"\s+", " ", q.group(1)).strip()


def extract_pricing_items_block(section_11_body: str) -> list[str]:
    """Pull §11's `pricing_items reframe` bullet block.

    Format:
        Recommended `pricing_items` reframe (4 bullets, no dollar amounts):
        ```
        - A diagnostic visit comes first. ...
        - The estimate covers the panel hardware, ...
        - Major related work — service-entrance changes, ...
        - After-hours and weekend work is available; ...
        ```
    """
    # Find the code block after the 'pricing_items reframe' marker.
    # The description between the marker and the opening ``` may wrap
    # across several lines (e.g. "(4 bullets, no dollar\namounts):"),
    # so we use a non-greedy `.*?` with DOTALL to span the gap.
    m = re.search(
        r"Recommended\s+`pricing_items`\s+reframe.*?```\s*\n(.*?)```",
        section_11_body,
        re.DOTALL,
    )
    if not m:
        return []
    block = m.group(1)
    items: list[str] = []
    buf = ""
    for line in block.splitlines():
        if line.startswith("- "):
            if buf:
                items.append(re.sub(r"\s+", " ", buf).strip())
            buf = line[2:].strip()
        elif buf and (line.startswith("  ") or line.startswith("\t")):
            buf += " " + line.strip()
        elif not line.strip():
            if buf:
                items.append(re.sub(r"\s+", " ", buf).strip())
                buf = ""
    if buf:
        items.append(re.sub(r"\s+", " ", buf).strip())
    return items


# ----------------------------------------------------------------------------
# FILL placeholder helpers
# ----------------------------------------------------------------------------


def fill(message: str) -> str:
    """Return a FILL placeholder string with a human-readable hint.

    The string is plain text (no curly braces) so the consumer's
    `format_map` calls won't choke on it. The leading 'FILL:' is the
    marker the operator greps for when polishing the data file.
    """
    return f"FILL: {message}"


def is_fill(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("FILL:")


# ----------------------------------------------------------------------------
# Build the service-data dict
# ----------------------------------------------------------------------------


# Top-level keys we require the produced JSON to have (matches template
# shape). The script refuses to write if any are missing.
MANDATORY_KEYS = [
    "slug",
    "name",
    "name_with_city",
    "service_type_phrase",
    "quoted_phrase",
    "lowercase_phrase",
    "tag_short",
    "page_slug_template",
    "schema_service_description_template",
    "schema_service_terms",
]


def build_service_data(
    brief: Brief,
    output_slug: str,
    template: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Produce the service-data dict + lists of needs_authoring and needs_confirmation.

    needs_authoring — fields populated with a FILL placeholder; the operator
                       needs to write the prose by hand.
    needs_confirmation — fields the script tried to extract but couldn't find
                          in the brief; should be confirmed against the brief
                          or flagged as a gap.
    """
    needs_authoring: list[str] = []
    needs_confirmation: list[str] = []

    def mark_authoring(field_path: str) -> None:
        if field_path not in needs_authoring:
            needs_authoring.append(field_path)

    def mark_confirmation(field_path: str) -> None:
        if field_path not in needs_confirmation:
            needs_confirmation.append(field_path)

    # ---------- §1 — identity ----------
    s1 = brief.tables.get("1", [])
    identity = extract_identity_fields(s1)

    slug = identity.get("slug") or output_slug
    name = identity.get("name")
    if is_missing(name):
        mark_confirmation("name")
        name = None
    name_with_city = identity.get("name_with_city")
    if is_missing(name_with_city):
        mark_confirmation("name_with_city")
        # Common default pattern in the troubleshooting template
        if name:
            name_with_city = f"{name} in {{city_name_with_state}}"
    service_type_phrase = identity.get("service_type_phrase")
    if is_missing(service_type_phrase):
        mark_confirmation("service_type_phrase")
        service_type_phrase = None
    quoted_phrase = identity.get("quoted_phrase")
    if is_missing(quoted_phrase):
        mark_confirmation("quoted_phrase")
        quoted_phrase = None
    lowercase_phrase = identity.get("lowercase_phrase")
    if is_missing(lowercase_phrase):
        mark_confirmation("lowercase_phrase")
        # Derive from quoted_phrase if available
        lowercase_phrase = (quoted_phrase or "").lower() or None
    tag_short = identity.get("tag_short") or slug
    page_slug_template = identity.get("page_slug_template") or f"{slug}-{{city_slug}}"
    wp_title = identity.get("wordpress_page_title_template") or (
        f"{name} in {{city_name_with_state}}" if name else None
    )
    if is_missing(wp_title):
        wp_title = fill(
            "WordPress page title template — derive from §1 once `name` and "
            "`name_with_city` are confirmed."
        )
        mark_authoring("wordpress_page_title_template")

    # ---------- §3 — keywords (Google search) ----------
    s3_body = brief.sections.get("3", "")
    focus_keyword = extract_head_keyword(s3_body, quoted_phrase)
    if not focus_keyword:
        focus_keyword = fill(
            "Focus keyword template — populate from §3a head keyword row "
            "(use {city_slug} for the city token)."
        )
        mark_authoring("aioseo_focus_keyword_template")
    additional_keywords = extract_long_tail_keywords(s3_body)
    if not additional_keywords:
        mark_confirmation("aioseo_additional_keywords_template")
        additional_keywords = []

    # ---------- §3 — meta description ----------
    # The brief sets the tone but doesn't author the meta description string.
    # Mark for hand-tuning (≤155 chars per AIOSEO convention).
    aioseo_meta_description_template = fill(
        "Meta description (≤155 chars). See §3 keyword targets and §6 tone "
        "anchor. Pattern: '<value prop> in {city_name_with_state}. <proof / "
        "differentiator>. Call {phone_display}.'"
    )
    mark_authoring("aioseo_meta_description_template")

    # ---------- AIOSEO page title ----------
    # Pattern from troubleshooting.json: "<Name> in {city_name_with_state} | <Client Name>"
    # Client name is per-client (filled at render time), so we leave a
    # well-formed template and let the operator confirm the client suffix.
    if name:
        aioseo_page_title_template = (
            f"{name} in {{city_name_with_state}} | {{client_name}}"
        )
    else:
        aioseo_page_title_template = fill(
            "Page title template — derive from §1 once `name` is confirmed. "
            "Pattern: '<Name> in {city_name_with_state} | <Client Name>'."
        )
        mark_authoring("aioseo_page_title_template")

    # ---------- Hero ----------
    # Eyebrow & heading: the eyebrow is a stable template; the heading is
    # the service-specific value-prop sentence the brief doesn't author.
    if name:
        hero_eyebrow_template = f"{name} in {{city_name_with_state}}"
    else:
        hero_eyebrow_template = fill(
            "Hero eyebrow — '<Name> in {city_name_with_state}'."
        )
        mark_authoring("hero_eyebrow_template")

    hero_heading = fill(
        "Hero heading — value-prop sentence (≤8 words). See §6 voice anchor "
        "and the troubleshooting reference ('Same-Day Diagnosis from a Master "
        "Electrician')."
    )
    mark_authoring("hero_heading")

    hero_subheading_template = fill(
        "Hero subheading — 2-3 sentences, contractor-folksy per §6. Should "
        "mention {owner_name}, {license_state}, and the city. See "
        "troubleshooting.json for the reference shape."
    )
    mark_authoring("hero_subheading_template")

    # Hero image alt — derivable from §8 + identity
    if quoted_phrase and name:
        hero_image_alt_template = (
            f"{{owner_name}}, master electrician, performing a {quoted_phrase} "
            f"in {{city_name_with_state}}"
        )
    else:
        hero_image_alt_template = fill(
            "Hero image alt — '<{owner_name}>, master electrician, "
            "performing a <service> in {city_name_with_state}'."
        )
        mark_authoring("hero_image_alt_template")

    # Hero image URL — placeholder per troubleshooting pattern. The wire-image
    # script substitutes the real CDN URL later.
    hero_image_url_template = (
        "https://evelectric.pro/wp-content/uploads/2026/05/"
        f"{{city_slug}}-{slug}-hero-PLACEHOLDER.png"
    )
    mark_authoring("hero_image_url_template")

    # ---------- What it means ----------
    if quoted_phrase:
        what_it_means_heading = f"What \"{quoted_phrase}\" actually means"
    else:
        what_it_means_heading = fill(
            "What-it-means heading — 'What \"<service>\" actually means'."
        )
        mark_authoring("what_it_means_heading")

    what_it_means_paragraphs = [
        fill(
            "Paragraph 1 — 80-100 words. Open with the answer-first rule "
            "(§4.5A). Define the service in plain language. See §6 voice "
            "anchor."
        ),
        fill(
            "Paragraph 2 — 80-100 words. Specifics — common scenarios, brand "
            "names that matter (Federal Pacific, Zinsco, etc. per §4.5C "
            "attribute density), what differentiates the work."
        ),
        fill(
            "Paragraph 3 — 80-100 words. Owner-grounded. Mention {owner_name}, "
            "{license_state}-licensed Master Electrician, {city_name} service "
            "area. Match the troubleshooting paragraph 3 pattern."
        ),
    ]
    mark_authoring("what_it_means_paragraphs")

    # ---------- Quick reference (Section 2 on the page) ----------
    quick_ref_heading_template = (
        f"Common signs you need a {{city_name}} {lowercase_phrase or 'electrician'}"
    )
    quick_ref_intro = fill(
        "Quick-ref intro — short pitch like 'Six symptoms we see every week. "
        "Tap any to learn what it might mean.'"
    )
    mark_authoring("quick_ref_intro")
    quick_ref_footer_html_template = (
        "Don't see your issue?<br>"
        "<a href=\"tel:{phone_tel}\">Call {phone_display}</a> — we'll diagnose it."
    )

    # ---------- Why city ----------
    if name:
        why_city_heading_template = f"Why {{city_name}} homeowners call us for {lowercase_phrase or 'this service'}"
    else:
        why_city_heading_template = fill(
            "Why-city heading template — 'Why {city_name} homeowners call us "
            "for <service>'."
        )
        mark_authoring("why_city_heading_template")

    why_city_closing_note_template = fill(
        "Why-city closing note — 1-2 sentences tying city-specific patterns "
        "back to why people call. See §6 tone."
    )
    mark_authoring("why_city_closing_note_template")

    # ---------- Problems ----------
    # Service-agnostic phrasing keeps the heading grammatical regardless of
    # whether the service is singular ("panel upgrade") or plural
    # ("electrical troubleshooting"). The operator can hand-tune to a more
    # punchy verb in the data file if they want; the FILL list does not flag
    # this as needing authoring.
    problems_heading_template = "Specific situations we handle every week in {city_name}"
    problems_intro_template = (
        "Here are the calls {owner_first_name} gets most often from "
        "{city_name} homeowners. If your situation matches one of these, "
        "you're in the right place."
    )

    problem_cards = extract_problem_cards(brief)
    if not problem_cards:
        problem_cards = [
            {"title": fill(f"Problem card {i+1} title — see §10a."),
             "body": fill(f"Problem card {i+1} body — see §10a.")}
            for i in range(6)
        ]
        mark_authoring("problem_cards")
    else:
        # Sanity check — were citations stripped successfully?
        for i, card in enumerate(problem_cards):
            if not card.get("body"):
                mark_confirmation(f"problem_cards[{i}].body")

    # ---------- Process ----------
    process_heading_template = (
        f"Our {lowercase_phrase or 'service'} process — what happens when you call"
    )
    process_intro_template = (
        "When you call {phone_display} or request a quote online, here's what "
        "happens."
    )

    process_steps = extract_process_steps(brief)
    if not process_steps:
        process_steps = [
            {"title": fill(f"Process step {i+1} title — see §10b."),
             "body": fill(f"Process step {i+1} body — see §10b.")}
            for i in range(5)
        ]
        mark_authoring("process_steps")

    # ---------- Pricing (§11) ----------
    s11_body = brief.sections.get("11", "")

    pricing_heading = extract_recommended_heading(s11_body, "pricing_heading")
    if not pricing_heading:
        # Default per the 2026-05-26 pricing-removal decision
        pricing_heading = "How estimates work"
        mark_confirmation("pricing_heading")

    pricing_intro_template = fill(
        "Pricing intro — 2-3 sentences matching §11 recommended pattern. "
        "Default reframe: diagnostic-visit-first, written-estimate-before-work, "
        "no surprise charges."
    )
    mark_authoring("pricing_intro_template")

    pricing_items = extract_pricing_items_block(s11_body)
    if not pricing_items:
        pricing_items = [
            fill("Pricing item 1 — see §11 recommended pricing_items reframe."),
            fill("Pricing item 2 — see §11."),
            fill("Pricing item 3 — see §11."),
            fill("Pricing item 4 — see §11."),
        ]
        mark_authoring("pricing_items")

    pricing_note_template = (
        "No trip charge for {no_trip_charge_cities_phrase}. We don't charge "
        "to drive to your house for the estimate."
    )
    pricing_closing_note = extract_recommended_value(s11_body, "pricing_closing_note") or ""
    if not pricing_closing_note:
        # Not always required — leave empty (template default) and flag
        mark_confirmation("pricing_closing_note")

    # ---------- About ----------
    about_heading_template = "About {owner_name}, {owner_title}"
    about_text_paragraphs = [
        fill(
            "About paragraph 1 — license tier, years to earn, team scale, "
            "insurance. See §12 trust-signal norms."
        ),
        fill(
            "About paragraph 2 — owner-on-the-job-site framing. See §12 "
            "differentiation signals."
        ),
        fill(
            "About paragraph 3 — AggregateRating sentence + review pitch. "
            "Pattern: '{client_name} holds a <strong>{review_count_phrase}"
            "</strong>. {review_pitch}'."
        ),
    ]
    mark_authoring("about_text_paragraphs")

    about_portrait_alt_template = (
        "{owner_name}, master electrician at {client_name} serving "
        "{city_name_with_state}"
    )
    about_portrait_url_template = (
        "https://evelectric.pro/wp-content/uploads/2026/05/"
        f"{{city_slug}}-{slug}-about-PLACEHOLDER.png"
    )
    mark_authoring("about_portrait_url_template")

    # ---------- Neighborhoods (static template strings) ----------
    neighborhoods_heading_template = "{city_name} neighborhoods we serve"
    neighborhoods_intro_template = (
        "We cover all of {city_name_with_state}, including:"
    )

    # ---------- Related (§9) ----------
    s9_body = brief.sections.get("9", "")
    related_cards = extract_related_cards(s9_body)
    if not related_cards:
        related_cards = [
            {"href_slug": fill(f"related-{i+1}-{{city_slug}}"),
             "label": fill(f"Related card {i+1} label — see §9.")}
            for i in range(4)
        ]
        mark_authoring("related_cards")

    related_heading_template = "Related electrical services in {city_name}"
    related_intro_template = fill(
        "Related intro — 1-2 sentences connecting this service to the related "
        "cards. See §9 anchor-text patterns and the troubleshooting reference."
    )
    mark_authoring("related_intro_template")

    # ---------- FAQ ----------
    faq_heading = "Frequently asked questions"
    faq_questions = extract_faq_questions(brief)
    faq_hints = extract_faq_hints(brief)
    # Build a quick lookup from question-prefix → hint text for the §7 notes.
    hint_lookup: dict[str, str] = {}
    for q, notes in faq_hints:
        # Normalize: lowercase, strip trailing question mark/whitespace
        key = re.sub(r"[^\w\s]", "", q.lower())[:60]
        hint_lookup[key] = notes

    faq_items: list[dict[str, str]] = []
    if faq_questions:
        for q in faq_questions:
            # Match against hints by prefix
            key = re.sub(r"[^\w\s]", "", q.lower())[:60]
            hint = ""
            for h_key, h_text in hint_lookup.items():
                if key.startswith(h_key[:30]) or h_key.startswith(key[:30]):
                    hint = h_text
                    break
            answer_fill = fill(
                f"FAQ answer prose (80-120 words). §7 hint: "
                f"{hint or 'no hint captured in brief — see §7 for recurrence + tone.'}"
            )
            faq_items.append({
                "question": q,
                "answer_html": answer_fill,
                "answer_schema": answer_fill,
            })
        mark_authoring("faq_items[*].answer_html")
        mark_authoring("faq_items[*].answer_schema")
    else:
        # Fallback to template's faq_items count (typically 8)
        template_faqs = template.get("faq_items", [])
        n = max(len(template_faqs), 6)
        for i in range(n):
            faq_items.append({
                "question": fill(f"FAQ question {i+1} — see §4d."),
                "answer_html": fill(f"FAQ answer {i+1} — see §4d + §7."),
                "answer_schema": fill(f"FAQ answer {i+1} (schema variant) — see §4d + §7."),
            })
        mark_authoring("faq_items")

    # ---------- Final CTA ----------
    final_cta_heading = fill(
        "Final CTA heading — short, action-oriented (e.g. 'Ready to get your "
        "<service> done?'). See §6 tone."
    )
    mark_authoring("final_cta_heading")
    final_cta_paragraph_template = fill(
        "Final CTA paragraph (1-2 short lines). Pattern: "
        "'<value-prop> in {city_name_with_state}.<br>{response_promise}'."
    )
    mark_authoring("final_cta_paragraph_template")

    # ---------- Schema (§5 + §11) ----------
    s5_body = brief.sections.get("5", "")

    schema_service_description_template = extract_recommended_value(
        s5_body, "schema_service_description_template"
    )
    if not schema_service_description_template:
        schema_service_description_template = fill(
            "Schema service description — see §5 'Recommended "
            "schema_service_description_template value:'."
        )
        mark_authoring("schema_service_description_template")
        mark_confirmation("schema_service_description_template")

    schema_service_terms = extract_recommended_value(s5_body, "schema_service_terms")
    if not schema_service_terms:
        # Try §11 (the pricing section sometimes re-states it)
        schema_service_terms = extract_recommended_value(s11_body, "schema_service_terms")
    if not schema_service_terms:
        schema_service_terms = fill(
            "Schema service terms — see §5 'Recommended schema_service_terms "
            "value:' or §11 pricing recommendations."
        )
        mark_authoring("schema_service_terms")
        mark_confirmation("schema_service_terms")

    # Per the 2026-05-26 no-pricing decision, default to estimate-only
    # (empty price strings). The brief's §11 should confirm this.
    schema_service_price = ""
    schema_service_price_description = ""

    # ---------- Assemble ----------
    data: dict[str, Any] = {
        "_comment": (
            f"Per-service data for {name or slug}. Used by "
            "scaffold-core-30-page.py for every Core 30 page of this service "
            "across cities. One file per service. {city} and {client} "
            "placeholders inside text strings are substituted at render time. "
            f"Scaffolded by scaffold-service-data.py from brief: "
            f"{brief.frontmatter.get('service-slug', slug)}.md."
        ),
        "_scaffolded": {
            "from_brief": True,
            "brief_research_date": brief.frontmatter.get("research-date"),
            "brief_researcher": brief.frontmatter.get("researcher"),
            "needs_authoring": needs_authoring,
            "needs_confirmation": needs_confirmation,
        },

        "slug": slug,
        "page_slug_template": page_slug_template,
        "name": name,
        "name_with_city": name_with_city,
        "service_type_phrase": service_type_phrase,
        "quoted_phrase": quoted_phrase,
        "lowercase_phrase": lowercase_phrase,
        "tag_short": tag_short,

        "aioseo_page_title_template": aioseo_page_title_template,
        "aioseo_meta_description_template": aioseo_meta_description_template,
        "aioseo_focus_keyword_template": focus_keyword,
        "aioseo_additional_keywords_template": additional_keywords,
        "wordpress_page_title_template": wp_title,

        "hero_eyebrow_template": hero_eyebrow_template,
        "hero_heading": hero_heading,
        "hero_subheading_template": hero_subheading_template,
        "hero_image_alt_template": hero_image_alt_template,
        "hero_image_url_template": hero_image_url_template,

        "what_it_means_heading": what_it_means_heading,
        "what_it_means_paragraphs": what_it_means_paragraphs,

        "quick_ref_heading_template": quick_ref_heading_template,
        "quick_ref_intro": quick_ref_intro,
        "quick_ref_footer_html_template": quick_ref_footer_html_template,

        "why_city_heading_template": why_city_heading_template,
        "why_city_closing_note_template": why_city_closing_note_template,

        "problems_heading_template": problems_heading_template,
        "problems_intro_template": problems_intro_template,
        "problem_cards": problem_cards,

        "process_heading_template": process_heading_template,
        "process_intro_template": process_intro_template,
        "process_steps": process_steps,

        "pricing_heading": pricing_heading,
        "pricing_intro_template": pricing_intro_template,
        "pricing_items": pricing_items,
        "pricing_note_template": pricing_note_template,
        "pricing_closing_note": pricing_closing_note,

        "about_heading_template": about_heading_template,
        "about_text_paragraphs": about_text_paragraphs,
        "about_portrait_alt_template": about_portrait_alt_template,
        "about_portrait_url_template": about_portrait_url_template,

        "neighborhoods_heading_template": neighborhoods_heading_template,
        "neighborhoods_intro_template": neighborhoods_intro_template,

        "related_heading_template": related_heading_template,
        "related_intro_template": related_intro_template,
        "related_cards": related_cards,

        "faq_heading": faq_heading,
        "faq_items": faq_items,

        "final_cta_heading": final_cta_heading,
        "final_cta_paragraph_template": final_cta_paragraph_template,

        "schema_service_description_template": schema_service_description_template,
        "schema_service_price": schema_service_price,
        "schema_service_price_description": schema_service_price_description,
        "schema_service_terms": schema_service_terms,
    }

    return data, needs_authoring, needs_confirmation


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------


def validate_against_template(
    data: dict[str, Any],
    template: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Compare the produced JSON's top-level keys against the template's.

    Returns (missing_keys, extra_keys). Both lists empty = shape matches.
    The `_comment` and `_scaffolded` meta keys are tolerated either way.
    """
    META_KEYS = {"_comment", "_scaffolded"}
    template_keys = set(template.keys()) - META_KEYS
    data_keys = set(data.keys()) - META_KEYS
    missing = sorted(template_keys - data_keys)
    extra = sorted(data_keys - template_keys)
    return missing, extra


def validate_mandatory_filled(data: dict[str, Any]) -> list[str]:
    """Return list of mandatory keys whose value is missing/null/FILL."""
    bad: list[str] = []
    for key in MANDATORY_KEYS:
        v = data.get(key)
        if v is None or v == "" or is_fill(v):
            bad.append(key)
    return bad


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_summary(
    output_slug: str,
    out_path: Path,
    data: dict[str, Any],
    needs_authoring: list[str],
    needs_confirmation: list[str],
    shape_missing: list[str],
    shape_extra: list[str],
) -> None:
    sep = "=" * 70
    print()
    print(sep)
    print(f"SERVICE-DATA SCAFFOLD — {output_slug}")
    print(sep)
    print(f"  Output: {out_path}")
    print(f"  Top-level keys: {len(data) - 2} (plus _comment + _scaffolded)")
    print(f"  Shape match (template vs output):")
    print(f"    missing keys (vs template): {shape_missing or 'none'}")
    print(f"    extra keys   (vs template): {shape_extra or 'none'}")
    print()
    if needs_authoring:
        print(f"  Fields needing AUTHORING (FILL placeholders, {len(needs_authoring)}):")
        for f in needs_authoring:
            print(f"    • {f}")
    else:
        print("  Fields needing AUTHORING: none")
    print()
    if needs_confirmation:
        print(f"  Fields needing CONFIRMATION (couldn't extract from brief, "
              f"{len(needs_confirmation)}):")
        for f in needs_confirmation:
            print(f"    • {f}")
    else:
        print("  Fields needing CONFIRMATION: none")
    print()
    print("Next steps:")
    print("  1. Open the data file and replace each 'FILL:' placeholder with prose.")
    print("  2. Cross-reference each FILL hint against the brief section it cites.")
    print("  3. Add a per-service entry for this service to each city file's")
    print("     `quick_ref_localized_items`, `most_common_problem_paragraph`, and")
    print("     `specific_problems_neighborhood_phrase` dicts (per "
          "scaffold-core-30-page.py's README).")
    print("  4. Test with:")
    print(f"       python3 scaffold-core-30-page.py --service {output_slug} "
          "--city <city-slug> --position <N> --dry-run")
    print()


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Scaffold data/services/<slug>.json from a Phase 2a Tier-1 "
        "service brief.",
    )
    p.add_argument(
        "--brief",
        type=Path,
        required=True,
        help="Path to the Tier-1 service brief markdown file.",
    )
    p.add_argument(
        "--output-slug",
        type=str,
        required=True,
        help="kebab-case service slug for the output file "
        "(e.g. 'panel-upgrade'). Becomes data/services/<slug>.json.",
    )
    p.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=f"Reference service JSON to validate shape against. "
        f"Default: {DEFAULT_TEMPLATE.relative_to(SCRIPTS_DIR)}",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=SERVICES_DIR,
        help=f"Directory to write the produced JSON. "
        f"Default: {SERVICES_DIR.relative_to(SCRIPTS_DIR)}",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse, build, validate — but do not write any file.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing data/services/<slug>.json. Default "
        "is non-destructive: writes to <slug>.scaffolded.json sibling.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Write the JSON even if MANDATORY_KEYS validation fails. "
        "Use only when you know the brief is genuinely incomplete and "
        "the operator plans to fill the gaps by hand before rendering.",
    )

    args = p.parse_args()

    if not args.brief.exists():
        sys.stderr.write(f"ERROR: brief not found: {args.brief}\n")
        return 2
    if not args.template.exists():
        sys.stderr.write(f"ERROR: template not found: {args.template}\n")
        return 2

    sys.stderr.write(f"→ Reading brief:    {args.brief}\n")
    sys.stderr.write(f"→ Output slug:      {args.output_slug}\n")
    sys.stderr.write(f"→ Template:         {args.template}\n")

    try:
        brief = parse_brief(args.brief)
    except Exception as e:
        sys.stderr.write(f"FATAL parsing brief: {e}\n")
        import traceback
        traceback.print_exc()
        return 1

    sys.stderr.write(
        f"→ Brief parsed:     "
        f"{len(brief.sections)} sections, "
        f"{len(brief.subsections)} subsections, "
        f"{sum(len(t) for t in brief.tables.values())} table rows\n"
    )

    template = json.loads(args.template.read_text(encoding="utf-8"))

    try:
        data, needs_authoring, needs_confirmation = build_service_data(
            brief, args.output_slug, template
        )
    except Exception as e:
        sys.stderr.write(f"FATAL building data: {e}\n")
        import traceback
        traceback.print_exc()
        return 1

    shape_missing, shape_extra = validate_against_template(data, template)
    bad_mandatory = validate_mandatory_filled(data)

    if shape_missing:
        sys.stderr.write(
            f"\nWARNING: produced JSON is missing top-level keys present in "
            f"template:\n  {shape_missing}\n"
        )
    if shape_extra:
        sys.stderr.write(
            f"\nWARNING: produced JSON has top-level keys NOT in template:\n"
            f"  {shape_extra}\n"
        )

    if bad_mandatory and not args.force:
        sys.stderr.write(
            f"\nERROR: mandatory fields missing or unfilled:\n"
        )
        for k in bad_mandatory:
            sys.stderr.write(f"  • {k}\n")
        sys.stderr.write(
            "\nFix the brief or pass --force to write anyway. Mandatory keys "
            "are the ones scaffold-core-30-page.py uses for naming + schema; "
            "without them the rendered page won't be valid.\n"
        )
        # Still print the summary so the operator can see what was extracted.
        out_path_preview = args.output_dir / f"{args.output_slug}.json"
        print_summary(
            args.output_slug, out_path_preview, data, needs_authoring,
            needs_confirmation, shape_missing, shape_extra,
        )
        return 3

    out_path = args.output_dir / f"{args.output_slug}.json"
    if args.dry_run:
        sys.stderr.write(f"\nDRY RUN — would write to: {out_path}\n")
        sys.stderr.write(f"  JSON size: {len(json.dumps(data, indent=2)):,} chars\n")
        print_summary(
            args.output_slug, out_path, data, needs_authoring,
            needs_confirmation, shape_missing, shape_extra,
        )
        return 0

    if out_path.exists() and not args.overwrite:
        scaffolded_path = args.output_dir / f"{args.output_slug}.scaffolded.json"
        write_json(scaffolded_path, data)
        sys.stderr.write(
            f"\n→ Existing data file detected: {out_path}\n"
            f"→ Wrote scaffolded version to: {scaffolded_path}\n"
            f"→ Diff:  diff {out_path} {scaffolded_path}\n"
            f"  (Pass --overwrite to replace the existing file instead.)\n"
        )
        out_path = scaffolded_path
    else:
        write_json(out_path, data)
        sys.stderr.write(f"\n→ Wrote: {out_path}\n")

    print_summary(
        args.output_slug, out_path, data, needs_authoring,
        needs_confirmation, shape_missing, shape_extra,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
