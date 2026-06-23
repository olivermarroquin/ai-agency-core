#!/usr/bin/env python3
"""
verify-artifact.py — consolidated pre-publish verification engine.

One command that runs the full on-disk sweep across all configured surfaces
and returns a per-check pass/fail verdict. Agnostic engine + per-artifact-type
profiles. Core-30 page-folder is ONE profile. No SEO/Core-30 hardcoding here.

USAGE
-----
Verify a Core-30 page folder:

    python verify-artifact.py \
        --profile profiles/verify-core-30-page.json \
        --artifact-dir /path/to/page-folder/ \
        --vars city_slug=vienna-va client_slug=ev-electric-services service_slug=ev-charger-installation

Verify a research brief:

    python verify-artifact.py \
        --profile profiles/verify-research-brief.json \
        --artifact-dir /path/to/brief/ \
        --vars city_slug=vienna-va

JSON output for machine consumption:

    python verify-artifact.py \
        --profile profiles/verify-core-30-page.json \
        --artifact-dir /path/to/page-folder/ \
        --vars city_slug=vienna-va client_slug=ev-electric-services service_slug=ev-charger-installation \
        --json

EXIT CODES
----------
  0 — all checks pass (clean)
  1 — at least one blocking finding
  2 — configuration / file error

ARCHITECTURE
------------
Engine loads a verification profile (JSON) that declares:
  - surfaces: which files to load from the artifact directory
  - ground_truth: where to load authoritative data from
  - checks: ordered list of check configurations

Each check has a "type" that dispatches to a check function in this engine.
Check functions receive the loaded surfaces, ground truth, and check config,
and return a list of findings. The engine aggregates findings into a verdict.

Check types:
  - pattern-sweep: regex pattern matching across surfaces (placeholder/FILL family)
  - identity-leak: cross-client brand name leak detection
  - value-cross-check: facts cross-check against ground truth data
  - schema-coverage: meta/og/JSON-LD field coverage + validity
  - image-integrity: placeholder image src + HTTP resolution
  - link-resolution: internal link 404 detection
  - hardcode-scan: delegates to hardcode-scanner.py (SC-1)

Profiles are registered instances. The engine never mentions any domain.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Profile + surface loading
# ---------------------------------------------------------------------------

def load_profile(path: Path) -> dict[str, Any]:
    """Load and validate a verification profile."""
    if not path.is_file():
        sys.stderr.write(f"ERROR: verification profile not found: {path}\n")
        sys.exit(2)
    profile = json.loads(path.read_text(encoding="utf-8"))
    required = {"profile_id", "surfaces", "checks"}
    missing = required - set(profile.keys())
    if missing:
        sys.stderr.write(f"ERROR: profile missing required keys: {missing}\n")
        sys.exit(2)
    return profile


def resolve_template(template: str, variables: dict[str, str]) -> str:
    """Resolve {var} placeholders in a string using variables dict."""
    try:
        return template.format(**variables)
    except KeyError as e:
        sys.stderr.write(
            f"ERROR: template '{template}' requires variable {e} "
            f"not provided in --vars. Available: {list(variables.keys())}\n"
        )
        sys.exit(2)


def load_surfaces(
    artifact_dir: Path,
    surface_specs: list[dict[str, Any]],
    variables: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Load artifact surfaces from disk. Returns {surface_id: {path, content, type}}."""
    surfaces: dict[str, dict[str, Any]] = {}
    for spec in surface_specs:
        sid = spec["id"]
        glob_pattern = resolve_template(spec.get("glob", ""), variables)
        surface_type = spec.get("type", "text")

        matches = sorted(artifact_dir.glob(glob_pattern))
        if not matches:
            if spec.get("required", True):
                surfaces[sid] = {
                    "path": str(artifact_dir / glob_pattern),
                    "content": None,
                    "type": surface_type,
                    "found": False,
                    "error": f"No files matching '{glob_pattern}' in {artifact_dir}",
                }
            continue

        # Use first match (profiles should use specific enough globs)
        fpath = matches[0]
        try:
            content = fpath.read_text(encoding="utf-8")
        except OSError as e:
            content = None
            surfaces[sid] = {
                "path": str(fpath),
                "content": None,
                "type": surface_type,
                "found": True,
                "error": str(e),
            }
            continue

        surfaces[sid] = {
            "path": str(fpath),
            "content": content,
            "type": surface_type,
            "found": True,
        }

    return surfaces


