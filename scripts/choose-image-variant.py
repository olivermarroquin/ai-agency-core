#!/usr/bin/env python3
"""
choose-image-variant.py — automated variant chooser for the imagery pipeline.

Given N variant images for a single slot (hero, about, or scene), scores each
against a rubric using Claude's vision capabilities, selects the keeper, and
writes the outcome tag the learning loop reads.

TWO IMAGE CLASSES (per operator direction 2026-06-04):
  owner-personalized — hero/about on anchor-city pages. The chooser weights
      likeness to the Soul Character / real owner reference heavily.
  topic/content — the majority of slots. NO owner face expected. The chooser
      judges topic relevance, professional quality, brand-appropriateness,
      and artifact-freedom.

The rubric branches by class:  owner slots → likeness-weighted;
topic slots → topic-relevance-weighted.

VISION BACKENDS (pluggable):
  in-session  — Claude Code reads the images + rubric directly in the active
                session. Zero setup, no API cost. Default for interactive use.
  anthropic   — Standalone Anthropic API calls via SDK. Loads key from the
                tier-3 secrets path (same discipline as claude_query.py /
                perplexity_sonar.py). For headless/autonomous use cases
                (gate-peer-reviewer wave-4).

USAGE
-----
Standalone (anthropic backend):

    python choose-image-variant.py \\
        --variants img1.png img2.png img3.png img4.png \\
        --reference-photo /path/to/owner-ref.jpg \\
        --slot-type hero --image-class owner \\
        --client-config /path/to/client.config.json \\
        --page-service "panel upgrade" --page-city "Woodbridge, VA" \\
        --backend anthropic

In-session (Claude Code reads images and scores — default, interactive):
    Called programmatically via `build_scoring_prompt()` + `pick_keeper()`.
    The orchestrating Claude Code session reads images, applies rubric, and
    calls `pick_keeper()` with the scores.

Output JSON:
    {
      "keeper_index": 2,
      "keeper_path": "img3.png",
      "outcome_tag": "keeper",
      "rationale": "Best likeness + clean hands + correct brand mark",
      "scores": [ ... per-variant detail ... ],
      "image_class": "owner"
    }

PRECONDITIONS (anthropic backend only)
--------------------------------------
- Key file at ~/workspace/second-brain-tier3/automation/secrets/anthropic.key
  (or ~/mnt/workspace/... for cowork sandbox).
- anthropic Python SDK installed.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Rubric definitions
# ---------------------------------------------------------------------------

# Owner-class rubric: likeness is the dominant criterion.
# v2 (2026-06-05): Tightened after pilot showed chooser over-scoring likeness
# vs operator eye. Likeness raised to 0-45, decomposed into hard sub-checks.
# "Kinda similar bald guy" must NOT score above 20. Only a face the operator
# would recognize as the real owner across a room scores 35+.
OWNER_RUBRIC = """\
You are an expert image quality reviewer for a local-business website.
You are HARSH on likeness — the operator will compare your judgment to their
own eye, and you must not over-score. When in doubt, score likeness DOWN.

Score this variant image against the following rubric. The image is for an
OWNER-PERSONALIZED slot — the owner's face MUST be recognizable as the
specific person in the reference photo, not merely "a similar-looking person."

## Scoring criteria (100 points total)

### 1. LIKENESS (0-45 points)  [DOMINANT — score harshly]
Score each sub-check independently. A "similar type" (same ethnicity, build,
bald) is NOT likeness — likeness means THIS SPECIFIC PERSON is recognizable.

- Face shape match (oval/round/square, width-to-height ratio)? (0-8)
  8 = identical shape. 4 = similar. 0 = clearly different shape.
- Jawline + chin match? (0-7)
  7 = same jawline. 3 = similar. 0 = visibly different.
- Eye shape, brow, and spacing match? (0-7)
  7 = same eyes. 3 = similar. 0 = different.
- Nose shape + size match? (0-6)
  6 = same nose. 3 = similar. 0 = different.
