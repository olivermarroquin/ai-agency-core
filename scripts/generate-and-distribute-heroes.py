#!/usr/bin/env python3
"""
generate-and-distribute-heroes.py — per-service hero generation + distribution.

Generates topic/faceless hero images for each service in the Core 30 build,
runs the auto-chooser to select keepers, and distributes them across all city
pages for that service via organize-image-downloads.py --copy-to.

STRATEGY (operator direction 2026-06-05):
  - ONE hero per SERVICE, reused across all cities for that service.
  - Heroes are faceless/topic-class (no owner face).
  - About slot reuses page-01's owner portrait site-wide (cache mechanism).
  - ~4 variants per service, chooser picks keeper, alternates rotated across
    cities for light variety via --copy-to.

USAGE
-----
Generate for one service (test first):

    python generate-and-distribute-heroes.py \\
        --client s-and-h-contracting \\
        --service emergency-electrician \\
        --dry-run

Generate for all services and distribute:

    python generate-and-distribute-heroes.py \\
        --client s-and-h-contracting

Skip a service that already has a keeper:

    python generate-and-distribute-heroes.py \\
        --client s-and-h-contracting \\
        --skip-services emergency-electrician

OUTPUTS
-------
- Per-service staging dir with 4 variants
- Chooser JSON with scores + keeper selection
- Keeper filed into first city page, alternates distributed to other cities
- Stdout: list of page folders touched
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent

# Service → prompt template. Faceless/topic-class, no owner, no face.
# Each prompt produces a single hero-slot image for that service.
SERVICE_PROMPTS = {
    "panel-upgrade": (
        "A freshly installed 200-amp residential electrical panel with the "
        "cover open, showing neatly dressed wires, labeled circuit breakers "
        "in rows, and a clean panel interior. Modern home utility area, "
        "neutral drywall walls, soft warm natural light from a nearby window. "
        "No people in frame. Tight editorial shot, panel fills 70% of frame. "
        "Photo-realistic, 50mm lens, shallow depth of field on background. "
        "Avoid: people, faces, hands, hard hats, hi-vis vests, watermarks, "
        "garbled text, gibberish labels, impossible wiring, cartoon style."
    ),
    "emergency-electrician": (
        "A residential electrical panel with the cover removed, showing a "
        "tripped breaker in the off position while the rest are on — the "
        "classic sign of an electrical emergency. Tight shot of the open "
        "panel interior: neat rows of breakers, one clearly flipped to the "
        "tripped position, label strips with legible real room names visible. "
        "Interior wall of a modern residential utility area, soft warm "
        "lighting. No people in frame. Photo-realistic editorial photography, "
        "50mm lens, sharp focus on the tripped breaker. "
        "Avoid: people, faces, hands, hard hats, watermarks, garbled text, "
        "gibberish labels, impossible wiring, melted components, cartoon style."
    ),
    "ev-charger-installation": (
        "A wall-mounted Level 2 EV charger freshly installed in a residential "
        "garage, with a clean cable coiled neatly and a dedicated 240V outlet "
        "or hardwired conduit visible behind it. Modern garage interior, "
        "concrete floor, organized shelving in soft focus. The charger is the "
        "hero — a sleek modern unit (white or gray) mounted at chest height. "
        "Soft warm overhead lighting. No people, no vehicles. "
        "Photo-realistic editorial, tight composition, charger fills 60% of "
        "frame. Avoid: people, faces, hands, brand logos, watermarks, garbled "
        "text, cartoon style, impossible wiring."
    ),
    "light-fixture-installation": (
        "A modern residential pendant light fixture freshly installed above a "
        "kitchen island, glowing warmly. The fixture is elegant and "
        "contemporary — brushed metal or matte black, clean lines. Below it "
        "a granite or quartz countertop with nothing on it. Warm evening "
        "interior lighting, the fixture is the focal point. No people. "
        "Photo-realistic editorial, slightly low angle looking up at the "
        "fixture, shallow depth of field. "
        "Avoid: people, faces, hands, hard hats, watermarks, garbled text, "
        "cartoon style, construction mess."
    ),
}


def parse_build_order(build_order_path: Path) -> dict[int, str]:
    """Parse _build-order.md → {position: page-slug}."""
    mapping: dict[int, str] = {}
    text = build_order_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*([\w-]+)\s*\|", line)
        if m:
            pos = int(m.group(1))
            slug = m.group(2).strip()
            if slug and slug != "(reserved)":
                mapping[pos] = slug
    return mapping


def pages_for_service(build_order: dict[int, str], service_slug: str) -> list[tuple[int, str]]:
    """Return [(position, slug)] for all pages matching a service."""
    return [
        (pos, slug) for pos, slug in sorted(build_order.items())
        if slug.startswith(service_slug + "-") or slug == service_slug
    ]


def find_page_folder(core_30_dir: Path, position: int) -> Optional[Path]:
    """Find the page folder for a position."""
    prefix = f"{position:02d}-"
    for d in sorted(core_30_dir.iterdir()):
        if d.is_dir() and d.name.startswith(prefix):
            return d
    return None


def generate_variants(
    prompt: str,
    count: int,
    staging_dir: Path,
    dry_run: bool,
) -> list[Path]:
    """Generate N variants via Higgsfield CLI. Returns list of downloaded paths."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for i in range(1, count + 1):
        out_path = staging_dir / f"{i}.png"
        if dry_run:
            sys.stderr.write(f"  (dry-run: would generate variant {i}/{count})\n")
            # Create a placeholder for dry-run flow
            out_path.touch()
            paths.append(out_path)
            continue

        sys.stderr.write(f"  Generating variant {i}/{count}... ")
        cmd = [
            "higgsfield", "generate", "create", "nano_banana_2",
            "--prompt", prompt,
            "--aspect_ratio", "4:3",
            "--resolution", "1k",
            "--wait", "--json",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            sys.stderr.write("TIMEOUT\n")
            continue

        if result.returncode != 0:
            sys.stderr.write(f"FAILED: {result.stderr[:100]}\n")
            continue

        try:
            data = json.loads(result.stdout)
            url = data[0]["result_url"]
        except (json.JSONDecodeError, KeyError, IndexError):
            sys.stderr.write("PARSE ERROR\n")
            continue

        import urllib.request
        urllib.request.urlretrieve(url, str(out_path))
        sys.stderr.write(f"OK\n")
        paths.append(out_path)

    return paths


def run_chooser(
    variant_paths: list[Path],
    client_config: Path,
    service_name: str,
    dry_run: bool,
) -> Optional[dict[str, Any]]:
    """Run the auto-chooser (standalone anthropic backend) on variants."""
    if dry_run:
        sys.stderr.write("  (dry-run: would run chooser)\n")
        return {"keeper_index": 0, "keeper_name": variant_paths[0].name,
                "outcome_tag": "dry-run", "all_ranked": []}

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "choose-image-variant.py"),
        "--variants", *[str(p) for p in variant_paths],
        "--slot-type", "hero",
        "--image-class", "topic",
        "--client-config", str(client_config),
        "--page-service", service_name,
        "--page-city", "Northern Virginia",
        "--backend", "anthropic",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        sys.stderr.write(f"  Chooser failed: {result.stderr[:200]}\n")
        return None

    # Print stderr (progress) to our stderr
    if result.stderr:
        for line in result.stderr.splitlines():
            sys.stderr.write(f"  {line}\n")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"  Chooser output parse error\n")
        return None


