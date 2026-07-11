"""Shared fixtures for review-gate tests."""

import os
import sys

import pytest

SCRIPT_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, SCRIPT_DIR)
import engine


@pytest.fixture(autouse=True)
def reset_engine_caches():
    """Reset engine caches before each test to prevent cross-test contamination."""
    engine._reset_plumbing_cache()
    engine._plumbing_regex_cache.clear()
    yield
    engine._reset_plumbing_cache()
    engine._plumbing_regex_cache.clear()