def load_ground_truth(
    gt_config: dict[str, Any] | None,
    variables: dict[str, str],
    workspace_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load ground truth data files. Returns {alias: data_dict}."""
    if not gt_config:
        return {}

    base_path_template = gt_config.get("base_path", "")
    if workspace_root and not Path(base_path_template).is_absolute():
        base = workspace_root / base_path_template
    else:
        base = Path(base_path_template)

    ground_truth: dict[str, dict[str, Any]] = {}

    for source in gt_config.get("sources", []):
        alias = source["alias"]
        pattern = resolve_template(source["pattern"], variables)
        fpath = base / pattern

        # Support fallback patterns (e.g., service overrides)
        fallback = source.get("fallback_pattern")
        if not fpath.is_file() and fallback:
            fpath = base / resolve_template(fallback, variables)

        if not fpath.is_file():
            ground_truth[alias] = {"_error": f"File not found: {fpath}", "_path": str(fpath)}
            continue

        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            data["_path"] = str(fpath)
            ground_truth[alias] = data
        except (json.JSONDecodeError, OSError) as e:
            ground_truth[alias] = {"_error": str(e), "_path": str(fpath)}

    return ground_truth


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

class _MetaOgExtractor(HTMLParser):
    """Extract meta tags and og: properties from HTML head."""

    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}       # name -> content
        self.og: dict[str, str] = {}         # property -> content
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = dict(attrs)
            name = a.get("name", "")
            prop = a.get("property", "")
            content = a.get("content", "")
            if name:
                self.meta[name.lower()] = content
            if prop:
                self.og[prop.lower()] = content

    def handle_data(self, data: str):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag: str):
        if tag == "title":
            self._in_title = False


def extract_meta_og(html: str) -> dict[str, Any]:
    """Extract meta tags, og properties, and title from HTML."""
    parser = _MetaOgExtractor()
    parser.feed(html)
    return {"meta": parser.meta, "og": parser.og, "title": parser.title.strip()}


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Extract and parse all JSON-LD blocks from HTML."""
    pattern = re.compile(
        r'<script\s+type="application/ld\+json">\s*(.*?)\s*</script>',
        re.DOTALL | re.IGNORECASE,
    )
    blocks = []
    for match in pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            blocks.append(data)
        except json.JSONDecodeError as e:
            blocks.append({"_parse_error": str(e), "_raw": match.group(1)[:200]})
    return blocks


def extract_jsonld_types(blocks: list[dict[str, Any]]) -> set[str]:
    """Get all @type values from parsed JSON-LD blocks, recursively.

    Walks the entire JSON-LD structure (including nested objects like
    hasMenu, potentialAction, founder, aggregateRating, etc.) to collect
    every @type value at any depth. This is essential for restaurant pages
    where Menu, ReserveAction, and OrderAction are nested properties of
    the Restaurant node, not separate @graph entries.
    """
    types: set[str] = set()

    def _collect_types(obj: Any) -> None:
        if isinstance(obj, dict):
            if "_parse_error" in obj:
                return
            t = obj.get("@type")
            if isinstance(t, str):
                types.add(t)
            elif isinstance(t, list):
                types.update(tt for tt in t if isinstance(tt, str))
            for v in obj.values():
                _collect_types(v)
        elif isinstance(obj, list):
            for item in obj:
                _collect_types(item)

    for block in blocks:
        _collect_types(block)

    return types


def extract_img_srcs(html: str) -> list[dict[str, str]]:
    """Extract all <img src> values with context."""
    results = []
    for m in re.finditer(r'<img\b[^>]*\bsrc="([^"]*)"[^>]*>', html, re.IGNORECASE):
        full_tag = m.group(0)
        src = m.group(1)
        alt = ""
        alt_m = re.search(r'alt="([^"]*)"', full_tag, re.IGNORECASE)
        if alt_m:
            alt = alt_m.group(1)
        line = html[:m.start()].count("\n") + 1
        results.append({"src": src, "alt": alt, "line": line})
    return results


def extract_internal_links(html: str, site_domain: str | None = None) -> list[dict[str, Any]]:
    """Extract internal <a href> links from HTML."""
    results = []
    for m in re.finditer(r'<a\b[^>]*\bhref="([^"]*)"[^>]*>', html, re.IGNORECASE):
        href = m.group(1)
        line = html[:m.start()].count("\n") + 1
        # Classify as internal
        is_internal = False
        if href.startswith("/"):
            is_internal = True
        elif site_domain and site_domain in href:
            is_internal = True
        if is_internal:
            text_end = html.find("</a>", m.end())
            link_text = ""
            if text_end > 0:
                link_text = re.sub(r"<[^>]+>", "", html[m.end():text_end]).strip()[:80]
            results.append({"href": href, "line": line, "text": link_text})
    return results


# ---------------------------------------------------------------------------
# Check implementations — dispatched by type string
# ---------------------------------------------------------------------------

def _check_pattern_sweep(
    surfaces: dict[str, dict[str, Any]],
    _gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    _vars: dict[str, str],
) -> list[dict[str, Any]]:
    """Sweep surfaces for regex patterns (placeholder/FILL family)."""
    findings: list[dict[str, Any]] = []
    target_surfaces = config.get("surfaces", list(surfaces.keys()))
    severity = config.get("severity", "blocking")
    patterns = config.get("patterns", [])

    compiled = []
    for p in patterns:
        try:
            compiled.append((re.compile(p["regex"], re.IGNORECASE if p.get("case_insensitive", True) else 0), p))
        except re.error as e:
            findings.append({
                "check_id": config["id"],
                "severity": "warning",
                "surface": "(config)",
                "message": f"Invalid regex '{p['regex']}': {e}",
            })

    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("content"):
            continue
        content = surface["content"]
        for regex, pspec in compiled:
            for match in regex.finditer(content):
                line = content[:match.start()].count("\n") + 1
                snippet = match.group(0)[:100]
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "file": surface["path"],
                    "line": line,
                    "pattern_label": pspec.get("label", pspec["regex"]),
                    "matched": snippet,
                    "message": f"{pspec.get('label', 'pattern match')}: '{snippet}' at line {line}",
                })

    return findings


