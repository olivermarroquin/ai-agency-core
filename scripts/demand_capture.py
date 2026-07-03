#!/usr/bin/env python3
"""
demand_capture.py — Automated demand + AI-answer-engine data capture (LU-R9).

Composes the §4 runbook from workflow-demand-ai-data-capture into a single
command that emits a per-slug demand dossier the content brief consumes.

Usage:
    python3 demand_capture.py <service> <city> \\
        --client <slug> --domain <domain> \\
        --gsc-window <YYYY-MM-DD:YYYY-MM-DD> \\
        [--state <abbrev>] [--location-code <code>] [--config <path>]

Examples:
    # EV — reproduce Wave-1 slug
    python3 demand_capture.py electrical-troubleshooting fairfax \\
        --client ev-electric-services --domain evelectric.pro \\
        --gsc-window 2026-05-23:2026-06-20

    # S&H — 2nd instance from args alone
    python3 demand_capture.py panel-upgrade woodbridge \\
        --client s-and-h-contracting --domain shcontractingunlimited.com \\
        --gsc-window 2026-06-01:2026-06-28

ENGINE + ARGS. Zero hardcoded client values (CR-155/156 discipline).
Every path derives from --client; every query is built from service/city args.
Config provides client-specific templates (occupation, head terms, etc.).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

# Add scripts dir to path for sibling imports
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dfs_demand import (
    probe_dataforseo_reachability,
    pull_s1_s2_s8_serp,
    pull_s3_difficulty,
    pull_s3_volume,
    pull_s4_keyword_suggestions,
)
from demand_capture_emitter import build_dossier, gate_check, write_dossier

# Tier-3 scripts (Sonar)
_TIER3_CANDIDATES = [
    Path.home() / "workspace" / "second-brain-tier3" / "automation" / "scripts",
    Path.home() / "mnt" / "workspace" / "second-brain-tier3" / "automation" / "scripts",
]
TIER3_SCRIPTS = next(
    (p for p in _TIER3_CANDIDATES if p.exists()), _TIER3_CANDIDATES[0]
)

VAULT_CLIENTS = (
    Path.home() / "workspace" / "second-brain"
    / "04_projects" / "clients" / "_active"
)


# ---------------------------------------------------------------------------
# Safe format_map: missing keys stay as {key} instead of raising KeyError
# ---------------------------------------------------------------------------

class _SafeMap(dict):
    """Dict subclass that returns '{key}' for missing keys in str.format_map."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_demand_config(config_path: Path | None, client_slug: str) -> dict:
    """Load the demand-capture config for a client.

    Search order:
      1. Explicit --config path
      2. configs/<client_slug>.demand.json in the scripts directory
      3. Empty dict (all defaults)
    """
    if config_path and config_path.exists():
        return json.loads(config_path.read_text())
    convention = SCRIPTS_DIR / "configs" / f"{client_slug}.demand.json"
    if convention.exists():
        return json.loads(convention.read_text())
    return {}


# ---------------------------------------------------------------------------
# Query builders — all parameterized from args + config, zero hardcodes
# ---------------------------------------------------------------------------

def build_slug(service: str, city: str, state: str) -> str:
    """Build the page slug: <service>-<city>-<state>."""
    return f"{service}-{city}-{state}"


def build_service_display(service: str, config: dict) -> str:
    """Convert service slug to display name. Config overrides allowed."""
    display_map = config.get("service_display_names", {})
    if service in display_map:
        return display_map[service]
    return service.replace("-", " ")


def _subs(service: str, city: str, state: str, config: dict) -> _SafeMap:
    """Build the substitution map for template expansion."""
    return _SafeMap(
        service=build_service_display(service, config),
        city=city,
        state=state,
        occupation=config.get("occupation", ""),
    )


def build_volume_keywords(
    service: str, city: str, state: str, config: dict,
) -> list[str]:
    """Build the volume keyword list from config templates + head terms."""
    sm = _subs(service, city, state, config)
    keywords: list[str] = []

    for term in config.get("volume_head_terms", []):
        kw = term.format_map(sm).strip()
        if kw and kw not in keywords:
            keywords.append(kw)

    default_templates = ["{service} {city} {state}", "{occupation} {city} {state}"]
    for tmpl in config.get("volume_templates", default_templates):
        kw = tmpl.format_map(sm).strip()
        if kw and kw not in keywords:
            keywords.append(kw)

    return keywords