- Skin tone + complexion match? (0-5)
  5 = accurate. 2 = close. 0 = noticeably off.
- Facial hair pattern match (stubble density, coverage area)? (0-5)
  5 = same pattern. 2 = similar. 0 = different.
- Overall "would the operator recognize this as the same person across a
  room" gut check? (0-7)
  7 = unmistakably him. 3 = might be him. 0 = different person.

IMPORTANT: If ANY sub-check scores 0 (clearly different), cap the total
likeness at 15 max regardless of other sub-scores. A face that fails on
one major feature is NOT the right person.

### 2. BRAND MARK (0-20 points)
- Renders THIS client's mark correctly? (+10)
  Client mark: {brand_mark_description}
- Owner name spelled right if visible? (+5)
- No competitor logos or wrong brand? (+5, or -15 if wrong brand rendered)

### 3. AI ARTIFACTS (0-15 points)
- No extra/melted fingers? (+4)
- No distorted face? (+4)
- No garbled text? (+4)
- No watermarks? (+3)
- Deduct -8 per visible artifact class

### 4. COMPOSITION + ASPECT (0-10 points)
- Framing fits the slot:
  Hero (4:3): face engaged, 3/4 or front angle, working context (+7)
  About (3:4): chest-up portrait, professional, approachable (+7)
- Image fills frame without awkward cropping? (+3)

### 5. AVOID-LIST COMPLIANCE (0-10 points)
- No sunglasses? (+2)
- No hi-vis vest? (+2)
- No hard hat? (+2)
- No thumbs-up pose? (+2)
- No plastic/waxy skin texture? (+2)

## Output format (JSON only, no markdown fences):
{{
  "score": <int 0-100>,
  "likeness": <int 0-45>,
  "likeness_breakdown": {{
    "face_shape": <int 0-8>,
    "jawline_chin": <int 0-7>,
    "eyes_brow": <int 0-7>,
    "nose": <int 0-6>,
    "skin_tone": <int 0-5>,
    "facial_hair": <int 0-5>,
    "gut_check": <int 0-7>
  }},
  "brand_mark": <int 0-20>,
  "ai_artifacts": <int 0-15>,
  "composition": <int 0-10>,
  "avoid_list": <int 0-10>,
  "rationale": "<one sentence explaining the score>"
}}
"""

# Topic-class rubric: likeness is irrelevant; topic relevance + quality dominate.
# v3 (2026-06-05): After operator calibration on topic batch. Garbled/nonsense
# text is now a heavyweight penalty (was minor -4). For a trades site, a panel
# closeup with gibberish labels destroys credibility. TEXT LEGIBILITY is now
# its own 0-20 criterion; legible real-looking labels get positive score.
# Electrical-accuracy kept from v2.
TOPIC_RUBRIC = """\
You are an expert image quality reviewer for a local-business website.
This is an ELECTRICAL CONTRACTOR's site — technical accuracy AND text
legibility matter enormously. A homeowner will zoom in. A competitor will
screenshot. Score HARSHLY on garbled/nonsense text and AI-hallucinated
electrical details.

Score this variant image against the following rubric. The image is for a
TOPIC/CONTENT slot — it should depict the service, equipment, or scenario
described below. There should be NO identifiable owner face. The goal is
a professional, established-company look that a licensed electrician would
not be embarrassed to have on their site.

Service depicted: {page_service}
City context: {page_city}

## Scoring criteria (100 points total)

### 1. TEXT LEGIBILITY (0-20 points)  [TRADES-CRITICAL — score harshly]
AI generators produce gibberish text that looks plausible at thumbnail
but is nonsense on inspection. For a trades site, visible garbled labels
on panels, breakers, or equipment destroy credibility instantly.

- Are ALL visible text/labels legible real words? (0-10)
  10 = all text is real English words, correctly spelled (e.g., "KITCHEN",
       "LIVING ROOM", "HVAC", "ON", "OFF", "TRIP")
  5  = most text legible, one or two slightly fuzzy but passable
  0  = visible gibberish, nonsense letter combinations, garbled labels
       (e.g., "Timos", "Kandex", "Cimdc", "Duntory", "NO" where "ON"
       should be)
