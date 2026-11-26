from __future__ import annotations

import io
import unittest

from test_support import IsolatedDataTestCase

from tools.supreme_values_daily import (
    CATEGORIES,
    EXPECTED_WEAPONS,
    STABILITY_SCORES,
    build_snapshot,
    extract_sv_popup,
    fetch_all_categories,
)


class SupremePopupParserTests(IsolatedDataTestCase):
    def test_extracts_embedded_object_and_preserves_braces_in_strings(self):
        html = '<script>var _svPopup = {"Bat":{"rawValue":160,"stability":"Stable","origin":"contains }; marker"}};</script>'
        parsed = extract_sv_popup(html)
        self.assertEqual(parsed["Bat"]["rawValue"], 160)
        self.assertEqual(parsed["Bat"]["origin"], "contains }; marker")

    def test_rejects_missing_empty_and_invalid_popup(self):
        for html in ("<html></html>", "<script>var _svPopup = {};</script>", '<script>var _svPopup = {"A":};</script>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                extract_sv_popup(html)


class SupremeDailySnapshotTests(IsolatedDataTestCase):
    @staticmethod
    def fixture_data(stability="Stable"):
        identities = {}
        source_items = {}
        categories = ("godlies", "ancients", "vintages", "chromas", "legendaries", "rares", "uncommons", "commons", "uniques")
        for index in range(EXPECTED_WEAPONS):
            category = categories[index % len(categories)]
            name = f"Test Weapon {index:03d}"
            slug = f"test-weapon-{index:03d}"
            rarity = {
                "godlies": "godly", "ancients": "ancient", "vintages": "vintage",
                "chromas": "chroma", "legendaries": "legendary", "rares": "rare",
                "uncommons": "uncommon", "commons": "common", "uniques": "unique",
            }.get(category, "godly")
            identities[slug] = {
                "robase_slug": slug,
                "robase_name": name,
                "bloxswaps_type": "knife",
                "bloxswaps_rarity": rarity,
                "bloxswaps_chroma": category == "chromas",
            }
            source_items[(category, name.casefold())] = {
                "name": name,
                "category": category,
                "url": f"https://supremevalues.com/mm2/{category}",
                "raw": {
                    "value": "160",
                    "rawValue": 160,
                    "range": "150 - 170",
                    "stability": stability,
                    "demand": "5",
                    "rarity": "4",
                    "origin": "Test",
                    "diff": "+10",
                    "pctChange": "+6.7%",
                    "aliases": "",
                    "riseChance": "50",
                },
            }
        return identities, source_items

    def test_builds_direct_supreme_snapshot_with_source_stability_score(self):
        identities, items = self.fixture_data("Doing Well")
        snapshot = build_snapshot(
            identities, items,
            {"category_count": len(CATEGORIES), "category_counts": {}, "source_last_updated": "Nov 25, 2026", "source_timestamp_consistent": True, "source_items": len(items)},
            snapshot_date="2026-11-26",
            fetched_at_utc="2026-11-26T16:00:00+00:00",
        )
        self.assertEqual(snapshot["source"], "Supreme Values direct website")
        self.assertEqual(snapshot["counts"]["matched"], EXPECTED_WEAPONS)
        self.assertEqual(snapshot["items"][0]["stability"], "Doing Well")
        self.assertEqual(snapshot["items"][0]["stability_score"], 7)
        self.assertEqual(snapshot["items"][0]["value"], 160)
        self.assertEqual(snapshot["items"][0]["demand"], 5)

    def test_stability_is_read_from_each_new_daily_snapshot(self):
        identities, items = self.fixture_data("Stable")
        metadata = {"category_count": len(CATEGORIES), "category_counts": {}, "source_last_updated": None, "source_timestamp_consistent": False, "source_items": len(items)}
        day_one = build_snapshot(identities, items, metadata, snapshot_date="2026-11-25", fetched_at_utc="2026-11-25T16:00:00+00:00")
        items[("godlies", "test weapon 000")]["raw"]["stability"] = "Rising"
        day_two = build_snapshot(identities, items, metadata, snapshot_date="2026-11-26", fetched_at_utc="2026-11-26T16:00:00+00:00")
        self.assertEqual(day_one["items"][0]["stability"], "Stable")
        self.assertEqual(day_one["items"][0]["stability_score"], STABILITY_SCORES["Stable"])
        self.assertEqual(day_two["items"][0]["stability"], "Rising")
        self.assertEqual(day_two["items"][0]["stability_score"], STABILITY_SCORES["Rising"])

    def test_category_timestamps_must_be_present_on_every_page(self):
        class FakeResponse(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        def fake_open(request, timeout=30):
            category = request.full_url.rsplit("/", 1)[-1]
            timestamp = "" if category == "pets" else (
                "<div>Values Last Updated - November 25th, 2026 at 7:36 PM // Join our Discord Server</div>"
            )
            body = timestamp + '<script>var _svPopup = {"Example":{"rawValue":1}};</script>'
            return FakeResponse(body.encode("utf-8"))

        _, metadata = fetch_all_categories(sleep=lambda _: None, opener=fake_open, delay=0)
        self.assertFalse(metadata["source_timestamp_consistent"])
        self.assertIsNone(metadata["source_last_updated"])
        self.assertEqual(metadata["source_timestamp_count"], len(CATEGORIES) - 1)

    def test_category_fetch_fails_closed_when_source_object_is_missing(self):
        class FakeResponse(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        def fake_open(request, timeout=30):
            category = request.full_url.rsplit("/", 1)[-1]
            if category == "sets":
                return FakeResponse(b"<html>no source object</html>")
            return FakeResponse(b'<script>var _svPopup = {"Example":{"rawValue":1}};</script>')

        with self.assertRaisesRegex(ValueError, "_svPopup"):
            fetch_all_categories(sleep=lambda _: None, opener=fake_open, delay=0)


if __name__ == "__main__":
    unittest.main()
