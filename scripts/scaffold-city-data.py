#!/usr/bin/env python3
"""
scaffold-city-data.py — Phase 3b city scaffolder.

Reads a Phase 2b city brief (markdown) plus zero or more Phase 2c
service-x-city intersection briefs and produces a populated
`data/cities/<slug>.json` data file that `scaffold-page.py`
consumes.

The city JSON has a dual shape:

  - Top-level city-only fields (slug, neighborhoods, housing_patterns
    title/neighborhood_examples/context_paragraph, geographic_anchor_paragraph,
    audience_descriptor, other_areas_paragraph, no_trip_charge_cities,
    ev_charger_homes_phrase, distance_from_hq_phrase) come from the city brief.

  - Service-keyed nested dicts (`quick_ref_localized_items`,
    `most_common_problem_paragraph`, `specific_problems_neighborhood_phrase`,
    `ev_neighborhood_phrase`, `distance_phrase`)
    come from intersection briefs, one entry per service slug.

  - `housing_patterns[].symptoms` is a single string per pattern in the consumer.
    When intersection briefs are passed, the FIRST one's symptoms voicing wins
    (override with `--symptoms-from <service-slug>`). On re-run with no
    intersection brief, the existing string is preserved.

USAGE
-----
First run for a new city (city brief only — service-keyed dicts will be empty):

    python scaffold-city-data.py \\
        --city-brief ~/workspace/second-brain/05_shared-intelligence/research-briefs/cities/vienna-va.md \\
        --output-slug vienna-va

With a single intersection brief (panel-upgrade for Vienna):

    python scaffold-city-data.py \\
        --city-brief ~/workspace/second-brain/05_shared-intelligence/research-briefs/cities/vienna-va.md \\
        --intersection-briefs panel-upgrade \\
        --output-slug vienna-va

With multiple intersection briefs (comma-separated service slugs):

    python scaffold-city-data.py \\
        --city-brief .../cities/vienna-va.md \\
        --intersection-briefs panel-upgrade,troubleshooting,ev-charger \\
        --output-slug vienna-va

Re-run later when a new intersection brief lands. Existing service-keyed
entries are preserved; only the new service is added:

    python scaffold-city-data.py \\
        --city-brief .../cities/vienna-va.md \\
        --intersection-briefs whole-house-rewire \\
        --output-slug vienna-va

DATA FLOW
---------
1. Parse the city brief. Fill top-level city-only fields.
2. For each intersection brief, parse and fill the service-keyed slots
   (`quick_ref_localized_items[<service>]`,
   `most_common_problem_paragraph[<service>]`,
   `specific_problems_neighborhood_phrase[<service>]`).
3. If the output JSON already exists, load it as the starting point and
   MERGE — preserves service-keyed entries for services NOT in this run.
4. `housing_patterns[].symptoms` is set from the chosen primary intersection
   brief (default: first in the list, override via `--symptoms-from`).
5. Write `data/cities/<output-slug>.json`.

DESIGN NOTES
------------
- Non-destructive: if `data/cities/<slug>.json` already exists, the script
  merges into it. Pass `--overwrite` to force a fresh write that drops
  unrecognized keys.
- The "How the scaffolder consumes this brief" table at the bottom of each
  intersection brief is the primary source — it spells out literal proposed
  content for `most_common_problem_paragraph[<service>]`,
  `specific_problems_neighborhood_phrase[<service>]`, `ev_charger_homes_phrase`,
  and `housing_patterns[].symptoms`. The script extracts those strings directly.
- For `quick_ref_localized_items[<service>]`, no brief section gives literal
  {summary, body} pairs — the cleanest faithful move is to seed `summary` from
  §4d's distilled-questions table and emit body as `TBD: ...` so the operator
  authors the final copy before publish.
- The intersection brief for the same service is resolved at:
  `<city-brief-parent>/../intersections/<service-slug>--<city-slug>.md`
  (or pass full paths via `--intersection-briefs path1.md,path2.md`).
- `distance_from_hq_phrase` is client-scoped (depends on which client's HQ
  city). If `--client-slug` is passed and `data/client-<slug>.json` exists,
  the script looks up the client's locality and reads the matching drive-time
  row from the city brief §9 to compose the phrase. Otherwise the field is
  left as TBD for Phase 3c.

REFERENCES
----------
- City brief template:
    second-brain/05_shared-intelligence/research-briefs/_template-city-brief.md
- Intersection brief template:
    second-brain/05_shared-intelligence/research-briefs/_template-intersection-brief.md
- Blueprint (Phase 3b):
    second-brain/05_shared-intelligence/blueprints/client-seo-onboarding-automation.md
- Reference JSON shape:
    repos/ai-agency-core/scripts/data/cities/vienna-va.json
- Consumer:
    repos/ai-agency-core/scripts/scaffold-page.py (see build_context,
    render_quick_ref_items, render_pattern_cards)
- Sibling scaffolder (style/pattern source):
    repos/ai-agency-core/scripts/scaffold-client-data.py
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
CITIES_DIR = DATA_DIR / "cities"


# Tokens that mean "this value was not captured in the brief"
_MISSING_TOKENS = (
    "TBD",
    "NOT captured",
    "Not captured",
    "not captured",
    "(pending",
    "Pending",
    "unconfirmed",
    "unknown",
    "(TBD",
    "not yet",
    "Not yet",
    "<TBD",
)

_CITATION_PATTERN = re.compile(r"\[source:[^\]]*\]")
_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


# ----------------------------------------------------------------------------
# Generic brief parsing — same model as scaffold-client-data.py
# ----------------------------------------------------------------------------


def strip_citations(value: str) -> str:
    cleaned = _CITATION_PATTERN.sub("", value)
    return cleaned.strip()


def is_missing(value: Optional[str]) -> bool:
    if value is None or value == "":
        return True
    v = value.strip()
    if v in {"—", "-", "–", "N/A", "n/a"}:
        return True
    leading = re.sub(r"^[\*_⚠️🔴🟡✅❓⚠\s<>]+", "", v)
    leading_lower = leading.lower()
    for token in _MISSING_TOKENS:
        if leading_lower.startswith(token.lower()):
            return True
    return False


@dataclass
class Brief:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    path: Optional[Path] = None


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
    """`## N. Title` headers carve out sections. Returns {N -> body}."""
    sections: dict[str, str] = {}
    header_pattern = re.compile(r"^## (\d+)\. .*$", re.MULTILINE)
    matches = list(header_pattern.finditer(text))
    for i, m in enumerate(matches):
        num = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[start:end].strip()
    return sections


def parse_markdown_tables(section_body: str) -> list[list[dict[str, str]]]:
    """Pull every markdown table out of a section body."""
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
    return Brief(
        frontmatter=parse_frontmatter(text),
        sections=split_sections(text),
        raw_text=text,
        path=brief_path,
    )


# ----------------------------------------------------------------------------
# Generic value extractors
# ----------------------------------------------------------------------------


def find_value_in_table(
    rows: list[dict[str, str]],
    label_patterns: list[str],
) -> Optional[str]:
    """First row whose first column substring-matches any pattern → value column."""
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


def strip_md_decorations(value: Optional[str]) -> Optional[str]:
    """Strip backticks, quotes, and bold/italic markers wrapping a value cell."""
    if value is None:
        return None
    v = value.strip()
    # Strip outer **...**, *...*, _..._
    v = re.sub(r"^\*\*(.*)\*\*$", r"\1", v).strip()
    v = re.sub(r"^\*(.*)\*$", r"\1", v).strip()
    v = re.sub(r"^_(.*)_$", r"\1", v).strip()
    # Strip surrounding backticks
    if v.startswith("`") and v.endswith("`"):
        v = v[1:-1].strip()
    # Strip surrounding double-quotes
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1].strip()
    return v


def collapse_blockquote(raw: str) -> str:
    """Turn a multi-line `> ...` blockquote into one cleaned line."""
    lines = [ln.lstrip("> ").rstrip() for ln in raw.splitlines()]
    text = " ".join(ln for ln in lines if ln)
    return strip_citations(text).strip()


def extract_blockquote_after(label: str, body: str) -> Optional[str]:
    """Find a blockquote that follows a labeled line.

    Tolerates the three label formats the templates use:
      `Label:` then blockquote
      `**Label:**` then blockquote (label and colon both inside bold)
      `**Label**:` then blockquote (colon outside bold)
    """
    # `[*:\s]*` between the label and the first `\n\n` swallows any combo of
    # trailing bold markers, colon, and whitespace.
    pattern = re.compile(
        rf"\**\s*{re.escape(label)}[*:\s]*\n\n((?:>\s*.*\n?)+)",
        re.IGNORECASE,
    )
    m = pattern.search(body)
    if not m:
        return None
    return collapse_blockquote(m.group(1))


# ----------------------------------------------------------------------------
# City brief — section extractors
# ----------------------------------------------------------------------------


def extract_city_identity(brief: Brief) -> dict[str, Any]:
    """§1 — the field/value table of slug, name, etc.

    Falls back to frontmatter for slug/name/state/county if the table parse
    misses anything (the frontmatter is always populated per the template).
    """
    out: dict[str, Any] = {}
    section_body = brief.sections.get("1", "")
    rows: list[dict[str, str]] = []
    for table in parse_markdown_tables(section_body):
        rows.extend(table)

    direct_map = {
        "slug": ["slug"],
        "name": ["`name`", "name "],
        "name_with_state": ["name_with_state"],
        "state": ["`state`"],
        "state_full": ["state_full"],
        "county": ["`county`", "county "],
        "county_full": ["county_full"],
    }
    for field_name, patterns in direct_map.items():
        v = find_value_in_table(rows, patterns)
        v = strip_md_decorations(v)
        if v and not is_missing(v):
            out[field_name] = v

    # Frontmatter fallbacks (always present per template)
    fm = brief.frontmatter
    if "slug" not in out and fm.get("city-slug"):
        out["slug"] = fm["city-slug"]
    if "name" not in out and fm.get("city-name"):
        out["name"] = fm["city-name"]
    if "state" not in out and fm.get("state"):
        out["state"] = fm["state"]
    if "county" not in out and fm.get("county"):
        out["county"] = fm["county"]
    if "name_with_state" not in out and "name" in out and "state" in out:
        out["name_with_state"] = f"{out['name']}, {out['state']}"

    # state_full fallback via lookup
    state_full_map = {
        "VA": "Virginia",
        "MD": "Maryland",
        "DC": "District of Columbia",
        "WV": "West Virginia",
        "PA": "Pennsylvania",
        "NC": "North Carolina",
        "DE": "Delaware",
    }
    if "state_full" not in out and out.get("state") in state_full_map:
        out["state_full"] = state_full_map[out["state"]]

    # county_full fallback
    if "county_full" not in out and "county" in out and "state_full" in out:
        out["county_full"] = f"{out['county']}, {out['state_full']}"

    return out


def extract_geographic_anchor_paragraph(brief: Brief) -> Optional[str]:
    body = brief.sections.get("2", "")
    # Try common label variants the template + worked example use
    for label in (
        "Geographic anchor paragraph (ready for JSON)",
        "**Geographic anchor paragraph (ready for JSON)**",
        "Geographic anchor paragraph",
    ):
        v = extract_blockquote_after(label, body)
        if v:
            return v
    return None


def extract_neighborhoods(brief: Brief) -> list[dict[str, str]]:
    """§3 table — capture Name + Blurb columns (positions 0 and 1).

    The template's table can have more columns (dominant decade, notable
    features). We only need name + blurb for the JSON. Skip rows whose first
    cell contains a parenthetical like '(proposed additional entry)' — those
    are notes about future expansion, not entries the current JSON should
    carry.
    """
    body = brief.sections.get("3", "")
    out: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for table in parse_markdown_tables(body):
        for row in table:
            keys = list(row.keys())
            if len(keys) < 2:
                continue
            name = strip_md_decorations(row[keys[0]]) or ""
            blurb_raw = row[keys[1]]
            blurb = strip_md_decorations(blurb_raw) or ""
            if not name or not blurb:
                continue
            # Skip placeholder rows like "(proposed additional entry)"
            if "(proposed" in blurb.lower():
                # Strip the marker then keep the entry only if explicitly approved
                # (template phrasing: "(proposed additional entry) <real blurb>")
                # By default we drop proposed entries from the active JSON.
                continue
            # Skip header-restating rows (e.g. "Name | Blurb (5-12 words)")
            if name.lower() == "name" or "(5-12" in name.lower():
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            # Ampersand encoding — the existing JSON uses "&amp;" for HTML safety
            blurb_encoded = blurb.replace(" & ", " &amp; ").replace("&O", "&amp;O")
            # Don't double-encode if already encoded
            blurb_encoded = blurb_encoded.replace("&amp;amp;", "&amp;")
            out.append({"name": name, "blurb": blurb_encoded})
    return out


def extract_audience_descriptor(brief: Brief) -> Optional[str]:
    body = brief.sections.get("4", "")
    for label in (
        "Audience descriptor (ready for JSON)",
        "**Audience descriptor (ready for JSON)**",
        "Audience descriptor",
    ):
        v = extract_blockquote_after(label, body)
        if v:
            return v
    return None


def extract_housing_patterns(brief: Brief) -> list[dict[str, str]]:
    """§5 — each `### Pattern N: <title>` subsection has a Field/Value table.

    Pull title, neighborhood_examples, context_paragraph. Leave symptoms as
    a TBD placeholder — it gets filled by the intersection brief.
    """
    body = brief.sections.get("5", "")
    # Find all "### Pattern N: <title>" subsection blocks
    subsections = re.split(r"^### Pattern \d+:", body, flags=re.MULTILINE)
    # First element is the prose before any pattern subsection — skip
    if len(subsections) < 2:
        return []
    out: list[dict[str, str]] = []
    for sub in subsections[1:]:
        rows: list[dict[str, str]] = []
        for table in parse_markdown_tables(sub):
            rows.extend(table)
        title = strip_md_decorations(find_value_in_table(rows, ["`title`", "title"]))
        neigh = strip_md_decorations(find_value_in_table(rows, ["`neighborhood_examples`", "neighborhood_examples"]))
        ctx = strip_md_decorations(find_value_in_table(rows, ["`context_paragraph`", "context_paragraph"]))
        if title:
            # Match the existing JSON's HTML-encoded ampersand
            title_enc = title.replace(" & ", " &amp; ")
            pattern: dict[str, str] = {
                "title": title_enc,
                "neighborhood_examples": neigh or "",
                "context_paragraph": ctx or "",
                "symptoms": "<TBD by intersection brief>",
            }
            out.append(pattern)
    return out


def extract_no_trip_charge_cities(brief: Brief) -> list[str]:
    """§9 — the proposed default list. Format in the template:

        ```
        ["Vienna", "McLean", "Oakton", "Tysons", "Fairfax"]
        ```
    """
    body = brief.sections.get("9", "")
    # Look for a fenced code block holding a JSON-style array
    m = re.search(r"```\s*\n\s*(\[[^\]]+\])\s*\n\s*```", body)
    if m:
        try:
            arr = json.loads(m.group(1))
            if isinstance(arr, list):
                return [str(x).strip() for x in arr]
        except json.JSONDecodeError:
            pass
    return []


def extract_other_areas_paragraph(brief: Brief, href_prefix: str = "") -> Optional[str]:
    """§9 — the prose paragraph with bracket-links. Convert [[slug|Text]] and
    [[slug]] wikilinks to HTML <a href="/...path.../">Text</a>.

    `href_prefix` is passed through to `convert_wikilinks_to_html` to control
    the generated href paths. When empty, produces neutral `/{slug}/` hrefs.
    When set (e.g. "electrical-troubleshooting"), produces `/{prefix}-{slug}/`.
    """
    body = brief.sections.get("9", "")
    for label in (
        "Other-areas paragraph (ready for JSON)",
        "**Other-areas paragraph (ready for JSON)**",
        "Other-areas paragraph",
    ):
        v = extract_blockquote_after(label, body)
        if v:
            return convert_wikilinks_to_html(v, href_prefix=href_prefix)
    return None


def convert_wikilinks_to_html(text: str, href_prefix: str = "") -> str:
    """Convert `[[slug|Display]]` and `[[slug]]` to <a href="..."> tags.

    `href_prefix` is the service-slug prefix for the generated hrefs.
    When empty (default), produces neutral `/{slug}/` hrefs the operator
    overrides per page. When set (e.g. "electrical-troubleshooting"),
    produces `/{prefix}-{slug}/`.

    The downstream consumer template can substitute the service prefix at
    render time when this JSON gets wired into the per-page templates.
    """
    def _make_href(target: str) -> str:
        if href_prefix:
            return f"/{href_prefix}-{target}/"
        return f"/{target}/"

    return re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]|\[\[([^\]]+)\]\]",
                  lambda m: (
                      f'<a href="{_make_href(m.group(1))}">{m.group(2)}</a>'
                      if m.group(1) and m.group(2)
                      else f'<a href="{_make_href(m.group(3))}">{m.group(3)}</a>'
                  ),
                  text)


def extract_drive_time_to_hq(brief: Brief, hq_city: str) -> Optional[str]:
    """§9 — first-ring/second-ring neighbor city tables.

    Find a row whose first column matches `hq_city` and return the drive-time
    cell, formatted as `<City> is <DD> minutes from our home base in <HQ>`.
    """
    body = brief.sections.get("9", "")
    rows: list[dict[str, str]] = []
    for table in parse_markdown_tables(body):
        rows.extend(table)
    target = hq_city.lower().split(",")[0].strip()
    for row in rows:
        keys = list(row.keys())
        if not keys:
            continue
        cell = row[keys[0]].strip()
        # Normalize cell — strip "(City of)" qualifiers
        cell_norm = re.sub(r"\s*\(City of\)", "", cell, flags=re.IGNORECASE).lower()
        if target in cell_norm:
            # Drive time is usually the third column; column headers vary
            for col_name in keys[1:]:
                col_val = row[col_name].strip()
                m = re.search(r"(\d+\s*[-–]\s*\d+|\d+)\s*min", col_val, re.IGNORECASE)
                if m:
                    return m.group(0)
    return None


def compose_distance_phrase(drive_time: str, hq_city: str, current_city: str) -> str:
    """`<Current city> is <drive-time> from our home base in <HQ city>`."""
    return f"{current_city} is {drive_time} from our home base in {hq_city}"


# ----------------------------------------------------------------------------
# Intersection brief — section + consumption-table extractors
# ----------------------------------------------------------------------------


def extract_intersection_identity(brief: Brief) -> dict[str, str]:
    """§1 — service-slug + city-slug + service-name fall-back via frontmatter."""
    fm = brief.frontmatter
    out: dict[str, str] = {}
    if fm.get("service-slug"):
        out["service_slug"] = fm["service-slug"]
    if fm.get("city-slug"):
        out["city_slug"] = fm["city-slug"]
    # Also pull from §1 table when the frontmatter is short
    body = brief.sections.get("1", "")
    rows: list[dict[str, str]] = []
    for table in parse_markdown_tables(body):
        rows.extend(table)
    if "service_slug" not in out:
        v = strip_md_decorations(find_value_in_table(rows, ["Service slug", "service-slug"]))
        if v:
            out["service_slug"] = v
    if "city_slug" not in out:
        v = strip_md_decorations(find_value_in_table(rows, ["City slug", "city-slug"]))
        if v:
            out["city_slug"] = v
    return out


def extract_consumption_table_rows(brief: Brief) -> list[dict[str, str]]:
    """Find the `## How the scaffolder consumes this brief` section and return
    its primary three-column table's rows.

    Returns a list of rows with keys 'JSON field', 'Brief section',
    'Recommended content' (or whatever the actual header columns are).
    """
    text = brief.raw_text
    m = re.search(
        r"^##\s+How the scaffolder consumes this brief\s*$",
        text,
        re.MULTILINE,
    )
    if not m:
        return []
    rest = text[m.end():]
    # Cap at the next H2 heading
    next_h2 = re.search(r"^##\s+\S", rest, re.MULTILINE)
    if next_h2:
        rest = rest[: next_h2.start()]
    tables = parse_markdown_tables(rest)
    if not tables:
        return []
    # Prefer the largest table (the primary consumption mapping)
    return max(tables, key=len)


def extract_quoted_strings(value: str) -> list[str]:
    """Pull double-quoted substrings out of a cell. Multi-pattern support
    for housing_patterns[].symptoms voicing (Pattern 1: "..." Pattern 2: "...").
    """
    # Match "..."" with non-greedy capture, allowing escaped quotes
    return re.findall(r'"([^"]+)"', value)


def parse_consumption_value(field_name: str, value: str) -> Any:
    """Convert a consumption-table value cell into the JSON-ready value.

    Strategy depends on the field shape:
    - `most_common_problem_paragraph[<svc>]` / `specific_problems_neighborhood_phrase[<svc>]`:
      single quoted string
    - `ev_charger_homes_phrase`: single quoted phrase
    - `housing_patterns[].symptoms`: multiple `Pattern N (...): "..."` instances
    """
    cleaned = strip_citations(value)
    quoted = extract_quoted_strings(cleaned)

    if field_name.startswith("housing_patterns[].symptoms"):
        # Multiple quoted strings, one per pattern, in document order
        return quoted

    # Single-value fields: prefer the longest quoted string (handles the case
    # where the brief includes a parenthetical short quote followed by the
    # actual phrasing in another quote, e.g.
    # `"1960s-80s..." — works in the existing JSON's "..." shape`)
    if quoted:
        return max(quoted, key=len)

    # No quotes — return the cell with leading "Single paragraph: " etc. stripped
    cleaned = re.sub(r"^\s*Single paragraph:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def extract_service_keyed_value(rows: list[dict[str, str]], field_root: str, service_slug: str) -> Optional[Any]:
    """Find row whose first column references `<field_root>[<service_slug>]` (or
    similar) and parse its third-column value.

    `field_root` is the JSON field name without the [service] subscript, e.g.
    'most_common_problem_paragraph'.
    """
    # Match patterns like `most_common_problem_paragraph["panel-upgrade"]` or
    # `most_common_problem_paragraph[<svc>]` regardless of quote style
    patterns = [
        f'{field_root}["{service_slug}"]',
        f"{field_root}['{service_slug}']",
        f"{field_root}[{service_slug}]",
    ]
    for row in rows:
        keys = list(row.keys())
        if len(keys) < 3:
            continue
        label = row[keys[0]]
        # Strip backticks for matching
        label_plain = label.replace("`", "")
        for pat in patterns:
            if pat in label_plain:
                value_cell = row[keys[-1]]
                return parse_consumption_value(field_root, value_cell)
    # Also tolerate the row where field_root appears alone (no [service] —
    # used for shared phrases like `ev_charger_homes_phrase`)
    return None


def extract_unkeyed_value(rows: list[dict[str, str]], field_name: str) -> Optional[Any]:
    """For shared/un-keyed fields (`ev_charger_homes_phrase`)."""
    for row in rows:
        keys = list(row.keys())
        if len(keys) < 3:
            continue
        label = row[keys[0]].replace("`", "").strip()
        if label.split()[0] == field_name:
            return parse_consumption_value(field_name, row[keys[-1]])
    return None


def extract_distilled_questions(brief: Brief) -> list[dict[str, str]]:
    """§4d — pull the distilled `questions our city-page must answer` table.

    Used to seed `quick_ref_localized_items[<service>]`. The Question column
    becomes `summary`; `body` is marked TBD with the voicing-notes hint so the
    operator authors the final body. Brief promotion notes say
    'no invented facts' — TBD markers are the honest move.
    """
    body = brief.sections.get("4", "")
    # Find the §4d subsection
    m = re.search(r"###\s*4d\.\s.*?(?=###|\Z)", body, re.DOTALL)
    if not m:
        return []
    sub = m.group(0)
    out: list[dict[str, str]] = []
    for table in parse_markdown_tables(sub):
        for row in table:
            keys = list(row.keys())
            if len(keys) < 1:
                continue
            question_raw = row[keys[0]]
            question = strip_md_decorations(strip_citations(question_raw)) or ""
            if not question or question.lower() == "question":
                continue
            voicing = ""
            if len(keys) >= 3:
                voicing = strip_md_decorations(strip_citations(row[keys[2]])) or ""
            summary = question.strip().strip("?") + "?" if question.strip() else question
            # Strip surrounding quotes if present
            summary = summary.strip('"').strip("'").strip()
            if summary.startswith('"') and summary.endswith('"'):
                summary = summary[1:-1]
            body_marker = f"<!-- TBD: author body for this Q&A. Voicing: {voicing} -->" if voicing else "<!-- TBD: author body for this Q&A. -->"
            out.append({"summary": summary, "body": body_marker})
    return out


# ----------------------------------------------------------------------------
# JSON build + merge
# ----------------------------------------------------------------------------


def build_city_block(brief: Brief, href_prefix: str = "") -> dict[str, Any]:
    """Top-level city-only fields from the city brief."""
    out: dict[str, Any] = {
        "_comment": (
            "Per-city data for {name_full}. Used by scaffold-page.py for "
            "Section 2 quick-reference Q&As, Section 3 housing-stock pattern cards, "
            "Section 7 neighborhood list, Section 8 service-area paragraphs, JSON-LD "
            "audience.audienceType. One file per city across the Core 30."
        ),
    }

    identity = extract_city_identity(brief)
    out.update(identity)

    # Format _comment with the name once we know it
    if identity.get("name_with_state"):
        out["_comment"] = out["_comment"].format(name_full=identity["name_with_state"])
    elif identity.get("name"):
        out["_comment"] = out["_comment"].format(name_full=identity["name"])

    geo_para = extract_geographic_anchor_paragraph(brief)
    if geo_para:
        out["geographic_anchor_paragraph"] = geo_para

    audience = extract_audience_descriptor(brief)
    if audience:
        out["audience_descriptor"] = audience

    no_trip = extract_no_trip_charge_cities(brief)
    if no_trip:
        out["no_trip_charge_cities"] = no_trip

    neighborhoods = extract_neighborhoods(brief)
    if neighborhoods:
        out["neighborhoods"] = neighborhoods

    other_areas = extract_other_areas_paragraph(brief, href_prefix=href_prefix)
    if other_areas:
        out["other_areas_paragraph"] = other_areas

    patterns = extract_housing_patterns(brief)
    if patterns:
        out["housing_patterns"] = patterns

    return out


def apply_intersection_brief(
    city_json: dict[str, Any],
    intersection: Brief,
    update_symptoms: bool,
) -> str:
    """Mutate `city_json` to fold in this intersection brief's service-keyed
    slots. Returns the service-slug processed.
    """
    identity = extract_intersection_identity(intersection)
    service_slug = identity.get("service_slug")
    if not service_slug:
        raise ValueError(
            f"intersection brief {intersection.path} has no service-slug "
            "in frontmatter or §1 table"
        )

    rows = extract_consumption_table_rows(intersection)

    # Service-keyed nested dicts — initialize if missing
    for field_name in (
        "quick_ref_localized_items",
        "most_common_problem_paragraph",
        "specific_problems_neighborhood_phrase",
        "ev_neighborhood_phrase",
        "distance_phrase",
    ):
        city_json.setdefault(field_name, {})

    # `quick_ref_localized_items[<service>]` — seed from §4d distilled questions
    quick_ref_items = extract_distilled_questions(intersection)
    if quick_ref_items:
        city_json["quick_ref_localized_items"][service_slug] = quick_ref_items
    elif service_slug not in city_json["quick_ref_localized_items"]:
        city_json["quick_ref_localized_items"][service_slug] = []

    # `most_common_problem_paragraph[<service>]`
    mcpp = extract_service_keyed_value(rows, "most_common_problem_paragraph", service_slug)
    if mcpp:
        city_json["most_common_problem_paragraph"][service_slug] = mcpp

    # `specific_problems_neighborhood_phrase[<service>]`
    spnp = extract_service_keyed_value(rows, "specific_problems_neighborhood_phrase", service_slug)
    if spnp:
        city_json["specific_problems_neighborhood_phrase"][service_slug] = spnp

    # `ev_neighborhood_phrase[<service>]` — per-service neighborhood framing
    evnp = extract_service_keyed_value(rows, "ev_neighborhood_phrase", service_slug)
    if evnp:
        city_json["ev_neighborhood_phrase"][service_slug] = evnp

    # `distance_phrase[<service>]` — per-service distance/drive-time framing
    dp = extract_service_keyed_value(rows, "distance_phrase", service_slug)
    if dp:
        city_json["distance_phrase"][service_slug] = dp

    # Shared `ev_charger_homes_phrase` (single string; intersection brief may
    # propose a refinement)
    evp = extract_unkeyed_value(rows, "ev_charger_homes_phrase")
    if evp and not city_json.get("ev_charger_homes_phrase"):
        city_json["ev_charger_homes_phrase"] = evp

    # `housing_patterns[].symptoms` — only update if this brief is the primary
    if update_symptoms:
        symptoms_list = extract_service_keyed_value(rows, "housing_patterns[].symptoms", service_slug)
        # Older briefs may also use the un-keyed variant
        if not symptoms_list:
            for row in rows:
                keys = list(row.keys())
                if len(keys) < 3:
                    continue
                label = row[keys[0]].replace("`", "")
                if "housing_patterns[].symptoms" in label:
                    symptoms_list = parse_consumption_value(
                        "housing_patterns[].symptoms", row[keys[-1]]
                    )
                    break
        if isinstance(symptoms_list, list) and symptoms_list:
            patterns = city_json.get("housing_patterns") or []
            for i, sym in enumerate(symptoms_list):
                if i < len(patterns):
                    patterns[i]["symptoms"] = sym

    return service_slug


def load_existing_city_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def merge_top_level(existing: dict[str, Any], new: dict[str, Any], overwrite: bool) -> dict[str, Any]:
    """Merge city-only top-level fields. New wins for present keys; existing
    keys not in `new` are preserved unless `overwrite=True`.

    Special case for `housing_patterns[].symptoms`: the city brief produces
    `<TBD by intersection brief>` placeholders. On re-run, preserve any
    real symptoms strings already in the on-disk JSON instead of clobbering
    them back to TBD.
    """
    if overwrite:
        return dict(new)
    out = dict(existing)
    for k, v in new.items():
        if v is None or v == "" or v == []:
            continue
        if k == "housing_patterns" and isinstance(v, list) and isinstance(existing.get(k), list):
            merged_patterns: list[dict[str, Any]] = []
            existing_patterns = existing[k]
            for i, new_pat in enumerate(v):
                pat = dict(new_pat)
                if i < len(existing_patterns):
                    existing_sym = existing_patterns[i].get("symptoms")
                    new_sym = new_pat.get("symptoms", "")
                    # Keep existing symptoms when the new value is just a placeholder
                    if existing_sym and not is_missing(existing_sym) and (
                        not new_sym or is_missing(new_sym)
                    ):
                        pat["symptoms"] = existing_sym
                merged_patterns.append(pat)
            out[k] = merged_patterns
        else:
            out[k] = v
    return out


def merge_service_keyed(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Service-keyed dicts: union of services. New entries overwrite same-slug
    existing entries; other services are preserved.
    """
    out = dict(existing)
    for k, v in new.items():
        out[k] = v
    return out


