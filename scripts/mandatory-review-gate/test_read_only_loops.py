#!/usr/bin/env python3
"""Tests for loop-aware read-only classification (CR-138).

Validates that _is_segment_read_only and is_read_only_bash correctly
classify shell loops (for/while/until/if/case/select) by their
constituent commands — ALL body commands must be read-only for the
loop to be read-only. Control tokens (for/do/done/then/fi/in/esac)
are structural, not commands.

Usage:
    python3 test_read_only_loops.py       # from any directory
    python3 test_read_only_loops.py -v    # verbose
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine


class TestLoopReadOnly(unittest.TestCase):
    """CR-138: loops of read-only commands must classify as read-only."""

    # ------------------------------------------------------------------
    # MUST pass as read-only (True)
    # ------------------------------------------------------------------

    def test_for_curl_head_read_only(self):
        """for loop with curl -I (HEAD request, no write flags) is read-only."""
        cmd = 'for u in a b c; do curl -I -s -o /dev/null -w "%{http_code}" "$u"; done'
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_for_grep_read_only(self):
        """for loop with grep is read-only."""
        cmd = 'for f in *.md; do grep -c X "$f"; done'
        self.assertTrue(engine.is_read_only_bash(cmd))

    # ------------------------------------------------------------------
    # MUST flag as NOT read-only (False) — no bypass
    # ------------------------------------------------------------------

    def test_for_rm_not_read_only(self):
        """for loop with rm -rf is NOT read-only."""
        cmd = 'for x in 1; do rm -rf /tmp/x; done'
        self.assertFalse(engine.is_read_only_bash(cmd))

    def test_for_curl_tee_not_read_only(self):
        """for loop with curl piped to tee (file write) is NOT read-only."""
        cmd = 'for u in a b; do curl -s "$u" | tee f; done'
        self.assertFalse(engine.is_read_only_bash(cmd))

    def test_while_echo_redirect_not_read_only(self):
        """while loop with echo >> (file append) is NOT read-only."""
        cmd = 'while read l; do echo "$l" >> out; done'
        self.assertFalse(engine.is_read_only_bash(cmd))

    def test_for_curl_post_not_read_only(self):
        """for loop with curl -X POST (write flag) is NOT read-only."""
        cmd = 'for u in a b; do curl -X POST "$u"; done'
        self.assertFalse(engine.is_read_only_bash(cmd))

    # ------------------------------------------------------------------
    # Additional edge cases
    # ------------------------------------------------------------------

    def test_newline_separated_loop_read_only(self):
        """Newline-separated for loop with read-only body."""
        cmd = 'for u in a b c\ndo curl -I -s -o /dev/null "$u"\ndone'
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_if_then_else_read_only(self):
        """if/then/else/fi with read-only commands."""
        cmd = 'if grep -q foo bar.txt; then echo found; else echo missing; fi'
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_while_read_only_body(self):
        """while loop with read-only body (no redirect)."""
        cmd = 'while true; do curl -s http://example.com; done'
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_for_with_subshell_in_header(self):
        """for loop with $(cat ...) in the header, read-only body."""
        cmd = 'for u in $(cat urls.txt); do curl -I -s -o /dev/null "$u"; done'
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_control_flow_standalone_keywords(self):
        """Standalone control-flow keywords are read-only segments."""
        for kw in ('done', 'fi', 'esac'):
            self.assertTrue(engine._is_segment_read_only(kw),
                            f'{kw} should be read-only')

    def test_control_flow_prefix_bare(self):
        """Bare prefix keywords (do, then, else) with no body are read-only."""
        for kw in ('do', 'then', 'else'):
            self.assertTrue(engine._is_segment_read_only(kw),
                            f'bare {kw} should be read-only')

    def test_for_header_read_only(self):
        """for ... in ... header segment is read-only."""
        self.assertTrue(engine._is_segment_read_only('for u in a b c'))

    # ------------------------------------------------------------------
    # CR-138 backslash-newline continuation (the actual gate-tripping shape)
    # ------------------------------------------------------------------

    def test_backslash_continuation_read_only(self):
        """for loop with backslash-newline continuation and read-only body."""
        cmd = (
            'for slug in \\\n'
            '  ceiling-fan-installation-vienna-va \\\n'
            '  ceiling-fan-installation-oakton-va; do\n'
            '  curl -s -o /dev/null -w "%{http_code}" --max-redirs 0 '
            '"https://evelectric.pro/$slug/"\n'
            'done'
        )
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_backslash_continuation_with_leading_comment(self):
        """Same backslash-continuation loop with a leading # comment line."""
        cmd = (
            '# check redirects\n'
            'for slug in \\\n'
            '  ceiling-fan-installation-vienna-va \\\n'
            '  ceiling-fan-installation-oakton-va; do\n'
            '  curl -s -o /dev/null -w "%{http_code}" --max-redirs 0 '
            '"https://evelectric.pro/$slug/"\n'
            'done'
        )
        self.assertTrue(engine.is_read_only_bash(cmd))

    def test_backslash_continuation_rm_not_read_only(self):
        """Backslash-continuation does NOT bypass: rm in body still flags."""
        cmd = (
            'for x in 1 \\\n'
            '  2; do rm -rf /tmp/x; done'
        )
        self.assertFalse(engine.is_read_only_bash(cmd))


if __name__ == '__main__':
    unittest.main()
