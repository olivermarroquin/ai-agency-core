#!/usr/bin/env python3
"""
wp_surgical_replace.py — targeted regex edits to WP post_content, with proof.

Unlike publish-differentiation-wave.py (which replaces a page's whole
post_content from a draft file), this makes NAMED, COUNTED substitutions inside
the content that is already live. Use it for sitewide copy corrections where
rebuilding every page from a draft would be absurd and risky.

Every edit declares how many times it must match. A count mismatch aborts that
page untouched — a silent partial edit across 31 pages is the failure mode this
exists to prevent.

    python3 wp_surgical_replace.py --config edits.json            # dry run
    python3 wp_surgical_replace.py --config edits.json --apply
    python3 wp_surgical_replace.py --config edits.json --dump home
                                          # write one object's RAW content to a
                                          # file and exit. Use this BEFORE
                                          # writing an edit, to copy the exact
                                          # string instead of guessing it.

Config:
    {
      "client_slug": "...",
      "domain": "https://example.com",
      "wp_user": "oliver",
      "backup_dir": "/abs/path/backups",
      "validate_jsonld": true,          // gate: every <script ld+json> block on a
                                        // page must still parse after the edits,
                                        // or that page is skipped untouched
      "edits": [
        {"name": "trip-fee pairing",
         "find": "No trip charge for ",
         "replace": "No trip fee and free estimates for ",
         "regex": false,
         "slugs": "*",              // "*" = every page/post, or a list
         "expect": ">=1",           // "0", "1", ">=1", "<=3", "*" (any)
         "skip_if_present": "No trip fee"}  // optional: if this string is already
                                            // in the content, skip this edit on
                                            // that object. Makes reruns safe.
      ],
      "forbid_after": ["trip charge"]   // must not survive anywhere we edited
    }

⚠️  ORDER OF OPERATIONS for a page built by a page builder (Elementor et al.):
    take a REAL backup first —

        python3 wp_page_snapshot.py backup <client-slug> <id>

    This script backs up `post_content` only. `_elementor_data` lives in postmeta
    and is NOT in that backup. wp_page_snapshot is the one that can actually
    restore.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_secrets import load_wp_app_password

# Hostinger's WAF returns a BODYLESS 403 to python-requests' default User-Agent.
# Verified 2026-08-24: no headers -> 403/0 bytes; browser UA -> 200. Do not remove.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def check_expect(n: int, spec: str) -> bool:
    spec = str(spec).strip()
    if spec in ("*", "any"):
        return True
    if spec.startswith(">="):
        return n >= int(spec[2:])
    if spec.startswith("<="):
        return n <= int(spec[2:])
    if spec.startswith(">"):
        return n > int(spec[1:])
    return n == int(spec)


JSONLD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def validate_jsonld(content: str) -> tuple[bool, str]:
    """Every JSON-LD block in `content` must still parse.

    WHY: page 8 on ev-electric-services carries ~74 KB of MINIFIED JSON-LD inside
    a wp:html block. A find/replace that lands one character wrong there produces
    content that saves fine and silently destroys the site's structured data —
    Google simply stops seeing the LocalBusiness. A count check cannot catch that;
    only parsing can. Returns (ok, human-readable summary).
    """
    blocks = JSONLD_BLOCK.findall(content)
    if not blocks:
        return True, "no JSON-LD blocks"
    types: list[str] = []
    for i, blk in enumerate(blocks, 1):
        try:
            parsed = json.loads(blk)
        except json.JSONDecodeError as exc:
            return False, f"block {i} of {len(blocks)} no longer parses: {exc}"
        nodes = parsed.get("@graph", [parsed]) if isinstance(parsed, dict) else parsed
        if isinstance(nodes, list):
            types += [n.get("@type") for n in nodes if isinstance(n, dict)]
    return True, f"{len(blocks)} block(s) valid: {types}"


def fetch_all(session, domain, headers):
    """Every page and post, with RAW content (context=edit needs auth)."""
    objs = []
    for kind in ("pages", "posts"):
        page = 1
        while True:
            # NOTE: do NOT send `status`. WordPress rejects any non-default value
            # with 400 "Status is forbidden" whenever the request is not
            # authenticated as someone who may list drafts — and that 400 reads
            # like a parameter bug, not an auth failure, which cost a debugging
            # round on 2026-08-25. Default (publish) is what we want anyway.
            r = session.get(f"{domain}/wp-json/wp/v2/{kind}",
                            params={"per_page": 100, "page": page,
                                    "context": "edit",
                                    "_fields": "id,slug,type,title,content,link"},
                            headers=headers, timeout=45)
            if r.status_code != 200:
                if page == 1:
                    print(f"  ! {kind}: HTTP {r.status_code} {r.text[:160]}")
                break
            batch = r.json()
            if not batch:
                break
            objs += batch
            if len(batch) < 100:
                break
            page += 1
    return objs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it, nothing is sent.")
    ap.add_argument("--dump", metavar="SLUG_OR_ID",
                    help="write that object's RAW content to a file and exit. "
                         "Read-only. Use it to copy exact strings for an edit.")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).expanduser().read_text())
    domain = cfg["domain"].rstrip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(cfg.get("backup_dir", "./wp-backups")).expanduser() / stamp
    edits = cfg.get("edits", [])
    forbid_after = cfg.get("forbid_after", [])
    want_jsonld = bool(cfg.get("validate_jsonld"))

    pw = load_wp_app_password(cfg)
    token = b64encode(f"{cfg['wp_user']}:{pw}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "User-Agent": BROWSER_UA}
    session = requests.Session()

    mode = "DUMP" if args.dump else ("APPLY" if args.apply else "DRY RUN")
    print(f"{mode} · {domain}")

    # Prove who we are BEFORE doing anything. An unauthenticated request still
    # gets 200s on public endpoints, so "it fetched something" is not evidence
    # that auth worked — and context=edit silently degrades.
    me = session.get(f"{domain}/wp-json/wp/v2/users/me",
                     params={"context": "edit"}, headers=headers, timeout=30)
    if me.status_code != 200:
        print(f"FATAL: not authenticated. /users/me -> HTTP {me.status_code}")
        print(f"  {me.text[:300]}")
        print("\n  Check, in order:")
        print(f"   1. the key file has the app password for wp_user '{cfg['wp_user']}':")
        print("      ~/workspace/second-brain-tier3/automation/secrets/"
              f"wp-app-password-{cfg['client_slug']}.key")
        print("   2. it is the *application* password (24 chars, 6 groups of 4), not the login password")
        print("   3. the host is not stripping the Authorization header")
        return 1
    who = me.json()
    print(f"Authenticated as {who.get('slug')} (id {who.get('id')}, "
          f"caps: {'edit_pages' if 'edit_pages' in (who.get('capabilities') or {}) else 'unknown'})")

    objs = fetch_all(session, domain, headers)
    print(f"Fetched {len(objs)} objects (raw content)\n")
    if not objs:
        print("FATAL: nothing fetched — check credentials.")
        return 1

    if args.dump:
        target = args.dump.strip()
        hit = next((o for o in objs
                    if o["slug"] == target or str(o["id"]) == target), None)
        if not hit:
            print(f"FATAL: no page or post with slug/id '{target}'.")
            print("  Available slugs: "
                  + ", ".join(sorted(o["slug"] for o in objs)[:40]))
            return 1
        out = Path(f"dump-{hit['slug']}-{hit['id']}.html").resolve()
        out.write_text(hit["content"]["raw"], encoding="utf-8")
        print(f"Wrote {len(hit['content']['raw']):,} chars -> {out}")
        ok_ld, note = validate_jsonld(hit["content"]["raw"])
        print(f"JSON-LD as it stands: {'ok' if ok_ld else 'BROKEN'} — {note}")
        print("\nNothing was written to the site. Grep the file for the exact "
              "string you want to change, then paste it into the edit's 'find'.")
        return 0

    if not edits:
        print("FATAL: config has no 'edits'. (Did you mean --dump?)")
        return 1

    planned, skipped, total_subs = [], [], 0

    for o in objs:
        raw = (o.get("content") or {}).get("raw")
        if raw is None:
            continue
        new, applied, aborted = raw, [], None
        for e in edits:
            scope = e.get("slugs", "*")
            if scope != "*" and o["slug"] not in scope:
                continue
            # Idempotency guard: the desired end-state is already there, so this
            # edit is a no-op rather than a failure. Without it, a rerun trips the
            # expect count and aborts a page that is in fact already correct.
            guard = e.get("skip_if_present")
            if guard and guard in new:
                continue
            if e.get("regex"):
                pat = re.compile(e["find"], re.DOTALL)
                n = len(pat.findall(new))
            else:
                n = new.count(e["find"])
            if not check_expect(n, e.get("expect", "*")):
                aborted = (f"edit '{e['name']}' matched {n}x, "
                           f"expected {e.get('expect')}")
                break
            if n == 0:
                continue
            new = (pat.sub(e["replace"], new) if e.get("regex")
                   else new.replace(e["find"], e["replace"]))
            applied.append((e["name"], n))

        if aborted:
            skipped.append((o["slug"], aborted))
            continue
        if not applied:
            continue
        residue = [f for f in forbid_after if f.lower() in new.lower()]
        if residue:
            skipped.append((o["slug"], f"forbidden string survives: {residue}"))
            continue

        if want_jsonld:
            was_ok, _ = validate_jsonld(raw)
            now_ok, note = validate_jsonld(new)
            if was_ok and not now_ok:
                skipped.append((o["slug"], f"edit would BREAK JSON-LD — {note}"))
                continue
            if not was_ok:
                # Already broken before we touched it. Not ours to fix silently,
                # but say so loudly — this is a live SEO defect.
                print(f"  ⚠ {o['slug']}: JSON-LD was ALREADY invalid before this "
                      f"edit — {note}")

        n_subs = sum(n for _, n in applied)
        total_subs += n_subs
        planned.append((o, new, applied))
        print(f"  {o['slug']}  (id {o['id']}, {n_subs} substitutions)")
        for name, n in applied:
            print(f"      · {name} ×{n}")

    print(f"\n{len(planned)} objects to change, {total_subs} substitutions total")
    if skipped:
        print(f"\n[SKIPPED — untouched] {len(skipped)}")
        for slug, why in skipped:
            print(f"  ! {slug}: {why}")

    if not args.apply:
        print("\nDry run. Nothing was written. Re-run with --apply.")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for o, new, _ in planned:
        (backup_dir / f"{o['slug']}.{o['id']}.html").write_text(
            o["content"]["raw"], encoding="utf-8")
        r = session.post(f"{domain}/wp-json/wp/v2/{o['type']}s/{o['id']}",
                         json={"content": new}, headers=headers, timeout=60)
        if r.status_code in (200, 201):
            back = r.json().get("content", {}).get("raw", "")
            bad = [f for f in forbid_after if back and f.lower() in back.lower()]
            if bad:
                print(f"  ! {o['slug']}: written but forbidden string still present {bad}")
                fail += 1
            else:
                print(f"  ok {o['slug']}")
                ok += 1
        else:
            print(f"  FAIL {o['slug']}: HTTP {r.status_code} {r.text[:160]}")
            fail += 1

    print(f"\n{ok} written, {fail} failed. Backups: {backup_dir}")
    print("Purge the cache, then re-run the verifier.")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
