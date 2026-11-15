import ast
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import discord_parser
import market_history
import scanner
from test_support import IsolatedDataTestCase


ROOT = Path(__file__).resolve().parents[1]


def _hash_data_tree():
    result = {}
    for path in sorted((ROOT / "data").rglob("*")):
        if path.is_file():
            result[str(path.relative_to(ROOT / "data"))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class NoDataPollutionTests(IsolatedDataTestCase):
    def test_every_test_case_uses_the_isolated_base_class(self):
        tests_root = Path(__file__).resolve().parent
        for test_file in sorted(tests_root.glob("test_*.py")):
            if test_file.name == "test_support.py":
                continue
            module = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
            for node in ast.walk(module):
                if not isinstance(node, ast.ClassDef):
                    continue
                for base in node.bases:
                    base_name = ast.unparse(base)
                    self.assertNotIn(
                        base_name, {"unittest.TestCase", "TestCase"},
                        f"{test_file.name}:{node.name} must inherit IsolatedDataTestCase",
                    )
                    if base_name.endswith("TestCase"):
                        self.assertEqual(
                            base_name, "IsolatedDataTestCase",
                            f"{test_file.name}:{node.name} bypasses the isolated base",
                        )

    def test_mocked_scan_never_changes_repository_data(self):
        before = _hash_data_tree()

        class FakeHistory:
            def record_values_snapshot(self, values):
                return False

            def record_opportunity(self, result):
                return None

            def record_listings(self, listings):
                return 0

            def close(self):
                return None

        stats = {
            "pagination_complete": True,
            "candidate_count": 0,
            "addition_count": 0,
            "watch_remaining_ids": [],
            "watch_search_complete": True,
            "pending_remaining_ids": [],
        }
        with (
            patch.dict(os.environ, {"DRY_RUN": "true"}),
            patch.object(scanner, "CASH_FILTER", False),
            patch.object(scanner, "update_metrics"),
            patch.object(scanner, "load_supreme_values", return_value={}),
            patch.object(scanner, "load_cash_price_catalog", side_effect=AssertionError("cash catalogue must not load")),
            patch.object(scanner, "cash_catalog_snapshot_hash", side_effect=AssertionError("cash snapshot must not be read")),
            patch.object(scanner, "load_aliases", return_value={}),
            patch.object(scanner, "load_seen_ads", return_value=set()),
            patch.object(scanner, "load_pending_ids", return_value={}),
            patch.object(scanner, "load_pending_recheck_schedule", return_value={}),
            patch.object(scanner, "load_watch_state", return_value={}),
            patch.object(scanner, "load_first_seen_ads", return_value={}),
            patch.object(scanner, "fetch_bloxswaps_all", return_value=([], stats, [])),
            patch.object(scanner, "send_maintenance_alert"),
            patch.object(scanner, "alert_cash_price_runway"),
            patch.object(scanner, "record_success_if_complete"),
            patch.object(scanner, "save_cash_catalog_snapshot", side_effect=AssertionError("cash snapshot must not be written")),
            patch.object(scanner, "save_first_seen_ads"),
            patch.object(discord_parser, "load_discord_listings", return_value=([], 0)),
            patch.object(market_history, "MarketHistory", return_value=FakeHistory()),
        ):
            scanner.scan_once(include_backlog_rechecks=False)

        self.assertEqual(_hash_data_tree(), before)


if __name__ == "__main__":
    import unittest
    unittest.main()
