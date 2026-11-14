"""Shared per-test temporary working directory for stateful scanner tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class IsolatedDataTestCase(unittest.TestCase):
    """Give every test a fresh cwd so relative data/ paths cannot touch the repo."""

    def __init__(self, methodName="runTest"):
        super().__init__(methodName)
        self._original_cwd = Path.cwd()
        self._test_temp = tempfile.TemporaryDirectory(prefix="mm2-test-")
        self.test_data_root = Path(self._test_temp.name)
        os.chdir(self.test_data_root)
        self.addCleanup(self._restore_test_cwd)

    def _restore_test_cwd(self):
        os.chdir(self._original_cwd)
        self._test_temp.cleanup()
