#!/usr/bin/env python3
"""verify_published_page.py — prove a freshly published page is actually right.

Read-only, no credentials, cache-busted. Run it immediately after publishing,
BEFORE publishing the next page, so a systemic problem is caught once instead
of on every page.

    python3 verify_published_page.py https://evelectric.pro/security-camera-installation/

Exit code = number of failures, so it drops into a shell &&-chain.

Each check exists because something went wrong once:
  shortcodes resolved     the drafts put [ev_review_count] inside JSON-LD and
                          nothing on the site had ever done that before
  no document wrapper     the publisher pushes the draft file whole; a stray
                          <!DOCTYPE>/<html>/<body> would land in post_content
  exactly one <h1>        the theme renders its own title band unless the block's
                          CSS suppresses it
  JSON-LD parses          an unresolved shortcode makes ratingValue a non-number
  images 200              CR-126
  no literal shortcode    if do_shortcode did not run, readers see the brackets
"""
import json, re, sys, time, urllib.error, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def get(url, timeout=45):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


def status(url):
    try:
        return get(url, 30).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR {e}"


def theme_title(html):
    import html as _h
    m = re.search(r'<h1[^>]*class="[^"]*dtr-page-title[^"]*"[^>]*>(.*?)</h1>', html, re.S | re.I)
    if not m:
        return None
    return _h.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1)))).strip()


def main():
    if len(sys.argv) < 2:
        print("usage: verify_published_page.py <url>")
        return 2
    url = sys.argv[1].rstrip("/") + "/"
    busted = f"{url}?cb={int(time.time())}"
    print(f"Fetching {busted}\n")
    try:
        r = get(busted)
    except urllib.error.HTTPError as e:
        print(f"FATAL: {e.code} on the page itself.")
        return 99
    html = r.read().decode("utf-8", "replace")
    print(f"HTTP {r.status}, {len(html):,} bytes\n")

    checks = []
    C = checks.append

    C(("page returns 200", r.status == 200))
    C(("no literal [ev_review_count] visible", "[ev_review_count]" not in html))
    C(("no literal [ev_review_rating] visible", "[ev_review_rating]" not in html))
    C(("no [ev_ shortcode of any kind left", not re.search(r"\[ev_[a-z_]+\]", html)))

    # the publisher pushes the file whole — none of this may reach post_content
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    inner = body.group(1) if body else html
    C(("no stray <!DOCTYPE> in the content", "<!DOCTYPE" not in inner.upper()[10:]))
    C(("no nested <html> tag", not re.search(r"<html\b", inner, re.I)))
    C(("no nested <body> tag", not re.search(r"<body\b", inner, re.I)))
    C(("no unrendered wp:html comment", "<!-- wp:html -->" not in html))

    # Every page on this site carries the theme's own <h1 class="dtr-page-title">,
    # which the block's CSS hides with display:none. Verified 2026-08-25 against
    # attic-fan-installation-fairfax-va and ev-charger-tysons-va: they both have it.
    # So count only the H1s that are actually visible to a reader.
    total_h1 = len(re.findall(r"<h1\b", html, re.I))
    hidden_h1 = len(re.findall(r'<h1[^>]*class="[^"]*dtr-page-title', html, re.I))
    visible_h1 = total_h1 - hidden_h1
    C((f"exactly one visible <h1> ({total_h1} total, {hidden_h1} hidden by the theme override)",
       visible_h1 == 1))
    t = theme_title(html)
    if t:
        print(f"  theme's hidden <h1> reads: {t!r}")
        if "|" in t or t.count("  ") > 3:
            print("    ^ that is the WP page title and it looks like an SEO title.")
            print("      Set the page title to match the visible H1; put the SEO title in AIOSEO.")

    C(("evp-corepage wrapper present", "evp-corepage" in html))

    blocks = re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    parsed, types, rating_ok = 0, set(), None
    for b in blocks:
        try:
            d = json.loads(b)
        except Exception:
            continue
        parsed += 1
        nodes = d.get("@graph", [d]) if isinstance(d, dict) else []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            t = n.get("@type")
            types.update(t if isinstance(t, list) else [t])
            ar = n.get("aggregateRating")
            if isinstance(ar, dict):
                rv, rc = str(ar.get("ratingValue", "")), str(ar.get("reviewCount", ""))
                rating_ok = bool(re.fullmatch(r"[\d.]+", rv) and re.fullmatch(r"\d+", rc))
                print(f"  aggregateRating → ratingValue={rv!r} reviewCount={rc!r}")
    C((f"all {len(blocks)} ld+json blocks parse", parsed == len(blocks) and blocks))
    C(("FAQPage in schema", "FAQPage" in types))
    C(("LocalBusiness in schema", "LocalBusiness" in types))
    C(("Service in schema", "Service" in types))
    C(("aggregateRating is numeric, not a shortcode", rating_ok is True))

    imgs = sorted(set(re.findall(r'<img[^>]+src="(https?://[^"]+)"', html)))
    bad_img = [(u, status(u)) for u in imgs]
    bad_img = [(u, c) for u, c in bad_img if c != 200]
    C((f"all {len(imgs)} images 200", not bad_img))
    for u, c in bad_img:
        print(f"    !! {c} {u}")

    sch = sorted(set(re.findall(r'"image"\s*:\s*"(https?://[^"]+)"', html)))
    bad_sch = [(u, status(u)) for u in sch]
    bad_sch = [(u, c) for u, c in bad_sch if c != 200]
    C((f"all {len(sch)} schema image URLs 200", not bad_sch))
    for u, c in bad_sch:
        print(f"    !! {c} {u}")

    txt = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I))
    for label, pat in (("no legal address", r"12116\s+Monument"),
                       ("no 'LLC'", r"\bLLC\b"),
                       ("no named competitor", r"Michael\s*&\s*Son|Beacon"),
                       ("no 'no service fee'", r"no service fee"),
                       ("no 'free visit'", r"free visit"),
                       ("no diagnostic price", r"\$3[45]0")):
        C((label, not re.search(pat, txt, re.I)))
    C(("NAP present and exact",
       bool(re.search(r"9488 Fairfax Blvd.*?Fairfax, VA 22031", txt, re.S))))
    C(("offer wording exact", "No trip fee, free estimates" in txt))

    print()
    fails = 0
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")
        fails += not ok
    print(f"\nFAILURES: {fails}")
    if fails:
        print("Do NOT publish the next page until these are understood.")
    return fails


if __name__ == "__main__":
    sys.exit(main())