def assemble_final_json(
    existing: dict[str, Any],
    city_block: dict[str, Any],
    intersection_briefs: list[Brief],
    primary_service: Optional[str],
    overwrite: bool,
) -> dict[str, Any]:
    """Combine city brief + intersection briefs + existing JSON into the final
    output."""
    # Decide which intersection brief gets to write housing_patterns[].symptoms
    if intersection_briefs:
        if primary_service:
            primary_idx = next(
                (i for i, b in enumerate(intersection_briefs)
                 if extract_intersection_identity(b).get("service_slug") == primary_service),
                0,
            )
        else:
            primary_idx = 0
    else:
        primary_idx = -1

    # Start from existing JSON (preserves unrelated service entries) unless --overwrite
    merged: dict[str, Any] = {} if overwrite else dict(existing)

    # Merge top-level city block
    top_merged = merge_top_level(merged, city_block, overwrite)
    merged.update(top_merged)

    # Preserve service-keyed dicts that exist in the on-disk JSON
    for field_name in (
        "quick_ref_localized_items",
        "most_common_problem_paragraph",
        "specific_problems_neighborhood_phrase",
        "ev_neighborhood_phrase",
        "distance_phrase",
    ):
        merged.setdefault(field_name, dict(existing.get(field_name, {})) if not overwrite else {})

    # Apply each intersection brief
    for i, brief in enumerate(intersection_briefs):
        apply_intersection_brief(merged, brief, update_symptoms=(i == primary_idx))

    # If symptoms still hold the city-brief placeholder, mark explicitly
    for p in merged.get("housing_patterns", []):
        if not p.get("symptoms") or p["symptoms"] == "<TBD by intersection brief>":
            if not intersection_briefs:
                p["symptoms"] = "<TBD: add an intersection brief for the primary service to fill this in>"

    # ev_charger_homes_phrase fallback — leave existing if intersection briefs
    # didn't propose a refinement
    if "ev_charger_homes_phrase" not in merged and existing.get("ev_charger_homes_phrase"):
        merged["ev_charger_homes_phrase"] = existing["ev_charger_homes_phrase"]

    # distance_from_hq_phrase — placeholder; resolved later via --client-slug
    if "distance_from_hq_phrase" not in merged:
        merged["distance_from_hq_phrase"] = (
            existing.get("distance_from_hq_phrase")
            or "<TBD: pass --client-slug to resolve from client HQ city>"
        )

    return merged


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------