def _check_identity_leak(
    surfaces: dict[str, dict[str, Any]],
    gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    variables: dict[str, str],
) -> list[dict[str, Any]]:
    """Detect cross-identity brand name leaks across surfaces.

    Loads sibling identity files from disk, collects identity strings for
    every identity EXCEPT the current one, and scans all configured surfaces.
    """
    findings: list[dict[str, Any]] = []
    severity = config.get("severity", "blocking")
    target_surfaces = config.get("surfaces", list(surfaces.keys()))
    identity_source_alias = config.get("identity_source", "client")
    identity_fields = config.get("identity_fields", ["name"])
    identity_dir_pattern = config.get("identity_dir_pattern", "")
    current_slug_var = config.get("current_slug_var", "client_slug")
    current_slug = variables.get(current_slug_var, "")

    # Resolve identity directory
    if identity_dir_pattern:
        identity_source = gt.get(identity_source_alias, {})
        source_path = identity_source.get("_path", "")
        if source_path:
            identity_dir = Path(source_path).parent
        else:
            return findings
    else:
        identity_source = gt.get(identity_source_alias, {})
        source_path = identity_source.get("_path", "")
        if source_path:
            identity_dir = Path(source_path).parent
        else:
            return findings

    file_glob = config.get("identity_file_glob", "client-*.json")

    # Collect foreign identity strings
    foreign_strings: list[tuple[str, str]] = []  # (string, source_file)
    for f in sorted(identity_dir.glob(file_glob)):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Determine if this is the current identity
        slug = data.get(current_slug_var.replace("_slug", "_slug"), "")
        if not slug:
            slug = data.get("client_slug", data.get("slug", ""))
        if slug == current_slug or not slug:
            continue
        for field in identity_fields:
            val = data.get(field, "")
            if val and len(str(val)) >= 4:
                foreign_strings.append((str(val), f.stem))

    # Scan surfaces
    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("content"):
            continue
        content = surface["content"]
        content_lower = content.lower()
        for brand, source_file in foreign_strings:
            if brand.lower() in content_lower:
                idx = content_lower.index(brand.lower())
                line = content[:idx].count("\n") + 1
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "file": surface["path"],
                    "line": line,
                    "foreign_string": brand,
                    "source_identity": source_file,
                    "message": f"Foreign identity '{brand}' (from {source_file}) found at line {line}",
                })

    return findings


