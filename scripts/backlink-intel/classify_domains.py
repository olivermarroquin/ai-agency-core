#!/usr/bin/env python3
"""
Backlink-intelligence domain classifier.

Takes raw DataForSEO referring-domain JSON files, extracts domains,
cross-references across competitors, and classifies each domain as
common / easy-grab / request / skip.

Usage:
    python3 classify_domains.py \
        --input-dir <path-to-raw-json-files> \
        --output <path-to-opportunity-matrix.json> \
        --ev-existing google.com,yelp.com,thumbtack.com,mapquest.com \
        --ev-pending apple.com,bbb.org,foursquare.com \
        --national-franchise mrelectric.com
"""

import json
import os
import argparse


# --- Known patterns for classification ---

SKIP_PATTERNS = [
    '.ru', '.com.au', 'blogspot', 'wordpress.com', 'getwebsiteworth', 'read.org.in',
    'willettonuniforms', 'flokii.com', 'intently.co', 'lantern.llc',
    'sites.google.com', '.xyz', '.party', '.bid', '.win', '.gdn',
    'web.app', 'pages.dev', 'tumblr.com', 'medium.com/@', 'weebly.com',
    'wixsite.com', 'ameblo.jp', 'livejournal.com',
    # Foreign TLDs irrelevant to US-local
    'seomuda.id', 'piscatore.dk', 'torun-stolarz.pl', 'synara.ar',
    'way2check.cv', 'urls-shortener.eu', 'shortenurls.eu',
    'australianwebdirectory.shop', 'australianwebdirectory.pro',
    # SEO tool / analytics
    'sitelike.org', 'siteprice.org', 'seolium.com',
    # Scraper / auto-generated / spam
    'booksreadr.org', 'globalecommerce.org', 'musweb.org',
    'globalcocolk.com', 'ododuorpremium.com', 'example3.com',
    'anchorurl.cloud', 'bye.fyi', 'alljobs.info',
    'imagetou.com', 'epictures.homes', 'drjack.world', 'fixithomestead.com',
    'robuta.com', 'universalelectricalllc.com', 'linkcentre.com', 'bizhwy.com',
    'permitdeck.com', 'bluecantera.com', 'tunca.org', 'sergechel.info',
    'lookfindcall.com', 'z1biz.com',
    'qdexx.com', 'viesearch.com', 'openshopsusa.com',
    'americatop10.com', 'spaziovet.net', 'localrepairsnow.com',
    'hoursfinder.com',
    # Generic junk directories
    'directory9.biz', 'directory8.org', '1directory.org',
    'canuck.biz', 'craigslistdirectory.net', 'altiusdirectory.com',
    'justdirectory.org', 'neustarlocaleze.biz',
    'topsiterankdirectory.com', 'backlinkboostdirectory.com',
    'seobacklinkdirectory.com', 'yelpdirectory.com',
    'eliterankdirectory.com', 'smartwebdirectory.com', 'simplewebdirectory.com',
    '1stdirectory.co.uk',
]

EASY_PATTERNS = [
    'yellowpages', 'superpages', 'dexknows', 'hotfrog', 'chamberofcommerce',
    'porch.com', 'buildzoom', 'manta.com', 'citysearch', 'bbb.org',
    'yelp.com', 'angi.com', 'houzz.com', 'thumbtack', 'homeadvisor',
    'facebook.com', 'foursquare', 'mapquest', 'apple.com', 'bing.com',
    'nextdoor', 'angieslist', 'ezlocal', 'findelectricalcontractors',
    'localelectricians', 'thebuildermarket', 'n49.com', 'localitybiz',
    '2findlocal', 'proiq.com', 'regionaldirectory',
    'sanleandrochamber', 'brownbook', 'showmelocal', 'callupcontact',
    'cylex', 'merchantcircle',
]

REQUEST_PATTERNS = [
    'todayshomeowner', 'patch.com', 'washingtonian', 'gazette', 'times',
    'post', 'news', 'press', 'journal', 'magazine', 'media', 'blog',
    'review', 'article', 'electrician.com', 'thisoldhouse', 'hgtv',
    'bobvila', 'familyhandyman',
]


def load_referring_domains(filepath):
    """Extract domain names + backlink counts from a DataForSEO referring-domains JSON file."""
    with open(filepath) as f:
        raw = json.load(f)

    # Handle both direct API response and the MCP wrapper format [{type, text}]
    if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and 'text' in raw[0]:
        data = json.loads(raw[0]['text'])
    elif isinstance(raw, dict):
        data = raw
    else:
        return {}

    items = data.get('items', [])
    return {item['domain']: item.get('backlinks', 0) for item in items if 'domain' in item}


