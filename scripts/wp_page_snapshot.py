#!/usr/bin/env python3
"""
wp_page_snapshot — take, verify and restore complete page backups on a WordPress site.

Built 2026-08-16, after discovering that the file we believed was a homepage backup
(`backups/ev-electric-services/2026-06-08_ev-homepage-8-backup.json`) could not restore
anything: it was an unauthenticated REST GET holding `content.rendered` — the page
builder's OUTPUT — and only the six publicly-exposed meta keys. `_elementor_data` was
not in it.

A backup that cannot restore is worse than no backup, because you act as if you are safe.

Commands
--------
  backup   <client-slug> <id> [id ...]   snapshot pages, then verify each one
  verify   <snapshot.json> [...]         re-check an existing snapshot on disk
  restore  <snapshot.json> --confirm     restore a page from its snapshot
  selftest <client-slug> <id>            backup → restore-in-place → confirm identical
  doctor   <client-slug> [id]            check connection, auth, role and plugin, layer by layer

`selftest` is the one that matters. It proves the restore path works on this specific
site BEFORE anything destructive happens, which is the only way to know a backup is real.

Requires the Keelworks Page Snapshot Bridge plugin on the target site. Without it, core
REST cannot read protected meta and this tool will say so rather than write a false
sense of safety.

Usage:
    cd ~/workspace/repos/ai-agency-core/scripts
    python3 wp_page_snapshot.py selftest ev-electric-services 103
    python3 wp_page_snapshot.py backup   ev-electric-services 8 97 95 103
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _load_secrets import load_wp_app_password  # engine-provided; key-file first, tier-3 fallback

BACKUP_ROOT = Path.home() / "workspace" / "backups"
SNAPSHOT_ROUTE = "{base}/wp-json/keelworks/v1/page-snapshot/{id}"
RESTORE_ROUTE = "{base}/wp-json/keelworks/v1/page-restore/{id}"
CORE_ROUTE = "{base}/wp-json/wp/v2/pages/{id}?context=edit"

# A snapshot missing any of these cannot rebuild a page-builder page.
REQUIRED_TOP = ("post", "meta", "checksums", "post_id")
BUILDER_META = ("_elementor_data", "_elementor_edit_mode", "_elementor_version")

# Meta keys to EXCLUDE from restore writes. The page-restore route's add_post_meta
# calls maybe_serialize(), which double-wraps strings that look like serialized PHP
# data (e.g. "a:0:{}" → "s:6:"a:0:{}";"). Each restore cycle nests one layer deeper.
# AIOSEO reads these from its own aioseo_posts table (via get_post_metadata filter),
# not from wp_postmeta, so the postmeta value is invisible to AIOSEO anyway.
# See known-issue-restore-double-wrap.md for the full write-up.
RESTORE_SKIP_META = {"_aioseo_keywords", "_aioseo_og_article_tags"}


def _client_config(slug: str) -> dict[str, Any]:
    # ev-electric-services -> ev-electric.config.json, per the existing convention
    here = Path(__file__).parent
    for name in (f"{slug}.config.json", f"{slug.replace('-services','')}.config.json"):
        cfg_path = here / name
        if cfg_path.exists():
            break
    if not cfg_path.exists():
        raise SystemExit(f"No client config at {cfg_path}")
    return json.loads(cfg_path.read_text())


def _auth(cfg: dict) -> tuple[str, str]:
    """Return (base_url, "user:password") for curl -u."""
    base = cfg.get("wp_base_url") or cfg.get("domain") or cfg.get("site_url")
    if not base:
        raise SystemExit("Client config has no 'wp_base_url'.")
    user = cfg.get("wp_username") or cfg.get("wp_user") or "oliver"
    return base.rstrip("/"), f"{user}:{load_wp_app_password(cfg)}"


# ---------------------------------------------------------------- transport
#
# ⚠️ This site sits behind a WAF (Hostinger/LiteSpeed) that returns a bodyless 403 to
# python-requests. The same call through curl succeeds. That is a known trait of this
# workspace — `.kos/scripts/ev_schema_inject_v2.py` carries the note "Uses curl
# subprocess to avoid WAF 403". Do not swap this back to requests.

class HttpResult:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)


def _curl(url: str, userpass: str, method: str = "GET", payload: str | None = None,
          timeout: int = 180) -> HttpResult:
    cmd = [
        "curl", "-sS", "-w", "\n%{http_code}",
        "-u", userpass,
        "-H", "Content-Type: application/json",
        # A browser-ish UA keeps the WAF from tripping on the default curl agent.
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "-X", method, url,
    ]
    if payload is not None:
        cmd += ["--data-binary", "@-"]

    r = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return HttpResult(0, f"curl exit {r.returncode}: {r.stderr[:300]}")

    out = r.stdout.rsplit("\n", 1)
    if len(out) == 2 and out[1].strip().isdigit():
        return HttpResult(int(out[1].strip()), out[0])
    return HttpResult(0, r.stdout)


def _explain(res: HttpResult, url: str) -> str:
    """Turn a bare status into something that names the likely cause."""
    if res.status_code == 0:
        return f"transport failure — {res.text[:200]}"
    if res.status_code == 401:
        return ("401 — credentials rejected. Check the application password in tier-3 and that "
                "`wp_username` matches the WP user it belongs to.")
    if res.status_code == 403 and not res.text.strip():
        return ("403 with an EMPTY body — this is the WAF, not WordPress. WordPress always returns "
                "a JSON error code. If this persists through curl, the WAF rule needs a look in "
                "hPanel, or the request needs to come from an allow-listed IP.")
    if res.status_code == 403 and "rest_forbidden" in res.text:
        return ("403 rest_forbidden — authenticated, but the user lacks `edit_others_pages`. "
                "The account must be an Administrator or Editor.")
    if res.status_code == 404 and "rest_no_route" in res.text:
        return ("404 rest_no_route — the Page Snapshot Bridge plugin is not installed or not "
                "activated on this site.")
    return f"{res.status_code} — {' '.join(res.text.split())[:220]}"


# ---------------------------------------------------------------- verify

def verify_snapshot(snap: dict, path: str = "<memory>") -> tuple[bool, list[str]]:
    """Assert this snapshot could actually rebuild the page. Returns (ok, problems)."""
    problems: list[str] = []

    for key in REQUIRED_TOP:
        if key not in snap:
            problems.append(f"missing top-level key '{key}'")

    post = snap.get("post", {})
    if "post_content" not in post:
        problems.append("post.post_content absent — nothing to restore into the content field")
    if post.get("post_content") is None:
        problems.append("post.post_content is null")

    if "content" in snap and "post" not in snap:
        problems.append(
            "looks like a raw core REST GET, not a snapshot — 'content.rendered' is the "
            "builder's OUTPUT and cannot rebuild the page"
        )

    meta = snap.get("meta", {})
    if not isinstance(meta, dict) or not meta:
        problems.append("meta is empty — protected keys were not captured")

    builder = snap.get("builder", {})
    if builder.get("is_elementor"):
        for k in BUILDER_META:
            if k not in meta:
                problems.append(f"page is Elementor-built but '{k}' is missing from meta")
        data = meta.get("_elementor_data")
        if isinstance(data, str):
            if len(data) < 100:
                problems.append(f"_elementor_data is only {len(data)} bytes — suspiciously small")
            try:
                json.loads(data)
            except Exception as exc:
                problems.append(f"_elementor_data is not valid JSON ({exc}) — a restore would blank the page")

    sums = snap.get("checksums", {})
    if not sums.get("post_content"):
        problems.append("no post_content checksum — a restore could not be verified")

    ok = not problems
    mark = "✅" if ok else "❌"
    print(f"{mark} {path}")
    for p in problems:
        print(f"     - {p}")
    if ok:
        size = len(str(meta.get("_elementor_data", "")))
        print(f"     {len(meta)} meta keys · post_content {len(post.get('post_content') or '')} bytes"
              + (f" · _elementor_data {size:,} bytes" if size else ""))
    return ok, problems


# ---------------------------------------------------------------- backup

def backup(slug: str, ids: list[int]) -> int:
    cfg = _client_config(slug)
    base, auth = _auth(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    outdir = BACKUP_ROOT / slug / f"page-snapshots-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    manifest, failures = [], 0
    for pid in ids:
        print(f"\n── page {pid}")
        r = _curl(SNAPSHOT_ROUTE.format(base=base, id=pid), auth)

        if r.status_code == 404 and "rest_no_route" in r.text:
            print("   ✗ snapshot route not found — the Page Snapshot Bridge plugin is not installed.")
            print("     Falling back to core REST context=edit, which CANNOT capture _elementor_data.")
            rc = _curl(CORE_ROUTE.format(base=base, id=pid), auth)
            fallback = outdir / f"page-{pid}-INCOMPLETE.json"
            fallback.write_text(json.dumps(rc.json(), indent=2))
            print(f"     wrote {fallback.name} — ⚠️ NOT a restorable backup")
            failures += 1
            continue

        if r.status_code >= 400 or r.status_code == 0:
            print(f"   ✗ {_explain(r, SNAPSHOT_ROUTE.format(base=base, id=pid))}")
            failures += 1
            continue

        snap = r.json()
        path = outdir / f"page-{pid}-{snap['post']['post_name'] or 'page'}.json"
        path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        ok, _ = verify_snapshot(snap, path.name)
        manifest.append({
            "post_id": pid,
            "slug": snap["post"]["post_name"],
            "file": path.name,
            "is_elementor": snap.get("builder", {}).get("is_elementor"),
            "elementor_data_bytes": snap.get("builder", {}).get("elementor_data_bytes"),
            "meta_keys": len(snap.get("meta", {})),
            "verified": ok,
        })
        if not ok:
            failures += 1

    (outdir / "MANIFEST.json").write_text(json.dumps({
        "client": slug, "site": base, "taken_at": stamp, "pages": manifest,
    }, indent=2), encoding="utf-8")

    print(f"\n{outdir}")
    print(f"{len(manifest)} snapshot(s), {failures} problem(s)")
    if failures:
        print("\n⚠️  DO NOT treat this as a safety net until every page verifies.")
    return 1 if failures else 0


# ---------------------------------------------------------------- restore

def restore(path: Path, confirm: bool, dry_run: bool) -> int:
    snap = json.loads(path.read_text())
    ok, _ = verify_snapshot(snap, path.name)
    if not ok:
        print("\nRefusing to restore from a snapshot that does not verify.")
        return 1

    slug_guess = path.parts[path.parts.index("backups") + 1] if "backups" in path.parts else None
    if not slug_guess:
        raise SystemExit("Could not infer client slug from the path; run from backups/<slug>/…")

    cfg = _client_config(slug_guess)
    base, auth = _auth(cfg)
    pid = snap["post_id"]

    body = dict(snap)
    # Strip meta keys that the restore route double-wraps via maybe_serialize
    if "meta" in body:
        skipped = [k for k in RESTORE_SKIP_META if k in body["meta"]]
        for k in skipped:
            del body["meta"][k]
        if skipped:
            print(f"   Skipped {len(skipped)} meta key(s) to avoid double-wrap: {', '.join(skipped)}")
    body["dry_run"] = dry_run
    if not dry_run:
        body["confirm"] = str(pid)

    r = _curl(RESTORE_ROUTE.format(base=base, id=pid), auth, "POST", json.dumps(body))
    if r.status_code >= 400 or r.status_code == 0:
        print(f"✗ {_explain(r, RESTORE_ROUTE.format(base=base, id=pid))}")
        return 1

    res = r.json()
    print(json.dumps(res, indent=2))
    if not dry_run and not res.get("all_ok"):
        print("\n⚠️  Restore reported a checksum mismatch — inspect before trusting the page.")
        return 1
    return 0


# ---------------------------------------------------------------- selftest

def selftest(slug: str, pid: int) -> int:
    """Prove the restore path works on this site, using the page as its own source.

    Snapshot → restore that exact snapshot in place → snapshot again → compare.
    Nothing changes, but every code path a real restore uses gets exercised.
    """
    cfg = _client_config(slug)
    base, auth = _auth(cfg)

    print(f"1/4  snapshot page {pid} …")
    a = _curl(SNAPSHOT_ROUTE.format(base=base, id=pid), auth)
    if a.status_code != 200:
        print(f"   ✗ {_explain(a, SNAPSHOT_ROUTE.format(base=base, id=pid))}")
        return 1
    snap = a.json()
    if not verify_snapshot(snap, f"page-{pid} (live)")[0]:
        return 1

    # Strip meta keys that the restore route double-wraps
    skipped = [k for k in RESTORE_SKIP_META if k in snap.get("meta", {})]
    for k in skipped:
        del snap["meta"][k]
    if skipped:
        print(f"   Skipped {len(skipped)} meta key(s) to avoid double-wrap: {', '.join(skipped)}")

    print("2/4  dry-run restore …")
    d = _curl(RESTORE_ROUTE.format(base=base, id=pid), auth, "POST",
              json.dumps({**snap, "dry_run": True}))
    if d.status_code != 200:
        print(f"   ✗ {_explain(d, RESTORE_ROUTE.format(base=base, id=pid))}")
        return 1
    print(f"   would restore {len(d.json()['would_restore']['meta_keys'])} meta keys")

    print("3/4  live restore of the identical snapshot …")
    w = _curl(RESTORE_ROUTE.format(base=base, id=pid), auth, "POST",
              json.dumps({**snap, "confirm": str(pid)}))
    if w.status_code != 200:
        print(f"   ✗ {_explain(w, RESTORE_ROUTE.format(base=base, id=pid))}")
        return 1
    print(f"   {json.dumps(w.json()['verify'])}")

    print("4/4  re-snapshot and compare …")
    b = _curl(SNAPSHOT_ROUTE.format(base=base, id=pid), auth).json()
    same_content = snap["checksums"]["post_content"] == b["checksums"]["post_content"]
    same_builder = snap["checksums"]["_elementor_data"] == b["checksums"]["_elementor_data"]

    print(f"\n   post_content identical    : {'✅' if same_content else '❌'}")
    print(f"   _elementor_data identical : {'✅' if same_builder else '❌'}")
    if same_content and same_builder:
        print("\n✅ Restore path verified end to end on this site. Backups here are real.")
        return 0
    print("\n❌ Round-trip changed the page. Do NOT rely on restore until this is fixed.")
    print("   Most likely cause: wp_slash() handling on _elementor_data.")
    return 1


# ---------------------------------------------------------------- doctor

def doctor(slug: str, probe_id: int = 103) -> int:
    """Walk the connection one layer at a time so a failure names itself."""
    cfg = _client_config(slug)
    base, auth = _auth(cfg)
    user = auth.split(":", 1)[0]
    print(f"site     {base}")
    print(f"user     {user}")
    print(f"password {'resolved (' + str(len(auth.split(':', 1)[1])) + ' chars)' if len(auth.split(':', 1)) > 1 and auth.split(':', 1)[1] else 'MISSING'}")
    print()

    ok = True

    print("1  core REST reachable (unauthenticated) …")
    r = _curl(f"{base}/wp-json/", ":")
    if r.status_code == 200:
        print("   ✅ 200")
    else:
        print(f"   ❌ {_explain(r, base)}")
        ok = False

    print("2  authentication + role …")
    r = _curl(f"{base}/wp-json/wp/v2/users/me?context=edit", auth)
    if r.status_code == 200:
        me = r.json()
        roles = me.get("roles", [])
        caps = me.get("capabilities", {}) or {}
        can = bool(caps.get("edit_others_pages"))
        print(f"   ✅ authenticated as '{me.get('slug')}' roles={roles}")
        print(f"   {'✅' if can else '❌'} edit_others_pages = {can}"
              + ("" if can else "  ← the snapshot routes require this"))
        ok = ok and can
    else:
        print(f"   ❌ {_explain(r, base)}")
        ok = False

    print("3  Page Snapshot Bridge plugin …")
    r = _curl(SNAPSHOT_ROUTE.format(base=base, id=probe_id), auth)
    if r.status_code == 200:
        snap = r.json()
        b = snap.get("builder", {})
        print(f"   ✅ installed — page {probe_id} '{snap['post']['post_name']}': "
              f"{len(snap.get('meta', {}))} meta keys, elementor={b.get('is_elementor')}, "
              f"_elementor_data={b.get('elementor_data_bytes', 0):,} bytes")
    else:
        print(f"   ❌ {_explain(r, SNAPSHOT_ROUTE.format(base=base, id=probe_id))}")
        ok = False

    print("4  AIOSEO bridge plugin (needed for the title/meta write) …")
    r = _curl(f"{base}/wp-json/keelworks/v1/aioseo-meta/{probe_id}", auth, "POST", "{}")
    if r.status_code in (200, 400):
        print("   ✅ route present")
    elif r.status_code == 404 and "rest_no_route" in r.text:
        print("   ⚠️  not installed — title/meta writes will need it")
    else:
        print(f"   ⚠️  {_explain(r, base)}")

    print()
    print("✅ Ready — run `selftest` next." if ok else "❌ Fix the ❌ lines above before backing anything up.")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup"); b.add_argument("slug"); b.add_argument("ids", nargs="+", type=int)
    v = sub.add_parser("verify"); v.add_argument("files", nargs="+")
    r = sub.add_parser("restore"); r.add_argument("file"); r.add_argument("--confirm", action="store_true"); r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("selftest"); s.add_argument("slug"); s.add_argument("id", type=int)
    dr = sub.add_parser("doctor"); dr.add_argument("slug"); dr.add_argument("id", nargs="?", type=int, default=103)

    a = ap.parse_args()
    if a.cmd == "backup":
        return backup(a.slug, a.ids)
    if a.cmd == "verify":
        bad = 0
        for f in a.files:
            if not verify_snapshot(json.loads(Path(f).read_text()), f)[0]:
                bad += 1
        return 1 if bad else 0
    if a.cmd == "restore":
        if not (a.confirm or a.dry_run):
            print("Pass --dry-run first, then --confirm.")
            return 1
        return restore(Path(a.file), a.confirm, a.dry_run)
    if a.cmd == "selftest":
        return selftest(a.slug, a.id)
    if a.cmd == "doctor":
        return doctor(a.slug, a.id)
    return 1


if __name__ == "__main__":
    sys.exit(main())