def _check_value_cross_check(
    surfaces: dict[str, dict[str, Any]],
    gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    variables: dict[str, str],
) -> list[dict[str, Any]]:
    """Cross-check claimed values against ground truth (GPR-10).

    Two check modes per fact:

    1. **presence** (default): Verify the ground-truth value appears in the
       configured surfaces (case-insensitive). Missing = advisory/blocking
       depending on config. No regex extraction — just string search.

    2. **structured-extract**: Use a precise regex to extract a value from a
       structured location (e.g., JSON-LD "ratingValue": "4.9") and compare
       against ground truth. Use ONLY when the extraction target is
       unambiguous (schema fields, meta content attributes, structured data).

    Additionally, each fact can declare `wrong_values` — strings that MUST NOT
    appear in the surfaces (e.g., wrong-city county name on another city's page).
    """
    findings: list[dict[str, Any]] = []
    severity = config.get("severity", "blocking")
    facts = config.get("checkable_facts", [])

    for fact in facts:
        fact_key = fact["fact_key"]
        gt_alias = fact["ground_truth_alias"]
        gt_field = fact["ground_truth_field"]
        fact_severity = fact.get("severity", severity)
        required_in_sources = fact.get("required_in_sources", True)
        check_mode = fact.get("check_mode", "presence")
        check_surfaces = fact.get("extraction_surfaces", [])
        extraction_patterns = fact.get("extraction_patterns", [])
        wrong_values = fact.get("wrong_values", [])

        # Get ground truth value
        gt_data = gt.get(gt_alias, {})
        if "_error" in gt_data:
            if required_in_sources:
                findings.append({
                    "check_id": config["id"],
                    "severity": fact_severity,
                    "fact_key": fact_key,
                    "message": f"Ground truth source '{gt_alias}' not available: {gt_data['_error']}",
                    "expected": "(unavailable)",
                    "found": "(n/a)",
                })
            continue

        # Navigate nested fields (supports dot notation + array indices)
        gt_value = gt_data
        for part in gt_field.split("."):
            resolved = resolve_template(part, variables) if "{" in part else part
            if isinstance(gt_value, dict):
                gt_value = gt_value.get(resolved)
            elif isinstance(gt_value, list):
                try:
                    gt_value = gt_value[int(resolved)]
                except (ValueError, IndexError):
                    gt_value = None
                    break
            else:
                gt_value = None
                break

        if gt_value is None or (isinstance(gt_value, str) and not gt_value.strip()):
            if required_in_sources:
                findings.append({
                    "check_id": config["id"],
                    "severity": fact_severity,
                    "fact_key": fact_key,
                    "message": f"Ground truth field '{gt_field}' missing/empty in {gt_alias}",
                    "expected": "(missing in ground truth)",
                    "found": "(n/a)",
                })
            continue

        gt_str = str(gt_value).strip()

        # --- MODE: presence ---
        if check_mode == "presence":
            for surface_id in check_surfaces:
                surface = surfaces.get(surface_id)
                if not surface or not surface.get("content"):
                    continue
                if gt_str.lower() not in surface["content"].lower():
                    findings.append({
                        "check_id": config["id"],
                        "severity": fact_severity,
                        "fact_key": fact_key,
                        "surface": surface_id,
                        "expected": gt_str,
                        "found": "(not found in surface)",
                        "message": (
                            f"Ground truth value for '{fact_key}' not found in "
                            f"{surface_id}: expected '{gt_str}'"
                        ),
                    })

        # --- MODE: structured-extract ---
        elif check_mode == "structured-extract":
            compiled_patterns = []
            for p in extraction_patterns:
                try:
                    compiled_patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    continue

            for surface_id in check_surfaces:
                surface = surfaces.get(surface_id)
                if not surface or not surface.get("content"):
                    continue
                content = surface["content"]

                for pat in compiled_patterns:
                    for m in pat.finditer(content):
                        extracted = m.group(1) if m.lastindex else m.group(0)
                        extracted = extracted.strip()
                        if extracted.lower() != gt_str.lower():
                            line = content[:m.start()].count("\n") + 1
                            findings.append({
                                "check_id": config["id"],
                                "severity": fact_severity,
                                "fact_key": fact_key,
                                "surface": surface_id,
                                "line": line,
                                "expected": gt_str,
                                "found": extracted,
                                "message": (
                                    f"Value mismatch for '{fact_key}': "
                                    f"expected '{gt_str}', found '{extracted}' "
                                    f"at {surface_id}:{line}"
                                ),
                            })

        # --- WRONG VALUES CHECK (both modes) ---
        # Resolve wrong_values templates (e.g., load from sibling data)
        resolved_wrong: list[str] = []
        for wv in wrong_values:
            if isinstance(wv, str):
                resolved_wrong.append(wv)
            elif isinstance(wv, dict):
                # Load from another ground truth source
                wv_alias = wv.get("source_alias", "")
                wv_field = wv.get("field", "")
                wv_data = gt.get(wv_alias, {})
                if "_error" not in wv_data:
                    wv_val = wv_data
                    for part in wv_field.split("."):
                        if isinstance(wv_val, dict):
                            wv_val = wv_val.get(part)
                        else:
                            wv_val = None
                            break
                    if wv_val and isinstance(wv_val, str):
                        resolved_wrong.append(wv_val)

        for wrong_val in resolved_wrong:
            if len(wrong_val) < 4:
                continue
            for surface_id in check_surfaces:
                surface = surfaces.get(surface_id)
                if not surface or not surface.get("content"):
                    continue
                content_lower = surface["content"].lower()
                wrong_lower = wrong_val.lower()
                if wrong_lower in content_lower:
                    idx = content_lower.index(wrong_lower)
                    line = surface["content"][:idx].count("\n") + 1
                    findings.append({
                        "check_id": config["id"],
                        "severity": fact_severity,
                        "fact_key": fact_key,
                        "surface": surface_id,
                        "line": line,
                        "expected": f"NOT '{wrong_val}'",
                        "found": wrong_val,
                        "message": (
                            f"Wrong value for '{fact_key}': "
                            f"'{wrong_val}' found at {surface_id}:{line} "
                            f"(expected '{gt_str}')"
                        ),
                    })

    return findings