- Is text ABSENT or too small/blurred to read? (score 15/20 — neutral;
  no text is better than gibberish text)
- Are circuit directory labels (if visible) realistic room names? (0-5)
  5 = real names (Kitchen, Bedroom, HVAC). 0 = gibberish or absent.
- Are breaker toggle labels correct ("ON"/"OFF"/"TRIP")? (0-5)
  5 = correct. 2 = mostly correct with minor issues. 0 = garbled.

IMPORTANT: If an image has EXTENSIVE visible gibberish (3+ garbled
labels/words), cap this section at 3/20 MAX regardless of other text.
This is the single biggest AI tell on trades equipment closeups.

### 2. TOPIC RELEVANCE (0-20 points)
- Does the image actually depict the service/scenario described? (+12)
- Is the setting residential (not warehouse, not studio)? (+4)
- Would a homeowner recognize what service this represents? (+4)

### 3. TECHNICAL ACCURACY (0-20 points)  [ELECTRICAL-SPECIFIC]
- Wiring looks physically plausible? (0-6)
  6 = realistic. 3 = acceptable. 0 = obviously wrong.
- Breakers/components look like real products? (0-6)
  6 = realistic. 3 = generic but acceptable. 0 = melted/impossible.
- Panel layout follows basic electrical logic? (0-4)
  4 = plausible. 2 = simplified but OK. 0 = nonsensical.
- No code-violation red flags? (0-4)
  4 = clean. 0 = visible safety issue.

FLAG (do not auto-reject, but surface to operator): If the image depicts
a recognizable third-party brand/product (e.g., Tesla Wall Connector,
specific breaker manufacturer), note it in the rationale. The operator
decides whether brand-specific equipment fits the client's positioning.

If the image does NOT show electrical equipment, score 16/20 (neutral).

### 4. AI ARTIFACTS (0-15 points)
- No extra/melted fingers or tools? (+5)
- No distorted/impossible objects? (+5)
- No watermarks? (+5)
- Deduct -8 per visible artifact class
(Note: garbled text is scored in TEXT LEGIBILITY above, not here.)

### 5. PROFESSIONAL QUALITY + BRAND (0-25 points)
- Subject prominence — does the main subject (panel, charger, fixture,
  equipment) fill at least 50% of the frame and read as THE subject? (+8)
  8 = tight editorial crop, subject dominates. 4 = subject visible but
  competing with room context. 0 = wide room shot where the subject is
  incidental / not the clear focus. A wide shot where the equipment isn't
  the obvious subject should NOT be a keeper.

IMPORTANT: If subject_prominence is 2 or below (subject is NOT the
clear focus), cap the TOTAL score at 70 MAX regardless of other criteria.
A technically-perfect wide room shot where the service equipment is
incidental must NOT be a keeper.
- Lighting natural and warm (not clinical)? (+6)
- Doesn't look like generic stock photography? (+5)
- No competitor logos or wrong brand? (+3)
- No identifiable faces / no hard hats on residential? (+3)

