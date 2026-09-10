"""Rebuild the FAQPage mainEntity from the VISIBLE <details> FAQ, verbatim.

Exists because an independent review found 4/8 (C4) and 8/8 (C6) schema answers
had drifted from the answers on the page. Hand-syncing is how they drifted.
Run this after ANY edit to the FAQ section, then re-run ev_page_gate.py.
"""
import json, re, sys, html as H

path = sys.argv[1]
s = open(path, encoding='utf-8').read()

# --- the visible FAQ: the LAST <section> containing <details> blocks ---
secs = re.findall(r'<section\b.*?</section>', s, re.S)
cands = [x for x in secs if 6 <= x.count('<details>') <= 8]
if len(cands) != 1:
    sys.exit(f"FATAL: expected exactly 1 section with 6-8 <details>, found {len(cands)}")
block = cands[0]

items = re.findall(r'<details><summary>(.*?)</summary><div>(.*?)</div></details>', block, re.S)
if not items:
    sys.exit("FATAL: no <details> pairs matched")

def totext(frag):
    frag = re.sub(r'<!--.*?-->', ' ', frag, flags=re.S)
    frag = re.sub(r'<[^>]+>', ' ', frag)
    return re.sub(r'\s+', ' ', H.unescape(frag)).strip()

qs = [(totext(q), totext(a)) for q, a in items]
print(f"{len(qs)} visible FAQ items")
if not 6 <= len(qs) <= 8:
    sys.exit(f"FATAL: FAQ must be 6-8 questions, found {len(qs)}")

# --- the JSON-LD graph ---
m = re.search(r'(<script type="application/ld\+json">)(.*?)(</script>)', s, re.S)
graph = json.loads(m.group(2))
nodes = graph['@graph']
faq = [n for n in nodes if n.get('@type') == 'FAQPage']
if len(faq) != 1:
    sys.exit(f"FATAL: expected exactly 1 FAQPage node, found {len(faq)}")

faq[0]['mainEntity'] = [
    {"@type": "Question", "name": q,
     "acceptedAnswer": {"@type": "Answer", "text": a}}
    for q, a in qs
]

new_json = json.dumps(graph, ensure_ascii=False, separators=(',', ':'))
s = s[:m.start()] + m.group(1) + "\n" + new_json + "\n" + m.group(3) + s[m.end():]
open(path, 'w', encoding='utf-8').write(s)

# --- verify ---
s2 = open(path, encoding='utf-8').read()
g2 = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', s2, re.S).group(1))
f2 = [n for n in g2['@graph'] if n.get('@type') == 'FAQPage'][0]
bad = [q for (q, a), n in zip(qs, f2['mainEntity'])
       if n['name'] != q or n['acceptedAnswer']['text'] != a]
print("types in @graph:", [n.get('@type') for n in g2['@graph']])
print(f"schema answers matching visible answers: {len(qs)-len(bad)}/{len(qs)}")
if bad:
    sys.exit("FATAL: mismatch after write")