def distribute_to_pages(
    keeper_path: Path,
    alternate_paths: list[Path],
    pages: list[tuple[int, str]],
    core_30_dir: Path,
    service_slug: str,
    dry_run: bool,
) -> list[Path]:
    """Distribute keeper + alternates across city pages for a service.

    First city gets the keeper. Others get alternates (rotated) or
    the keeper if not enough alternates.
    """
    touched = []
    all_variants = [keeper_path] + alternate_paths

    for i, (pos, slug) in enumerate(pages):
        page_folder = find_page_folder(core_30_dir, pos)
        if not page_folder:
            sys.stderr.write(f"  WARN: page folder not found for {pos:02d}-{slug}\n")
            continue

        # Pick which variant this page gets (rotate through available)
        variant_to_use = all_variants[i % len(all_variants)]
        variant_num = all_variants.index(variant_to_use) + 1

        stem = re.sub(r"^\d+-", "", page_folder.name)
        dst_name = f"{stem}-hero-v1-variant-{variant_num}.png"
        dst = page_folder / "images" / dst_name

        if dst.exists():
            sys.stderr.write(f"  {page_folder.name}: hero already exists — skipped\n")
            touched.append(page_folder)
            continue

        sys.stderr.write(f"  {page_folder.name}: → {dst_name}")
        if variant_to_use == keeper_path:
            sys.stderr.write(" (keeper)")
        else:
            sys.stderr.write(" (alternate)")
        sys.stderr.write("\n")

        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(variant_to_use), str(dst))

        touched.append(page_folder)

    return touched