## Output format (JSON only, no markdown fences):
{{
  "score": <int 0-100>,
  "text_legibility": <int 0-20>,
  "text_legibility_breakdown": {{
    "all_text_legible": <int 0-10>,
    "circuit_labels": <int 0-5>,
    "toggle_labels": <int 0-5>
  }},
  "topic_relevance": <int 0-20>,
  "technical_accuracy": <int 0-20>,
  "technical_accuracy_breakdown": {{
    "wiring": <int 0-6>,
    "components": <int 0-6>,
    "layout": <int 0-4>,
    "code_safety": <int 0-4>
  }},
  "ai_artifacts": <int 0-15>,
  "subject_prominence": <int 0-8>,
  "professional_quality_brand": <int 0-25>,
  "rationale": "<one sentence explaining the score>"
}}
"""


# Outcome tag thresholds
KEEPER_THRESHOLD = 75
ITERATE_THRESHOLD = 45


# ---------------------------------------------------------------------------
# Display-context weighting (D-06)
# ---------------------------------------------------------------------------

# When an image is displayed at hero size (1200x900px on a webpage), small text
# like breaker labels is not readable. Penalizing text_legibility at full weight
# (20/100) over-punishes hero images for garbled 8pt text nobody can read at
# display size. The modifier tells the vision model to down-weight legibility.
DISPLAY_CONTEXT_MODIFIERS = {
    "hero": (
        "\n\n## DISPLAY-CONTEXT ADJUSTMENT (hero image)\n"
        "This image will be displayed as a HERO image at approximately 1200×900px "
        "on a webpage. At this display size, small text on equipment (breaker labels, "
        "circuit directory text, panel markings) is NOT readable by the viewer. "
        "Therefore:\n"
        "- DOWN-WEIGHT text_legibility: score it out of 8 instead of 20. "
        "Small-text garbling that would be visible at full resolution is NOT "
        "visible at hero display size and should not heavily penalize the image.\n"
        "- Text that IS large enough to read at hero size (e.g., large signage, "
        "prominent lettering) should still be scored normally for legibility.\n"
        "- All other criteria remain at full weight.\n"
        "- In the output JSON, still use the 0-20 scale for text_legibility but "
        "apply the hero-display leniency (i.e., a score of 12-15 for small "
        "garbled text that would otherwise score 3-5).\n"
    ),
    "thumbnail": (
        "\n\n## DISPLAY-CONTEXT ADJUSTMENT (thumbnail)\n"
        "This image will be displayed as a small thumbnail. Text is entirely "
        "unreadable at this size. Score text_legibility 15/20 (neutral) unless "
        "there is LARGE garbled text visible even at thumbnail scale.\n"
    ),
}


def _display_context_modifier(display_context: str) -> str:
    """Return the rubric modifier for the given display context, or empty string."""
    return DISPLAY_CONTEXT_MODIFIERS.get(display_context, "")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def load_image_b64(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file.

    Detects actual content type from file header bytes, not extension —
    Higgsfield CLI serves JPEG content with .png URLs/filenames.
    """
    with open(path, "rb") as f:
        raw = f.read()

    # Detect from magic bytes
    if raw[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        # Fallback to extension
        mime, _ = mimetypes.guess_type(str(path))
        if mime is None:
            mime = "image/png"

    data = base64.standard_b64encode(raw).decode("ascii")
    return data, mime


def load_client_data(config_path: Path) -> dict[str, Any]:
    """Load the client config + client data JSON for brand fields."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    client_slug = config.get("client_slug", "")
    client_data_path = SCRIPTS_DIR / "data" / f"client-{client_slug}.json"
    if client_data_path.is_file():
        client_data = json.loads(client_data_path.read_text(encoding="utf-8"))
    else:
        client_data = {}
    return {**config, **client_data}


# ---------------------------------------------------------------------------
# Rubric builder (shared by both backends)
# ---------------------------------------------------------------------------


def build_rubric(
    image_class: str,
    client_data: dict[str, Any],
    page_service: str = "",
    page_city: str = "",
) -> str:
    """Return the filled rubric string for the given image class."""
    if image_class == "owner":
        return OWNER_RUBRIC.format(
            brand_mark_description=client_data.get(
                "brand_mark_description", "(unknown)"
            ),
        )
    return TOPIC_RUBRIC.format(
        page_service=page_service,
        page_city=page_city,
        primary_color=client_data.get("primary_color", "(unknown)"),
    )


def build_scoring_prompt(
    variant_paths: list[Path],
    reference_path: Optional[Path],
    slot_type: str,
    image_class: str,
    client_config_path: Path,
    page_service: str = "",
    page_city: str = "",
    display_context: str = "",
) -> dict[str, Any]:
    """Build the full scoring context for the in-session backend.

    Returns a dict with:
      rubric              — the filled rubric text
      variants            — list of {path, name} dicts
      reference           — {path, name} or None
      slot_type           — hero/about/scene
      image_class         — owner/topic
      client_data         — merged client config + data
      display_context     — hero/thumbnail/full-page or "" (D-06)
      display_modifier    — rubric modifier text for display context (D-06)
      variant_system_prompt_fn — callable name for D-04 filename binding

    The calling Claude Code session uses this to:
      1. Read each variant image via the Read tool
      2. Read the reference image (if owner-class)
      3. Apply the rubric visually (with display_modifier prepended if non-empty)
      4. Return per-variant JSON scores
      5. Call pick_keeper() with those scores
    """
    client_data = load_client_data(client_config_path)
    rubric = build_rubric(image_class, client_data, page_service, page_city)
    return {
        "rubric": rubric,
        "variants": [{"path": str(p), "name": p.name} for p in variant_paths],
        "reference": {"path": str(reference_path), "name": reference_path.name}
        if reference_path
        else None,
        "slot_type": slot_type,
        "image_class": image_class,
        "client_data": {
            k: v
            for k, v in client_data.items()
            if not k.startswith("_") and k != "owner_bio_paragraphs"
        },
        "display_context": display_context,
        "display_modifier": _display_context_modifier(display_context),
    }


# ---------------------------------------------------------------------------
# Outcome logic (shared by both backends)
# ---------------------------------------------------------------------------


def determine_outcome_tag(score: int) -> str:
    """Map score to outcome tag for the learning loop."""
    if score >= KEEPER_THRESHOLD:
        return "keeper"
    if score >= ITERATE_THRESHOLD:
        return "iterate"
    return "regression"


def pick_keeper(scores: list[dict[str, Any]], image_class: str = "", slot_type: str = "") -> dict[str, Any]:
    """Select the best variant and determine the outcome.

    `scores` is a list of dicts, each with at minimum:
      score         — int 0-100
      rationale     — str
      variant_path  — str
      variant_name  — str
    """
    # Separate parse-error variants (score -1) — they must not win the ranking
    valid = [s for s in scores if s.get("score", 0) >= 0]
    parse_errors = [s for s in scores if s.get("score", 0) < 0]
    if parse_errors:
        sys.stderr.write(
            f"  NOTE: {len(parse_errors)} variant(s) had parse errors — "
            f"excluded from ranking, flagged for human review.\n"
        )
    ranked = sorted(valid or scores, key=lambda s: s.get("score", 0), reverse=True)
    best = ranked[0]
    tag = determine_outcome_tag(best.get("score", 0))
    return {
        "keeper_index": scores.index(best),
        "keeper_path": best.get("variant_path", ""),
        "keeper_name": best.get("variant_name", ""),
        "outcome_tag": tag,
        "rationale": best.get("rationale", ""),
        "score": best.get("score", 0),
        "image_class": image_class,
        "slot_type": slot_type,
        "all_ranked": [
            {
                "rank": i + 1,
                "name": s.get("variant_name", ""),
                "score": s.get("score", 0),
                "tag": determine_outcome_tag(s.get("score", 0)),
                "rationale": s.get("rationale", ""),
            }
            for i, s in enumerate(ranked)
        ],
    }


# ---------------------------------------------------------------------------
# Backend: Anthropic API (standalone / headless)
# ---------------------------------------------------------------------------


# Key-loading discipline mirrors claude_query.py / perplexity_sonar.py in
# second-brain-tier3/automation/. Key stored as plain text value in a .key
# file; dual-path candidates for local Mac + cowork sandbox.
_TIER3_TAIL = Path("second-brain-tier3") / "automation" / "secrets" / "anthropic-claude.key"
_KEY_CANDIDATES = [
    Path.home() / "workspace" / _TIER3_TAIL,
    Path.home() / "mnt" / "workspace" / _TIER3_TAIL,
]


def _load_anthropic_key() -> str:
    """Load the Anthropic API key from the tier-3 secrets path."""
    key_path = next((p for p in _KEY_CANDIDATES if p.exists()), _KEY_CANDIDATES[0])
    if not key_path.exists():
        sys.exit(
            f"ERROR: {key_path} not found.\n"
            "The Anthropic API key should live at\n"
            f"  {_KEY_CANDIDATES[0]}\n"
            "as plain text (key value only, no JSON). Same discipline as\n"
            "claude_query.py / perplexity_sonar.py in second-brain-tier3/automation/.\n"
        )
    key = key_path.read_text().strip()
    if not key.startswith("sk-ant-"):
        sys.exit(
            f"ERROR: key at {key_path} doesn't look right "
            f"(expected sk-ant-... prefix, got {key[:10]}...).\n"
        )
    return key


def _extract_score_json(text: str, variant_name: str) -> Optional[dict]:
    """Try to extract a valid JSON score dict from model output.

    Handles: raw JSON, markdown-fenced JSON (```json ... ```), and JSON
    embedded in surrounding prose.  Returns None if nothing parseable found.
    """
    # Strip markdown code fences
    cleaned = text
    if cleaned.startswith("```"):
        # Remove opening fence line (```json or ```)
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        cleaned = cleaned.strip()

    # Attempt 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 2: find the first { ... } block (greedy)
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(cleaned[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _check_rationale_binding(result: dict[str, Any], variant_name: str, other_variant_names: list[str]) -> dict[str, Any]:
    """Post-score sanity-check: rationale must reference THIS variant's content only.

    D-04 fix: the vision model's rationale can cross-contaminate between variants
    (e.g., citing breaker labels from variant 1 in variant 3's rationale). Detect
    this by checking if the rationale references another variant's filename.
    """
    rationale = result.get("rationale", "")
    contaminated_refs = [
        name for name in other_variant_names
        if name != variant_name and name in rationale
    ]
    if contaminated_refs:
        result["cross_variant_contamination"] = contaminated_refs
        result["rationale"] = (
            f"[CONTAMINATION WARNING: rationale references {', '.join(contaminated_refs)}, "
            f"not just {variant_name}] {rationale}"
        )
        sys.stderr.write(
            f"  WARN: rationale for {variant_name} references other variant(s): "
            f"{', '.join(contaminated_refs)}\n"
        )
    return result


def _build_variant_system_prompt(variant_name: str) -> str:
    """System prompt anchoring the vision model to a specific variant file.

    D-04 fix: every vision call gets the variant filename in the system prompt
    so the model's rationale is bound to THAT specific image, not a prior one
    in the conversation.
    """
    return (
        f"You are scoring the image variant named '{variant_name}'. "
        f"Your rationale MUST describe only what you see in THIS specific image "
        f"('{variant_name}'). Do NOT reference content from any other image. "
        f"If you mention specific visual details (labels, text, objects), they "
        f"must be visible in '{variant_name}'."
    )


def _score_variant_api(
    api_client: Any,  # anthropic.Anthropic
    variant_path: Path,
    reference_path: Optional[Path],
    rubric: str,
    slot_type: str,
    all_variant_names: Optional[list[str]] = None,
    display_context: str = "",
) -> dict[str, Any]:
    """Score a single variant via the Anthropic Messages API."""
    variant_name = variant_path.name
    system_prompt = _build_variant_system_prompt(variant_name)
    messages_content: list[dict[str, Any]] = []

    if reference_path is not None:
        ref_b64, ref_mime = load_image_b64(reference_path)
        messages_content.append({
            "type": "text",
            "text": "REFERENCE PHOTO (the real owner — compare likeness to this):",
        })
        messages_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": ref_mime, "data": ref_b64},
        })

    var_b64, var_mime = load_image_b64(variant_path)
    messages_content.append({
        "type": "text",
        "text": f"VARIANT TO SCORE (slot: {slot_type}, file: {variant_name}):",
    })
    messages_content.append({
        "type": "image",
        "source": {"type": "base64", "media_type": var_mime, "data": var_b64},
    })

    # D-06: inject display-context rubric modifier before the rubric itself
    if display_context:
        messages_content.append({
            "type": "text",
            "text": _display_context_modifier(display_context),
        })

    messages_content.append({"type": "text", "text": rubric})

    # --- D-05: 3-tier retry on parse failure (was: 1 retry then punt) ---
    result = None
    raw_text = ""

    # Tier 1: standard call
    response = api_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": messages_content}],
    )
    raw_text = response.content[0].text.strip()
    result = _extract_score_json(raw_text, variant_name)

    # Tier 2: same prompt retry (model sometimes wraps in markdown on first try)
    if result is None:
        sys.stderr.write(
            f"  WARN: tier-1 parse failed for {variant_name}, retrying (tier 2)…\n"
        )
        t2_response = api_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": messages_content}],
        )
        raw_text = t2_response.content[0].text.strip()
        result = _extract_score_json(raw_text, variant_name)

    # Tier 3: simplified prompt with explicit JSON-only instruction
    if result is None:
        sys.stderr.write(
            f"  WARN: tier-2 parse failed for {variant_name}, retrying (tier 3 — simplified)…\n"
        )
        simplified_content = messages_content.copy()
        simplified_content.append({
            "type": "text",
            "text": (
                "\n\nIMPORTANT: Respond with ONLY a raw JSON object. "
                "No markdown fences, no commentary, no text before or after the JSON. "
                "Start your response with '{' and end with '}'."
            ),
        })
        t3_response = api_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": simplified_content}],
        )
        raw_text = t3_response.content[0].text.strip()
        result = _extract_score_json(raw_text, variant_name)

    # All 3 tiers failed — escalate with a recommended pick, don't silently punt
    if result is None:
        sys.stderr.write(
            f"  ERROR: all 3 parse tiers failed for {variant_name}\n"
        )
        result = {
            "score": -1,
            "rationale": (
                f"Parse error after 3-tier retry for '{variant_name}'. "
                f"Raw response sample: {raw_text[:150]}. "
                f"Escalating to operator — variant excluded from ranking but "
                f"operator should visually inspect before discarding."
            ),
            "outcome_tag": "parse-error-escalated",
            "parse_tiers_attempted": 3,
        }

    result["variant_path"] = str(variant_path)
    result["variant_name"] = variant_name

    # D-04: post-score cross-variant contamination check
    if all_variant_names and result.get("score", 0) >= 0:
        result = _check_rationale_binding(result, variant_name, all_variant_names)

    return result


def run_anthropic_backend(
    variant_paths: list[Path],
    reference_path: Optional[Path],
    slot_type: str,
    image_class: str,
    client_config_path: Path,
    page_service: str,
    page_city: str,
    display_context: str = "",
) -> dict[str, Any]:
    """Score all variants via the Anthropic API backend."""
    import anthropic

    key = _load_anthropic_key()
    api_client = anthropic.Anthropic(api_key=key)
    client_data = load_client_data(client_config_path)
    rubric = build_rubric(image_class, client_data, page_service, page_city)
    all_variant_names = [vp.name for vp in variant_paths]

    sys.stderr.write(f"→ Scoring {len(variant_paths)} variants for {slot_type} ({image_class}-class)\n")
    sys.stderr.write(f"→ Backend: anthropic API\n")
    if display_context:
        sys.stderr.write(f"→ Display context: {display_context}\n")
    if reference_path:
        sys.stderr.write(f"→ Reference: {reference_path.name}\n")

    scores = []
    for i, vp in enumerate(variant_paths):
        sys.stderr.write(f"  [{i + 1}/{len(variant_paths)}] {vp.name} ... ")
        result = _score_variant_api(
            api_client=api_client,
            variant_path=vp,
            reference_path=reference_path if image_class == "owner" else None,
            rubric=rubric,
            slot_type=slot_type,
            all_variant_names=all_variant_names,
            display_context=display_context,
        )
        # Enforce composition cap for topic-class: subject_prominence ≤ 2 → cap at 70
        if (image_class == "topic"
                and result.get("subject_prominence", 8) <= 2
                and result.get("score", 0) > 70):
            old_score = result["score"]
            result["score"] = 70
            result["rationale"] = (
                f"[COMPOSITION-CAPPED from {old_score}→70: subject not prominent] "
                + result.get("rationale", "")
            )
            sys.stderr.write(f"score={old_score}→70 (composition cap)\n")
        else:
            sys.stderr.write(f"score={result.get('score', '?')}\n")
        scores.append(result)

    outcome = pick_keeper(scores, image_class=image_class, slot_type=slot_type)
    outcome["scores"] = scores
    outcome["backend"] = "anthropic"

    sys.stderr.write(f"\n→ KEEPER: {outcome['keeper_name']} "
                     f"(score={outcome['score']}, tag={outcome['outcome_tag']})\n")
    sys.stderr.write(f"  Rationale: {outcome['rationale']}\n")

    if outcome["outcome_tag"] != "keeper":
        sys.stderr.write(
            f"\n  ⚠ Best variant scored {outcome['score']} — below keeper threshold "
            f"({KEEPER_THRESHOLD}). Tag: {outcome['outcome_tag']}. "
            f"Operator review recommended.\n"
        )

    return outcome


# ---------------------------------------------------------------------------
# CLI entry point (anthropic backend only — in-session is called from Claude
# Code directly via build_scoring_prompt() + pick_keeper())
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Score imagery variants against the auto-chooser rubric and "
            "select the keeper. Standalone mode uses the Anthropic API; "
            "in-session mode is invoked programmatically from Claude Code."
        ),
    )
    p.add_argument(
        "--variants",
        type=Path,
        nargs="+",
        required=True,
        help="Paths to variant images (PNG/JPG/WebP). Usually 4 from Higgsfield.",
    )
    p.add_argument(
        "--reference-photo",
        type=Path,
        default=None,
        help=(
            "Owner reference photo for likeness comparison. Required for "
            "owner-class slots; ignored for topic-class."
        ),
    )
    p.add_argument(
        "--slot-type",
        choices=["hero", "about", "scene"],
        required=True,
        help="Which page slot this image fills.",
    )
    p.add_argument(
        "--image-class",
        choices=["owner", "topic"],
        required=True,
        help=(
            "owner = owner-personalized (likeness-weighted); "
            "topic = content/scene (topic-relevance-weighted)."
        ),
    )
    p.add_argument(
        "--client-config",
        type=Path,
        required=True,
        help="Client config JSON (same one wire-page-images.py uses).",
    )
    p.add_argument(
        "--page-service",
        type=str,
        default="",
        help="Service name for the page (used in topic-class rubric).",
    )
    p.add_argument(
        "--page-city",
        type=str,
        default="",
        help="City display name for the page (used in topic-class rubric).",
    )
    p.add_argument(
        "--display-context",
        choices=["hero", "thumbnail", "full-page"],
        default="",
        help=(
            "Display context for the image. 'hero' down-weights small-text "
            "legibility (breaker labels not readable at hero display size). "
            "'thumbnail' treats all text as unreadable. 'full-page' uses "
            "default weights. Omit for default (full weight)."
        ),
    )
    p.add_argument(
        "--backend",
        choices=["anthropic"],
        default="anthropic",
        help="Vision backend. 'anthropic' = standalone API calls. In-session mode is not CLI-invocable.",
    )

    args = p.parse_args()

    for vp in args.variants:
        if not vp.is_file():
            sys.stderr.write(f"ERROR: variant not found: {vp}\n")
            return 2

    if args.image_class == "owner" and args.reference_photo is None:
        sys.stderr.write("ERROR: --reference-photo is required for owner-class slots.\n")
        return 2

    if args.reference_photo and not args.reference_photo.is_file():
        sys.stderr.write(f"ERROR: reference photo not found: {args.reference_photo}\n")
        return 2

    result = run_anthropic_backend(
        variant_paths=args.variants,
        reference_path=args.reference_photo,
        slot_type=args.slot_type,
        image_class=args.image_class,
        client_config_path=args.client_config,
        page_service=args.page_service,
        page_city=args.page_city,
        display_context=args.display_context,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