_REQUIRED_TOP_LEVEL_UNIVERSAL = {
    "slug": str,
    "name": str,
    "name_with_state": str,
    "state": str,
    "state_full": str,
    "county": str,
    "county_full": str,
    "distance_from_hq_phrase": str,
    "geographic_anchor_paragraph": str,
    "audience_descriptor": str,
    "no_trip_charge_cities": list,
    "neighborhoods": list,
    "other_areas_paragraph": str,
}

# Fields required only for matrix/electrician-type city data
_REQUIRED_TOP_LEVEL_MATRIX = {
    "housing_patterns": list,
    "quick_ref_localized_items": dict,
    "most_common_problem_paragraph": dict,
    "specific_problems_neighborhood_phrase": dict,
    "ev_neighborhood_phrase": dict,
    "distance_phrase": dict,
    "ev_charger_homes_phrase": str,
}


def validate_shape(city_json: dict[str, Any], business_type: str = "electrician") -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block; warnings just notify.

    business_type controls which fields are required:
      - Universal fields are always required.
      - Matrix-specific fields (housing_patterns, ev_*, service-keyed dicts)
        are required only for matrix-type business types (e.g. electrician).
        Non-matrix types (e.g. restaurant) skip them.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Always validate universal fields
    required = dict(_REQUIRED_TOP_LEVEL_UNIVERSAL)
    # Matrix-type business types also require the service-keyed fields
    if business_type == "electrician":
        required.update(_REQUIRED_TOP_LEVEL_MATRIX)

    for field_name, expected_type in required.items():
        if field_name not in city_json:
            errors.append(f"missing field: {field_name}")
            continue
        if not isinstance(city_json[field_name], expected_type):
            errors.append(
                f"wrong type for {field_name}: expected {expected_type.__name__}, "
                f"got {type(city_json[field_name]).__name__}"
            )

    # Neighborhoods shape check
    for i, n in enumerate(city_json.get("neighborhoods", []) or []):
        if not isinstance(n, dict) or "name" not in n or "blurb" not in n:
            errors.append(f"neighborhoods[{i}] missing 'name' or 'blurb'")

    # Housing patterns shape check (only if present — non-matrix types may omit)
    for i, p in enumerate(city_json.get("housing_patterns", []) or []):
        for k in ("title", "neighborhood_examples", "context_paragraph", "symptoms"):
            if k not in p:
                errors.append(f"housing_patterns[{i}] missing '{k}'")

    # Service-keyed quick_ref shape check (only if present)
    for svc, items in (city_json.get("quick_ref_localized_items") or {}).items():
        if not isinstance(items, list):
            errors.append(f"quick_ref_localized_items[{svc}] is not a list")
            continue
        for j, it in enumerate(items):
            if not isinstance(it, dict) or "summary" not in it or "body" not in it:
                errors.append(f"quick_ref_localized_items[{svc}][{j}] missing 'summary' or 'body'")

    # TBD warning
    def has_tbd(s: Any) -> bool:
        return isinstance(s, str) and ("TBD" in s or "<TBD" in s)

    for k, v in city_json.items():
        if has_tbd(v):
            warnings.append(f"{k}: still TBD — needs follow-up")
    for i, p in enumerate(city_json.get("housing_patterns", []) or []):
        for k, v in p.items():
            if has_tbd(v):
                warnings.append(f"housing_patterns[{i}].{k}: still TBD")

    return errors, warnings