def classify_domain(domain, competitor_count_local, total_bl):
    """Classify a single domain. Returns one of: skip, easy-grab, common, request."""
    d = domain.lower()

    # Skip check first
    for pat in SKIP_PATTERNS:
        if pat in d:
            return 'skip'

    # Easy-grab directories/citations
    for pat in EASY_PATTERNS:
        if pat in d:
            return 'easy-grab'

    # Common: 2+ local competitors (excluding national franchise)
    if competitor_count_local >= 2:
        return 'common'

    # Request patterns
    for pat in REQUEST_PATTERNS:
        if pat in d:
            return 'request'

    # Default: low-BL unknown = skip; otherwise request
    if total_bl <= 2:
        return 'skip'
    return 'request'


def build_matrix(input_dir, ev_existing, ev_pending, national_franchise=None):
    """Build the full opportunity matrix from raw JSON files in input_dir."""

    # Load all competitor profiles
    competitors = {}
    for fname in os.listdir(input_dir):
        if fname.startswith('referring-domains-') and fname.endswith('.json'):
            comp_name = fname.replace('referring-domains-', '').replace('.json', '')
            filepath = os.path.join(input_dir, fname)
            domains = load_referring_domains(filepath)
            if domains:
                competitors[comp_name] = domains

    # Cross-reference: for each unique domain, which competitors link to it
    all_domains = {}
    for comp_name, domains in competitors.items():
        for domain, bl_count in domains.items():
            if domain not in all_domains:
                all_domains[domain] = {'competitors': {}, 'total_bl': 0}
            all_domains[domain]['competitors'][comp_name] = bl_count
            all_domains[domain]['total_bl'] += bl_count

    # Classify each domain
    results = []
    for domain, info in all_domains.items():
        comp_linking = list(info['competitors'].keys())

        # Count local competitors (exclude national franchise)
        local_comps = [c for c in comp_linking
                       if not national_franchise or national_franchise not in c]
        local_count = len(local_comps)

        cls = classify_domain(domain, local_count, info['total_bl'])

        ev_has = any(ex in domain for ex in ev_existing)
        ev_pend = any(ex in domain for ex in ev_pending)

        results.append({
            'domain': domain,
            'class': cls,
            'competitors_linking': comp_linking,
            'competitor_count': len(comp_linking),
            'local_competitor_count': local_count,
            'total_backlinks_across_competitors': info['total_bl'],
            'ev_already_has': ev_has,
            'ev_pending': ev_pend,
            'notes': '',
        })

    # Sort: common first (by local_competitor_count desc, then BL desc), then easy-grab, request, skip
    class_order = {'common': 0, 'easy-grab': 1, 'request': 2, 'skip': 3}
    results.sort(key=lambda d: (
        class_order.get(d['class'], 9),
        -d.get('local_competitor_count', 0),
        -d.get('total_backlinks_across_competitors', 0)
    ))

    return results


def main():
    parser = argparse.ArgumentParser(description='Classify backlink referring domains')
    parser.add_argument('--input-dir', required=True, help='Directory with referring-domains-*.json files')
    parser.add_argument('--output', required=True, help='Output opportunity-matrix.json path')
    parser.add_argument('--ev-existing', default='', help='Comma-separated domains EV already has')
    parser.add_argument('--ev-pending', default='', help='Comma-separated domains EV has pending')
    parser.add_argument('--national-franchise', default=None, help='Domain to exclude from local competitor count')
    args = parser.parse_args()

    ev_existing = set(args.ev_existing.split(',')) if args.ev_existing else set()
    ev_pending = set(args.ev_pending.split(',')) if args.ev_pending else set()

    results = build_matrix(args.input_dir, ev_existing, ev_pending, args.national_franchise)

    # Count classes
    classes = {}
    for d in results:
        c = d['class']
        classes[c] = classes.get(c, 0) + 1

    output = {
        'generated': '2026-06-24',
        'competitors_analyzed': sorted(set(
            c for d in results for c in d['competitors_linking']
        )),
        'ev_existing_citations': sorted(ev_existing),
        'ev_pending_citations': sorted(ev_pending),
        'domains': results,
    }

    with open(args.output, 'w') as f:
        json.dump(output, f, indent=2)

    print(f'Total domains: {len(results)}')
    for c in ['common', 'easy-grab', 'request', 'skip']:
        print(f'  {c}: {classes.get(c, 0)}')

    print(f'\nWritten to {args.output}')


if __name__ == '__main__':
    main()
