"""Shared test bootstrap and per-test temporary working directory.

Keep test-only defaults here so unittest discovery works the same way locally
and in CI, without requiring shell-specific setup. Individual tests can still
override these values with patch.dict.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# scanner.py reads MM2_TESTING while it is imported, so force this test-only
# value before any test module imports scanner or a module that imports it.
os.environ["MM2_TESTING"] = "true"
os.environ["CASH_MAX_AGE_DAYS"] = "14"


class IsolatedDataTestCase(unittest.TestCase):
    """Give every test a fresh cwd so relative data/ paths cannot touch the repo."""

    def run(self, result=None):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="mm2-test-") as temporary:
            self.test_data_root = Path(temporary)
            os.chdir(self.test_data_root)
            try:
                return super().run(result)
            finally:
                os.chdir(original_cwd)
