#!/usr/bin/env python3
"""wp_create_page.py — create an empty WordPress page so the publisher can find it.

publish-differentiation-wave.py resolves a page by slug and FATALs if none exists.
New pages therefore need a shell to publish INTO. This makes one: correct slug,
correct title, empty content, status=draft. It writes nothing else — no SEO fields,
no content — and it refuses to touch a slug that already exists.

    python3 wp_create_page.py --domain https://evelectric.pro \
        --slug bathroom-exhaust-fan-installation \
        --title "Bathroom Exhaust Fan Installation Fairfax VA | EV-Electric"

Auth via _load_secrets.py, same as the publisher. Nothing is hardcoded here.
"""
import argparse, json, sys, urllib.parse
from base64 import b64encode
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_secrets import load_wp_app_password

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--wp-user", default="oliver")
    ap.add_argument("--client-slug", default="ev-electric-services")
    a = ap.parse_args()

    pw = load_wp_app_password({"client_slug": a.client_slug})
    headers = {
        "Authorization": "Basic " + b64encode(f"{a.wp_user}:{pw}".encode()).decode(),
        "User-Agent": BROWSER_UA,
        "Content-Type": "application/json",
    }

    q = f"{a.domain}/wp-json/wp/v2/pages?slug={urllib.parse.quote(a.slug)}&status=any&_fields=id,slug,status,link"
    r = requests.get(q, headers=headers, timeout=40)
    r.raise_for_status()
    existing = r.json()
    if existing:
        p = existing[0]
        print(f"ALREADY EXISTS — slug '{a.slug}' is WP ID {p['id']} ({p['status']}) {p.get('link','')}")
        print("Nothing created. Use this ID in the publisher config.")
        return 0

    r = requests.post(f"{a.domain}/wp-json/wp/v2/pages", headers=headers, timeout=60,
                      json={"slug": a.slug, "title": a.title, "content": "", "status": "draft"})
    if r.status_code >= 400:
        print(f"FAIL {r.status_code} — {r.text[:400]}")
        return 1
    d = r.json()
    print(f"CREATED  slug '{a.slug}'  →  WP ID {d['id']}  (draft)  {d.get('link','')}")
    print(f"Put wp_id {d['id']} in the publisher config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
