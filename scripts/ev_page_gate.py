#!/usr/bin/env python3
"""
ev_page_gate.py — deterministic pre-publish gate for EV-Electric Services pages.

Runs against an ASSEMBLED, wp:html-wrapped draft (not a section fragment).
Strips <style> blocks and HTML comments before extracting text, so CSS and
authoring notes cannot mask or fake a check.

    python3 ev_page_gate.py <draft.html> --reviewer "Ben Olson" [--forbid NAME=REGEX ...]

Every row encodes a rule the operator stated, or a defect we actually shipped:

  PRICING — allowlist, not a ban. Ahmad approved publishing prices 2026-08-24.
  Source of truth: clients/_active/ev-electric-services/pricing-approved-2026-08-24.md
  Approved: panel replacement $5,000-$7,500 (same amperage only) ·
            EV charger install $300-$2,400 (unit NOT included) · GFCI $100-$160
  Never:    the $350/$349 diagnostic (Ahmad withdrew it) · the planned $70-75
            service fee · any invented panel-UPGRADE band
  Enforced: every figure must be on the allowlist; the EV figure must be
            accompanied by the unit exclusion; the panel figure must be
            accompanied by the replacement-vs-upgrade distinction; figures must
            be year-stamped and must sit inside a SWAPPABLE PRICING BLOCK so
            they come out in one edit when the 90-day test ends.

  no 'no service fee' / 'free visit' / first-visit inspection   G-02, 2026-08-21
  no tax credit / rebate / incentive claim                      A4, 2026-08-23
  no pricing promise                                            A3/A4 city grid, 2026-08-23
  no invented reviewers                                         G-04, 2026-08-22
  exactly one <h1>                                              Waves 1-3 kicker-H1 defect
  bg strictly alternates                                        A2 v2 white-white collision
  every evp-section has inner                                   splice-integrity check

Exit code = number of failures, so it drops straight into a shell &&-chain.
"""
import re, html as H, sys, argparse


def _all_ldjson_parses(body):
    import json as _json
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    if not blocks:
        return False
    for b in blocks:
        try:
            _json.loads(b)
        except Exception:
            return False
    return True


def _strip_ldjson(body):
    return re.sub(r'<script type="application/ld\+json">.*?</script>', ' ', body, flags=re.S)


def _loose_prices(body):
    """Dollar figures in VISIBLE COPY that are not inside a SWAPPABLE PRICING BLOCK span.

    JSON-LD is excluded deliberately. An HTML comment cannot wrap part of a JSON graph —
    `<!-- -->` inside <script type="application/ld+json"> makes the whole block
    unparseable, which is a worse defect than the one it tries to prevent (learned the
    hard way, 2026-08-24). Structured data is kept removable a different way: the
    price-bearing nodes live in their OWN ld+json block, marked from outside, and
    `_schema_survives_price_removal` proves the rest still parses without it.

    The opening marker must be the exact token `<!-- SWAPPABLE PRICING BLOCK -->`;
    appending an explanation to that same comment makes every span matcher miss it.
    """
    vis = _strip_ldjson(body)
    spans = [(m.start(), m.end()) for m in re.finditer(
        r'<!-- SWAPPABLE PRICING BLOCK -->.*?<!-- /SWAPPABLE PRICING BLOCK -->', vis, re.S)]
    return [m.group(0) for m in re.finditer(r'\$[\d,]+', vis)
            if not any(a <= m.start() < z for a, z in spans)]


def _schema_survives_price_removal(body):
    """Simulate ending the 90-day pricing test: strip every marked span, then require
    that a valid ld+json graph is still present and that no FAQ question was taken with
    it. A reversibility guarantee that breaks the page is not a guarantee."""
    import json as _json
    stripped = re.sub(r'<!-- SWAPPABLE PRICING BLOCK -->.*?<!-- /SWAPPABLE PRICING BLOCK -->',
                      '', body, flags=re.S)
    if body.count('<summary>') != stripped.count('<summary>'):
        return False
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', stripped, re.S)
    if not blocks:
        return False
    try:
        g = _json.loads(blocks[0])
    except Exception:
        return False
    return {'FAQPage', 'LocalBusiness'} <= {n.get('@type') for n in g.get('@graph', [])}


