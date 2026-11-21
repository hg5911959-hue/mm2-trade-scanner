"""Shared per-test temporary working directory for stateful scanner tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CASH_MAX_AGE_DAYS", "14")


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
