#!/usr/bin/env python3
"""
demand_capture_emitter.py — Dossier emitter + source-status gate for demand-capture.

Builds the two-file human+machine dossier split:
  - Markdown dossier: source-status table, NO-MIRROR assertion, rendered data
  - JSON companion: machine-readable parsed data for brief consumption

Gate: MUST FAIL if any in-scope source is neither pulled nor labelled with
its SOP + reason. No silent skips (CR-155/156 discipline).

Zero hardcoded client values. All rendering is parameterized from dossier_data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# In-scope sources that MUST be pulled or labelled with SOP/reason
IN_SCOPE_SOURCES = [
    "S1", "S2", "S3", "S4",
    "S5-sonar", "S5-chatgpt-gemini",
    "S7-existing", "S8",
]

SOURCE_DESCRIPTIONS = {
    "S1": "Google PAA (People Also Ask)",
    "S2": "Google AI Overviews",
    "S3": "DataForSEO Volume + Difficulty",
    "S4": "Keyword Suggestions (question-tree)",
    "S5-sonar": "Perplexity Sonar (answer-engine)",
    "S5-chatgpt-gemini": "ChatGPT + Gemini (answer-engine, manual)",
    "S7-existing": "GSC First-Party (existing report)",
    "S7-fresh": "GSC Fresh Per-Slug (host-side)",
    "S8": "Competitor On-Page (SERP organic)",
}


# ---------------------------------------------------------------------------
# Gate check
# ---------------------------------------------------------------------------

def gate_check(source_status: dict[str, dict]) -> tuple[bool, str]:
    """Check that every in-scope source is pulled OR labelled with reason/SOP.

    Returns (pass, message). Fails if any source is unlabelled.
    Gated sources (manual steps with SOP documented) count as labelled.
    """
    missing: list[str] = []
    for source in IN_SCOPE_SOURCES:
        status = source_status.get(source, {})
        pulled = status.get("pulled", False)
        gated = status.get("gated", False)
        has_reason = bool(status.get("reason"))
        if not pulled and not gated and not has_reason:
            missing.append(source)

    pulled_count = sum(
        1 for s in IN_SCOPE_SOURCES
        if source_status.get(s, {}).get("pulled")
    )
    gated_count = sum(
        1 for s in IN_SCOPE_SOURCES
        if source_status.get(s, {}).get("gated")
    )
    labelled_count = sum(
        1 for s in IN_SCOPE_SOURCES
        if source_status.get(s, {}).get("reason")
        and not source_status.get(s, {}).get("pulled")
    )
    total = len(IN_SCOPE_SOURCES)

    if missing:
        return False, (
            f"{pulled_count}/{total} pulled, {gated_count} gated, "
            f"{labelled_count} labelled, {len(missing)} UNLABELLED: "
            f"{', '.join(missing)}"
        )

    # CR-164: minimum-pulled-count floor — a dossier with zero actual
    # demand data is not usable even if every gap is labelled.
    if pulled_count < 2:
        return False, (
            f"Minimum 2 sources must be actually pulled (got {pulled_count}). "
            f"{labelled_count} labelled, {gated_count} gated — "
            f"not enough data for a dossier."
        )

    return True, (
        f"{pulled_count}/{total} IN-SCOPE sources pulled, "
        f"{gated_count} gated, {labelled_count} labelled with SOP/reason"
    )


# ---------------------------------------------------------------------------
# Dossier rendering helpers
# ---------------------------------------------------------------------------

def _render_source_status_table(source_status: dict[str, dict]) -> str:
    """Render the source-status table for the dossier markdown."""
    lines = [
        "| # | Source | Status | Artifacts | Notes |",
        "|---|--------|--------|-----------|-------|",
    ]
    all_sources = [
        "S1", "S2", "S3", "S4",
        "S5-sonar", "S5-chatgpt-gemini",
        "S7-existing", "S7-fresh", "S8",
    ]

    for source_id in all_sources:
        desc = SOURCE_DESCRIPTIONS.get(source_id, source_id)
        status = source_status.get(source_id, {})

        if status.get("pulled"):
            status_str = "DONE"
        elif status.get("gated"):
            status_str = "GATED (manual)"
        elif status.get("reason"):
            status_str = f"GAP — {status['reason']}"
        else:
            status_str = "NOT ATTEMPTED"

        artifacts_str = ", ".join(
            f"`{a}`" for a in status.get("artifacts", [])
        ) or "—"

        notes_parts: list[str] = []
        if status.get("sop"):
            notes_parts.append(f"SOP: {status['sop']}")
        if status.get("cost"):
            notes_parts.append(f"${status['cost']:.4f}")
        notes = " | ".join(notes_parts) or "—"

        lines.append(
            f"| {source_id} | {desc} | {status_str} | {artifacts_str} | {notes} |"
        )

    return "\n".join(lines)


def _render_volume_table(raw_data: dict[str, Any]) -> str:
    """Render S3 volume data from dfs-volume.json."""
    vol_data = raw_data.get("dfs-volume.json")
    if not vol_data:
        return "_No volume data available._"

    from dfs_demand import parse_volume_response

    parsed = parse_volume_response(vol_data)
    if not parsed:
        return "_Volume response contained no keyword data._"

    lines = [
        "| Keyword | Volume | Competition | CPC |",
        "|---------|--------|-------------|-----|",
    ]
    for kw in parsed:
        vol = kw["volume"] if kw["volume"] is not None else "—"
        comp = kw["competition"] if kw["competition"] is not None else "—"
        cpc = f"${kw['cpc']:.2f}" if kw["cpc"] is not None else "—"
        lines.append(f"| {kw['keyword']} | {vol} | {comp} | {cpc} |")

    return "\n".join(lines)


def _render_serp_summary(raw_data: dict[str, Any], serp_key: str) -> str:
    """Render SERP summary from serp-*.json."""
    serp_data = raw_data.get(serp_key)
    if not serp_data:
        return "_No SERP data available._"

    from dfs_demand import parse_serp_response

    parsed = parse_serp_response(serp_data)
    lines: list[str] = []

    lines.append(f"**AI Overview:** {'YES' if parsed['ai_overview'] else 'NO'}")

    if parsed["paa"]:
        lines.append(f"\n**PAA ({len(parsed['paa'])} questions):**")
        for q in parsed["paa"]:
            lines.append(f"- {q}")
    else:
        lines.append("\n**PAA:** None (thin SERP)")

    if parsed["organic"]:
        lines.append(f"\n**Top Organic (showing {min(len(parsed['organic']), 10)}):**")
        lines.append("| Pos | Domain | Title |")
        lines.append("|-----|--------|-------|")
        for org in parsed["organic"][:10]:
            title = (org.get("title") or "")[:60]
            lines.append(
                f"| {org.get('position', '?')} | {org.get('domain', '?')} | {title} |"
            )

    if parsed["local_pack"]:
        lines.append("\n**Local Pack:**")
        for lp in parsed["local_pack"]:
            lines.append(f"- {lp['title']} ({lp.get('domain') or '—'})")

    return "\n".join(lines)


def _render_s4_summary(
    raw_data: dict[str, Any], service: str, city: str,
) -> str:
    """Render S4 keyword suggestions summary."""
    from dfs_demand import parse_keyword_suggestions

    lines: list[str] = []

    # Per-city seed
    city_key = f"s4-{service}-{city}.json"
    city_data = raw_data.get(city_key)
    if city_data:
        city_parsed = parse_keyword_suggestions(city_data)
        seed_label = f"{service.replace('-', ' ')} {city}"
        if city_parsed:
            lines.append(
                f"**Per-city seed** (`{seed_label}`): {len(city_parsed)} items"
            )
        else:
            lines.append(
                f"**Per-city seed** (`{seed_label}`): 0 items — "
                f"no hyperlocal keyword DB entries (confirmed finding)"
            )

    # Broad seed
    broad_key = f"s4-broad-{service}.json"
    broad_data = raw_data.get(broad_key)
    if broad_data:
        broad_parsed = parse_keyword_suggestions(broad_data)
        broad_label = service.replace("-", " ")
        if broad_parsed:
            lines.append(
                f"\n**Broad seed** (`{broad_label}`): {len(broad_parsed)} items"
            )
            question_starters = (
                "how", "why", "what", "do", "can", "is", "when", "where", "should",
            )
            questions = [
                k for k in broad_parsed
                if k["keyword"].lower().startswith(question_starters)
            ]
            if questions:
                lines.append(
                    f"\nTop question-form keywords ({len(questions)} found):"
                )
                for q in questions[:5]:
                    vol = q.get("volume", "—")
                    lines.append(f"- {q['keyword']} (vol: {vol})")
        else:
            lines.append(f"\n**Broad seed** (`{broad_label}`): 0 items")

    return "\n".join(lines) if lines else "_No S4 data available._"


def _render_gsc_summary(raw_data: dict[str, Any], slug: str) -> str:
    """Render GSC first-party data summary."""
    gsc_data = raw_data.get("gsc-existing")
    if not gsc_data:
        return "_No existing GSC report found._"

    meta = gsc_data.get("metadata", {})
    summary = gsc_data.get("summary", {})
    window = meta.get("window", {})

    lines = [
        f"**Property:** `{meta.get('gsc_property', '?')}`",
        f"**Window:** {window.get('start', '?')} to {window.get('end', '?')}",
        (
            f"**Total queries:** {meta.get('total_query_rows', '?')} | "
            f"**Impressions:** {summary.get('total_impressions', '?')}"
        ),
    ]

    # Extract service part from slug for relevance filtering
    parts = slug.rsplit("-", 2)
    service_part = parts[0] if len(parts) >= 3 else slug
    service_words = service_part.replace("-", " ")

    top_queries = gsc_data.get("top_queries_by_impressions", [])
    relevant = [
        q for q in top_queries
        if any(
            service_words in (k.lower() if isinstance(k, str) else "")
            for k in q.get("keys", [])
        )
    ]

    if relevant:
        lines.append(f"\n**Relevant queries (matching '{service_words}'):**")
        lines.append("| Query | Impressions | Clicks | Position |")
        lines.append("|-------|-------------|--------|----------|")
        for q in relevant[:10]:
            keys = q.get("keys", ["?"])
            lines.append(
                f"| {keys[0]} | {q.get('impressions', 0)} | "
                f"{q.get('clicks', 0)} | {q.get('position', 0):.1f} |"
            )

    striking = gsc_data.get("striking_distance_queries", [])
    if striking:
        lines.append(
            f"\n**Striking distance ({len(striking)} queries between pos 5–20)**"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dossier builder
# ---------------------------------------------------------------------------

def build_dossier(
    dossier_data: dict[str, Any],
    raw_data: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, dict]:
    """Build the dossier markdown + machine-readable JSON.

    Returns (markdown_content, json_dict).
    """
    slug = dossier_data["slug"]
    service = dossier_data["service"]
    service_display = dossier_data["service_display"]
    city = dossier_data["city"]
    state = dossier_data["state"]
    client = dossier_data["client"]
    domain = dossier_data["domain"]
    serp_key = f"serp-{slug}.json"

    # ── Markdown ──
    sonar_line = (
        "See `sonar-report.md` in this directory."
        if dossier_data["source_status"].get("S5-sonar", {}).get("pulled")
        else "_Not pulled._"
    )

    md_lines = [
        "---",
        "type: demand-dossier",
        f"service: {service}",
        f"city: {city}",
        f"state: {state}",
        f"client: {client}",
        f"domain: {domain}",
        f"slug: {slug}",
        f"created: {dossier_data['pulled_at']}",
        f"updated: {dossier_data['pulled_at']}",
        f"tags: [demand-dossier, {client}, {city}]",
        "---",
        "",
        f"# Demand Dossier — {service_display} × {city} ({state})",
        "",
        f"**Client:** {client} | **Domain:** {domain}",
        (
            f"**Pulled:** {dossier_data['pulled_at']} | "
            f"**DataForSEO cost:** ${dossier_data['cost_total']:.4f}"
        ),
        f"**GSC window:** {dossier_data['gsc_window']}",
        "",
        "## Source Status",
        "",
        _render_source_status_table(dossier_data["source_status"]),
        "",
        "## NO-MIRROR Assertion",
        "",
        (
            f"All data in this dossier was pulled for **{city}, {state}** "
            f"specifically."
        ),
        (
            f"SERP query: `{service_display} {city} {state}` | "
            f"S4 city seed: `{service_display.replace('-', ' ')} {city}`"
        ),
        "No data was inherited from any sibling city's pull.",
        "",
        "## S3 — Search Volume + Difficulty",
        "",
        _render_volume_table(raw_data),
        "",
        "## S1/S2/S8 — SERP Analysis",
        "",
        _render_serp_summary(raw_data, serp_key),
        "",
        "## S4 — Keyword Suggestions (Question-Tree)",
        "",
        _render_s4_summary(raw_data, service, city),
        "",
        "## S7 — GSC First-Party",
        "",
        _render_gsc_summary(raw_data, slug),
        "",
        "## S5 — Answer-Engine Capture",
        "",
        "### Sonar",
        "",
        sonar_line,
        "",
        "### ChatGPT + Gemini (GATED)",
        "",
        f"See `s5-chatgpt-gemini-{slug}.md` — fill manually via SOP-S5.",
        "",
    ]

    md = "\n".join(md_lines)

    # ── Machine-readable JSON ──
    from dfs_demand import (
        parse_keyword_suggestions,
        parse_serp_response,
        parse_volume_response,
    )

    machine: dict[str, Any] = {
        "metadata": {
            "slug": slug,
            "service": service,
            "service_display": service_display,
            "city": city,
            "state": state,
            "client": client,
            "domain": domain,
            "pulled_at": dossier_data["pulled_at"],
            "cost_total": dossier_data["cost_total"],
            "gsc_window": dossier_data["gsc_window"],
            "location_code": dossier_data["location_code"],
        },
        "source_status": dossier_data["source_status"],
        "artifacts": dossier_data.get("artifacts", {}),
    }

    vol_data = raw_data.get("dfs-volume.json")
    if vol_data:
        machine["s3_volume"] = parse_volume_response(vol_data)

    serp_data = raw_data.get(serp_key)
    if serp_data:
        machine["s1_s2_s8_serp"] = parse_serp_response(serp_data)

    city_key = f"s4-{service}-{city}.json"
    if city_key in raw_data:
        machine["s4_city"] = parse_keyword_suggestions(raw_data[city_key])

    broad_key = f"s4-broad-{service}.json"
    if broad_key in raw_data:
        machine["s4_broad"] = parse_keyword_suggestions(raw_data[broad_key])

    gsc_existing = raw_data.get("gsc-existing")
    if gsc_existing:
        machine["s7_existing"] = {
            "metadata": gsc_existing.get("metadata"),
            "summary": gsc_existing.get("summary"),
            "top_queries": gsc_existing.get(
                "top_queries_by_impressions", []
            )[:20],
            "striking_distance": gsc_existing.get(
                "striking_distance_queries", []
            )[:20],
        }

    return md, machine


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_dossier(
    md_path: Path,
    md_content: str,
    json_path: Path,
    json_content: dict,
) -> None:
    """Write the two-file human+machine dossier split."""
    md_path.write_text(md_content, encoding="utf-8")
    json_path.write_text(
        json.dumps(json_content, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
