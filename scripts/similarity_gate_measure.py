#!/usr/bin/env python3
"""CANONICAL Criterion B similarity measurement — EV Electric Services.

Pinned 2026-08-27 after three rounds produced four different numbers for the
same pair of pages. Every reviewer MUST run this script and paste its output.
⛔ Do not write your own. The gate threshold is meaningless without a fixed method.

v2 (2026-08-29): Added heading overlap (Check 2) and repeated-block detection
(Check 3). The original difflib check is now Check 1. All three are reported;
the verdict is FAIL if ANY check trips its threshold.

Usage:  python3 similarity_gate_measure.py <candidate.html> <sibling.html> [more-siblings.html ...]
"""
import re, html, difflib, sys, unicodedata

# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _strip_swappable_and_boilerplate(s):
    """Remove SWAPPABLE blocks (identical sitewide by design) before analysis."""
    s = re.sub(r'<!--\s*SWAPPABLE (OFFER|PRICING) BLOCK\s*-->.*?<!--\s*/SWAPPABLE \1 BLOCK\s*-->',
               ' ', s, flags=re.S | re.I)
    s = re.sub(r'<!--.*?-->', '', s, flags=re.S)
    s = re.sub(r'<script.*?</script>', '', s, flags=re.S | re.I)
    s = re.sub(r'<style.*?</style>', '', s, flags=re.S | re.I)
    return s

def sections(path):
    s = open(path, encoding='utf-8').read()
    s = _strip_swappable_and_boilerplate(s)
    out = []
    for m in re.finditer(r'<section\b.*?</section>', s, flags=re.S | re.I):
        raw = m.group(0)
        t = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
        t = ' '.join(t.split())
        h = re.search(r'<h2[^>]*>(.*?)</h2>', raw, flags=re.S)
        head = html.unescape(re.sub(r'<[^>]+>', '', h.group(1))).strip() if h else '(no h2)'
        if len(t.split()) > 25:
            out.append((head, t))
    return out

def headings(path):
    """Extract all H2 and H3 text from the page, after stripping swappable blocks."""
    s = open(path, encoding='utf-8').read()
    s = _strip_swappable_and_boilerplate(s)
    out = []
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', s, flags=re.S | re.I):
        raw = html.unescape(re.sub(r'<[^>]+>', '', m.group(1)))
        out.append(_normalise_heading(raw))
    return out

def _normalise_heading(text):
    """Normalise case, punctuation, HTML entities for heading comparison."""
    text = text.lower().strip()
    # Remove punctuation (keep alphanumeric and spaces)
    text = re.sub(r'[^\w\s]', '', text)
    # Collapse whitespace
    text = ' '.join(text.split())
    return text

# ---------------------------------------------------------------------------
# Check 1: difflib (original)
# ---------------------------------------------------------------------------

def ratio(a, b):
    """WORD-level, autojunk DISABLED. Both are load-bearing:
    char-level with autojunk on gave 4.41% and off gave 17.85% for the same pair."""
    return difflib.SequenceMatcher(None, a.split(), b.split(), autojunk=False).ratio() * 100

def ngrams(t, n):
    w = t.split()
    return set(tuple(w[i:i + n]) for i in range(len(w) - n + 1))

# ---------------------------------------------------------------------------
# Check 2: Heading overlap
# ---------------------------------------------------------------------------

def heading_overlap(headings_a, headings_b):
    """Compare H2/H3 heading sets. Returns (pct_shared, shared_list).
    pct_shared is relative to the smaller set (so a page with 5 headings
    sharing 3 with a page that has 20 headings scores 60%, not 15%)."""
    set_a = set(headings_a)
    set_b = set(headings_b)
    if not set_a or not set_b:
        return 0.0, []
    shared = set_a & set_b
    pct = len(shared) / min(len(set_a), len(set_b)) * 100
    return pct, sorted(shared)

# ---------------------------------------------------------------------------
# Check 3: Repeated-block detection
# ---------------------------------------------------------------------------

def find_repeated_blocks(text_a, text_b, min_words=25):
    """Find passages of min_words+ words that appear near-identically on both
    pages. Uses SequenceMatcher to find matching blocks, then merges adjacent
    ones and filters by length."""
    words_a = text_a.split()
    words_b = text_b.split()
    sm = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False)
    blocks = []
    for match in sm.get_matching_blocks():
        if match.size >= min_words:
            passage = ' '.join(words_a[match.a:match.a + match.size])
            blocks.append(passage)
    return blocks

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

