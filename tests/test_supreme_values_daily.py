import unittest

from tools.supreme_values_daily import (
    SOURCE_CATEGORIES,
    build_weapon_snapshot,
    extract_source_updated,
    extract_sv_popup,
    normalize_name,
    parse_value_fields,
)


class SupremeValuesDailyTests(unittest.TestCase):
    def test_extracts_nested_json_object_with_braces_inside_strings(self):
        html = '<script>var _svPopup={"Bat":{"value":"155","origin":"x } {; \\"ok\\"","stability":"Stable"}};</script>'
        parsed = extract_sv_popup(html)
        self.assertEqual(parsed["Bat"]["value"], "155")
        self.assertEqual(parsed["Bat"]["stability"], "Stable")

    def test_rejects_challenge_page_without_source_object(self):
        with self.assertRaisesRegex(ValueError, "_svPopup"):
            extract_sv_popup("<html><title>Access denied</title></html>")

    def test_reads_visible_source_update_timestamp(self):
        html = "<div>Values Last Updated - November 23rd, 2026 at 7:13 PM // Join our Discord Server</div>"
        self.assertEqual(
            extract_source_updated(html),
            "November 23rd, 2026 at 7:13 PM",
        )

    def test_normalizes_possessives_without_fuzzy_matching(self):
        self.assertEqual(normalize_name("Vampire's Gun"), "vampire gun")
        self.assertEqual(normalize_name("Chroma Bat"), "chroma bat")
        self.assertNotEqual(normalize_name("Bat"), normalize_name("Batwing"))

    def test_numeric_value_and_range_are_preserved(self):
        fields = parse_value_fields({
            "value": "1,925", "rawValue": 1925,
            "range": "1,900 - 1,975", "stability": "Doing Well",
            "demand": "6", "rarity": "4", "diff": "+50", "pctChange": "+2.7%",
        })
        self.assertEqual(fields["value"], 1925)
        self.assertEqual(fields["range_min"], 1900)
        self.assertEqual(fields["range_max"], 1975)
        self.assertEqual(fields["stability"], "Doing Well")
        self.assertEqual(fields["stability_score"], 7)
        self.assertEqual(fields["demand"], 6)
        self.assertEqual(fields["rarity"], 4)

    def test_tiered_values_are_not_converted_to_fake_numbers(self):
        fields = parse_value_fields({"value": "x3 T1 Commons", "stability": "Stable"})
        self.assertIsNone(fields["value"])
        self.assertEqual(fields["value_type"], "tiered_items")

    def test_missing_stability_stays_null(self):
        fields = parse_value_fields({"value": "155"})
        self.assertIsNone(fields["stability"])
        self.assertIsNone(fields["stability_score"])

    def test_exact_category_and_alias_mapping(self):
        identity = {
            "bat": {
                "robase_slug": "bat", "robase_name": "Bat",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": False,
            },
            "chroma-bat": {
                "robase_slug": "chroma-bat", "robase_name": "Chroma Bat",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": True,
            },
        }
        pages = {category: {} for category in SOURCE_CATEGORIES}
        pages["godlies"] = {"Bat": {"value": "155", "stability": "Stable"}}
        pages["chromas"] = {"Chroma Bat": {"value": "1,200", "stability": "Rising"}}
        result = build_weapon_snapshot(
            identity, pages, "November 23rd, 2026 at 7:13 PM",
            fetched_at_utc="2026-11-25T00:00:00+00:00",
            snapshot_date="2026-11-25", expected_count=2,
        )
        self.assertEqual(result["counts"]["matched_numeric"], 2)
        by_slug = {row["bloxswaps_slug"]: row for row in result["items"]}
        self.assertEqual(by_slug["bat"]["value"], 155)
        self.assertEqual(by_slug["chroma-bat"]["stability_score"], 9)

    def test_alias_match_is_limited_to_expected_category(self):
        identity = {
            "bat": {
                "robase_slug": "bat", "robase_name": "Bat",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": False,
            }
        }
        pages = {category: {} for category in SOURCE_CATEGORIES}
        pages["ancients"] = {"Bat": {"value": "155", "stability": "Stable"}}
        result = build_weapon_snapshot(
            identity, pages, "update",
            fetched_at_utc="2026-11-25T00:00:00+00:00",
            snapshot_date="2026-11-25", expected_count=1,
        )
        self.assertEqual(result["items"][0]["status"], "missing")

    def test_duplicate_source_identity_is_rejected(self):
        identity = {
            slug: {
                "robase_slug": slug, "robase_name": "Bat",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": False,
            }
            for slug in ("bat", "bat-alt")
        }
        pages = {category: {} for category in SOURCE_CATEGORIES}
        pages["godlies"] = {"Bat": {"value": "155", "stability": "Stable"}}
        result = build_weapon_snapshot(
            identity, pages, "update",
            fetched_at_utc="2026-11-25T00:00:00+00:00",
            snapshot_date="2026-11-25", expected_count=2,
        )
        self.assertEqual(result["counts"]["ambiguous"], 2)
        self.assertTrue(all(row["value"] is None for row in result["items"]))

    def test_all_rows_are_preserved_even_when_missing(self):
        identity = {
            "bat": {
                "robase_slug": "bat", "robase_name": "Bat",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": False,
            },
            "batwing": {
                "robase_slug": "batwing", "robase_name": "Batwing",
                "bloxswaps_type": "knife", "bloxswaps_rarity": "ancient",
                "bloxswaps_chroma": False,
            },
        }
        pages = {category: {} for category in SOURCE_CATEGORIES}
        pages["godlies"] = {"Bat": {"value": "155", "stability": "Stable"}}
        result = build_weapon_snapshot(
            identity, pages, "update",
            fetched_at_utc="2026-11-25T00:00:00+00:00",
            snapshot_date="2026-11-25", expected_count=2,
        )
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["counts"]["missing"], 1)


if __name__ == "__main__":
    unittest.main()
