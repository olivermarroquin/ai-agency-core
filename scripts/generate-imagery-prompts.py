#!/usr/bin/env python3
"""
generate-imagery-prompts.py — compose Higgsfield prompts for a Core 30 page.

Phase 3d of the client-SEO-onboarding automation roadmap. Reads the page's
draft frontmatter + the Tier-1/2/3 research briefs + the client-fact brief +
any prior imagery-prompts-log.md files in the same client, and writes a
fresh `imagery-prompts-log.md` into the page folder with 3 ready-to-paste
Higgsfield prompts (Hero, About, optional Scene) plus outcome placeholders.

CLI
---
Basic:

    python3 generate-imagery-prompts.py \\
        --page-folder /path/to/<NN>-<slug>/ \\
        --client       ev-electric-services

Flags:

    --no-scene            Skip the optional Scene prompt (default: include it).
    --overwrite           Replace an existing imagery-prompts-log.md.
                          (Default: refuse — non-destructive on existing logs.)
    --dry-run             Print the rendered log to stdout, write nothing.
    --briefs-root <path>  Override the research-briefs root.
                          (Default: ~/workspace/second-brain/05_shared-intelligence/research-briefs)
    --core-30-root <path> Override where to scan for prior imagery logs.
                          (Default: derived from page-folder by walking up.)

DATA SOURCES
------------
1. <page-folder>/draft-v1.md            -- frontmatter: service, city, page-slug, position
2. data/client-<slug>.json              -- brand facts (owner name, brand colors, logo URL)
3. briefs/clients/<slug>/brief.md       -- client-fact brief (owner reference, wardrobe notes)
4. briefs/services/<service>.md         -- Tier-1 service brief (§8 image-style observations)
5. briefs/cities/<city-slug>.md         -- Tier-2 city brief (geographic anchor, setting)
6. briefs/intersections/<svc>--<cty>.md -- Tier-3 intersection brief (local angles, review themes)
7. Prior <client>/.../imagery-prompts-log.md files -- the learning loop

Missing briefs are non-fatal — the script flags each gap and continues.

LEARNING LOOP
-------------
On each run, the script scans every imagery-prompts-log.md file under the
client's core-30 folder and extracts:

  - Wardrobe blocks from the most recent keeper prompts (carry forward)
  - "Lesson worth carrying forward to SOP" callouts (bias future prompts)
  - Selected-variant markers (counts keepers per slot type)
  - About-portrait cache hits keyed by city (suggest reuse rather than regen)

The new log opens with a "## Keeper-bias notes from prior pages" header
that documents which lessons informed this run's prompt composition.

OUTPUT
------
Writes `<page-folder>/imagery-prompts-log.md` in the same shape as the
hand-written logs for pages 1 and 2 — frontmatter, setup context,
generations (Hero v1, About v1, Scene v1), open follow-ups, related.
Each generation includes a placeholder `**Selected variant:**` / `**Verdict:**` /
`**Outcome:**` block the operator fills in after running Higgsfield.

NO THIRD-PARTY DEPS — pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------

# No default client — must be specified explicitly via --client to prevent
# source-client-leak (see pattern-source-client-leak-audit).
DEFAULT_CLIENT = None
DEFAULT_BRIEFS_ROOT = Path.home() / "workspace/second-brain/05_shared-intelligence/research-briefs"

SCRIPTS_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS_DIR / "data"
SECOND_BRAIN = Path.home() / "workspace/second-brain"

# Reference-photo home per _meta/conventions.md § "Client imagery reference assets"
def reference_photo_home(client_slug: str) -> Path:
    return SECOND_BRAIN / "04_projects/clients/_active" / client_slug / "marketing-assets/reference-photos"


# ----------------------------------------------------------------------------
# Logging helpers (stderr — keeps stdout clean for --dry-run output)
# ----------------------------------------------------------------------------

def info(msg: str) -> None:
    sys.stderr.write(f"[info] {msg}\n")


def warn(msg: str) -> None:
    sys.stderr.write(f"[warn] {msg}\n")


def fatal(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"[error] {msg}\n")
    sys.exit(code)


# ----------------------------------------------------------------------------
# Frontmatter parsing (no PyYAML — we only need a few simple fields)
# ----------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract a simple flat YAML frontmatter block. Lists/dicts return as raw strings."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def read_draft_metadata(page_folder: Path) -> dict[str, str]:
    draft = page_folder / "draft-v1.md"
    if not draft.is_file():
        fatal(f"no draft-v1.md in {page_folder} — cannot derive page metadata")
    fm = parse_frontmatter(draft.read_text(encoding="utf-8"))
    required = ("service", "city", "page-slug")
    missing = [k for k in required if k not in fm]
    if missing:
        fatal(f"draft-v1.md frontmatter missing: {', '.join(missing)}")
    return fm


# ----------------------------------------------------------------------------
# City-slug derivation
# ----------------------------------------------------------------------------

# Page-slug shape: <service-slug>-<city>-<state>. We trust the page-slug
# more than the bare `city` field, because the page-slug encodes the state.

def derive_city_slug(meta: dict[str, str]) -> str:
    """Return e.g. 'vienna-va' from page metadata.

    Strategy (most-reliable first):
      1. If frontmatter `city:` is set AND page-slug ends in a 2-letter state,
         return `<city>-<state>`. This is the canonical convention.
      2. If page-slug starts with the service-slug, peel it off and use the rest.
      3. Fall back to the bare `city` frontmatter field.

    Examples:
      city='vienna' + page-slug='ev-charger-vienna-va' -> 'vienna-va'
      city='falls-church' + page-slug='panel-upgrade-falls-church-va' -> 'falls-church-va'
      city='' + page-slug='troubleshooting-mclean-va' (service=troubleshooting) -> 'mclean-va'
    """
    page_slug = meta.get("page-slug", "").strip()
    service = meta.get("service", "").strip()
    city = meta.get("city", "").strip()

    # 1. Combine bare city + trailing state from page-slug (most reliable)
    if city:
        parts = page_slug.split("-")
        if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
            state_suffix = parts[-1]
            # If city already ends with the state suffix, don't double it
            if city.endswith(f"-{state_suffix}"):
                return city
            return f"{city}-{state_suffix}"
        return city

    # 2. Service-prefix peel
    if page_slug and service and page_slug.startswith(service + "-"):
        return page_slug[len(service) + 1:]

    # 3. Bare page-slug
    return page_slug


# ----------------------------------------------------------------------------
# Brief loaders (markdown → section dict)
# ----------------------------------------------------------------------------

SECTION_RE = re.compile(r"^## +(\d+[a-z]?\.? +.+?|.+?)$", re.MULTILINE)


def load_markdown_sections(path: Path) -> dict[str, str]:
    """Split a markdown file into {section-heading: body}. Lossy but useful."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    # Strip frontmatter
    text = FRONTMATTER_RE.sub("", text, count=1)
    sections: dict[str, str] = {"_preamble": ""}
    current = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def find_section(sections: dict[str, str], *needles: str) -> str:
    """Return the body of the first section whose heading contains any needle (case-insensitive)."""
    for needle in needles:
        nl = needle.lower()
        for heading, body in sections.items():
            if nl in heading.lower():
                return body
    return ""


