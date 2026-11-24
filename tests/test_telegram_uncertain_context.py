import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase
import scanner


class TelegramUncertainContextTests(IsolatedDataTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "telegram_uncertain_context.json"
        self.patch_context = patch("scanner.UNCERTAIN_CONTEXT_FILE", self.path)
        self.patch_context.start()
        self.addCleanup(self.patch_context.stop)

    def test_context_round_trips_public_listing_reference_and_can_be_cleared(self):
        listing = {
            "source": "bloxswaps",
            "source_ad_id": "post-123",
            "url": "https://bloxswaps.com/trade-mm2?ad=post-123",
            "owner_url": "https://bloxswaps.com/fr/users/12345",
            "alert_attempted_at_utc": "2026-11-23T15:00:00+00:00",
        }

        scanner.record_uncertain_context(listing, "stable-hash", "digest")

        context = scanner.load_uncertain_context()
        self.assertEqual(context["stable-hash"]["source_ad_id"], "post-123")
        self.assertEqual(context["stable-hash"]["listing_url"], listing["url"])
        self.assertEqual(context["stable-hash"]["owner_url"], listing["owner_url"])
        self.assertEqual(context["stable-hash"]["delivery_path"], "digest")
        self.assertEqual(context["stable-hash"]["attempted_at_utc"], listing["alert_attempted_at_utc"])

        scanner.clear_uncertain_context("stable-hash")
        self.assertEqual(scanner.load_uncertain_context(), {})

    def test_external_links_are_not_persisted(self):
        listing = {
            "source": "bloxswaps",
            "source_ad_id": "post-456",
            "url": "https://attacker.example/collect",
            "owner_url": "http://bloxswaps.com/fr/users/12345",
        }

        scanner.record_uncertain_context(listing, "stable-hash", "individual")

        context = scanner.load_uncertain_context()["stable-hash"]
        self.assertEqual(context["listing_url"], "")
        self.assertEqual(context["owner_url"], "")

    def test_corrupt_context_fails_closed(self):
        self.path.write_text(json.dumps({"version": 9, "details": {}}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "invalid structure"):
            scanner.load_uncertain_context()



class TelegramUncertainStateTests(IsolatedDataTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "telegram_uncertain.json"
        self.patch_state = patch("scanner.UNCERTAIN_FILE", self.path)
        self.patch_state.start()
        self.addCleanup(self.patch_state.stop)

    def test_old_uncertain_delivery_remains_quarantined_indefinitely(self):
        old_timestamp = time.time() - 90 * 24 * 60 * 60
        self.path.write_text(
            json.dumps({
                "version": 2,
                "ids": ["old-trade"],
                "created_at": {"old-trade": old_timestamp},
            }),
            encoding="utf-8",
        )

        state, expired = scanner.load_uncertain_state()

        self.assertEqual(state, {"old-trade": old_timestamp})
        self.assertEqual(expired, [])
        self.assertEqual(scanner.load_uncertain_ids(), {"old-trade"})

    def test_non_finite_uncertain_timestamp_fails_closed(self):
        self.path.write_text(
            '{"version":2,"ids":["bad-trade"],"created_at":{"bad-trade":NaN}}',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "timestamp is invalid"):
            scanner.load_uncertain_state()

    def test_uncertain_quarantine_over_limit_fails_closed(self):
        ids = [f"trade-{index}" for index in range(scanner.MAX_UNCERTAIN_DELIVERIES + 1)]
        self.path.write_text(json.dumps({"version": 2, "ids": ids}), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "safety limit"):
            scanner.load_uncertain_state()





class AlertedRetentionTests(IsolatedDataTestCase):
    def test_alerted_ids_are_retained_for_the_full_seen_ad_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerted.json"
            with patch("scanner.ALERTED_FILE", path):
                ids = [f"listing-{index}" for index in range(scanner.MAX_ALERTED + 1)]
                scanner.save_alerted(ids)
                stored = json.loads(path.read_text(encoding="utf-8"))["alerted"]

        self.assertEqual(scanner.MAX_ALERTED, scanner.MAX_SEEN_ADS)
        self.assertEqual(len(stored), scanner.MAX_SEEN_ADS)
        self.assertEqual(stored[0], "listing-1")
        self.assertEqual(stored[-1], f"listing-{scanner.MAX_SEEN_ADS}")



if __name__ == "__main__":
    unittest.main()