def _check_reviewer(body, txt, expect_reviewer):
    """Check reviewer name — scoped to pages with .evp-review component.

    Returns True (pass) in three cases:
      - No .evp-review component on the page → NOT APPLICABLE (pass)
      - .evp-review present AND expect_reviewer is set AND name found → PASS
    Returns False (fail) when:
      - .evp-review present but no --reviewer was provided
      - .evp-review present but reviewer name not found in text
    """
    has_review_component = 'evp-review' in body
    if not has_review_component:
        return True  # NOT APPLICABLE — no review section on this page
    if not expect_reviewer:
        return False  # review component present but no reviewer name given
    return expect_reviewer in txt


def _check_offer_pairing(txt):
    """Check that every 'no trip fee' occurrence is paired with 'free estimates'
    within the same sentence or the immediately adjacent one.

    Returns (pass: bool, unpaired_positions: list[int]).
    """
    lo = txt.lower()
    # Split into sentences (crude but sufficient for this gate)
    sentences = re.split(r'(?<=[.!?])\s+', lo)

    # Find all sentence indices containing 'no trip fee'
    unpaired: list[int] = []
    for i, sent in enumerate(sentences):
        if 'no trip fee' not in sent:
            continue
        # Check this sentence and immediately adjacent ones
        window = []
        if i > 0:
            window.append(sentences[i - 1])
        window.append(sent)
        if i + 1 < len(sentences):
            window.append(sentences[i + 1])
        combined = ' '.join(window)
        if 'free estimate' not in combined:
            unpaired.append(i)

    return len(unpaired) == 0, unpaired