def run(
    client_slug: str,
    core_30_dir: Path,
    client_config: Path,
    services: Optional[list[str]],
    skip_services: list[str],
    variants_count: int,
    dry_run: bool,
) -> int:
    build_order_path = core_30_dir / "_build-order.md"
    if not build_order_path.is_file():
        sys.stderr.write(f"ERROR: build-order not found: {build_order_path}\n")
        return 2

    build_order = parse_build_order(build_order_path)
    staging_root = Path.home() / "workspace" / "_inbox" / "hero-generation" / client_slug

    # Determine which services to process
    all_services = list(SERVICE_PROMPTS.keys())
    if services:
        target_services = [s for s in services if s in SERVICE_PROMPTS]
    else:
        target_services = all_services
    target_services = [s for s in target_services if s not in skip_services]

    sys.stderr.write(f"→ Client: {client_slug}\n")
    sys.stderr.write(f"→ Services: {', '.join(target_services)}\n")
    sys.stderr.write(f"→ Variants per service: {variants_count}\n")
    if dry_run:
        sys.stderr.write(f"→ MODE: DRY RUN\n")

    all_touched: list[Path] = []
    total_gens = 0

    for service in target_services:
        sys.stderr.write(f"\n{'='*60}\n")
        sys.stderr.write(f"SERVICE: {service}\n")
        sys.stderr.write(f"{'='*60}\n")

        pages = pages_for_service(build_order, service)
        # Exclude page 01 (panel-upgrade already has a manual hero)
        pages = [(p, s) for p, s in pages if p > 1]

        if not pages:
            sys.stderr.write(f"  No pages need heroes for {service}\n")
            continue

        sys.stderr.write(f"  Pages: {', '.join(f'{p:02d}' for p, _ in pages)}\n")

        prompt = SERVICE_PROMPTS[service]
        staging = staging_root / service

        # Generate
        sys.stderr.write(f"\n  --- Generating {variants_count} variants ---\n")
        variant_paths = generate_variants(prompt, variants_count, staging, dry_run)
        total_gens += len(variant_paths)

        if not variant_paths:
            sys.stderr.write(f"  ERROR: no variants generated for {service}\n")
            continue

        # Choose
        sys.stderr.write(f"\n  --- Running chooser ---\n")
        chooser_result = run_chooser(variant_paths, client_config, service, dry_run)

        if chooser_result is None:
            sys.stderr.write(f"  ERROR: chooser failed for {service}\n")
            continue

        # Save chooser result
        chooser_out = staging / "chooser-result.json"
        if not dry_run:
            chooser_out.write_text(json.dumps(chooser_result, indent=2))

        keeper_name = chooser_result.get("keeper_name", "")
        outcome_tag = chooser_result.get("outcome_tag", "")
        score = chooser_result.get("score", 0)
        sys.stderr.write(f"\n  KEEPER: {keeper_name} (score={score}, tag={outcome_tag})\n")

        if outcome_tag not in ("keeper", "dry-run"):
            sys.stderr.write(
                f"  ⚠ No keeper-quality variant for {service} "
                f"(best={score}). Operator review needed.\n"
            )
            continue

        # Identify keeper + usable alternates (score >= 75)
        keeper_path = staging / keeper_name if not dry_run else variant_paths[0]
        usable_alts = []
        for ranked in chooser_result.get("all_ranked", []):
            if ranked["name"] != keeper_name and ranked.get("tag") == "keeper":
                alt_path = staging / ranked["name"]
                if alt_path.exists():
                    usable_alts.append(alt_path)

        sys.stderr.write(f"  Usable alternates for variety: {len(usable_alts)}\n")

        # Distribute
        sys.stderr.write(f"\n  --- Distributing to {len(pages)} pages ---\n")
        touched = distribute_to_pages(
            keeper_path, usable_alts, pages, core_30_dir, service, dry_run,
        )
        all_touched.extend(touched)

    sys.stderr.write(f"\n{'='*60}\n")
    sys.stderr.write(f"SUMMARY\n")
    sys.stderr.write(f"  Generations: {total_gens}\n")
    sys.stderr.write(f"  Pages touched: {len(all_touched)}\n")
    sys.stderr.write(f"{'='*60}\n")

    for pf in all_touched:
        print(pf)

    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Generate per-service topic heroes and distribute across city pages.",
    )
    p.add_argument("--client", required=True, help="Client slug.")
    p.add_argument(
        "--service", type=str, nargs="*", default=None,
        help="Specific service(s) to process. Default: all.",
    )
    p.add_argument(
        "--skip-services", type=str, nargs="*", default=[],
        help="Service(s) to skip (already have keepers).",
    )
    p.add_argument("--variants", type=int, default=4, help="Variants per service (default: 4).")
    p.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    projects_base = (
        Path.home() / "workspace" / "second-brain" / "04_projects"
        / "clients" / "_active" / args.client
    )
    core_30_dir = projects_base / "website-archive" / "new" / "core-30"
    client_config = SCRIPTS_DIR / f"{args.client}.config.json"

    if not core_30_dir.is_dir():
        sys.stderr.write(f"ERROR: {core_30_dir} not found\n")
        return 2
    if not client_config.is_file():
        sys.stderr.write(f"ERROR: {client_config} not found\n")
        return 2

    return run(
        client_slug=args.client,
        core_30_dir=core_30_dir,
        client_config=client_config,
        services=args.service,
        skip_services=args.skip_services,
        variants_count=args.variants,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