def _check_schema_coverage(
    surfaces: dict[str, dict[str, Any]],
    gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    _vars: dict[str, str],
) -> list[dict[str, Any]]:
    """Check meta/og/JSON-LD field coverage + validity (G3 schema check)."""
    findings: list[dict[str, Any]] = []
    severity = config.get("severity", "blocking")
    target_surfaces = config.get("surfaces", list(surfaces.keys()))

    required_meta = config.get("required_meta", [])
    required_og = config.get("required_og", [])
    required_schema_types = set(config.get("required_schema_types", []))
    required_jsonld_fields = config.get("required_jsonld_fields", {})

    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("content"):
            continue
        if surface.get("type") != "html":
            continue

        html = surface["content"]
        head_data = extract_meta_og(html)

        # Check title (skip for body-only fragments like WP-WRAPPED HTML)
        if not config.get("skip_title_check") and not head_data["title"]:
            findings.append({
                "check_id": config["id"],
                "severity": severity,
                "surface": sid,
                "message": "Missing <title> tag",
            })

        # Check required meta tags
        for meta_name in required_meta:
            if meta_name.lower() not in head_data["meta"]:
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "message": f"Missing meta tag: {meta_name}",
                    "expected": meta_name,
                    "found": "(absent)",
                })
            elif not head_data["meta"][meta_name.lower()].strip():
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "message": f"Empty meta tag: {meta_name}",
                    "expected": f"non-empty {meta_name}",
                    "found": "(empty)",
                })

        # Check required og tags
        for og_prop in required_og:
            if og_prop.lower() not in head_data["og"]:
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "message": f"Missing OG property: {og_prop}",
                    "expected": og_prop,
                    "found": "(absent)",
                })
            elif not head_data["og"][og_prop.lower()].strip():
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "message": f"Empty OG property: {og_prop}",
                    "expected": f"non-empty {og_prop}",
                    "found": "(empty)",
                })

        # Check JSON-LD
        jsonld_blocks = extract_jsonld(html)
        if not jsonld_blocks:
            findings.append({
                "check_id": config["id"],
                "severity": severity,
                "surface": sid,
                "message": "No JSON-LD blocks found",
            })
        else:
            # Check for parse errors
            for i, block in enumerate(jsonld_blocks):
                if "_parse_error" in block:
                    findings.append({
                        "check_id": config["id"],
                        "severity": severity,
                        "surface": sid,
                        "message": f"JSON-LD block {i} parse error: {block['_parse_error']}",
                    })

            # Check required types
            found_types = extract_jsonld_types(jsonld_blocks)
            missing_types = required_schema_types - found_types
            if missing_types:
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "message": f"Missing required JSON-LD @type: {sorted(missing_types)} (found: {sorted(found_types)})",
                    "expected": sorted(required_schema_types),
                    "found": sorted(found_types),
                })

            # Check required fields within JSON-LD types
            for type_name, required_fields in required_jsonld_fields.items():
                # Find the block with this type
                for block in jsonld_blocks:
                    if "_parse_error" in block:
                        continue
                    items = []
                    if isinstance(block, dict):
                        if "@graph" in block:
                            items = block["@graph"]
                        else:
                            items = [block]
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get("@type", "")
                        if isinstance(item_type, list):
                            type_match = type_name in item_type
                        else:
                            type_match = item_type == type_name
                        if type_match:
                            for field in required_fields:
                                if field not in item or not item[field]:
                                    findings.append({
                                        "check_id": config["id"],
                                        "severity": "advisory",
                                        "surface": sid,
                                        "message": f"JSON-LD {type_name} missing field: {field}",
                                        "expected": field,
                                        "found": "(absent)",
                                    })

    return findings


def _check_image_integrity(
    surfaces: dict[str, dict[str, Any]],
    _gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    _vars: dict[str, str],
) -> list[dict[str, Any]]:
    """Check image src for placeholder tokens and HTTP resolution."""
    findings: list[dict[str, Any]] = []
    severity = config.get("severity", "blocking")
    target_surfaces = config.get("surfaces", list(surfaces.keys()))
    placeholder_tokens = config.get("placeholder_tokens", [
        "PLACEHOLDER", "placeholder", "FILL", "TBD", "MISSING",
    ])
    check_resolution = config.get("check_resolution", True)
    timeout_seconds = config.get("timeout_seconds", 10)

    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("content"):
            continue

        imgs = extract_img_srcs(surface["content"])
        for img in imgs:
            src = img["src"]

            # Check for placeholder tokens in src
            for token in placeholder_tokens:
                if token.lower() in src.lower():
                    findings.append({
                        "check_id": config["id"],
                        "severity": severity,
                        "surface": sid,
                        "file": surface["path"],
                        "line": img["line"],
                        "src": src,
                        "message": f"Placeholder token '{token}' in image src: {src} (line {img['line']})",
                    })
                    break  # One finding per image

            # Check HTTP resolution (only for absolute URLs)
            if check_resolution and src.startswith("http"):
                try:
                    req = urllib.request.Request(src, method="HEAD")
                    req.add_header("User-Agent", "verify-artifact/1.0")
                    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                        if resp.status >= 400:
                            findings.append({
                                "check_id": config["id"],
                                "severity": severity,
                                "surface": sid,
                                "file": surface["path"],
                                "line": img["line"],
                                "src": src,
                                "http_status": resp.status,
                                "message": f"Image returns HTTP {resp.status}: {src} (line {img['line']})",
                            })
                except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
                    status = getattr(e, "code", None) or "error"
                    findings.append({
                        "check_id": config["id"],
                        "severity": severity,
                        "surface": sid,
                        "file": surface["path"],
                        "line": img["line"],
                        "src": src,
                        "http_status": status,
                        "message": f"Image unreachable ({status}): {src} (line {img['line']})",
                    })

    return findings


