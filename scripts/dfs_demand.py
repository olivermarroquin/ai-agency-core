#!/usr/bin/env python3
"""
dfs_demand.py — DataForSEO demand-data helpers for the demand-capture routine.

Provides:
  - Reachability probe (CR-142 discipline: probe before assuming blocked)
  - Payload builders for volume, difficulty, SERP-advanced, keyword_suggestions
  - Subprocess callers that invoke dataforseo_query.py (tier-3, secrets vend)
  - Response parsers lifted from the EV Wave-1 run into reusable functions

Zero hardcoded client values (CR-155/156). All query terms come from caller args.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Tier-3 script location (where DataForSEO credentials vend)
_TIER3_CANDIDATES = [
    Path.home() / "workspace" / "second-brain-tier3" / "automation" / "scripts",
    Path.home() / "mnt" / "workspace" / "second-brain-tier3" / "automation" / "scripts",
]
TIER3_SCRIPTS = next((p for p in _TIER3_CANDIDATES if p.exists()), _TIER3_CANDIDATES[0])
DFS_SCRIPT = TIER3_SCRIPTS / "dataforseo_query.py"


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------

def probe_dataforseo_reachability() -> tuple[bool, str]:
    """Probe DataForSEO with a free balance check (CR-142: never assume blocked).

    Returns (reachable, message). The balance check endpoint is free.
    """
    if not DFS_SCRIPT.exists():
        return False, f"Script not found: {DFS_SCRIPT}"

    try:
        result = subprocess.run(
            [sys.executable, str(DFS_SCRIPT),
             "--endpoint", "appendix/user_data",
             "--method", "GET", "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=str(TIER3_SCRIPTS),
        )
        if result.returncode != 0:
            return False, f"Balance check failed: {result.stderr[:200]}"
        resp = json.loads(result.stdout)
        for task in resp.get("tasks") or []:
            for item in task.get("result") or []:
                balance = (item.get("money") or {}).get("balance")
                if balance is not None:
                    return True, f"Funded (${balance:.2f} balance)"
        return True, "Reachable (balance not parsed from response)"
    except subprocess.TimeoutExpired:
        return False, "Balance check timed out (30s)"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Subprocess caller
# ---------------------------------------------------------------------------

def _call_dfs(endpoint: str, payload: list[dict], out_path: Path) -> tuple[bool, str, float]:
    """Call DataForSEO via subprocess → dataforseo_query.py.

    Returns (ok, message, cost_usd).
    """
    data_file = out_path.parent / f"_tmp-dfs-{out_path.stem}.json"
    data_file.write_text(json.dumps(payload))

    try:
        result = subprocess.run(
            [sys.executable, str(DFS_SCRIPT),
             "--endpoint", endpoint,
             "--data-file", str(data_file),
             "--out", str(out_path),
             "--pretty"],
            capture_output=True, text=True, timeout=120,
            cwd=str(TIER3_SCRIPTS),
        )
        data_file.unlink(missing_ok=True)

        if result.returncode != 0:
            return False, result.stderr[:300], 0.0

        # Parse cost from stderr: _DataForSEO POST /...: ... cost=$0.001234_
        cost = 0.0
        for line in result.stderr.splitlines():
            if "cost=$" in line:
                try:
                    cost_str = line.split("cost=$")[1].rstrip("_").split()[0]
                    cost = float(cost_str)
                except (ValueError, IndexError):
                    pass

        return True, result.stderr.strip(), cost
    except subprocess.TimeoutExpired:
        data_file.unlink(missing_ok=True)
        return False, "DataForSEO call timed out (120s)", 0.0
    except Exception as e:
        data_file.unlink(missing_ok=True)
        return False, str(e), 0.0


# ---------------------------------------------------------------------------
# Source pullers (payload builders + callers)
# ---------------------------------------------------------------------------

def pull_s3_volume(
    keywords: list[str], location_code: int, out_path: Path,
) -> tuple[bool, Path | None, str, float]:
    """Pull S3 search volume via DataForSEO Google Ads search_volume/live."""
    payload = [{
        "location_code": location_code,
        "language_code": "en",
        "keywords": keywords,
    }]
    ok, msg, cost = _call_dfs(
        "keywords_data/google_ads/search_volume/live", payload, out_path,
    )
    return ok, out_path if ok else None, msg, cost


def pull_s3_difficulty(
    keywords: list[str], location_code: int, out_path: Path,
) -> tuple[bool, Path | None, str, float]:
    """Pull S3 keyword difficulty via DataForSEO Labs bulk_keyword_difficulty/live."""
    payload = [{
        "location_code": location_code,
        "language_code": "en",
        "keywords": keywords,
    }]
    ok, msg, cost = _call_dfs(
        "dataforseo_labs/google/bulk_keyword_difficulty/live", payload, out_path,
    )
    return ok, out_path if ok else None, msg, cost


def pull_s1_s2_s8_serp(
    keyword: str, location_code: int, out_path: Path,
) -> tuple[bool, Path | None, str, float]:
    """Pull S1 PAA + S2 AI Overview + S8 who-ranks via SERP organic/live/advanced.

    ONE task per call — the endpoint rejects batches.
    """
    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": "en",
        "device": "desktop",
        "depth": 20,
    }]
    ok, msg, cost = _call_dfs(
        "serp/google/organic/live/advanced", payload, out_path,
    )
    return ok, out_path if ok else None, msg, cost


def pull_s4_keyword_suggestions(
    seed: str, location_code: int, out_path: Path,
) -> tuple[bool, Path | None, str, float]:
    """Pull S4 keyword suggestions via DataForSEO Labs keyword_suggestions/live."""
    payload = [{
        "keyword": seed,
        "location_code": location_code,
        "language_code": "en",
        "limit": 40,
    }]
    ok, msg, cost = _call_dfs(
        "dataforseo_labs/google/keyword_suggestions/live", payload, out_path,
    )
    return ok, out_path if ok else None, msg, cost


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def parse_volume_response(resp: dict) -> list[dict[str, Any]]:
    """Parse DFS volume response → [{keyword, volume, competition, cpc}].

    Handles both the keyword_data wrapper (search_volume endpoint) and the
    direct-item structure (bulk endpoints).
    """
    results: list[dict[str, Any]] = []
    for task in resp.get("tasks") or []:
        for item in task.get("result") or []:
            if not isinstance(item, dict):
                continue
            kw_data = item.get("keyword_data")
            if kw_data and isinstance(kw_data, dict):
                kw_info = kw_data.get("keyword_info") or {}
                results.append({
                    "keyword": kw_data.get("keyword", ""),
                    "volume": kw_info.get("search_volume"),
                    "competition": kw_info.get("competition"),
                    "cpc": kw_info.get("cpc"),
                })
            else:
                results.append({
                    "keyword": item.get("keyword", ""),
                    "volume": item.get("search_volume"),
                    "competition": item.get("competition"),
                    "cpc": item.get("cpc"),
                })
    return results


def parse_serp_response(resp: dict) -> dict[str, Any]:
    """Parse SERP advanced response → {paa, ai_overview, organic, local_pack}.

    Extracts S1 (PAA questions), S2 (AI Overview flag + cited sources),
    and S8 (organic rankings + local pack).
    """
    result: dict[str, Any] = {
        "paa": [],
        "ai_overview": False,
        "ai_overview_sources": [],
        "organic": [],
        "local_pack": [],
    }

    for task in resp.get("tasks") or []:
        for task_result in task.get("result") or []:
            if not isinstance(task_result, dict):
                continue
            for item in task_result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")

                if item_type == "people_also_ask":
                    for sub in item.get("items") or []:
                        title = sub.get("title", "") if isinstance(sub, dict) else ""
                        if title:
                            result["paa"].append(title)

                elif item_type in ("ai_overview", "generative_answers"):
                    result["ai_overview"] = True
                    for ref in item.get("references") or []:
                        src = ref.get("source", "") if isinstance(ref, dict) else ""
                        if src:
                            result["ai_overview_sources"].append(src)

                elif item_type == "organic":
                    result["organic"].append({
                        "position": item.get("rank_group"),
                        "domain": item.get("domain"),
                        "url": item.get("url"),
                        "title": item.get("title"),
                    })

                elif item_type == "local_pack":
                    for sub in item.get("items") or []:
                        if isinstance(sub, dict):
                            result["local_pack"].append({
                                "title": sub.get("title", ""),
                                "domain": sub.get("domain", ""),
                            })

    return result


def parse_keyword_suggestions(resp: dict) -> list[dict[str, Any]]:
    """Parse keyword suggestions → [{keyword, volume, competition}].

    Empty per-city results (items_count=0) are a legitimate finding —
    confirms no hyperlocal keyword DB entries exist for that seed.
    """
    results: list[dict[str, Any]] = []
    for task in resp.get("tasks") or []:
        for task_result in task.get("result") or []:
            if not isinstance(task_result, dict):
                continue
            for item in task_result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                kw_data = item.get("keyword_data") or {}
                kw_info = kw_data.get("keyword_info") or {} if isinstance(kw_data, dict) else {}
                results.append({
                    "keyword": kw_data.get("keyword", item.get("keyword", ""))
                    if isinstance(kw_data, dict) else item.get("keyword", ""),
                    "volume": kw_info.get("search_volume"),
                    "competition": kw_info.get("competition"),
                })
    return results