# ----------------------------------------------------------------------------
# Client HQ resolution for distance_from_hq_phrase
# ----------------------------------------------------------------------------


def resolve_distance_phrase(
    brief: Brief,
    client_slug: Optional[str],
    city_name: str,
) -> Optional[str]:
    """If --client-slug is given AND data/client-<slug>.json exists, look up the
    client's address.locality, then ask the city brief for the drive time.
    """
    if not client_slug:
        return None
    client_path = DATA_DIR / f"client-{client_slug}.json"
    if not client_path.is_file():
        sys.stderr.write(
            f"WARN: client data file not found at {client_path}; "
            "distance_from_hq_phrase will be TBD.\n"
        )
        return None
    try:
        client_data = json.loads(client_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(f"WARN: couldn't parse {client_path}: {e}\n")
        return None
    hq_city = (
        client_data.get("address", {}).get("locality")
        or client_data.get("brand_area_county")
        or ""
    )
    if not hq_city:
        sys.stderr.write(
            f"WARN: client {client_slug} has no address.locality; "
            "distance_from_hq_phrase will be TBD.\n"
        )
        return None
    drive_time = extract_drive_time_to_hq(brief, hq_city)
    if not drive_time:
        sys.stderr.write(
            f"WARN: city brief §9 has no drive time to {hq_city}; "
            "distance_from_hq_phrase will be TBD.\n"
        )
        return None
    return compose_distance_phrase(drive_time, hq_city, city_name)


# ----------------------------------------------------------------------------
# Intersection brief path resolution
# ----------------------------------------------------------------------------


def resolve_intersection_brief_path(
    spec: str, city_brief_path: Path, city_slug: str
) -> Path:
    """Resolve a --intersection-briefs spec to an actual file path.

    Spec can be:
      - a full path ending in .md (containing /)
      - a service slug like 'panel-upgrade' — resolved to
        `<city-brief-parent>/../intersections/<service>--<city-slug>.md`
    """
    if "/" in spec or spec.endswith(".md"):
        path = Path(spec).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    intersections_dir = city_brief_path.parent.parent / "intersections"
    return intersections_dir / f"{spec}--{city_slug}.md"


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Scaffold data/cities/<slug>.json from a city brief + intersection briefs.",
    )
    p.add_argument(
        "--city-brief", required=True, type=Path,
        help="Path to the Phase 2b city brief markdown file.",
    )
    p.add_argument(
        "--intersection-briefs", default="",
        help=(
            "Comma-separated list of intersection briefs. Each entry is either "
            "a service slug (resolved via convention: "
            "<city-brief-parent>/../intersections/<service>--<city-slug>.md) "
            "or a full path to a .md file."
        ),
    )
    p.add_argument(
        "--output-slug", required=True,
        help="City slug for the output file: data/cities/<output-slug>.json",
    )
    p.add_argument(
        "--output-folder", type=Path, default=None,
        help="Override the output directory (defaults to data/cities/).",
    )
    p.add_argument(
        "--client-slug", default=None,
        help=(
            "Optional client slug for resolving distance_from_hq_phrase. If "
            "data/client-<slug>.json exists, its address.locality drives the "
            "drive-time lookup from the city brief §9."
        ),
    )
    p.add_argument(
        "--symptoms-from", default=None,
        help=(
            "Service slug whose intersection brief fills "
            "housing_patterns[].symptoms. Defaults to the first brief in "
            "--intersection-briefs."
        ),
    )
    p.add_argument(
        "--overwrite", action="store_true",
        help=(
            "Drop the existing JSON entirely before writing (default: merge). "
            "Service-keyed entries for services NOT in this run are dropped."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Render and validate but don't write the file.",
    )
    p.add_argument(
        "--href-prefix", default="",
        help=(
            "Service-slug prefix for wikilink-to-HTML conversion in "
            "other_areas_paragraph. E.g. 'electrical-troubleshooting' produces "
            "hrefs like /electrical-troubleshooting-fairfax-va/. "
            "Default: empty (neutral /{slug}/ hrefs)."
        ),
    )
    p.add_argument(
        "--business-type", default="electrician",
        help=(
            "Business type for validation. Controls which fields are required. "
            "Default: electrician (requires EV fields, housing_patterns, etc.)."
        ),
    )

    args = p.parse_args()

    # --- Load + parse the city brief
    city_brief_path = args.city_brief.expanduser().resolve()
    if not city_brief_path.is_file():
        sys.stderr.write(f"ERROR: city brief not found: {city_brief_path}\n")
        return 2
    sys.stderr.write(f"→ City brief: {city_brief_path}\n")
    city_brief = parse_brief(city_brief_path)

    # --- Build the city-only top-level block
    city_block = build_city_block(city_brief, href_prefix=args.href_prefix)
    city_name = city_block.get("name_with_state") or city_block.get("name") or args.output_slug

    # --- Resolve intersection brief paths and parse them
    # Intersection filenames follow the CITY BRIEF's actual slug, not
    # --output-slug (which the operator may set to a test/staging slug).
    intersection_lookup_slug = (
        city_brief.frontmatter.get("city-slug")
        or city_block.get("slug")
        or args.output_slug
    )
    intersection_briefs: list[Brief] = []
    if args.intersection_briefs.strip():
        specs = [s.strip() for s in args.intersection_briefs.split(",") if s.strip()]
        for spec in specs:
            path = resolve_intersection_brief_path(
                spec, city_brief_path, intersection_lookup_slug
            )
            if not path.is_file():
                sys.stderr.write(f"ERROR: intersection brief not found: {path}\n")
                return 2
            sys.stderr.write(f"→ Intersection brief: {path}\n")
            intersection_briefs.append(parse_brief(path))

    # --- Resolve output path
    output_folder = args.output_folder.expanduser().resolve() if args.output_folder else CITIES_DIR
    output_path = output_folder / f"{args.output_slug}.json"

    # --- Load any existing JSON for non-destructive merge
    existing = load_existing_city_json(output_path)
    if existing and not args.overwrite:
        sys.stderr.write(f"→ Merging into existing: {output_path}\n")
    elif args.overwrite and existing:
        sys.stderr.write(f"→ --overwrite set; existing {output_path} will be replaced\n")

    # --- Assemble final JSON
    final = assemble_final_json(
        existing=existing,
        city_block=city_block,
        intersection_briefs=intersection_briefs,
        primary_service=args.symptoms_from,
        overwrite=args.overwrite,
    )

    # --- distance_from_hq_phrase via --client-slug
    if args.client_slug:
        phrase = resolve_distance_phrase(
            city_brief, args.client_slug, final.get("name") or args.output_slug
        )
        if phrase:
            final["distance_from_hq_phrase"] = phrase
            sys.stderr.write(f"→ Resolved distance_from_hq_phrase: {phrase}\n")

    # --- Stable key order: match the existing vienna-va.json shape
    key_order = [
        "_comment",
        "slug",
        "name",
        "name_with_state",
        "state",
        "state_full",
        "county",
        "county_full",
        "distance_from_hq_phrase",
        "geographic_anchor_paragraph",
        "audience_descriptor",
        "no_trip_charge_cities",
        "neighborhoods",
        "other_areas_paragraph",
        "housing_patterns",
        "quick_ref_localized_items",
        "most_common_problem_paragraph",
        "specific_problems_neighborhood_phrase",
        "ev_neighborhood_phrase",
        "distance_phrase",
        "ev_charger_homes_phrase",
    ]
    ordered: dict[str, Any] = {}
    for k in key_order:
        if k in final:
            ordered[k] = final[k]
    # Append any unrecognized keys at the end (preserves operator additions)
    for k, v in final.items():
        if k not in ordered:
            ordered[k] = v
    final = ordered

    # --- Validate
    errors, warnings = validate_shape(final, business_type=args.business_type)
    for w in warnings:
        sys.stderr.write(f"WARN: {w}\n")
    if errors:
        for e in errors:
            sys.stderr.write(f"ERROR: {e}\n")
        sys.stderr.write("\nValidation failed. Fix the brief or pass --overwrite=false to keep existing values.\n")
        return 4

    # --- Report
    services_present = sorted(set(
        list((final.get("quick_ref_localized_items") or {}).keys())
        + list((final.get("most_common_problem_paragraph") or {}).keys())
        + list((final.get("specific_problems_neighborhood_phrase") or {}).keys())
        + list((final.get("ev_neighborhood_phrase") or {}).keys())
        + list((final.get("distance_phrase") or {}).keys())
    ))
    sys.stderr.write(f"\n→ City:           {city_name}\n")
    sys.stderr.write(f"→ Neighborhoods:  {len(final.get('neighborhoods', []))}\n")
    sys.stderr.write(f"→ Patterns:       {len(final.get('housing_patterns', []))}\n")
    sys.stderr.write(f"→ Services keyed: {services_present or '(none)'}\n")
    sys.stderr.write(f"→ Warnings:       {len(warnings)}\n")

    if args.dry_run:
        sys.stderr.write("\nDRY RUN — no files written.\n")
        sys.stdout.write(json.dumps(final, indent=2, ensure_ascii=False) + "\n")
        return 0

    # --- Write
    output_folder.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(final, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    sys.stderr.write(f"\n→ Wrote: {output_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