def _check_link_resolution(
    surfaces: dict[str, dict[str, Any]],
    _gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    variables: dict[str, str],
) -> list[dict[str, Any]]:
    """Check internal links resolve (not 404)."""
    findings: list[dict[str, Any]] = []
    severity = config.get("severity", "blocking")
    target_surfaces = config.get("surfaces", list(surfaces.keys()))
    site_domain = config.get("site_domain", variables.get("site_domain", ""))
    check_http = config.get("check_http", True)
    timeout_seconds = config.get("timeout_seconds", 10)

    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("content"):
            continue

        links = extract_internal_links(surface["content"], site_domain)
        checked_hrefs: set[str] = set()

        for link in links:
            href = link["href"]
            # Normalize and deduplicate
            if href in checked_hrefs:
                continue
            checked_hrefs.add(href)

            # Skip anchors and mailto
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue

            # Build full URL for checking
            if href.startswith("/") and site_domain:
                scheme = "https"
                full_url = f"{scheme}://{site_domain}{href}"
            elif href.startswith("http"):
                full_url = href
            else:
                continue  # Relative path without domain — can't resolve

            if not check_http:
                continue

            try:
                req = urllib.request.Request(full_url, method="HEAD")
                req.add_header("User-Agent", "verify-artifact/1.0")
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    if resp.status >= 400:
                        findings.append({
                            "check_id": config["id"],
                            "severity": severity,
                            "surface": sid,
                            "file": surface["path"],
                            "line": link["line"],
                            "href": href,
                            "link_text": link["text"],
                            "http_status": resp.status,
                            "message": f"Internal link returns HTTP {resp.status}: {href} ('{link['text']}' at line {link['line']})",
                        })
            except urllib.error.HTTPError as e:
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "file": surface["path"],
                    "line": link["line"],
                    "href": href,
                    "link_text": link["text"],
                    "http_status": e.code,
                    "message": f"Internal link returns HTTP {e.code}: {href} ('{link['text']}' at line {link['line']})",
                })
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                findings.append({
                    "check_id": config["id"],
                    "severity": severity,
                    "surface": sid,
                    "file": surface["path"],
                    "line": link["line"],
                    "href": href,
                    "link_text": link["text"],
                    "http_status": "error",
                    "message": f"Internal link unreachable: {href} ('{link['text']}' at line {link['line']}): {e}",
                })

    return findings