# ----------------------------------------------------------------------------
# Client config + brief
# ----------------------------------------------------------------------------

def load_client_config(client_slug: str) -> dict[str, Any]:
    path = DATA_DIR / f"client-{client_slug}.json"
    if not path.is_file():
        fatal(f"client config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_client_brief(briefs_root: Path, client_slug: str) -> dict[str, str]:
    path = briefs_root / "clients" / client_slug / "brief.md"
    return load_markdown_sections(path)


def load_service_brief(briefs_root: Path, service_slug: str) -> dict[str, str]:
    path = briefs_root / "services" / f"{service_slug}.md"
    return load_markdown_sections(path)


def load_city_brief(briefs_root: Path, city_slug: str) -> dict[str, str]:
    path = briefs_root / "cities" / f"{city_slug}.md"
    return load_markdown_sections(path)


def load_intersection_brief(briefs_root: Path, service_slug: str, city_slug: str) -> dict[str, str]:
    path = briefs_root / "intersections" / f"{service_slug}--{city_slug}.md"
    return load_markdown_sections(path)


# ----------------------------------------------------------------------------
# Prior imagery-log scanning (the learning loop)
# ----------------------------------------------------------------------------

GENERATION_HEADING_RE = re.compile(r"^### +(.+?)$", re.MULTILINE)
LESSON_RE = re.compile(
    r"\*\*Lesson worth carrying forward[^*]*?\*\*\s*(.+?)(?=\n\n|\n###|\n##|\Z)",
    re.DOTALL,
)
SELECTED_VARIANT_RE = re.compile(r"\*\*Selected variant:\*\*\s*(.+?)(?=\n\n|\Z)", re.DOTALL)
VERDICT_RE = re.compile(r"\*\*Verdict:\*\*\s*(.+?)(?=\n\n|\Z)", re.DOTALL)
PROMPT_QUOTE_RE = re.compile(r"\*\*Prompt sent[^*]*?\*\*[^\n]*\n\n>\s*(.+?)(?=\n\n[*#]|\Z)", re.DOTALL)


def scan_prior_logs(client_slug: str, core_30_root: Path) -> dict[str, Any]:
    """Walk the client's core-30 folder, parse every imagery-prompts-log.md.

    Returns a dict with:
      - lessons: list of {source_page, slot, text} dicts
      - keepers_by_slot: {slot_type: count}
      - city_about_keeper: {city_slug: source_page_relpath} — first match per city
      - latest_keeper_prompts: {slot_type: {prompt_text, source_page}} — most recent keeper per slot
    """
    out = {
        "lessons": [],
        "keepers_by_slot": {"hero": 0, "about": 0, "scene": 0},
        "city_about_keeper": {},
        "best_keeper_prompts": {},  # slot -> {prompt_text, source_page, heading, score}
        "logs_scanned": 0,
    }
    if not core_30_root.is_dir():
        return out

    log_paths = sorted(core_30_root.glob("*/imagery-prompts-log.md"))
    out["logs_scanned"] = len(log_paths)

    for log_path in log_paths:
        page_folder = log_path.parent
        page_relpath = page_folder.name
        text = log_path.read_text(encoding="utf-8")
        # The frontmatter gives us page identity (slug, client, etc.)
        fm = parse_frontmatter(text)
        page_slug = fm.get("page", page_relpath)
        # Try to derive the city-slug from the page name (e.g., '01-electrical-troubleshooting-vienna-va')
        m = re.search(r"-([a-z]+-[a-z]{2})$", page_relpath)
        page_city = m.group(1) if m else None

        # Split into generation blocks (### Hero v1 ... ### About portrait v1 ...)
        blocks = _split_generation_blocks(text)
        for heading, body in blocks:
            slot = _classify_slot(heading)
            if slot is None:
                continue
            verdict_m = VERDICT_RE.search(body)
            verdict = verdict_m.group(1).strip() if verdict_m else ""
            # Skip placeholder verdicts ("_<fill: ...>_") and pending generations.
            verdict_is_placeholder = (
                "_<" in verdict or ">_" in verdict or "<fill" in verdict.lower()
            )
            heading_is_pending = "pending" in heading.lower()
            verdict_l = verdict.lower()
            is_keeper = (
                not verdict_is_placeholder
                and not heading_is_pending
                and ("publication-quality" in verdict_l or "publication quality" in verdict_l)
            )
            if is_keeper:
                out["keepers_by_slot"][slot] = out["keepers_by_slot"].get(slot, 0) + 1
                pm = PROMPT_QUOTE_RE.search(body)
                if pm:
                    prompt_text = pm.group(1).strip()
                    # Score = length minus penalties for placeholder bracket-refs.
                    # We want the most-complete, self-contained keeper prompt — not
                    # one that references another by bracket ("[same wardrobe block as page 1 Hero v5]").
                    score = len(prompt_text)
                    if re.search(r"\[same .*? block\b", prompt_text, re.IGNORECASE):
                        score -= 5000
                    if re.search(r"\[same .*? as page", prompt_text, re.IGNORECASE):
                        score -= 5000
                    prior_best = out["best_keeper_prompts"].get(slot)
                    if prior_best is None or score > prior_best["score"]:
                        out["best_keeper_prompts"][slot] = {
                            "prompt_text": prompt_text,
                            "source_page": page_relpath,
                            "heading": heading,
                            "score": score,
                        }
                # City-keyed about cache: first keeper About per city wins
                if slot == "about" and page_city and page_city not in out["city_about_keeper"]:
                    out["city_about_keeper"][page_city] = page_relpath

            # Extract lesson callouts regardless of keeper status
            for lm in LESSON_RE.finditer(body):
                text_block = lm.group(1).strip()
                text_block = text_block.split("\n\n")[0].strip()
                out["lessons"].append({
                    "source_page": page_relpath,
                    "slot": slot,
                    "heading": heading,
                    "text": text_block,
                })
    return out


def _split_generation_blocks(text: str) -> list[tuple[str, str]]:
    """Return (heading, body) pairs for every '### ...' section under '## Generations'."""
    # Find the Generations section first
    gens = ""
    sections = load_markdown_sections_from_string(text)
    gens = sections.get("Generations", "")
    if not gens:
        # Fallback: scan whole doc
        gens = text
    blocks: list[tuple[str, str]] = []
    parts = re.split(r"^###\s+", gens, flags=re.MULTILINE)
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        blocks.append((head.strip(), body.strip()))
    return blocks


def load_markdown_sections_from_string(text: str) -> dict[str, str]:
    """Same as load_markdown_sections but takes the raw string."""
    text = FRONTMATTER_RE.sub("", text, count=1)
    sections: dict[str, str] = {"_preamble": ""}
    current = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def _classify_slot(heading: str) -> str | None:
    h = heading.lower()
    if "hero" in h:
        return "hero"
    if "about" in h or "portrait" in h:
        return "about"
    if "scene" in h or "contextual" in h or "output" in h:
        return "scene"
    return None


# ----------------------------------------------------------------------------
# Brief signal extraction
# ----------------------------------------------------------------------------

PROMPT_DIRECTION_RE = re.compile(
    r"(HERO PROMPT DIRECTION:|ABOUT PROMPT DIRECTION:|SCENE PROMPT DIRECTION:)\s*\n(.+?)(?=\n[A-Z]+ PROMPT DIRECTION:|\n```|\Z)",
    re.DOTALL,
)


def extract_prompt_directions(service_sections: dict[str, str]) -> dict[str, str]:
    """Pull HERO/ABOUT/SCENE PROMPT DIRECTION blocks from the service brief §8."""
    body = find_section(service_sections, "image-style", "image style", "imagery")
    if not body:
        return {}
    out: dict[str, str] = {}
    for m in PROMPT_DIRECTION_RE.finditer(body):
        key = m.group(1).split()[0].lower()  # 'hero' / 'about' / 'scene'
        out[key] = m.group(2).strip()
    return out


def extract_geographic_anchor(city_sections: dict[str, str]) -> str:
    """Pull a 1-3 sentence geographic anchor from the city brief §2."""
    body = find_section(city_sections, "geographic anchor", "geographic")
    if not body:
        return ""
    # Grab the first blockquote in the section (the ready-to-use paragraph)
    bq = re.search(r"^>\s*(.+?)(?=\n\n|\n[^>]|\Z)", body, re.MULTILINE | re.DOTALL)
    if bq:
        return re.sub(r"\n>\s*", " ", bq.group(1)).strip()
    # Fallback: take first paragraph
    para = body.split("\n\n")[0].strip()
    return para[:400]


def extract_setting_hints(city_sections: dict[str, str]) -> dict[str, str]:
    """Pull region descriptors and named neighborhoods for prompt scene-setting."""
    hints: dict[str, str] = {}
    # Look for utility/AHJ hints
    body = find_section(city_sections, "local infrastructure", "infrastructure quirks")
    if body:
        hints["infrastructure"] = body[:300]
    # Neighborhoods
    nbody = find_section(city_sections, "neighborhoods")
    if nbody:
        # Pull the first 2-3 neighborhood names from any table rows
        names = re.findall(r"\|\s*[\"\']?([A-Z][\w &'\-]+?)[\"\']?\s*\|", nbody)
        hints["neighborhoods"] = ", ".join(names[:3]) if names else ""
    return hints


def extract_intersection_signals(intersection_sections: dict[str, str]) -> dict[str, str]:
    """Pull review themes + cross-competitor image-style observations."""
    out: dict[str, str] = {}
    competitors = find_section(intersection_sections, "city-specific competitors", "competitors")
    if competitors:
        # Mine "Image style" lines from competitor cards
        styles = re.findall(r"Image style[^|]*?\|\s*(.+?)\s*\|", competitors)
        out["competitor_image_styles"] = "; ".join(styles[:3]) if styles else ""
    review_body = find_section(intersection_sections, "review themes", "localized review")
    if review_body:
        # Grab "what locals consistently want" if present
        m = re.search(r"What locals consistently want.*?\n(.+?)(?=\n##|\n\*\*|\Z)", review_body, re.DOTALL)
        if m:
            out["local_wants"] = m.group(1).strip()[:400]
    return out


# ----------------------------------------------------------------------------
# Prompt composition
# ----------------------------------------------------------------------------

# Fallback service → action mapping. Used only when the service brief has no
# HERO PROMPT DIRECTION block (e.g. the brief hasn't been written yet).
def _normalize_service_slug(slug: str) -> str:
    """Map common service-slug variants to the fallback-table canonical key.

    Examples:
      ev-charger-installation -> ev-charger
      electrical-troubleshooting -> troubleshooting
      smoke-alarm-installation -> smoke-alarm
      light-fixture -> light-fixture-installation (canonical adds -installation)
    """
    s = slug.lower()
    # Drop common decorators
    for suffix in ("-installation", "-install", "-replacement", "-service", "-services"):
        if s.endswith(suffix):
            s_trimmed = s[: -len(suffix)]
            if s_trimmed in SERVICE_FALLBACK_ACTIONS:
                return s_trimmed
    for prefix in ("electrical-", "residential-"):
        if s.startswith(prefix):
            s_trimmed = s[len(prefix):]
            if s_trimmed in SERVICE_FALLBACK_ACTIONS:
                return s_trimmed
    # Direct match
    if s in SERVICE_FALLBACK_ACTIONS:
        return s
    # Light-fixture special case (canonical key includes -installation)
    if s.startswith("light-fixture"):
        return "light-fixture-installation"
    return s


# Default service → action mapping for electrician business type.
# Override by passing --service-prompts-file with a JSON of the same shape.
# Restaurant (or any non-electrician) type profiles should ship their own
# prompts file at profiles/<type>/assets/service-prompts.json.
_ELECTRICIAN_SERVICE_ACTIONS: dict[str, dict[str, str]] = {
    "troubleshooting": {
        "hero_action": "mid-action at an open residential electrical panel, holding a digital "
                       "multimeter probe to a circuit breaker, examining a reading",
        "setting": "modern residential utility closet — clean, organized, modern panel with "
                   "visible labeled breakers",
        "scene_subject": "a clean residential breaker panel after a troubleshooting service — "
                         "neatly labeled, all breakers properly seated, no exposed live wires",
    },
    "panel-upgrade": {
        "hero_action": "mid-installation of a new residential electrical panel, connecting a "
                       "new circuit breaker to the panel bus bar using a screwdriver",
        "setting": "modern residential utility area — freshly installed panel, clean and "
                   "modern, with neatly routed new wires visible inside; some breakers "
                   "already in place, more being added",
        "scene_subject": "a finished residential 200-amp breaker panel installation — clean wire "
                         "dress, neatly labeled breakers, grounding visible, panel cover removed",
    },
    "ev-charger": {
        "hero_action": "mid-installation of a Level-2 EV charger on a residential garage wall, "
                       "running conduit from the panel down to the charger, screwdriver in hand",
        "setting": "modern residential garage — clean concrete floor, hint of a car bumper in "
                   "the background out of focus",
        "scene_subject": "a finished Level-2 EV charger installed on a residential garage wall — "
                         "neat conduit run, charger handle seated in its dock",
    },
    "light-fixture-installation": {
        "hero_action": "mid-installation of a residential ceiling light fixture from a step "
                       "ladder, hands wiring the fixture to the ceiling box",
        "setting": "modern residential living room or dining room — warm wood floor, "
                   "soft natural light from windows",
        "scene_subject": "a freshly installed residential pendant or chandelier fixture, lit, "
                         "clean ceiling cover, no wiring visible",
    },
    "smoke-alarm": {
        "hero_action": "installing or testing a residential smoke alarm on a hallway ceiling, "
                       "screwdriver in hand or pressing the test button",
        "setting": "modern residential hallway or bedroom doorway — neutral wall, hint of "
                   "warm interior light",
        "scene_subject": "a freshly installed residential smoke alarm on a clean hallway "
                         "ceiling, indicator light visible",
    },
}

# Active lookup table — replaced at runtime when --service-prompts-file is given.
SERVICE_FALLBACK_ACTIONS: dict[str, dict[str, str]] = dict(_ELECTRICIAN_SERVICE_ACTIONS)


def load_service_prompts_file(path: Path) -> dict[str, dict[str, str]]:
    """Load an external service-prompts JSON and replace SERVICE_FALLBACK_ACTIONS."""
    global SERVICE_FALLBACK_ACTIONS
    data = json.loads(path.read_text(encoding="utf-8"))
    SERVICE_FALLBACK_ACTIONS = data
    return data


# Embroidered-no-patch wardrobe clause (Issue #20). Any prompt that may show
# the uniform (OWNER-CENTRIC always; HANDS-ONLY when chest/shoulder may render)
# must carry this clause. The generator must NEVER instruct uploading the uniform
# mockup — its white callout boxes cause white name-patches.
def embroidered_no_patch_clause(client_cfg: dict) -> str:
    """Build the brand-mark clause from the client JSON instead of hardcoding."""
    mark = client_cfg.get("brand_mark_description")
    if mark:
        return (
            f"if any chest, shoulder, or sleeve branding shows, the {mark}, "
            "NO patch, no white background, no rectangular badge"
        )
    # Fallback: generic brand-mark clause
    name = client_cfg.get("name", "the company")
    return (
        f"if any chest, shoulder, or sleeve branding shows, the {name} mark is "
        "embroidered directly onto the shirt fabric — NO patch, no white background, "
        "no rectangular badge"
    )

# Prompt type classification for self-documenting headers (Issue #17).
# Each prompt carries: Type, Reference photos, Aspect, Produces.
PROMPT_TYPE_LEGEND = {
    "PURE SCENE": {
        "description": "The service / installed result — no person in frame",
        "reference_photos": "NONE",
    },
    "HANDS-ONLY": {
        "description": "A faceless worker's hands at the task, uniform cuff visible — no face",
        "reference_photos": "NONE (optional: brand logo art only). NEVER upload the uniform mockup.",
    },
    "OWNER-CENTRIC": {
        "description": "Owner performing the task / portrait — face is in the shot",
        "reference_photos": "Owner headshot (face/likeness) + brand logo art (chest embroidery). Do NOT upload the uniform mockup.",
    },
}

# Always-avoid terms for residential electrical imagery. Carried into every
# prompt's avoid list, regardless of slot or city.
DEFAULT_AVOID_TERMS = [
    "cheesy thumbs-up poses",
    "overly bright clinical lighting",
    "hi-vis safety vests",
    "hard hats indoors",
    "stock-photo handshake imagery",
    "exaggerated muscle hero poses",
    "plasticky AI skin",
    "cartoonish proportions",
    "watermarks",
    "text in image artifacts",
    "extra fingers",
    "melted faces",
]

# Slot-specific avoid terms
HERO_EXTRA_AVOID = [
    "sad or melancholic expression",
    "frowning",
    "downturned mouth",
    "drooping eyes",
    "tired look",
]

ABOUT_EXTRA_AVOID = [
    "sales-headshot stiffness",
    "fake corporate smile",
    "blank-wall studio background",
    "harsh fluorescent lighting",
]


def _compose_wardrobe_block(
    client_cfg: dict,
    prior: dict,
    learned_terms: list[str],
) -> str:
    """Carry forward the wardrobe block from the latest keeper Hero prompt.

    Falls back to a generic clean-work-shirt template if no prior keeper exists.
    """
    # Try the best keeper Hero first; fall back to the best keeper About if Hero
    # didn't yield a self-contained wardrobe (e.g. the only Hero keeper referenced
    # another page's wardrobe by bracket placeholder).
    for slot in ("hero", "about"):
        keeper = prior.get("best_keeper_prompts", {}).get(slot)
        if not keeper:
            continue
        prompt = keeper["prompt_text"]
        m = re.search(
            r"(He is wearing[^.]*\..*?)(?=Soft natural light|Behind him|Composition|Photo-realistic|Avoid:)",
            prompt,
            re.DOTALL,
        )
        if not m:
            continue
        wardrobe = re.sub(r"\s+", " ", m.group(1)).strip()
        # Skip if it's a bracket placeholder that references another page
        if re.search(r"\[same .*? (block|as page)", wardrobe, re.IGNORECASE):
            continue
        return wardrobe

    # Fallback — generic clean-work-shirt template using brand colors
    owner_first = client_cfg.get("owner_first_name", "the owner")
    accent = client_cfg.get("accent_yellow", "yellow-gold")
    client_short = client_cfg.get("alternate_name", client_cfg.get("name", "the client"))
    return (
        f"He is wearing his {client_short} work shirt: a clean short-sleeve button-up work "
        f"shirt in the brand's primary color, with the {client_short} brand mark embroidered "
        f"directly onto the chest fabric ({embroidered_no_patch_clause(client_cfg)}) and the name "
        f"'{owner_first.upper()}' embroidered on the opposite chest. Light pants."
    )


def _collect_avoid_terms(slot: str, learned_terms: list[str]) -> list[str]:
    terms = list(DEFAULT_AVOID_TERMS)
    if slot == "hero":
        terms = HERO_EXTRA_AVOID + terms
    elif slot == "about":
        terms = ABOUT_EXTRA_AVOID + terms
    # Append learned terms (deduped, preserving order)
    seen = {t.lower() for t in terms}
    for t in learned_terms:
        if t.lower() not in seen:
            terms.append(t)
            seen.add(t.lower())
    return terms


def _learned_avoid_terms_from_lessons(lessons: list[dict]) -> list[str]:
    """Mine lessons for negative-instruction phrases worth pinning into avoid lists."""
    out: list[str] = []
    blob = " ".join(L["text"] for L in lessons).lower()
    if "no patch" in blob or "white-rectangle patch" in blob or "white background" in blob:
        out.extend(["white chest patches", "rectangular badges", "sewn-on patches", "name tags with borders"])
    if "under-eye" in blob or "dark circles" in blob:
        out.extend(["under-eye bags", "dark circles under eyes", "puffy eyes"])
    if "previous-employer" in blob or "previous employer" in blob:
        out.append("logos from any previous employer's uniform")
    return out


def compose_hero_prompt(
    client_cfg: dict,
    client_meta: dict,
    service_slug: str,
    city_name: str,
    city_state: str,
    service_direction: str,
    geographic_anchor: str,
    setting_hints: dict,
    intersection: dict,
    prior: dict,
) -> str:
    """Compose a Hero prompt following the canonical 8-part SOP structure:

      1. Subject
      2. Action + expression
      3. Scene-specific detail
      4. Wardrobe
      5. Lighting / setting
      6. Composition + aspect ratio
      7. Style
      8. Avoid

    The fallback action table (SERVICE_FALLBACK_ACTIONS) carries the action +
    scene-spec language. The Tier-1 brief's HERO PROMPT DIRECTION (if present)
    is layered in as an enrichment hint after the wardrobe block, NOT as the
    scene seed — its wardrobe sentences conflict with the carry-forward
    wardrobe block when it's used as the seed.
    """
    region = f"{city_name}-area" if city_name else "Northern Virginia"
    normalized = _normalize_service_slug(service_slug)
    fallback = SERVICE_FALLBACK_ACTIONS.get(normalized, {})

    # Human-readable service phrase for the fallback action text
    pretty_service = service_slug.replace("-", " ")
    action = fallback.get("hero_action", f"mid-action on a residential {pretty_service} job")
    scene_spec = fallback.get("setting", "")

    # 1+2. Subject + action + expression
    subject_action = (
        f"The person in the first reference photo (use for face and likeness only), "
        f"{action} inside a modern {region} home utility area. He is working with a "
        f"calm, engaged expression — eyes intently on the work, jaw relaxed, mouth in "
        f"a neutral line, looking competent and present."
    )

    # 3. Scene-specific detail
    scene_detail = ""
    if scene_spec:
        scene_detail = scene_spec[0].upper() + scene_spec[1:] + "."

    # 4. Wardrobe (carry-forward from latest keeper)
    wardrobe = _compose_wardrobe_block(client_cfg, prior, [])

    # 5. Lighting + setting (geographic anchor as a brief hint, not the full paragraph)
    lighting = "Soft natural light from a window or doorway, warm interior."
    if geographic_anchor:
        # Keep it tight — first sentence only, so it nudges the model without overloading
        first_sentence = geographic_anchor.split(".")[0].strip()
        if first_sentence and len(first_sentence) < 200:
            lighting += f" Local context: {first_sentence}."

    # 6. Composition + aspect ratio
    composition = (
        "3/4 angle, chest-and-up framing, hands visible on the tool with a natural grip. "
        "4:3 landscape aspect ratio."
    )

    # 7. Style
    style = (
        "Photo-realistic editorial photography, 50mm lens look, shallow depth of field, "
        "sharp on subject, slight bokeh on background, warm but neutral color grade."
    )

    # 8. Avoid
    learned_terms = _learned_avoid_terms_from_lessons(prior.get("lessons", []))
    avoid_terms = _collect_avoid_terms("hero", learned_terms)
    avoid = "Avoid: " + ", ".join(avoid_terms) + "."

    parts = [subject_action, scene_detail, wardrobe, lighting, composition, style, avoid]
    return " ".join(p.strip() for p in parts if p.strip())


def compose_hero_service_scene(
    client_cfg: dict,
    service_slug: str,
    city_name: str,
    setting_hints: dict,
    prior: dict,
) -> str:
    """Compose a PURE SCENE hero — the installed result / work scene, no person."""
    region = f"{city_name}-area" if city_name else "Northern Virginia"
    normalized = _normalize_service_slug(service_slug)
    fallback = SERVICE_FALLBACK_ACTIONS.get(normalized, {})
    pretty_service = service_slug.replace("-", " ")
    subject = fallback.get(
        "scene_subject",
        f"the visible output of a residential {pretty_service} service — clean, professional, completed",
    )

    parts = [
        f"{subject[0].upper()}{subject[1:]}, newly completed in a modern {region} home.",
        (f"Setting: interior of a modern residential home — neutral drywall, warm wood floor, "
         f"soft natural light from a window or doorway, premium-but-approachable residential feel."),
        ("Composition: the finished result as the clear focal point at rule-of-thirds, "
         "surrounding environment softly out of focus. 4:3 landscape aspect ratio."),
        ("Photo-realistic editorial photography, 35mm lens look, shallow depth of field, "
         "sharp on the result, warm but neutral color grade."),
        ("Avoid: " + ", ".join(DEFAULT_AVOID_TERMS + [
            "people's faces", "ladders or visible install mess", "before/after composite layouts",
        ]) + "."),
    ]
    return " ".join(p.strip() for p in parts if p.strip())


def compose_hero_hands_only(
    client_cfg: dict,
    service_slug: str,
    city_name: str,
    setting_hints: dict,
    prior: dict,
) -> str:
    """Compose a HANDS-ONLY hero — faceless worker's hands at the task."""
    region = f"{city_name}-area" if city_name else "Northern Virginia"
    normalized = _normalize_service_slug(service_slug)
    fallback = SERVICE_FALLBACK_ACTIONS.get(normalized, {})
    pretty_service = service_slug.replace("-", " ")
    action = fallback.get("hero_action", f"mid-action on a residential {pretty_service} job")

    learned_terms = _learned_avoid_terms_from_lessons(prior.get("lessons", []))
    avoid_terms = _collect_avoid_terms("hero", learned_terms)
    avoid_terms.extend(["faces", "full portraits"])

    parts = [
        (f"Close, mid-action shot of a residential electrician's hands (wearing the cuff of a "
         f"light medium-blue postman-blue short-sleeve work shirt; {embroidered_no_patch_clause(client_cfg)}) "
         f"{action} inside a modern {region} home."),
        ("Focus on the hands and the work; no face in frame (chest-down / hands-and-work composition)."),
        (f"Soft warm natural light from a doorway, warm wood floor at the edge of frame."),
        ("4:3 landscape, hands and work as focal point, shallow depth of field."),
        ("Photo-realistic editorial photography, 50mm lens look, sharp on the work, "
         "slight background bokeh, warm neutral grade."),
        "Avoid: " + ", ".join(avoid_terms) + ".",
    ]
    return " ".join(p.strip() for p in parts if p.strip())


def compose_about_prompt(
    client_cfg: dict,
    city_name: str,
    geographic_anchor: str,
    prior: dict,
) -> str:
    owner_name = client_cfg["owner_name"]
    region = f"{city_name}-area" if city_name else "Northern Virginia"

    wardrobe = _compose_wardrobe_block(client_cfg, prior, [])
    learned_terms = _learned_avoid_terms_from_lessons(prior.get("lessons", []))
    avoid_terms = _collect_avoid_terms("about", learned_terms)

    lighting = "Warmly lit interior, soft natural light from camera-left."
    if geographic_anchor:
        first_sentence = geographic_anchor.split(".")[0].strip()
        if first_sentence and len(first_sentence) < 200:
            lighting += f" Local context: {first_sentence}."

    parts = [
        ("The person in the first reference photo (use for face and likeness only), "
         "in a confident chest-up portrait. He is facing the camera with a warm, trustworthy "
         "expression — slight smile, direct eye contact with the camera, looking competent and "
         "approachable."),
        wardrobe,
        ("Behind him at soft focus: an open residential electrical panel and a section of a "
         f"finished residential utility room in a modern {region} home. Hint of toolbag visible "
         "on the floor at the edge of the frame."),
        lighting,
        ("Composition: chest-up framing, subject slightly off-center. 3:4 portrait aspect ratio "
         "(not 1:1 — the About slot is grid-column-narrow with align-items: stretch and renders "
         "taller than wide once the text column fills with paragraphs)."),
        ("Photo-realistic editorial portrait, 85mm portrait lens look, very shallow depth of "
         "field on background, sharp on subject, clean color grade."),
        "Avoid: " + ", ".join(avoid_terms) + ".",
    ]
    return " ".join(p.strip() for p in parts if p.strip())


def compose_scene_prompt(
    client_cfg: dict,
    service_slug: str,
    city_name: str,
    intersection: dict,
) -> str:
    region = f"{city_name}-area" if city_name else "Northern Virginia"
    normalized = _normalize_service_slug(service_slug)
    fallback = SERVICE_FALLBACK_ACTIONS.get(normalized, {})
    pretty_service = service_slug.replace("-", " ")
    subject = fallback.get(
        "scene_subject",
        f"the visible output of a residential {pretty_service} service — clean, professional, completed",
    )

    avoid_terms = list(DEFAULT_AVOID_TERMS) + [
        "untidy wires",
        "exposed live wires",
        "stock-photo 'before/after' composite layouts",
        "people in frame",
    ]

    parts = [
        f"Scene: {subject}.",
        (f"Setting: interior wall in a {region} home — neutral drywall, hint of finished "
         "basement or utility-room context at edge of frame."),
        ("Composition: straight-on tight shot, subject takes up 70% of frame. 16:9 wide or 4:3 "
         "standard depending on layout slot."),
        "Mood: clean, professional, completed. The 'after' of a service.",
        ("Photo-realistic editorial, soft even lighting, sharp focus on the work surface."),
        "Avoid: " + ", ".join(avoid_terms) + ".",
    ]
    return " ".join(p.strip() for p in parts if p.strip())


# ----------------------------------------------------------------------------
# Output rendering
# ----------------------------------------------------------------------------

def render_log(
    *,
    page_folder: Path,
    client_cfg: dict,
    client_meta: dict,
    service_slug: str,
    city_slug: str,
    city_name: str,
    city_state: str,
    page_slug: str,
    position: str,
    hero_prompt: str,
    hero_style: str,
    about_prompt: str,
    scene_prompt: str | None,
    about_cache_hit: str | None,
    prior: dict,
    brief_status: dict[str, bool],
) -> str:
    today = date.today().isoformat()
    client_slug = client_cfg["client_slug"]
    # page_id_short is the filename stem the operator will see in example filenames.
    # Use the full page-slug as-is — it already encodes service + city + state.
    page_id_short = page_slug

    # Frontmatter — mirrors hand-written logs for pages 1 and 2
    fm = (
        f"---\n"
        f"type: imagery-log\n"
        f"status: draft\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"client: {client_slug}\n"
        f"page: {page_slug}\n"
        f"tool: higgsfield\n"
        f"model: nano-banana-pro\n"
        f"generated-by: generate-imagery-prompts.py\n"
        f"tags: [imagery-log, ai-imagery, core-30, {client_slug}]\n"
        f"---\n\n"
    )

    title = f"# Imagery prompts log — {city_name or city_slug} {service_slug} page (auto-generated)\n\n"

    intro = (
        f"Auto-generated by `generate-imagery-prompts.py` on {today}. "
        f"Phase 3d of the [[client-seo-onboarding-automation]] roadmap.\n\n"
        f"This file is the source of truth for what got sent to Higgsfield for page "
        f"`{position or '?'} — {page_slug}` and what came back. The operator pastes each prompt "
        f"into Higgsfield, generates variants, picks the keeper, then fills in the "
        f"`**Selected variant:**`, `**Output filenames:**`, `**Verdict:**`, and `**Outcome tag:**` "
        f"placeholders below.\n\n"
        f"The reusable prompt templates this page draws from live in "
        f"[[sop-ai-imagery-for-core-30-pages]]. Hand-written examples to mirror: "
        f"[[imagery-prompts-log|page 1]] and [[imagery-prompts-log|page 2]]. The learning loop "
        f"reads the `**Outcome tag:**` field on every prior generation, so fill it in honestly "
        f"(`keeper`, `keeper-with-postprocess`, `miss-likeness`, `miss-too-generic`, `regression`, "
        f"etc.) — future runs of this script bias toward keeper patterns.\n\n"
    )

    # Brief-status block — surfaces what was loadable and what wasn't
    brief_status_lines = []
    for key, label in [
        ("client", "Client-fact brief"),
        ("service", "Tier-1 service brief"),
        ("city", "Tier-2 city brief"),
        ("intersection", "Tier-3 intersection brief"),
    ]:
        mark = "✅" if brief_status.get(key) else "❌"
        brief_status_lines.append(f"- {mark} {label}")
    brief_status_block = "## Brief inputs\n\n" + "\n".join(brief_status_lines) + "\n\n"

    # Keeper-bias notes from prior pages — the learning loop's user-facing surface
    bias_lines: list[str] = []
    bias_lines.append(
        f"Scanned {prior['logs_scanned']} prior imagery-prompts-log.md file(s) under this client. "
        f"Keepers logged: Hero={prior['keepers_by_slot'].get('hero', 0)}, "
        f"About={prior['keepers_by_slot'].get('about', 0)}, "
        f"Scene={prior['keepers_by_slot'].get('scene', 0)}."
    )
    if prior["city_about_keeper"]:
        for city, src in prior["city_about_keeper"].items():
            if city == city_slug:
                bias_lines.append(
                    f"**About-portrait cache hit for `{city}`:** a keeper About portrait already "
                    f"exists at [[{src}/imagery-prompts-log|{src}]]. Strongly consider reusing it "
                    f"instead of generating fresh — the trust signal is identical across pages in "
                    f"the same city, and reuse saves a generation + upload cycle."
                )
    if prior["lessons"]:
        bias_lines.append("")
        bias_lines.append("**Lessons carried forward from prior runs:**")
        # Dedupe lessons by trimmed text
        seen = set()
        for L in prior["lessons"]:
            key = L["text"][:120].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            bias_lines.append(f"- [{L['source_page']} · {L['slot']}] {L['text']}")

    bias_block = "## Keeper-bias notes from prior pages\n\n" + "\n".join(bias_lines) + "\n\n"

    # Setup context
    setup_lines = [
        f"- **Tool:** Higgsfield (higgsfield.ai)",
        f"- **Model:** Nano Banana Pro",
        f"- **Reference photos to upload:** (1) owner face/likeness crop from "
        f"`{reference_photo_home(client_cfg.get('client_slug', ''))}` "
        f"(e.g. mohammed-front-facing-pic.jpg or front-facing-photo-from-drivers-license.png); "
        f"(2) the client's logo art for brand-mark fidelity on embroidered chest mark.",
        f"- **Owner:** {client_cfg.get('owner_name', '<owner>')} "
        f"({client_cfg.get('owner_title', '<title>')})",
        f"- **City:** {city_name or city_slug} — {city_state or '?'}",
        f"- **Service:** {service_slug}",
        f"- **Page slug:** `{page_slug}`",
    ]
    setup_block = "## Setup context\n\n" + "\n".join(setup_lines) + "\n\n"

    # Compute self-documenting header fields for the hero prompt
    pretty_service = service_slug.replace("-", " ")
    if hero_style == "service-scene":
        hero_type = "PURE SCENE"
        hero_refs = "NONE"
        hero_produces = f"the installed result of {pretty_service} — no person in frame"
    elif hero_style == "hands-only":
        hero_type = "HANDS-ONLY"
        hero_refs = "NONE (optional: brand logo art only). NEVER upload the uniform mockup."
        hero_produces = f"faceless worker's hands performing {pretty_service}"
    else:  # owner-centric
        hero_type = "OWNER-CENTRIC"
        hero_refs = (
            f"{client_cfg.get('owner_name', 'owner')} headshot (face/likeness) + "
            f"brand logo art (chest embroidery). Do NOT upload the uniform mockup."
        )
        hero_produces = f"{client_cfg.get('owner_name', 'owner')} performing {pretty_service}"

    # Types legend
    types_legend = (
        "## Prompt types legend (read once)\n\n"
        "| Type | What it produces | Reference photos to upload |\n"
        "|---|---|---|\n"
    )
    for ptype, info_dict in PROMPT_TYPE_LEGEND.items():
        types_legend += f"| **{ptype}** | {info_dict['description']} | {info_dict['reference_photos']} |\n"
    types_legend += (
        "\n**Reference-photo home:** `" + str(reference_photo_home(client_cfg.get("client_slug", ""))) + "`\n\n"
        "**Save/name/organize flow:** download all 4 variants from Higgsfield → pick the best → "
        "run `organize-image-downloads.py` (renames + files keeper at `<page>/images/`, "
        "alternates at `<page>/images/_drafts-and-alternates/`) → then `wire-page-images.py` "
        "(optimize → upload → wire HTML → republish).\n\n"
    )

    # Generations
    gens: list[str] = ["## Generations\n"]

    # --- Hero ---
    gens.append("### Hero v1 — pending\n")
    gens.append(f"**Type:** {hero_type} · **Reference photos:** {hero_refs} · "
                f"**Aspect:** 4:3 landscape · **Produces:** {hero_produces}\n")
    gens.append("**Aspect ratio note:** verify the Higgsfield aspect chip is locked to 4:3 before generating; "
                "it can flip to 3:4 between batches and changes the page-template wiring "
                "(landscape uses Pattern A, portrait uses Pattern B).\n")
    gens.append("**Prompt to paste into Higgsfield:**\n")
    gens.append("> " + hero_prompt.replace("\n", "\n> ") + "\n")
    gens.append("**Selected variant:** _<fill after generation — e.g. 'variant-3'>_\n")
    gens.append("**Output filenames:** _<fill after picking — e.g. '" + page_id_short + "-hero-v1-variant-3.png'>_\n")
    gens.append("**Verdict:** _<fill: 'Publication-quality' / 'Iterate' / 'Regression'>_\n")
    gens.append("**Outcome tag:** _<fill: 'keeper' / 'keeper-with-postprocess' / 'miss-likeness' / "
                "'miss-too-generic' / 'regression' — the learning loop reads this>_\n")
    gens.append("**Lesson worth carrying forward to SOP:** _<fill if anything generalizable came up>_\n\n")

    # --- About ---
    gens.append("### About portrait v1 — pending\n")
    about_owner = client_cfg.get("owner_name", "owner")
    gens.append(f"**Type:** OWNER-CENTRIC · **Reference photos:** {about_owner} headshot + brand logo art. "
                f"Do NOT upload the uniform mockup. · **Aspect:** 3:4 portrait · "
                f"**Produces:** {about_owner} chest-up portrait, warm and trustworthy\n")
    gens.append("**Aspect ratio note:** the About slot is grid-column-narrow with "
                "align-items: stretch and renders taller than wide; 1:1 from Higgsfield "
                "tends to render as 4:3 landscape and crops awkwardly.\n")
    if about_cache_hit:
        gens.append(f"**REUSE NOTE:** a city-wide keeper About portrait already exists at "
                    f"[[{about_cache_hit}/imagery-prompts-log|{about_cache_hit}]]. Consider reusing it "
                    f"rather than generating fresh — same person + same city = same trust signal. "
                    f"If you reuse, mark this prompt as **Selected variant:** `REUSED from "
                    f"{about_cache_hit}` and skip the Higgsfield run.\n")
    gens.append("**Prompt to paste into Higgsfield:**\n")
    gens.append("> " + about_prompt.replace("\n", "\n> ") + "\n")
    gens.append("**Selected variant:** _<fill after generation — e.g. 'variant-1' or 'REUSED from <page>'>_\n")
    gens.append("**Output filenames:** _<fill after picking — e.g. '" + page_id_short + "-about-v1-variant-1.png'>_\n")
    gens.append("**Verdict:** _<fill: 'Publication-quality' / 'Iterate' / 'Regression' / 'REUSED'>_\n")
    gens.append("**Outcome tag:** _<fill: 'keeper' / 'reused' / 'miss-likeness' / 'miss-too-generic' / 'regression'>_\n")
    gens.append("**Lesson worth carrying forward to SOP:** _<fill if anything generalizable came up>_\n\n")

    # --- Scene (optional) ---
    if scene_prompt:
        gens.append("### Scene v1 — pending (OPTIONAL — skip if page is already image-heavy)\n")
        gens.append(f"**Type:** PURE SCENE · **Reference photos:** NONE (logo art optional) · "
                    f"**Aspect:** 16:9 wide or 4:3 standard · "
                    f"**Produces:** the 'after' shot of {pretty_service} — clean, professional result\n")
        gens.append("**Prompt to paste into Higgsfield:**\n")
        gens.append("> " + scene_prompt.replace("\n", "\n> ") + "\n")
        gens.append("**Selected variant:** _<fill after generation>_\n")
        gens.append("**Output filenames:** _<fill after picking — e.g. '" + page_id_short + "-scene-v1-variant-N.png'>_\n")
        gens.append("**Verdict:** _<fill>_\n")
        gens.append("**Outcome tag:** _<fill>_\n")
        gens.append("**Lesson worth carrying forward to SOP:** _<fill if anything generalizable came up>_\n\n")

    generations_block = "\n".join(gens)

    # Follow-ups
    followups = (
        "## Open follow-ups for this page\n\n"
        "1. **Generate variants in Higgsfield.** Upload the two reference images, paste the Hero "
        "prompt, generate 4 variants on the paid plan (no watermark). Repeat for About (3:4) and "
        "Scene (if running).\n"
        "2. **Pick keepers + fill the placeholders above.** Once chosen, fill `**Selected variant:**`, "
        "`**Output filenames:**`, `**Verdict:**`, and `**Outcome tag:**` for each generation.\n"
        "3. **Run the image pipeline.** "
        "`python3 organize-image-downloads.py --client " + client_slug +
        " --page-folder " + str(page_folder) + " --image-type hero --selected-variant <N>` for each slot.\n"
        "4. **Wire into the page.** `python3 wire-page-images.py --page-folder " + str(page_folder) +
        " --config /path/to/" + client_slug + ".config.json`.\n"
        "5. **Verify on the live page** per the imagery SOP Step 6.\n"
        "6. **Update this log's `status:` frontmatter** to `active` once any keeper is wired, and "
        "to `completed` once all slots are wired and verified.\n\n"
    )

    related = (
        "## Related\n\n"
        "- [[sop-ai-imagery-for-core-30-pages]] — the reusable SOP this log is a per-page instance of\n"
        "- [[client-seo-onboarding-automation]] — the roadmap this script implements (Phase 3d)\n"
        f"- [[{service_slug}|service brief]] — Tier-1 brief feeding the hero scene\n"
        f"- [[{city_slug}|city brief]] — Tier-2 brief feeding the local setting\n"
        f"- [[{service_slug}--{city_slug}|intersection brief]] — Tier-3 brief feeding local angles\n"
        f"- [[brief|client-fact brief]] — owner reference + wardrobe descriptor\n"
    )

    return (
        fm + title + intro + brief_status_block + bias_block + setup_block +
        types_legend + generations_block + followups + related
    )


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate Higgsfield prompts for a Core 30 page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("CLI")[0],
    )
    ap.add_argument("--page-folder", required=True, type=Path,
                    help="Path to the page folder (contains draft-v1.md).")
    ap.add_argument("--client", required=True,
                    help="Client slug (e.g. ev-electric-services). Required.")
    ap.add_argument("--hero-style", choices=["owner-centric", "service-scene", "hands-only"],
                    default="owner-centric",
                    help="Hero prompt style: 'owner-centric' (owner performing task, default), "
                         "'service-scene' (installed result / work scene, no face), "
                         "'hands-only' (faceless worker's hands at the task).")
    ap.add_argument("--no-scene", action="store_true",
                    help="Skip the optional Scene prompt (default: include it).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Replace an existing imagery-prompts-log.md (default: refuse).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the rendered log to stdout; write nothing.")
    ap.add_argument("--briefs-root", type=Path, default=DEFAULT_BRIEFS_ROOT,
                    help=f"Research-briefs root (default: {DEFAULT_BRIEFS_ROOT})")
    ap.add_argument("--core-30-root", type=Path, default=None,
                    help="Core-30 root to scan for prior logs (default: derived from page-folder).")
    ap.add_argument("--service-prompts-file", type=Path, default=None,
                    help="JSON file with service→action prompt mappings (same shape as "
                         "SERVICE_FALLBACK_ACTIONS). Overrides the built-in electrician defaults. "
                         "Use profiles/<type>/assets/service-prompts.json for non-electrician types.")
    args = ap.parse_args()

    # Load external service prompts if provided
    if args.service_prompts_file:
        if not args.service_prompts_file.is_file():
            fatal(f"--service-prompts-file not found: {args.service_prompts_file}")
        load_service_prompts_file(args.service_prompts_file)
        info(f"loaded service prompts from {args.service_prompts_file} "
             f"({len(SERVICE_FALLBACK_ACTIONS)} services)")

    page_folder = args.page_folder.expanduser().resolve()
    if not page_folder.is_dir():
        fatal(f"--page-folder does not exist: {page_folder}")

    output_path = page_folder / "imagery-prompts-log.md"
    if output_path.exists() and not args.overwrite and not args.dry_run:
        fatal(f"refusing to overwrite existing log at {output_path}\n"
              f"        pass --overwrite to replace, or --dry-run to preview.")

    # 1. Page metadata
    meta = read_draft_metadata(page_folder)
    service_slug = meta["service"]
    city_slug = derive_city_slug(meta)
    page_slug = meta["page-slug"]
    position = meta.get("core-30-position", "?")
    info(f"page identity: service={service_slug} city={city_slug} slug={page_slug} pos={position}")

    # 2. Client config + brief
    client_cfg = load_client_config(args.client)
    client_brief = load_client_brief(args.briefs_root, args.client)
    info(f"client {args.client} loaded; brief sections: {len(client_brief)}")

    # 3. Briefs (gracefully degrade on missing files)
    service_brief = load_service_brief(args.briefs_root, service_slug)
    city_brief = load_city_brief(args.briefs_root, city_slug)
    intersection_brief = load_intersection_brief(args.briefs_root, service_slug, city_slug)
    brief_status = {
        "client": bool(client_brief),
        "service": bool(service_brief),
        "city": bool(city_brief),
        "intersection": bool(intersection_brief),
    }
    for key, ok in brief_status.items():
        info(f"brief[{key}]: {'loaded' if ok else 'MISSING'}")

    # 4. Extract signals
    directions = extract_prompt_directions(service_brief)
    geographic_anchor = extract_geographic_anchor(city_brief)
    setting_hints = extract_setting_hints(city_brief)
    intersection_signals = extract_intersection_signals(intersection_brief)

    # 5. Scan prior logs
    if args.core_30_root:
        core_30_root = args.core_30_root.expanduser().resolve()
    else:
        # Walk up from page-folder until we find the parent core-30 dir
        core_30_root = page_folder.parent
    prior = scan_prior_logs(args.client, core_30_root)
    info(f"prior logs scanned: {prior['logs_scanned']} files; "
         f"keepers Hero={prior['keepers_by_slot'].get('hero',0)} "
         f"About={prior['keepers_by_slot'].get('about',0)} "
         f"Scene={prior['keepers_by_slot'].get('scene',0)}")

    about_cache_hit = prior["city_about_keeper"].get(city_slug)

    # 6. Derive city name + state for prose
    city_name = ""
    city_state = ""
    if city_brief:
        # Frontmatter first-pass — re-read the file to get title-cased name
        path = args.briefs_root / "cities" / f"{city_slug}.md"
        if path.is_file():
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
            city_name = fm.get("city-name", "")
            city_state = fm.get("state", "")
    if not city_name:
        # Best effort from slug: last segment is state code, rest is multi-word city name
        parts = city_slug.split("-")
        if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
            city_name = " ".join(p.title() for p in parts[:-1])
            city_state = parts[-1].upper()
        else:
            city_name = city_slug.replace("-", " ").title()

    # 6.5. Check reference-photo home exists (warn if missing)
    ref_home = reference_photo_home(args.client)
    if ref_home.is_dir():
        ref_files = [f.name for f in ref_home.iterdir() if f.is_file() and not f.name.startswith(".")]
        info(f"reference-photo home: {ref_home} ({len(ref_files)} files)")
    else:
        warn(f"reference-photo home MISSING: {ref_home} — prompts will reference it but operator "
             f"must create the folder + add reference photos before Higgsfield session")

    # 7. Compose the three prompts (hero style determines which compose function)
    hero_style = args.hero_style
    info(f"hero style: {hero_style}")

    if hero_style == "service-scene":
        hero_prompt = compose_hero_service_scene(
            client_cfg=client_cfg,
            service_slug=service_slug,
            city_name=city_name,
            setting_hints=setting_hints,
            prior=prior,
        )
    elif hero_style == "hands-only":
        hero_prompt = compose_hero_hands_only(
            client_cfg=client_cfg,
            service_slug=service_slug,
            city_name=city_name,
            setting_hints=setting_hints,
            prior=prior,
        )
    else:  # owner-centric (default)
        hero_prompt = compose_hero_prompt(
            client_cfg=client_cfg,
            client_meta=client_brief,
            service_slug=service_slug,
            city_name=city_name,
            city_state=city_state,
            service_direction=directions.get("hero", ""),
            geographic_anchor=geographic_anchor,
            setting_hints=setting_hints,
            intersection=intersection_signals,
            prior=prior,
        )
    about_prompt = compose_about_prompt(
        client_cfg=client_cfg,
        city_name=city_name,
        geographic_anchor=geographic_anchor,
        prior=prior,
    )
    scene_prompt = None
    if not args.no_scene:
        scene_prompt = compose_scene_prompt(
            client_cfg=client_cfg,
            service_slug=service_slug,
            city_name=city_name,
            intersection=intersection_signals,
        )

    # 8. Render + write
    rendered = render_log(
        page_folder=page_folder,
        client_cfg=client_cfg,
        client_meta=client_brief,
        service_slug=service_slug,
        city_slug=city_slug,
        city_name=city_name,
        city_state=city_state,
        page_slug=page_slug,
        position=position,
        hero_prompt=hero_prompt,
        hero_style=hero_style,
        about_prompt=about_prompt,
        scene_prompt=scene_prompt,
        about_cache_hit=about_cache_hit,
        prior=prior,
        brief_status=brief_status,
    )

    if args.dry_run:
        sys.stdout.write(rendered)
        info("DRY RUN — nothing written")
        return 0

    output_path.write_text(rendered, encoding="utf-8")
    info(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