HEADING_OVERLAP_THRESHOLD = 50  # % — flag if > half of headings are shared
DIFFLIB_THRESHOLD = 40          # % — existing gate

def main():
    cand, sibs = sys.argv[1], sys.argv[2:]
    A = sections(cand)
    fa = ' '.join(t for _, t in A)
    ha = headings(cand)
    print(f"CANDIDATE: {cand}  ({len(fa.split())} prose words in {len(A)} sections, {len(ha)} headings)\n")

    worst_page, worst_sec = 0.0, 0.0
    worst_heading_overlap = 0.0
    all_repeated_blocks = []
    any_fail = False

    for sp in sibs:
        B = sections(sp)
        fb = ' '.join(t for _, t in B)
        hb = headings(sp)
        r = ratio(fa, fb)
        worst_page = max(worst_page, r)

        print(f"── vs {sp}")

        # Check 1: difflib
        print(f"   CHECK 1 — PAGE difflib (word, autojunk=False): {r:.2f}%   [gate: < {DIFFLIB_THRESHOLD}%]")
        for n in (3, 5, 8):
            x, y = ngrams(fa, n), ngrams(fb, n)
            print(f"   {n}-gram: candidate-fraction {len(x & y) / max(1, len(x)) * 100:.2f}%  "
                  f"jaccard {len(x & y) / max(1, len(x | y)) * 100:.2f}%   [informational]")
        print("   per-section, BEST-MATCH pairing (not heading-string pairing):")
        for h, t in A:
            best = max(B, key=lambda x: ratio(t, x[1]))
            rs = ratio(t, best[1])
            worst_sec = max(worst_sec, rs)
            flag = "🔴 FAIL" if rs >= 40 else ("⚠️ watch" if rs >= 30 else "   ok   ")
            print(f"     {flag} {rs:5.1f}%  {h[:42]:44s} vs {best[0][:38]}")

        # Check 2: heading overlap
        h_pct, h_shared = heading_overlap(ha, hb)
        worst_heading_overlap = max(worst_heading_overlap, h_pct)
        h_flag = "🔴 FAIL" if h_pct > HEADING_OVERLAP_THRESHOLD else ("⚠️ watch" if h_pct > 30 else "   ok   ")
        print(f"\n   CHECK 2 — HEADING OVERLAP: {h_pct:.1f}%  ({len(h_shared)} shared of {len(ha)} vs {len(hb)})   "
              f"[gate: <= {HEADING_OVERLAP_THRESHOLD}%]   {h_flag}")
        if h_shared:
            for heading in h_shared:
                print(f"     - \"{heading}\"")

        # Check 3: repeated blocks
        blocks = find_repeated_blocks(fa, fb, min_words=25)
        all_repeated_blocks.extend(blocks)
        b_flag = "🔴 FLAG" if blocks else "   ok   "
        print(f"\n   CHECK 3 — REPEATED BLOCKS (>=25 words): {len(blocks)} found   {b_flag}")
        for i, block in enumerate(blocks):
            # Truncate display at 120 chars
            display = block[:120] + ("..." if len(block) > 120 else "")
            print(f"     [{i+1}] ({len(block.split())} words) {display}")

        print()

    # Verdict
    check1_fail = worst_page >= DIFFLIB_THRESHOLD or worst_sec >= DIFFLIB_THRESHOLD
    check2_fail = worst_heading_overlap > HEADING_OVERLAP_THRESHOLD
    check3_flag = len(all_repeated_blocks) > 0

    print(f"WORST page-level difflib: {worst_page:.2f}%   WORST section: {worst_sec:.2f}%")
    print(f"WORST heading overlap: {worst_heading_overlap:.1f}%")
    print(f"Repeated blocks found: {len(all_repeated_blocks)}")
    print()

    if check1_fail:
        print("CHECK 1: FAIL (difflib >= 40%)")
    else:
        print("CHECK 1: PASS")
    if check2_fail:
        print(f"CHECK 2: FAIL (heading overlap > {HEADING_OVERLAP_THRESHOLD}%)")
    else:
        print("CHECK 2: PASS")
    if check3_flag:
        print("CHECK 3: FLAG (repeated blocks found — review manually)")
    else:
        print("CHECK 3: PASS")

    overall = "FAIL" if (check1_fail or check2_fail) else "PASS"
    if overall == "PASS" and check3_flag:
        overall = "REVIEW (repeated blocks)"
    print(f"\nVERDICT: {overall}")

if __name__ == '__main__':
    main()
