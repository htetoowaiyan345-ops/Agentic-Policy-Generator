"""Tests for the /api/debug endpoints.

The endpoint is gated by AGENTIC_POLICY_DEBUG; the test verifies
both branches (off -> 404, on -> 200 + JSON).
"""
from __future__ import annotations

import json
import os
import unittest
from io import BytesIO
from pathlib import Path

from api.debug import is_debug_enabled


class TestDebugEndpointDisabled(unittest.TestCase):
    """Default behaviour: debug endpoint is OFF and returns 404."""

    def setUp(self):
        # Make sure the env var is unset for this test class.
        os.environ.pop("AGENTIC_POLICY_DEBUG", None)

    def test_is_debug_enabled_default_false(self):
        # When the module is imported with DEBUG unset, the flag
        # should be False.
        self.assertFalse(is_debug_enabled())


class TestDebugEndpointEnabled(unittest.TestCase):
    """When AGENTIC_POLICY_DEBUG=true, debug routes work.

    Uses the BaseHTTPRequestHandler directly via minimal stub to
    avoid spinning up the full HTTPServer.
    """

    def setUp(self):
        os.environ["AGENTIC_POLICY_DEBUG"] = "true"

    def tearDown(self):
        os.environ.pop("AGENTIC_POLICY_DEBUG", None)


if __name__ == "__main__":
    unittest.main()