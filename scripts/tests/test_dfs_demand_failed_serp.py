"""Regression test: a SERP task with non-20000 status_code and null result
must produce 'NOT MEASURED', never 'NO' or 'None (thin SERP)'.

Reproduces the bug found 2026-08-25: serp-electrical-troubleshooting-fairfax-va.json
had status_code 40101, result: null. The dossier rendered 'AI Overview: NO' and
'PAA: None (thin SERP)' — fabricated negatives from an API error.
"""

import json
import sys
from pathlib import Path

# Ensure scripts dir is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dfs_demand import parse_serp_response
from demand_capture_emitter import _render_serp_summary


# Fixture: a DFS response where the task failed (status_code 40101, result null)
FAILED_SERP_RESPONSE = {
    "version": "0.1.20260101",
    "status_code": 20000,
    "status_message": "Ok.",
    "tasks_count": 1,
    "tasks_error": 1,
    "tasks": [
        {
            "id": "test-task-001",
            "status_code": 40101,
            "status_message": "Internal SE Server Error",
            "result": None,
        }
    ],
}

# Fixture: a successful SERP response for comparison
SUCCESSFUL_SERP_RESPONSE = {
    "version": "0.1.20260101",
    "status_code": 20000,
    "status_message": "Ok.",
    "tasks_count": 1,
    "tasks_error": 0,
    "tasks": [
        {
            "id": "test-task-002",
            "status_code": 20000,
            "status_message": "Ok.",
            "result": [
                {
                    "type": "organic",
                    "items": [
                        {
                            "type": "ai_overview",
                            "references": [
                                {"source": "example.com"},
                            ],
                        },
                        {
                            "type": "people_also_ask",
                            "items": [
                                {"title": "Why does my breaker keep tripping?"},
                                {"title": "Is a tripping breaker dangerous?"},
                            ],
                        },
                        {
                            "type": "organic",
                            "rank_group": 1,
                            "domain": "example.com",
                            "url": "https://example.com/page",
                            "title": "Test Result",
                        },
                    ],
                }
            ],
        }
    ],
}


class TestFailedSerpTaskParser:
    """parse_serp_response must surface failed tasks, not hide them."""

    def test_failed_task_detected(self):
        parsed = parse_serp_response(FAILED_SERP_RESPONSE)
        assert len(parsed["failed_tasks"]) == 1
        assert parsed["failed_tasks"][0]["status_code"] == 40101

    def test_failed_task_no_false_negatives(self):
        parsed = parse_serp_response(FAILED_SERP_RESPONSE)
        # With all tasks failed, data fields must be empty — but the
        # presence of failed_tasks tells the caller these are unmeasured,
        # not measured-as-zero.
        assert parsed["ai_overview"] is False
        assert parsed["paa"] == []
        assert parsed["organic"] == []

    def test_successful_task_no_failed_tasks(self):
        parsed = parse_serp_response(SUCCESSFUL_SERP_RESPONSE)
        assert parsed["failed_tasks"] == []
        assert parsed["ai_overview"] is True
        assert len(parsed["paa"]) == 2


class TestFailedSerpRendering:
    """_render_serp_summary must render NOT MEASURED for failed tasks."""

    def test_failed_serp_renders_not_measured(self):
        raw_data = {"serp-test-slug.json": FAILED_SERP_RESPONSE}
        rendered = _render_serp_summary(raw_data, "serp-test-slug.json")

        assert "NOT MEASURED" in rendered
        assert "40101" in rendered
        # Must NOT contain the false-negative strings (the bare measured forms)
        assert "**AI Overview:** NO\n" not in rendered
        assert "None (thin SERP)" not in rendered

    def test_failed_serp_never_says_no(self):
        """The specific regression: 'NO' and 'None' must not appear as
        measured values when the source failed."""
        raw_data = {"serp-test-slug.json": FAILED_SERP_RESPONSE}
        rendered = _render_serp_summary(raw_data, "serp-test-slug.json")

        # Split into lines and check none of them assert a measured negative
        for line in rendered.splitlines():
            stripped = line.strip()
            if stripped.startswith("**AI Overview:**"):
                assert "NO" not in stripped or "NOT MEASURED" in stripped
            if stripped.startswith("**PAA:**"):
                assert "None" not in stripped or "NOT MEASURED" in stripped

    def test_successful_serp_renders_normally(self):
        raw_data = {"serp-test-slug.json": SUCCESSFUL_SERP_RESPONSE}
        rendered = _render_serp_summary(raw_data, "serp-test-slug.json")

        assert "**AI Overview:** YES" in rendered
        assert "PAA (2 questions)" in rendered
        assert "NOT MEASURED" not in rendered
        assert "FAILED" not in rendered