def _check_hardcode_scan(
    surfaces: dict[str, dict[str, Any]],
    _gt: dict[str, dict[str, Any]],
    config: dict[str, Any],
    variables: dict[str, str],
) -> list[dict[str, Any]]:
    """Delegate to hardcode-scanner.py (SC-1) for template-default detection."""
    findings: list[dict[str, Any]] = []

    scanner_profile_path = config.get("scanner_profile")
    if not scanner_profile_path:
        return findings

    # Resolve relative to this script's directory
    script_dir = Path(__file__).resolve().parent
    profile_path = script_dir / scanner_profile_path

    # Try to import the scanner
    try:
        sys.path.insert(0, str(script_dir))
        from importlib import import_module
        # Use importlib to handle the hyphenated filename
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "hardcode_scanner", script_dir / "hardcode-scanner.py"
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            scan_from_code = mod.scan_from_code
        else:
            return findings
    except Exception as e:
        findings.append({
            "check_id": config["id"],
            "severity": "warning",
            "message": f"Could not load hardcode-scanner: {e}",
        })
        return findings

    # Run the scanner on each surface's parent directory
    scanned_dirs: set[str] = set()
    target_surfaces = config.get("surfaces", list(surfaces.keys()))
    for sid in target_surfaces:
        surface = surfaces.get(sid)
        if not surface or not surface.get("found"):
            continue
        surface_dir = str(Path(surface["path"]).parent)
        if surface_dir in scanned_dirs:
            continue
        scanned_dirs.add(surface_dir)

        try:
            result = scan_from_code(profile_path, Path(surface_dir), variables.get("subject_id"))
        except SystemExit:
            continue  # Scanner calls sys.exit on config error

        for f in result.get("findings", []):
            findings.append({
                "check_id": config["id"],
                "severity": f.get("severity", "blocking"),
                "surface": sid,
                "file": f.get("file", ""),
                "line": f.get("line", 0),
                "field": f.get("field", ""),
                "expected": "(not a template default)",
                "found": f.get("found_value", ""),
                "message": (
                    f"Template default detected — field '{f.get('field', '')}': "
                    f"'{f.get('found_value', '')}' at {f.get('file', '')}:{f.get('line', 0)}. "
                    f"{f.get('rationale', '')}"
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# Check dispatcher
# ---------------------------------------------------------------------------

_CHECK_REGISTRY: dict[str, Any] = {
    "pattern-sweep": _check_pattern_sweep,
    "identity-leak": _check_identity_leak,
    "value-cross-check": _check_value_cross_check,
    "schema-coverage": _check_schema_coverage,
    "image-integrity": _check_image_integrity,
    "link-resolution": _check_link_resolution,
    "hardcode-scan": _check_hardcode_scan,
}


# ---------------------------------------------------------------------------
# Engine: run all checks, aggregate verdict
# ---------------------------------------------------------------------------

def run_verification(
    profile: dict[str, Any],
    artifact_dir: Path,
    variables: dict[str, str],
    workspace_root: Path | None = None,
    skip_http: bool = False,
) -> dict[str, Any]:
    """Run the full verification sweep. Returns structured results."""
    # Load surfaces
    surfaces = load_surfaces(artifact_dir, profile["surfaces"], variables)

    # Check for missing required surfaces
    surface_errors = []
    for sid, s in surfaces.items():
        if not s.get("found") and not s.get("content"):
            surface_errors.append({
                "surface": sid,
                "error": s.get("error", "Surface file not found"),
                "path": s.get("path", ""),
            })

    # Load ground truth
    gt = load_ground_truth(profile.get("ground_truth"), variables, workspace_root)

    # Run each check
    all_findings: list[dict[str, Any]] = []
    check_results: list[dict[str, Any]] = []

    for check_config in profile["checks"]:
        check_id = check_config["id"]
        check_type = check_config["type"]
        enabled = check_config.get("enabled", True)

        if not enabled:
            check_results.append({
                "check_id": check_id,
                "check_type": check_type,
                "verdict": "SKIPPED",
                "findings_count": 0,
                "blocking_count": 0,
            })
            continue

        # Evaluate condition against ground truth — skip check if condition
        # is not met (e.g., Menu check skipped when menu_url is absent).
        # condition format: {"ground_truth_alias": "client", "field": "restaurant.menu_url"}
        # The check runs only when the field is truthy in the ground truth data.
        condition = check_config.get("condition")
        if condition and isinstance(condition, dict):
            gt_alias = condition.get("ground_truth_alias", "")
            gt_field = condition.get("field", "")
            gt_data = gt.get(gt_alias, {})
            if "_error" not in gt_data and gt_field:
                cond_value = gt_data
                for part in gt_field.split("."):
                    if isinstance(cond_value, dict):
                        cond_value = cond_value.get(part)
                    else:
                        cond_value = None
                        break
                if not cond_value:
                    check_results.append({
                        "check_id": check_id,
                        "check_type": check_type,
                        "verdict": "SKIPPED",
                        "findings_count": 0,
                        "blocking_count": 0,
                        "skip_reason": f"condition not met: {gt_alias}.{gt_field} is falsy",
                    })
                    continue

        # Override HTTP checks if skip_http
        effective_config = dict(check_config)
        if skip_http:
            if check_type in ("image-integrity", "link-resolution"):
                effective_config["check_resolution"] = False
                effective_config["check_http"] = False

        handler = _CHECK_REGISTRY.get(check_type)
        if not handler:
            check_results.append({
                "check_id": check_id,
                "check_type": check_type,
                "verdict": "ERROR",
                "error": f"Unknown check type: {check_type}",
                "findings_count": 0,
                "blocking_count": 0,
            })
            continue

        try:
            check_findings = handler(surfaces, gt, effective_config, variables)
        except Exception as e:
            check_findings = [{
                "check_id": check_id,
                "severity": "warning",
                "message": f"Check failed with error: {e}",
            }]

        blocking = [f for f in check_findings if f.get("severity") == "blocking"]

        check_results.append({
            "check_id": check_id,
            "check_type": check_type,
            "verdict": "FAIL" if blocking else ("WARN" if check_findings else "PASS"),
            "findings_count": len(check_findings),
            "blocking_count": len(blocking),
            "findings": check_findings,
        })

        all_findings.extend(check_findings)

    # Aggregate
    all_blocking = [f for f in all_findings if f.get("severity") == "blocking"]
    all_advisory = [f for f in all_findings if f.get("severity") != "blocking"]

    verdict = "FAIL" if all_blocking else ("WARN" if all_advisory else "PASS")

    return {
        "profile_id": profile["profile_id"],
        "artifact_dir": str(artifact_dir),
        "variables": variables,
        "surfaces_loaded": {
            sid: {"path": s["path"], "found": s.get("found", False)}
            for sid, s in surfaces.items()
        },
        "surface_errors": surface_errors,
        "ground_truth_loaded": {
            alias: {"path": data.get("_path", ""), "ok": "_error" not in data}
            for alias, data in gt.items()
        },
        "checks_run": len(check_results),
        "check_results": check_results,
        "total_findings": len(all_findings),
        "blocking_findings": len(all_blocking),
        "advisory_findings": len(all_advisory),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------

def print_report(results: dict[str, Any]) -> None:
    """Print a human-readable verification report."""
    v = results["verdict"]
    verdict_emoji = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}.get(v, v)

    print(f"\n{'='*70}")
    print(f"VERIFY-ARTIFACT — {results['profile_id']}")
    print(f"{'='*70}")
    print(f"Artifact dir: {results['artifact_dir']}")
    print(f"Variables:    {results['variables']}")
    print(f"Checks run:   {results['checks_run']}")
    print(f"Total findings: {results['total_findings']} "
          f"({results['blocking_findings']} blocking, {results['advisory_findings']} advisory)")
    print(f"Verdict:       {verdict_emoji}")
    print(f"{'='*70}")

    # Surface load status
    print("\nSurfaces:")
    for sid, info in results["surfaces_loaded"].items():
        status = "OK" if info["found"] else "MISSING"
        print(f"  [{status}] {sid}: {info['path']}")

    if results["surface_errors"]:
        for se in results["surface_errors"]:
            print(f"  [ERROR] {se['surface']}: {se['error']}")

    # Ground truth load status
    if results["ground_truth_loaded"]:
        print("\nGround truth:")
        for alias, info in results["ground_truth_loaded"].items():
            status = "OK" if info["ok"] else "ERROR"
            print(f"  [{status}] {alias}: {info['path']}")

    # Per-check results
    print(f"\n{'─'*70}")
    for cr in results["check_results"]:
        v = cr["verdict"]
        marker = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "SKIPPED": "SKIP", "ERROR": "ERR"}.get(v, v)
        label = f"[{marker}] {cr['check_id']} ({cr['check_type']})"
        count = f"{cr['findings_count']} findings ({cr['blocking_count']} blocking)"
        print(f"\n  {label}")
        print(f"    {count}")

        for f in cr.get("findings", []):
            sev = "BLOCK" if f.get("severity") == "blocking" else "ADVSR"
            msg = f.get("message", "")
            print(f"      [{sev}] {msg}")
            if f.get("expected") and f.get("found"):
                print(f"              expected: {f['expected']}")
                print(f"              found:    {f['found']}")

    print(f"\n{'='*70}")
    if results["verdict"] == "PASS":
        print("All checks passed. Artifact is clean.")
    elif results["verdict"] == "FAIL":
        print(f"{results['blocking_findings']} blocking finding(s). "
              f"Fix before publish.")
    else:
        print(f"{results['advisory_findings']} advisory finding(s). "
              f"Review before publish.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_vars(var_strings: list[str]) -> dict[str, str]:
    """Parse key=value variable arguments."""
    variables: dict[str, str] = {}
    for v in var_strings:
        if "=" not in v:
            sys.stderr.write(f"ERROR: --vars entry must be key=value, got: {v!r}\n")
            sys.exit(2)
        key, value = v.split("=", 1)
        variables[key.strip()] = value.strip()
    return variables


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Consolidated pre-publish verification engine. "
            "One command that runs the full on-disk sweep across all configured surfaces "
            "and returns a per-check pass/fail verdict."
        )
    )
    parser.add_argument(
        "--profile",
        required=True,
        type=Path,
        help="Path to the verification profile JSON",
    )
    parser.add_argument(
        "--artifact-dir",
        required=True,
        type=Path,
        help="Directory containing the artifact to verify",
    )
    parser.add_argument(
        "--vars",
        nargs="+",
        default=[],
        help="Subject variables as key=value pairs",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for resolving relative ground-truth paths (defaults to cwd)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--skip-http",
        action="store_true",
        help="Skip HTTP resolution checks (image + link), useful for offline/fast runs",
    )
    args = parser.parse_args()

    profile = load_profile(args.profile)
    variables = parse_vars(args.vars)
    workspace_root = args.workspace_root or Path.cwd()

    results = run_verification(
        profile, args.artifact_dir, variables, workspace_root, args.skip_http
    )

    if args.json:
        # Produce machine-readable output (gate-peer-reviewer return contract compatible)
        output = {
            "verdict": results["verdict"],
            "checks_run": [
                {
                    "name": cr["check_id"],
                    "type": cr["check_type"],
                    "verdict": cr["verdict"],
                    "blocking_count": cr["blocking_count"],
                    "findings": cr.get("findings", []),
                }
                for cr in results["check_results"]
            ],
            "catches": [
                {
                    "check_id": f.get("check_id", ""),
                    "severity": f.get("severity", ""),
                    "message": f.get("message", ""),
                    "expected": f.get("expected", ""),
                    "found": f.get("found", ""),
                }
                for f in (
                    finding
                    for cr in results["check_results"]
                    for finding in cr.get("findings", [])
                    if finding.get("severity") == "blocking"
                )
            ],
            "profile_id": results["profile_id"],
            "artifact_dir": results["artifact_dir"],
            "total_findings": results["total_findings"],
            "blocking_findings": results["blocking_findings"],
            "advisory_findings": results["advisory_findings"],
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(results)

    if results["verdict"] == "FAIL":
        sys.exit(1)


# -- Importable API for composition -----------------------------------------

def verify_from_code(
    profile_path: Path | str,
    artifact_dir: Path | str,
    variables: dict[str, str],
    workspace_root: Path | str | None = None,
    skip_http: bool = False,
) -> dict[str, Any]:
    """Run the verification engine programmatically. Returns structured results.

    Use this when composing verify-artifact into another script
    (e.g., publish-core-30-page.py --preflight-verify).
    """
    profile = load_profile(Path(profile_path))
    ws = Path(workspace_root) if workspace_root else Path.cwd()
    return run_verification(profile, Path(artifact_dir), variables, ws, skip_http)


if __name__ == "__main__":
    main()