def _expand_templates(
    templates: list[str],
    service: str, city: str, state: str, config: dict,
) -> list[str]:
    """Expand a list of query templates with substitutions."""
    sm = _subs(service, city, state, config)
    return [t.format_map(sm).strip() for t in templates if t.format_map(sm).strip()]


def build_sonar_queries(
    service: str, city: str, state: str, config: dict,
) -> list[str]:
    """Build Sonar query list from config templates."""
    defaults = ["{service} {city} {state}", "{occupation} near me {city} {state}"]
    return _expand_templates(
        config.get("sonar_query_templates", defaults),
        service, city, state, config,
    )


def build_chatgpt_gemini_queries(
    service: str, city: str, state: str, config: dict,
) -> list[str]:
    """Build ChatGPT/Gemini query list from config templates."""
    defaults = ["{service} {city} {state}", "{occupation} near me {city} {state}"]
    return _expand_templates(
        config.get("chatgpt_gemini_query_templates", defaults),
        service, city, state, config,
    )


# ---------------------------------------------------------------------------
# Reachability probes (CR-142: probe before assuming blocked)
# ---------------------------------------------------------------------------

def probe_sonar_reachability() -> tuple[bool, str]:
    """Check if Perplexity Sonar key file exists and looks valid."""
    secrets_dir = TIER3_SCRIPTS.parent / "secrets"
    key_path = secrets_dir / "perplexity-sonar.key"
    if not key_path.exists():
        return False, f"Key not found at {key_path}"
    key = key_path.read_text().strip()
    if key.startswith("pplx-"):
        return True, f"Key found at {key_path}"
    return False, f"Key at {key_path} does not start with 'pplx-'"


def probe_gsc_reachability(client_slug: str) -> tuple[bool, str]:
    """Check if GSC config exists for this client."""
    # Direct match
    config_path = SCRIPTS_DIR / f"{client_slug}.config.json"
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        return True, (
            f"Config: {config_path.name}, "
            f"property: {cfg.get('gsc_property', '?')}"
        )
    # Scan for matching client_slug
    for p in SCRIPTS_DIR.glob("*.config.json"):
        try:
            with open(p) as f:
                cfg = json.load(f)
            if cfg.get("client_slug") == client_slug:
                return True, (
                    f"Config: {p.name}, "
                    f"property: {cfg.get('gsc_property', '?')}"
                )
        except (json.JSONDecodeError, OSError):
            continue
    return False, f"No *.config.json with client_slug='{client_slug}' found"


# ---------------------------------------------------------------------------
# S5 Sonar pull
# ---------------------------------------------------------------------------