def gate(path, label, expect_reviewer, forbid_extra=()):
    s = open(path, encoding='utf-8').read()
    body = s[s.index('</style>'):] if '</style>' in s else s
    vis  = re.sub(r'<!--.*?-->', ' ', body, flags=re.S)          # drop HTML comments
    txt  = H.unescape(re.sub(r'\s+',' ', re.sub(r'<[^>]+>',' ', vis)))
    secs = re.findall(r'<section class="evp-section evp-section--(\w+)"', body)
    # ---- pricing: allowlist, not a ban (Ahmad approved 2026-08-24) ----------
    # Source of truth: clients/_active/ev-electric-services/pricing-approved-2026-08-24.md
    # Any dollar figure on a page must be one of these, exactly.
    APPROVED = {'$5,000', '$7,500', '$300', '$2,400', '$100', '$160', '$135', '$250'}
    found    = set(re.findall(r'\$[\d,]+', txt))
    unapprvd = found - APPROVED
    lo       = txt.lower()
    # The EV install number is meaningless without the exclusion; a reader who
    # sees $300 and expects a working charger leaves a one-star review.
    ev_shown = bool({'$300', '$2,400'} & found)
    ev_caveat = ('charger unit' in lo and re.search(r'not included|excluded|separate', lo)) \
                or 'installation only' in lo
    # $5,000-$7,500 is a SAME-AMPERAGE replacement, not an upgrade. Printing it
    # on an upgrade page without the distinction misprices the job Ahmad sells.
    panel_shown = bool({'$5,000', '$7,500'} & found)
    panel_caveat = ('replacement' in lo) and re.search(r'same amperage|amps as it is|keeping the amps|200[- ]?amp to 200', lo)
    # $100-$135 is PER ALARM, not per visit. A whole-house set is 5-8 alarms;
    # a reader who sees $135 and expects that as the job total leaves a one-star review.
    alarm_shown = '$135' in found
    alarm_caveat = 'per alarm' in lo
    # $100-$250 is PER FIXTURE. A six-can recessed lighting room at $250 each
    # is $1,500; a reader expecting $250 total leaves a one-star review.
    fixture_shown = '$250' in found
    fixture_caveat = 'per fixture' in lo

    checks = [
      ("only approved prices",         not unapprvd),
      ("no diagnostic price ever",     not re.search(r'\$\s?3(49|50)\b', txt)),
      ("no unapproved service fee",    not re.search(r'\$\s?7[05]\b', txt)),
      ("EV price carries unit exclusion", (not ev_shown) or bool(ev_caveat)),
      ("panel price carries replacement-vs-upgrade", (not panel_shown) or bool(panel_caveat)),
      ("alarm price carries per-alarm qualifier", (not alarm_shown) or alarm_caveat),
      ("fixture price carries per-fixture qualifier", (not fixture_shown) or fixture_caveat),
      ("prices year-stamped",          (not found) or ('2026' in txt)),
      # Hardened 2026-08-24 after independent review: the old row only asked whether the
      # marker string existed ANYWHERE on the page. 16 of 18 figures on /panel-upgrade/
      # sat outside every span and this returned ✅. Now every figure must fall inside a
      # marker span, or the 90-day test cannot be ended in one edit.
      ("every visible price inside a swappable span", not _loose_prices(body)),
      # Every ld+json block must parse. Wrapping part of a graph in HTML comments breaks
      # all of it silently — Google just ignores the block.
      ("all ld+json parses", _all_ldjson_parses(body)),
      # Ending the pricing test must leave the page and its schema intact.
      ("pricing test is reversible", _schema_survives_price_removal(body)),
      # The pairing rule was only ever tested one way round, so "no trip charge" —
      # explicitly banned — passed untested. Ban the whole family.
      ("no unpaired fee claim",
       not re.search(r'no trip charge|no service charge|no call[- ]out fee|no visit fee', txt, re.I)),
      # Substance claims a mechanical gate CAN catch, from the review's blocker list.
      ("no unsourced fire-risk claim",
       not re.search(r'known fire (?:risk|hazard)|fire hazard\b', txt, re.I)),
      ("no false code-minimum claim",
       not re.search(r'(?:minimum standard|code minimum|required by code)[^.]{0,60}(?:200|300|400)\s?[- ]?amp'
                     r'|(?:200|300|400)\s?[- ]?amp[^.]{0,40}(?:is|as)\s+the\s+(?:new\s+)?minimum', txt, re.I)),
      ("no 'no service fee'",          'no service fee' not in txt.lower()),
      ("no 'free visit'",              'free visit' not in txt.lower()),
      ("'no trip fee' paired per occurrence", _check_offer_pairing(txt)[0]),
      ("no free-first-visit claim",
       not re.search(r'(?:free|complimentary|no charge|no cost|without charge)[^.]{0,80}?first visit'
                     r'|first visit[^.]{0,80}?(?:free|complimentary|no charge|no cost|without charge)',
                     txt, re.I)),
      ("no certification claimed",
       not re.search(r'thermograph|NABCEP', txt, re.I)
       and not re.search(r'(?:we(?:\s+are|\'re)?|our|ahmad(?:\s+is)?|he\s+is|is)\s+'
                     r'(?:\w+\s+){0,3}certified\b', txt, re.I)),
      ("no Michael & Son / Beacon",    not re.search(r'Michael\s*&|Beacon', txt, re.I)),
      ("no invented reviewers",        not re.search(r'Sarah Johnson|Jennifer Chen|David Thompson|Mike Rodriguez|Lisa Parker|Robert Kim', txt)),
      (f"reviewer = {expect_reviewer or '(none — no .evp-review)'}",
       _check_reviewer(body, txt, expect_reviewer)),
      # Scoped 2026-08-24: a bare statement that a credit EXPIRED is honest and is the
      # strongest content on the EV page. What must never appear is a BENEFIT claim —
      # a percentage, a dollar cap, or a promise of eligibility. The old rule banned the
      # words outright and would have blocked the true statement along with the false one.
      ("no tax-credit benefit claim",
       not re.search(r'(?:tax credit|rebate|incentive)[^.]{0,120}?'
                     r'(?:\d+\s?%|\$\s?[\d,]+|up to|you (?:may |can |could )?(?:qualify|claim|receive|get)|'
                     r'eligible for|covers?\s+(?:up\s+to\s+)?\d)', txt, re.I)
       and not re.search(r'(?:\d+\s?%|\$\s?[\d,]+|you (?:may |can |could |might )?(?:qualify|claim|receive|get)|'
                     r'eligible for|up to)[^.]{0,120}?(?:tax credit|rebate|incentive)', txt, re.I)),
      ("no pricing promise",           not re.search(r'pricing (notes|details|info)', txt, re.I)),
      ("legal address absent",         '12116 Monument' not in txt),
      ("no 'LLC'",                     not re.search(r'\bLLC\b', txt)),
      ("exactly one <h1>",             body.count('<h1')==1),
      # Scoped 2026-08-24: JSON-LD is required structured data, not executable code.
      # The rule exists to stop JS (and the blank-page class of bug that came with it).
      ("no executable <script>",
       not re.search(r'<script(?![^>]*type=["\']application/ld\+json["\'])', body)),
      ("swappable comment present",    'SWAPPABLE OFFER BLOCK' in body),
      ("evp-reveal used",              body.count('evp-reveal')>=6),
      ("<section> balanced",           body.count('<section')==body.count('</section')),
      # Two unclosed <div>s shipped past the old gate because sections and the wp:html
      # wrapper stayed balanced. Browsers paper over it; WordPress may not.
      ("<div> balanced",                body.count('<div')==body.count('</div>')),
      # The required free-estimates sentence must survive ending the pricing test. It sat
      # inside a marked span, so "end the test" would have deleted a contract-required
      # line in exactly the state the test is designed to reach.
      ("free-estimates line outside pricing span",
       ('Estimates are free' not in txt) or
       ('Estimates are free' in re.sub(r'<!-- SWAPPABLE PRICING BLOCK -->.*?'
                                       r'<!-- /SWAPPABLE PRICING BLOCK -->', '', body, flags=re.S))),
      ("every evp-section has inner",  len(secs)==len(re.findall(r'evp-section-inner', body))),
      ("bg strictly alternates",       all(secs[i]!=secs[i+1] for i in range(len(secs)-1))),
      ("wp:html wrapper closed",       body.rstrip().endswith('<!-- /wp:html -->')),
    ]
    for extra_name, pat in forbid_extra:
        checks.append((extra_name, not re.search(pat, txt, re.I)))
    # Compute offer pairing details for enhanced output
    offer_ok, offer_unpaired = _check_offer_pairing(txt)

    # Reviewer applicability for enhanced output
    has_review_component = 'evp-review' in body

    print(f"\n═══ {label}  ({len(txt.split())} words, {len(secs)+2} sections)")
    bad=0
    for n,ok in checks:
        suffix = ""
        if "reviewer =" in n and not has_review_component:
            suffix = "  [NOT APPLICABLE — no .evp-review component]"
        if "paired per occurrence" in n and not offer_ok:
            suffix = f"  [{len(offer_unpaired)} unpaired at sentence(s): {offer_unpaired}]"
        print(f"  {'✅' if ok else '❌'} {n}{suffix}")
        bad += (not ok)
    return bad

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('draft')
    ap.add_argument('--reviewer', required=False, default=None)
    ap.add_argument('--label', default=None)
    ap.add_argument('--forbid', action='append', default=[],
                    help='extra check, NAME=REGEX — fails if REGEX matches the visible text')
    a = ap.parse_args()
    extra = []
    for f in a.forbid:
        n, _, p = f.partition('=')
        extra.append((n, p))
    bad = gate(a.draft, a.label or a.draft, a.reviewer, extra)
    print(f"\nFAILURES: {bad}")
    sys.exit(bad)

if __name__ == '__main__':
    main()
