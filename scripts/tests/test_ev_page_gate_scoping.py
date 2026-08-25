"""Regression tests for ev_page_gate scoping fixes (2026-08-25).

(a) --reviewer is optional. Pages without .evp-review → NOT APPLICABLE (pass).
    Pages WITH .evp-review but no --reviewer → FAIL.

(b) 'no trip fee' pairing is checked per occurrence, not page-wide.
    One paired + one unpaired → FAIL, naming the unpaired position.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ev_page_gate import _check_reviewer, _check_offer_pairing


# ---------------------------------------------------------------------------
# Minimal HTML bodies for reviewer tests
# ---------------------------------------------------------------------------

# A page with NO .evp-review component
BODY_NO_REVIEW = """
<style>.x{}</style>
<section class="evp-section evp-section--light">
  <div class="evp-section-inner">
    <h2>Our Services</h2>
    <p>We do electrical work.</p>
  </div>
</section>
"""

# A page WITH an .evp-review component containing the reviewer name
BODY_WITH_REVIEW = """
<style>.x{}</style>
<section class="evp-section evp-section--light">
  <div class="evp-section-inner evp-review">
    <h2>What Our Customers Say</h2>
    <p>"Great work!" — Ben Olson</p>
  </div>
</section>
"""

# A page WITH .evp-review but the reviewer name is wrong / missing
BODY_WITH_REVIEW_WRONG_NAME = """
<style>.x{}</style>
<section class="evp-section evp-section--light">
  <div class="evp-section-inner evp-review">
    <h2>What Our Customers Say</h2>
    <p>"Great work!" — Someone Else</p>
  </div>
</section>
"""


class TestReviewerScoping:
    """(a) --reviewer scoping to .evp-review presence."""

    def test_no_review_component_returns_pass(self):
        """Page without .evp-review → NOT APPLICABLE (True)."""
        result = _check_reviewer(BODY_NO_REVIEW, "We do electrical work.", None)
        assert result is True

    def test_no_review_component_ignores_reviewer_arg(self):
        """Even if --reviewer is passed, no .evp-review → still pass."""
        result = _check_reviewer(BODY_NO_REVIEW, "We do electrical work.", "Ben Olson")
        assert result is True

    def test_review_component_no_reviewer_arg_fails(self):
        """Page with .evp-review but --reviewer not provided → FAIL."""
        txt = 'What Our Customers Say "Great work!" — Ben Olson'
        result = _check_reviewer(BODY_WITH_REVIEW, txt, None)
        assert result is False

    def test_review_component_correct_reviewer_passes(self):
        """Page with .evp-review and matching --reviewer → PASS."""
        txt = 'What Our Customers Say "Great work!" — Ben Olson'
        result = _check_reviewer(BODY_WITH_REVIEW, txt, "Ben Olson")
        assert result is True

    def test_review_component_wrong_reviewer_fails(self):
        """Page with .evp-review but reviewer name not in text → FAIL."""
        txt = 'What Our Customers Say "Great work!" — Someone Else'
        result = _check_reviewer(BODY_WITH_REVIEW_WRONG_NAME, txt, "Ben Olson")
        assert result is False


# ---------------------------------------------------------------------------
# Text samples for offer pairing tests
# ---------------------------------------------------------------------------

# All occurrences paired
TEXT_ALL_PAIRED = (
    "No trip fee, free estimates. "
    "Call today for service. "
    "No trip fee — free estimates on every job."
)

# One paired, one unpaired
TEXT_ONE_UNPAIRED = (
    "No trip fee, free estimates. "
    "We serve the whole area. "
    "No trip fee for the visit. "
    "Quality work guaranteed."
)

# Multiple unpaired
TEXT_MULTIPLE_UNPAIRED = (
    "No trip fee for the visit. "
    "No obligation. "
    "No trip fee. No hassle. "
    "Great service."
)

# No occurrences at all (should pass — nothing to pair)
TEXT_NO_OFFER = (
    "We provide electrical services in Fairfax. "
    "Call us today for a quote."
)


class TestOfferPairingPerOccurrence:
    """(b) 'no trip fee' pairing checked per occurrence."""

    def test_all_paired_passes(self):
        ok, unpaired = _check_offer_pairing(TEXT_ALL_PAIRED)
        assert ok is True
        assert unpaired == []

    def test_one_unpaired_fails(self):
        """One paired + one unpaired → FAIL, naming the unpaired."""
        ok, unpaired = _check_offer_pairing(TEXT_ONE_UNPAIRED)
        assert ok is False
        assert len(unpaired) == 1

    def test_multiple_unpaired_fails(self):
        ok, unpaired = _check_offer_pairing(TEXT_MULTIPLE_UNPAIRED)
        assert ok is False
        assert len(unpaired) >= 2

    def test_no_offer_phrase_passes(self):
        """No 'no trip fee' on page at all → pass (nothing to check)."""
        ok, unpaired = _check_offer_pairing(TEXT_NO_OFFER)
        assert ok is True
        assert unpaired == []

    def test_adjacent_sentence_pairing(self):
        """'free estimates' in the next sentence counts as paired."""
        text = "No trip fee on any call. Free estimates always available."
        ok, unpaired = _check_offer_pairing(text)
        assert ok is True
        assert unpaired == []