def pull_s5_sonar(
    queries: list[str], out_dir: Path,
) -> tuple[bool, Path | None, str]:
    """Run S5 Sonar via perplexity_sonar.py --batch (≤4/batch for 45s cap)."""
    sonar_script = TIER3_SCRIPTS / "perplexity_sonar.py"
    if not sonar_script.exists():
        return False, None, f"Script not found: {sonar_script}"

    queries_file = out_dir / "_sonar-queries.txt"
    queries_file.write_text("\n".join(queries) + "\n")
    out_path = out_dir / "sonar-report.md"

    try:
        result = subprocess.run(
            [
                sys.executable, str(sonar_script),
                "--batch", str(queries_file),
                "--out", str(out_path),
                "--limit", "4",
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(TIER3_SCRIPTS),
        )
        if result.returncode != 0:
            return False, None, f"Sonar failed: {result.stderr[:300]}"
        return True, out_path, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, out_path, "Sonar timed out (120s)"
    except Exception as e:
        return False, None, str(e)


# ---------------------------------------------------------------------------
# S7 GSC first-party
# ---------------------------------------------------------------------------

def read_latest_gsc_report(client_slug: str) -> tuple[dict | None, Path | None]:
    """Read the latest gsc-performance-*.json for this client."""
    reports_dir = VAULT_CLIENTS / client_slug / "reports"
    if not reports_dir.exists():
        return None, None
    gsc_files = sorted(reports_dir.glob("gsc-performance-*.json"), reverse=True)
    if not gsc_files:
        return None, None
    latest = gsc_files[0]
    try:
        return json.loads(latest.read_text()), latest
    except (json.JSONDecodeError, OSError):
        return None, latest


def pull_s7_gsc_fresh(
    client_slug: str, gsc_window: str, slug: str, out_dir: Path,
) -> tuple[bool, Path | None, str]:
    """Pull fresh per-slug GSC data via gsc_search_analytics.py."""
    try:
        start_date, end_date = gsc_window.split(":")
    except ValueError:
        return False, None, (
            f"Invalid --gsc-window: '{gsc_window}' "
            f"(expected YYYY-MM-DD:YYYY-MM-DD)"
        )

    try:
        from gsc_search_analytics import (
            _get_gsc_property,
            _load_client_config,
            query_search_analytics,
        )

        config = _load_client_config(client_slug)
        gsc_property = _get_gsc_property(config, client_slug)
    except Exception as e:
        return False, None, f"GSC config error: {e}"

    try:
        rows = query_search_analytics(
            property_url=gsc_property,
            start_date=start_date,
            end_date=end_date,
            dimensions=["query", "page"],
            row_limit=5000,
            config=config,
        )
    except Exception as e:
        return False, None, f"GSC query failed: {e}"

    # Filter rows matching this slug in the page URL
    slug_rows = [
        r for r in rows
        if any(f"/{slug}" in k for k in r.get("keys", []))
    ]

    out_path = out_dir / f"s7-gsc-{slug}.json"
    data = {
        "metadata": {
            "client_slug": client_slug,
            "gsc_property": gsc_property,
            "window": {"start": start_date, "end": end_date},
            "slug_filter": slug,
            "pulled_at": date.today().isoformat(),
            "total_rows": len(rows),
            "slug_rows": len(slug_rows),
        },
        "rows": slug_rows,
        "all_rows": rows,
    }
    out_path.write_text(json.dumps(data, indent=2))
    return True, out_path, (
        f"{len(slug_rows)} slug-filtered rows from {len(rows)} total"
    )


# ---------------------------------------------------------------------------
# S5 ChatGPT/Gemini — gated manual step
# ---------------------------------------------------------------------------

def emit_s5_chatgpt_gemini_gate(
    queries: list[str], slug: str, out_dir: Path,
) -> Path:
    """Emit the gated manual step artifact for ChatGPT/Gemini capture.

    Creates the query list + an empty artifact the dossier gate checks.
    This source CANNOT run headless from Python — it requires the
    personal Chrome automation profile (SOP-S5).
    """
    artifact_path = out_dir / f"s5-chatgpt-gemini-{slug}.md"

    if not artifact_path.exists():
        lines = [
            f"# S5 ChatGPT + Gemini capture — {slug}",
            "",
            "**Status:** NOT FILLED (gated manual step)",
            "",
            "## Queries to run",
            "",
        ]
        for i, q in enumerate(queries, 1):
            lines.append(f"{i}. {q}")
        lines.extend([
            "",
            "## Instructions",
            "",
            "Run each query on the **personal Chrome automation profile**",
            "(per SOP-S5 in workflow-demand-ai-data-capture.md):",
            "",
            "1. `switch_browser` → pick personal Chrome",
            "2. Navigate to `https://chatgpt.com/` (new chat per query)",
            "3. Find the composer (contenteditable div) → `left_click` →"
            " `type` query → `key Return`",
            "4. Copy answer + cited domains into the Results section below",
            "5. Repeat for `https://gemini.google.com/app`",
            "6. Dismiss any 'Personal Intelligence' overlay with **Not now**",
            "",
            "## Results",
            "",
            "<!-- Fill in results below. The dossier gate checks this"
            " section is non-empty. -->",
            "",
        ])
        artifact_path.write_text("\n".join(lines))

    return artifact_path


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Automated demand + AI-answer-engine data capture (LU-R9). "
            "Composes the §4 runbook into a single command per slug."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "service",
        help="Service slug (e.g., panel-upgrade, electrical-troubleshooting)",
    )
    ap.add_argument(
        "city",
        help="City name (e.g., fairfax, vienna, woodbridge)",
    )
    ap.add_argument("--client", required=True, help="Client slug")
    ap.add_argument("--domain", required=True, help="Client domain")
    ap.add_argument(
        "--gsc-window", required=True,
        help="GSC date window as YYYY-MM-DD:YYYY-MM-DD",
    )
    ap.add_argument("--state", default="va", help="State abbreviation (default: va)")
    ap.add_argument(
        "--location-code", type=int, default=2840,
        help="DataForSEO location code (default: 2840 = US)",
    )
    ap.add_argument(
        "--config", type=Path, default=None,
        help="Path to demand-capture config JSON (default: configs/<client>.demand.json)",
    )
    ap.add_argument(
        "--skip-sonar", action="store_true",
        help="Skip S5 Sonar pull",
    )
    ap.add_argument(
        "--skip-gsc-fresh", action="store_true",
        help="Skip S7 fresh per-slug GSC pull",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without calling APIs",
    )
    args = ap.parse_args()

    slug = build_slug(args.service, args.city, args.state)
    config = load_demand_config(args.config, args.client)

    # Merge CLI args into config as defaults
    config.setdefault("state_abbrev", args.state)
    config.setdefault("location_code", args.location_code)
    config.setdefault("client_slug", args.client)
    config.setdefault("domain", args.domain)

    service_display = build_service_display(args.service, config)

    # Output directory
    out_dir = VAULT_CLIENTS / args.client / "content-briefs" / "demand-captures"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== demand-capture: {service_display} × {args.city} ({args.state}) ===")
    print(f"Client: {args.client} | Domain: {args.domain}")
    print(f"Slug: {slug}")
    print(f"Output: {out_dir}")
    print()

    source_status: dict[str, dict[str, Any]] = {}
    cost_total = 0.0
    artifacts: dict[str, Path] = {}

    # ─── PROBE REACHABILITY (CR-142 discipline) ───────────────────────────
    print("--- Probing reachability ---")

    dfs_ok, dfs_msg = probe_dataforseo_reachability()
    print(f"  DataForSEO:       {'OK' if dfs_ok else 'BLOCKED'} — {dfs_msg}")

    sonar_ok, sonar_msg = probe_sonar_reachability()
    print(f"  Sonar:            {'OK' if sonar_ok else 'BLOCKED'} — {sonar_msg}")

    gsc_ok, gsc_msg = probe_gsc_reachability(args.client)
    print(f"  GSC:              {'OK' if gsc_ok else 'BLOCKED'} — {gsc_msg}")

    print(
        "  ChatGPT/Gemini:   GATED "
        "(manual step — requires personal Chrome profile)"
    )
    print()

    # ─── DRY RUN ──────────────────────────────────────────────────────────
    if args.dry_run:
        print("--- DRY RUN — showing planned actions ---")
        vol_kws = build_volume_keywords(args.service, args.city, args.state, config)
        print(f"  S3 Volume keywords ({len(vol_kws)}):")
        for kw in vol_kws:
            print(f"    - {kw}")

        serp_q = f"{service_display} {args.city} {args.state}"
        print(f"  S1/S2/S8 SERP query: \"{serp_q}\"")

        city_seed = f"{service_display} {args.city}"
        print(f"  S4 City seed: \"{city_seed}\"")
        print(f"  S4 Broad seed: \"{service_display}\"")

        sonar_qs = build_sonar_queries(args.service, args.city, args.state, config)
        print(f"  S5 Sonar queries ({len(sonar_qs)}):")
        for q in sonar_qs:
            print(f"    - {q}")

        cg_qs = build_chatgpt_gemini_queries(
            args.service, args.city, args.state, config,
        )
        print(f"  S5 ChatGPT/Gemini queries ({len(cg_qs)}):")
        for q in cg_qs:
            print(f"    - {q}")

        print(f"  S7 GSC window: {args.gsc_window}")
        print(f"\n  Output dir: {out_dir}")
        return

    # ─── S3: VOLUME + DIFFICULTY ──────────────────────────────────────────
    print("--- S3: Volume + Difficulty ---")
    if dfs_ok:
        vol_kws = build_volume_keywords(
            args.service, args.city, args.state, config,
        )
        vol_ok, vol_path, vol_msg, vol_cost = pull_s3_volume(
            vol_kws, args.location_code, out_dir / "dfs-volume.json",
        )
        print(f"  Volume: {'OK' if vol_ok else 'FAIL'} — {vol_msg}")
        cost_total += vol_cost
        if vol_path:
            artifacts["dfs-volume.json"] = vol_path

        diff_kws = vol_kws[:10]  # top 10 for difficulty (cost-efficient)
        diff_ok, diff_path, diff_msg, diff_cost = pull_s3_difficulty(
            diff_kws, args.location_code, out_dir / "dfs-difficulty.json",
        )
        print(f"  Difficulty: {'OK' if diff_ok else 'FAIL'} — {diff_msg}")
        cost_total += diff_cost
        if diff_path:
            artifacts["dfs-difficulty.json"] = diff_path

        source_status["S3"] = {
            "pulled": vol_ok and diff_ok,
            "artifacts": ["dfs-volume.json", "dfs-difficulty.json"],
            "cost": round(vol_cost + diff_cost, 6),
        }
    else:
        source_status["S3"] = {
            "pulled": False,
            "reason": dfs_msg,
            "sop": "Fund DataForSEO at app.dataforseo.com/add-funds",
        }

    # ─── S1/S2/S8: SERP ADVANCED ─────────────────────────────────────────
    print("--- S1/S2/S8: SERP Advanced ---")
    serp_artifact = f"serp-{slug}.json"
    if dfs_ok:
        serp_query = f"{service_display} {args.city} {args.state}"
        serp_ok, serp_path, serp_msg, serp_cost = pull_s1_s2_s8_serp(
            serp_query, args.location_code, out_dir / serp_artifact,
        )
        print(f"  SERP: {'OK' if serp_ok else 'FAIL'} — {serp_msg}")
        cost_total += serp_cost
        if serp_path:
            artifacts[serp_artifact] = serp_path

        source_status["S1"] = {
            "pulled": serp_ok, "artifacts": [serp_artifact],
        }
        source_status["S2"] = {
            "pulled": serp_ok, "artifacts": [serp_artifact],
        }
        source_status["S8"] = {
            "pulled": serp_ok, "artifacts": [serp_artifact],
        }
    else:
        for s in ("S1", "S2", "S8"):
            source_status[s] = {"pulled": False, "reason": dfs_msg}

    # ─── S4: KEYWORD SUGGESTIONS ─────────────────────────────────────────
    print("--- S4: Keyword Suggestions ---")
    s4_city_artifact = f"s4-{args.service}-{args.city}.json"
    s4_broad_artifact = f"s4-broad-{args.service}.json"
    if dfs_ok:
        city_seed = f"{service_display} {args.city}"
        s4c_ok, s4c_path, s4c_msg, s4c_cost = pull_s4_keyword_suggestions(
            city_seed, args.location_code, out_dir / s4_city_artifact,
        )
        print(f"  Per-city seed: {'OK' if s4c_ok else 'FAIL'} — {s4c_msg}")
        cost_total += s4c_cost
        if s4c_path:
            artifacts[s4_city_artifact] = s4c_path

        broad_seed = service_display
        s4b_ok, s4b_path, s4b_msg, s4b_cost = pull_s4_keyword_suggestions(
            broad_seed, args.location_code, out_dir / s4_broad_artifact,
        )
        print(f"  Broad seed: {'OK' if s4b_ok else 'FAIL'} — {s4b_msg}")
        cost_total += s4b_cost
        if s4b_path:
            artifacts[s4_broad_artifact] = s4b_path

        source_status["S4"] = {
            "pulled": s4c_ok and s4b_ok,
            "artifacts": [s4_city_artifact, s4_broad_artifact],
            "cost": round(s4c_cost + s4b_cost, 6),
        }
    else:
        source_status["S4"] = {"pulled": False, "reason": dfs_msg}

    # ─── S5 SONAR ─────────────────────────────────────────────────────────
    print("--- S5: Sonar ---")
    if sonar_ok and not args.skip_sonar:
        sonar_qs = build_sonar_queries(
            args.service, args.city, args.state, config,
        )
        s5_ok, s5_path, s5_msg = pull_s5_sonar(sonar_qs, out_dir)
        print(f"  Sonar: {'OK' if s5_ok else 'FAIL'} — {s5_msg}")
        source_status["S5-sonar"] = {
            "pulled": s5_ok,
            "artifacts": ["sonar-report.md"] if s5_ok else [],
        }
    elif args.skip_sonar:
        print("  Sonar: SKIPPED (--skip-sonar)")
        source_status["S5-sonar"] = {
            "pulled": False,
            "reason": "Skipped via --skip-sonar",
        }
    else:
        print(f"  Sonar: BLOCKED — {sonar_msg}")
        source_status["S5-sonar"] = {
            "pulled": False,
            "reason": sonar_msg,
            "sop": "Install key at tier-3/automation/secrets/perplexity-sonar.key",
        }

    # ─── S5 ChatGPT/Gemini (GATED MANUAL STEP) ───────────────────────────
    print("--- S5: ChatGPT/Gemini (gated manual step) ---")
    cg_queries = build_chatgpt_gemini_queries(
        args.service, args.city, args.state, config,
    )
    cg_artifact = emit_s5_chatgpt_gemini_gate(cg_queries, slug, out_dir)
    print(f"  Query list emitted: {cg_artifact.name}")
    print(f"  {len(cg_queries)} queries. Run manually on personal Chrome profile.")
    source_status["S5-chatgpt-gemini"] = {
        "pulled": False,
        "gated": True,
        "reason": "Manual step — requires personal Chrome automation profile",
        "sop": "SOP-S5 in workflow-demand-ai-data-capture.md",
        "artifacts": [cg_artifact.name],
    }

    # ─── S7: GSC FIRST-PARTY ─────────────────────────────────────────────
    print("--- S7: GSC First-Party ---")
    gsc_report, gsc_report_path = read_latest_gsc_report(args.client)
    if gsc_report:
        print(f"  Existing report: {gsc_report_path.name}")
        source_status["S7-existing"] = {
            "pulled": True,
            "artifacts": [gsc_report_path.name],
        }
    else:
        print("  No existing GSC report found")
        source_status["S7-existing"] = {
            "pulled": False,
            "reason": "No gsc-performance-*.json found in reports/",
            "sop": "Run: python3 gsc_search_analytics.py <client_slug>",
        }

    if gsc_ok and not args.skip_gsc_fresh:
        s7_ok, s7_path, s7_msg = pull_s7_gsc_fresh(
            args.client, args.gsc_window, slug, out_dir,
        )
        print(f"  Fresh per-slug: {'OK' if s7_ok else 'FAIL'} — {s7_msg}")
        source_status["S7-fresh"] = {
            "pulled": s7_ok,
            "artifacts": [f"s7-gsc-{slug}.json"] if s7_ok else [],
        }
    elif args.skip_gsc_fresh:
        print("  Fresh per-slug: SKIPPED (--skip-gsc-fresh)")
        source_status["S7-fresh"] = {
            "pulled": False,
            "reason": "Skipped via --skip-gsc-fresh",
            "sop": "SOP-S7 in workflow-demand-ai-data-capture.md",
        }
    else:
        print(f"  Fresh per-slug: BLOCKED — {gsc_msg}")
        source_status["S7-fresh"] = {
            "pulled": False,
            "reason": gsc_msg,
            "sop": "SOP-S7 in workflow-demand-ai-data-capture.md",
        }

    # ─── EMIT DOSSIER ────────────────────────────────────────────────────
    print()
    print("--- Emitting dossier ---")

    dossier_data: dict[str, Any] = {
        "slug": slug,
        "service": args.service,
        "service_display": service_display,
        "city": args.city,
        "state": args.state,
        "client": args.client,
        "domain": args.domain,
        "gsc_window": args.gsc_window,
        "location_code": args.location_code,
        "source_status": source_status,
        "cost_total": round(cost_total, 6),
        "pulled_at": date.today().isoformat(),
        "artifacts": {k: str(v) for k, v in artifacts.items()},
    }

    # Load raw JSON artifacts for dossier rendering
    raw_data: dict[str, Any] = {}
    for name, path in artifacts.items():
        if path and Path(path).exists():
            try:
                raw_data[name] = json.loads(Path(path).read_text())
            except (json.JSONDecodeError, OSError):
                pass

    if gsc_report:
        raw_data["gsc-existing"] = gsc_report

    dossier_md, dossier_json = build_dossier(dossier_data, raw_data, config)

    md_path = out_dir / f"dossier-{slug}.md"
    json_path = out_dir / f"dossier-{slug}.json"
    write_dossier(md_path, dossier_md, json_path, dossier_json)

    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")

    # ─── GATE CHECK ──────────────────────────────────────────────────────
    print()
    gate_pass, gate_msg = gate_check(source_status)
    if gate_pass:
        print(f"GATE: PASS — {gate_msg}")
    else:
        print(f"GATE: FAIL — {gate_msg}")
        sys.exit(1)

    print(f"\nTotal DataForSEO cost: ${cost_total:.6f}")
    print(f"Done. Dossier at {md_path}")


if __name__ == "__main__":
    main()
