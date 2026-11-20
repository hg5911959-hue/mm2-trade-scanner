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

    def test_private_discord_unknown_items_never_enter_public_logs_or_unmatched_state(self):
        import io
        from contextlib import ExitStack, redirect_stdout

        private_listing = {
            "id": "private-discord-id",
            "source": "discord",
            "source_ad_id": "discord:private-message-id",
            "discord_message_id": "private-message-id",
            "source_text": "PrivateItem for AnotherPrivateItem",
            "receive_items": [{"name": "PrivateItem", "quantity": 1}],
            "give_items": [{"name": "AnotherPrivateItem", "quantity": 1}],
            "url": "https://discord.com/channels/private/123",
            "posted_at": "2026-11-19T20:00:00+00:00",
        }
        stats = {
            "pagination_complete": True,
            "candidate_count": 0,
            "addition_count": 0,
            "watch_remaining_ids": [],
            "watch_search_complete": True,
            "pending_remaining_ids": [],
        }

        class FakeHistory:
            def record_values_snapshot(self, values):
                return False

            def record_opportunity(self, result):
                return None

            def record_listings(self, listings):
                return 0

            def close(self):
                return None

        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                "DRY_RUN": "true", "PRIVATE_STATE_ENABLED": "true",
                "MARKET_BOOK_ENABLED": "false", "TELEGRAM_COMMANDS_ENABLED": "false",
                "INVENTORY_ENABLED": "false",
            }))
            stack.enter_context(patch.object(scanner, "CASH_FILTER", False))
            stack.enter_context(patch.object(scanner, "update_metrics"))
            stack.enter_context(patch.object(scanner, "load_supreme_values", return_value={}))
            stack.enter_context(patch.object(scanner, "load_aliases", return_value={}))
            stack.enter_context(patch.object(scanner, "load_seen_ads", return_value=set()))
            stack.enter_context(patch.object(scanner, "load_pending_ids", return_value={}))
            stack.enter_context(patch.object(scanner, "load_pending_recheck_schedule", return_value={}))
            stack.enter_context(patch.object(scanner, "load_watch_state", return_value={}))
            stack.enter_context(patch.object(scanner, "load_first_seen_ads", return_value={}))
            stack.enter_context(patch.object(scanner, "load_alerted", return_value=[]))
            stack.enter_context(patch.object(scanner, "load_uncertain_state", return_value=({}, [])))
            stack.enter_context(patch.object(scanner, "load_discord_alert_state", return_value={"alerted": [], "uncertain": {}}))
            stack.enter_context(patch.object(scanner, "fetch_bloxswaps_all", return_value=([], stats, [])))
            stack.enter_context(patch.object(scanner, "evaluate_listing", return_value=(None, ["PrivateItem"])))
            unmatched = stack.enter_context(patch.object(
                scanner, "record_unmatched_names",
                side_effect=AssertionError("private names must not be persisted"),
            ))
            stack.enter_context(patch.object(scanner, "send_maintenance_alert"))
            stack.enter_context(patch.object(scanner, "alert_cash_price_runway"))
            stack.enter_context(patch.object(scanner, "record_success_if_complete"))
            stack.enter_context(patch.object(scanner, "save_first_seen_ads"))
            stack.enter_context(patch.object(
                discord_parser, "load_discord_listings", return_value=([private_listing], 0),
            ))
            stack.enter_context(patch.object(
                market_history, "MarketHistory", return_value=FakeHistory(),
            ))
            stack.enter_context(redirect_stdout(output))
            scanner.scan_once(include_backlog_rechecks=False)

        unmatched.assert_not_called()
        rendered = output.getvalue()
        self.assertNotIn("PrivateItem", rendered)
        self.assertNotIn("private-message-id", rendered)
        self.assertNotIn("private-discord-id", rendered)

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
            patch.dict(os.environ, {"DRY_RUN": "true", "PRIVATE_STATE_ENABLED": "false"}),
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
            patch.object(discord_parser, "load_discord_listings", side_effect=AssertionError("private Discord offers must not be parsed without encrypted private state")),
            patch.object(market_history, "MarketHistory", return_value=FakeHistory()),
        ):
            scanner.scan_once(include_backlog_rechecks=False)

        self.assertEqual(_hash_data_tree(), before)


if __name__ == "__main__":
    import unittest
    unittest.main()
