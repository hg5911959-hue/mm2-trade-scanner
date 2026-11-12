import unittest
import scanner
import watchdog
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scanner import parse_listings, parse_page, calculate_side, load_supreme_values


class ParserTests(unittest.TestCase):
    def test_extracts_both_trade_sides_and_links(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone"></div>
          <div>Recherche <img alt="Chroma Laser"></div>
          <a href="/fr/trade-mm2?ad=abc123">Discussion</a>
          <a href="/fr/users/12345?tab=trade-ads">Owner</a>
        </article>
        """
        listings = parse_listings(html)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["receive_items"][0]["name"], "Chroma Gemstone")
        self.assertEqual(listings[0]["give_items"][0]["name"], "Chroma Laser")
        self.assertIn("ad=abc123", listings[0]["url"])
        self.assertIn("/users/12345", listings[0]["owner_url"])

    def test_owner_link_outside_nested_trade_side_panel_is_found(self):
        html = """
        <article>
          <div class="trade-items">
            <div>Propose <img alt="Chroma Gemstone"></div>
            <div>Recherche <img alt="Chroma Laser"></div>
          </div>
          <a href="/fr/users/98765?tab=trade-ads">Owner</a>
          <a href="/fr/trade-mm2?ad=nested-card">Discussion</a>
        </article>
        """
        listings, stats = parse_page(html)
        self.assertEqual(len(listings), 1)
        self.assertIn("/fr/users/98765", listings[0]["owner_url"])
        self.assertEqual(stats["owner_link_count"], 1)

    def test_excludes_addition_listings(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone"></div>
          <div>Recherche <img alt="Chroma Laser"></div>
          <span>Ajouts</span>
        </article>
        """
        self.assertEqual(parse_listings(html), [])

    def test_empty_or_unrecognized_html_returns_no_cards(self):
        self.assertEqual(parse_listings("<html><body>maintenance</body></html>"), [])

    def test_listing_id_ignores_relative_time_text(self):
        def make_html(age):
            return f"""
            <article>
              <div>Propose <img alt="Chroma Gemstone"></div>
              <div>Recherche <img alt="Chroma Laser"></div>
              <span>il y a {age} min</span>
              <a href="/fr/trade-mm2?ad=changing">Discussion</a>
              <a href="/fr/users/12345?tab=trade-ads">Owner</a>
            </article>
            """
        self.assertEqual(parse_listings(make_html(2))[0]["id"], parse_listings(make_html(3))[0]["id"])

    def test_listing_id_ignores_item_order(self):
        html_a = """
        <article><div>Propose <img alt="Chroma Gemstone"><img alt="Chroma Laser"></div>
        <div>Recherche <img alt="Chroma Fang"></div>
        <a href="/fr/users/12345">Owner</a></article>"""
        html_b = """
        <article><div>Propose <img alt="Chroma Laser"><img alt="Chroma Gemstone"></div>
        <div>Recherche <img alt="Chroma Fang"></div>
        <a href="/fr/users/12345">Owner</a></article>"""
        self.assertEqual(parse_listings(html_a)[0]["id"], parse_listings(html_b)[0]["id"])

    def test_repeated_identical_weapon_images_are_counted_individually(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone"><img alt="Chroma Gemstone"></div>
          <div>Recherche <img alt="Chroma Laser"></div>
          <a href="/fr/users/12345">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(len(listing["receive_items"]), 2)
        values = {
            "chroma gemstone": {"name": "Chroma Gemstone", "value": 10},
            "chroma laser": {"name": "Chroma Laser", "value": 5},
        }
        total, breakdown, missing = calculate_side(listing["receive_items"], values)
        self.assertEqual(total, 20)
        self.assertEqual(missing, [])
        self.assertIn("x2", breakdown[0])

    def test_quantity_in_image_alt_is_parsed(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone x2"></div>
          <div>Recherche <img alt="Chroma Laser"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"], [{"name": "Chroma Gemstone", "quantity": 2}])

    def test_non_finite_or_non_positive_values_are_treated_as_unknown(self):
        items = [{"name": "Bad Value Knife", "quantity": 1}]
        for invalid in (float("nan"), float("inf"), 0, -5):
            total, breakdown, missing = calculate_side(
                items, {"bad value knife": {"name": "Bad Value Knife", "value": invalid}}
            )
            self.assertEqual(total, 0)
            self.assertEqual(breakdown, [])
            self.assertEqual(missing, ["Bad Value Knife"])

    def test_items_without_numeric_supreme_value_are_ignored(self):
        items = [
            {"name": "Brew", "quantity": 1},
            {"name": "Drool", "quantity": 1},
            {"name": "Snowstorm", "quantity": 1},
        ]
        values = {"snowstorm": {"name": "Snowstorm", "value": 250}}
        total, breakdown, missing = calculate_side(items, values)
        self.assertEqual(total, 250)
        self.assertEqual(missing, [])
        self.assertEqual(breakdown, ["• Snowstorm — 250 each = 250"])

    def test_calculate_side_multiplies_quantities_and_groups_duplicates(self):
        items = [
            {"name": "Chroma Gemstone", "quantity": 2},
            {"name": "Chroma Gemstone", "quantity": 1},
        ]
        values = {"chroma gemstone": {"name": "Chroma Gemstone", "value": 10}}
        total, breakdown, missing = calculate_side(items, values)
        self.assertEqual(total, 30)
        self.assertEqual(missing, [])
        self.assertIn("x3", breakdown[0])

    def test_all_ajouts_is_distinguished_from_parser_failure(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone"></div>
          <div>Recherche <img alt="Chroma Laser"></div>
          <span>Ajouts</span>
        </article>
        """
        listings, stats = parse_page(html)
        self.assertEqual(listings, [])
        self.assertEqual(stats["candidate_count"], 1)
        self.assertEqual(stats["addition_count"], 1)

    def test_explicit_stack_quantity_is_parsed(self):
        html = """
        <article>
          <div>Propose <img alt="Chroma Gemstone"><span>x3</span></div>
          <div>Recherche <img alt="Chroma Laser"></div>
          <a href="/fr/users/12345">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 3)

    def test_stack_quantity_in_bloxswaps_badge_is_parsed(self):
        html = """
        <article>
          <div>Propose
            <div class="trade-item">
              <span class="item-quantity">2</span>
              <img alt="Harvester">
            </div>
            <div class="trade-item">
              <img alt="Spirit">
              <span class="quantity-badge">x3</span>
            </div>
          </div>
          <div>Recherche <img alt="Harvester"></div>
          <a href="/fr/users/12345">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(
            listing["receive_items"],
            [{"name": "Harvester", "quantity": 2},
             {"name": "Spirit", "quantity": 3}],
        )

    def test_overlay_quantity_badge_before_nested_weapon_image_is_detected(self):
        html = """
        <article>
          <div>Propose
            <div class="offer-items">
              <div class="item-wrap">
                <span class="overlay">×2</span>
                <div class="weapon-image"><img alt="Snowcannon"></div>
              </div>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
          <span class="price">750.0</span>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"], [{"name": "Snowcannon", "quantity": 2}])

    def test_quantity_from_data_attribute_is_parsed(self):
        html = """
        <article>
          <div>Propose <img alt="Harvester" data-quantity="2"></div>
          <div>Recherche <img alt="Spirit"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 2)

    def test_stale_supreme_cache_is_never_used_when_api_fails(self):
        values = {
            f"item {i}": {"name": f"Item {i}", "value": i + 1, "secondary_value": None}
            for i in range(100)
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "supreme_values.json"
            cache_path.write_text(json.dumps({"fetched_at": 1, "values": values}), encoding="utf-8")
            with patch("scanner.VALUES_FILE", cache_path), patch(
                "scanner.api_get_json", side_effect=RuntimeError("API unavailable")
            ):
                with self.assertRaisesRegex(RuntimeError, "refusing stale-cache calculations"):
                    load_supreme_values()



class ReliabilityTests(unittest.TestCase):
    def test_duplicate_normalized_names_keep_highest_supreme_value(self):
        # The same display name can describe two distinct MM2 items (e.g.
        # Rainbow Gun godly vs. Rainbow (Gun), valued as 4 T1 uncommons).
        # The API may return either record first; keep the largest numeric
        # Supreme value_source_b and its matching secondary value.
        for duplicate_rows in (
            [
                {"name": "Rainbow Gun", "value_source_b": 4, "value_source_a": 3},
                {"name": "Rainbow Gun", "value_source_b": 400, "value_source_a": 410},
            ],
            [
                {"name": "Rainbow Gun", "value_source_b": 400, "value_source_a": 410},
                {"name": "Rainbow Gun", "value_source_b": 4, "value_source_a": 3},
            ],
        ):
            items = duplicate_rows + [
                {"name": f"Test Item {i}", "value_source_b": i + 1, "value_source_a": None}
                for i in range(99)
            ]
            with tempfile.TemporaryDirectory() as directory:
                cache_path = Path(directory) / "supreme_values.json"
                with (
                    patch("scanner.VALUES_FILE", cache_path),
                    patch("scanner.api_get_json", return_value={"items": items, "total": len(items)}),
                    patch("scanner.time.sleep"),
                ):
                    values = load_supreme_values()
            self.assertEqual(values["rainbow gun"]["value"], 400)
            self.assertEqual(values["rainbow gun"]["secondary_value"], 410)

    def test_non_numeric_tier_notation_does_not_override_numeric_godly_value(self):
        items = [
            {"name": "Rainbow Gun", "value_source_b": "4T1", "value_source_a": None},
            {"name": "Rainbow Gun", "value_source_b": 400, "value_source_a": 410},
        ] + [
            {"name": f"Test Item {i}", "value_source_b": i + 1, "value_source_a": None}
            for i in range(99)
        ]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "supreme_values.json"
            with (
                patch("scanner.VALUES_FILE", cache_path),
                patch("scanner.api_get_json", return_value={"items": items, "total": len(items)}),
                patch("scanner.time.sleep"),
            ):
                values = load_supreme_values()
        self.assertEqual(values["rainbow gun"]["value"], 400)

    def test_stable_id_uses_ad_id_to_distinguish_identical_owner_offers(self):
        items = [{"name": "Chroma Gemstone", "quantity": 1}]
        first = scanner.stable_listing_id("https://bloxswaps.com/fr/users/1", items, items,
                                          "https://bloxswaps.com/fr/trade-mm2?ad=one")
        second = scanner.stable_listing_id("https://bloxswaps.com/fr/users/1", items, items,
                                           "https://bloxswaps.com/fr/trade-mm2?ad=two")
        self.assertNotEqual(first, second)

    def test_alerted_keeps_newest_ids_in_insertion_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerted.json"
            history = Path(directory) / "history.csv"
            with patch("scanner.ALERTED_FILE", path), patch("scanner.HISTORY_FILE", history):
                scanner.save_alerted([f"id-{i}" for i in range(5005)])
                saved = scanner.load_alerted()
        self.assertEqual(len(saved), 5000)
        self.assertEqual(saved[0], "id-5")
        self.assertEqual(saved[-1], "id-5004")


    def test_state_writer_is_atomic_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            scanner.atomic_write_text(path, '{"ok": true}')
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
            self.assertFalse(path.with_name("state.json.tmp").exists())

    def test_realistic_harvester_two_for_spirit_uses_supreme_values(self):
        html = """
        <article>
          <div>Propose <img alt="Spirit"></div>
          <div>Recherche
            <div class="trade-item">
              <span class="item-quantity">2</span>
              <img alt="Harvester">
            </div>
          </div>
        </article>
        """
        listing = parse_listings(html)[0]
        supreme_values = {
            "harvester": {"name": "Harvester", "value": 265},
            "spirit": {"name": "Spirit", "value": 540},
        }
        result, missing = scanner.evaluate_listing(listing, supreme_values)
        self.assertEqual(missing, [])
        self.assertEqual(listing["give_items"][0]["quantity"], 2)
        self.assertEqual(result["give_total"], 530)
        self.assertEqual(result["receive_total"], 540)
        self.assertEqual(result["difference"], 10)
        self.assertAlmostEqual(result["margin"], 10 / 530 * 100, places=2)

    def test_evaluate_listing_accounts_for_quantities(self):
        listing = {
            "give_items": [{"name": "Knife A", "quantity": 2}],
            "receive_items": [{"name": "Knife B", "quantity": 3}],
        }
        values = {
            "knife a": {"name": "Knife A", "value": 10},
            "knife b": {"name": "Knife B", "value": 10},
        }
        result, missing = scanner.evaluate_listing(listing, values)
        self.assertEqual(missing, [])
        self.assertEqual(result["give_total"], 20)
        self.assertEqual(result["receive_total"], 30)
        self.assertEqual(result["difference"], 10)
        self.assertAlmostEqual(result["margin"], 50)

    def test_evaluate_listing_rejects_unknown_values_and_unvalued_only_side(self):
        listing = {
            "give_items": [{"name": "Knife A", "quantity": 1}],
            "receive_items": [{"name": "Unknown Knife", "quantity": 1}],
        }
        values = {"knife a": {"name": "Knife A", "value": 10}}
        result, missing = scanner.evaluate_listing(listing, values)
        self.assertIsNone(result)
        self.assertEqual(missing, ["Unknown Knife"])

        ignored_only = {
            "give_items": [{"name": "Brew", "quantity": 1}],
            "receive_items": [{"name": "Snowstorm", "quantity": 1}],
        }
        values["snowstorm"] = {"name": "Snowstorm", "value": 250}
        result, reason = scanner.evaluate_listing(ignored_only, values)
        self.assertIsNone(result)
        self.assertEqual(reason, ["one side has no valued items"])

    def test_watchdog_should_alert_windows(self):
        self.assertFalse(watchdog.should_alert(29))
        self.assertTrue(watchdog.should_alert(30))
        self.assertFalse(watchdog.should_alert(45))
        self.assertTrue(watchdog.should_alert(360))
        self.assertFalse(watchdog.should_alert(380))

    def test_qualifies_rejects_fair_trades_even_when_thresholds_are_zero(self):
        self.assertFalse(scanner.qualifies({"difference": 0, "margin": 0}, min_diff=0, min_percent=0))
        self.assertTrue(scanner.qualifies({"difference": 1, "margin": 1}, min_diff=0, min_percent=0))

    def test_qualifies_applies_both_absolute_and_percent_thresholds(self):
        result = {"difference": 10, "margin": 4.99}
        self.assertFalse(scanner.qualifies(result, min_diff=0, min_percent=5))
        result["margin"] = 5
        self.assertTrue(scanner.qualifies(result, min_diff=0, min_percent=5))
        self.assertFalse(scanner.qualifies(result, min_diff=11, min_percent=5))

    def test_ignored_item_names_returns_explicitly_ignored_names(self):
        listing = {"give_items": [{"name": "Brew", "quantity": 1}], "receive_items": []}
        self.assertEqual(scanner.ignored_item_names(listing), ["Brew"])

    def test_telegram_message_is_truncated_before_request(self):
        captured = {}
        def fake_request(req, timeout, label, attempts=4):
            captured["payload"] = json.loads(req.data.decode())
            return b'{"ok": true}'
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.request_with_retry", side_effect=fake_request
        ):
            scanner.send_telegram("x" * 5000)
        self.assertLessEqual(len(captured["payload"]["text"]), scanner.TELEGRAM_MAX_CHARS)
        self.assertTrue(captured["payload"]["text"].endswith("[message truncated]"))

    def test_history_write_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            listing = {"id": "abc", "url": "https://example.test/ad", "owner_url": ""}
            result = {"difference": 5, "margin": 50, "give_total": 10, "receive_total": 15}
            with patch("scanner.HISTORY_FILE", path):
                scanner.append_history(listing, result)
            self.assertEqual(len(path.read_text().splitlines()), 2)
            self.assertFalse(path.with_name("history.csv.tmp").exists())

    def test_owner_profile_is_the_primary_telegram_link(self):
        listing = {
            "url": "https://bloxswaps.com/fr/trade-mm2?ad=abc",
            "owner_url": "https://bloxswaps.com/fr/users/12345?tab=trade-ads",
        }
        message = scanner.format_trade_message(listing, 10, ["give"], 20, ["receive"], 10)
        self.assertIn("OPEN OWNER PROFILE: https://bloxswaps.com/fr/users/12345", message)
        self.assertLess(message.index("OPEN OWNER PROFILE"), message.index("Listing details"))
        self.assertLess(message.index("OPEN OWNER PROFILE"), message.index("YOU GIVE"))

    def test_unvalued_trade_message_is_explicit_and_links_owner(self):
        listing = {
            "give_items": [{"name": "Knife A", "quantity": 2}],
            "receive_items": [{"name": "Knife B", "quantity": 1}],
            "owner_url": "https://bloxswaps.com/fr/users/123",
            "url": "https://bloxswaps.com/fr/trade-mm2?ad=abc",
        }
        message = scanner.format_unvalued_message(listing, ["Knife B"])
        self.assertIn("Knife A x2", message)
        self.assertIn("PROFIT: NOT CALCULATED", message)
        self.assertIn("OPEN OWNER PROFILE: https://bloxswaps.com/fr/users/123", message)

    def test_owner_link_survives_telegram_truncation(self):
        listing = {
            "url": "https://bloxswaps.com/fr/trade-mm2?ad=abc",
            "owner_url": "https://bloxswaps.com/fr/users/12345?tab=trade-ads",
        }
        many_items = [f"• Item {i} — 1 each = 1" for i in range(500)]
        message = scanner.format_trade_message(listing, 500, many_items, 500, many_items, 0)
        captured = {}
        def fake_request(req, timeout, label, attempts=4):
            captured["text"] = json.loads(req.data.decode())["text"]
            return b'{"ok": true}'
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.request_with_retry", side_effect=fake_request
        ):
            scanner.send_telegram(message)
        self.assertIn("OPEN OWNER PROFILE: https://bloxswaps.com/fr/users/12345", captured["text"])

    def test_main_retries_unmatched_offer_after_value_mapping_becomes_available(self):
        html = """
        <article>
          <div>Propose <img alt="Knife B"></div>
          <div>Recherche <img alt="Knife A"></div>
          <a href="/fr/trade-mm2?ad=retry-me">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sent = []
            value_sets = [
                {"knife a": {"name": "Knife A", "value": 10}},
                {"knife a": {"name": "Knife A", "value": 10},
                 "knife b": {"name": "Knife B", "value": 20}},
            ]
            with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), \
                 patch("scanner.ALERTED_FILE", root / "alerted.json"), \
                 patch("scanner.FAILURE_FILE", root / "failure.json"), \
                 patch("scanner.HISTORY_FILE", root / "history.csv"), \
                 patch("scanner.load_supreme_values", side_effect=value_sets), \
                 patch("scanner.fetch_bloxswaps", return_value=html), \
                 patch("scanner.send_telegram", side_effect=lambda message: sent.append(message)), \
                 patch("scanner.compare_secondary_values", return_value=""):
                scanner.main()
                scanner.main()
            # Unknown-valued offers are silently excluded, then re-evaluated.
            self.assertEqual(len(sent), 1)
            self.assertIn("SUPREME TRADE SCANNER", sent[0])
            self.assertNotIn("MANUAL VALUE CHECK", "".join(sent))
            self.assertTrue((root / "alerted.json").exists())

    def test_main_caps_alerts_and_defers_remaining_offers_across_scans(self):
        def html(ad_ids):
            cards = []
            for ad in ad_ids:
                cards.append(f"""
                <article>
                  <div>Propose <img alt="Knife B"></div>
                  <div>Recherche <img alt="Knife A"></div>
                  <a href="/fr/trade-mm2?ad={ad}">Discussion</a>
                  <a href="/fr/users/123">Owner</a>
                </article>""")
            return "<html><body>" + "".join(cards) + "</body></html>"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sent = []
            page = html(["a", "b", "c", "d", "e"])
            values = {
                "knife a": {"name": "Knife A", "value": 10},
                "knife b": {"name": "Knife B", "value": 20},
            }
            with (
                patch("scanner.BOT_TOKEN", "test-token"),
                patch("scanner.CHAT_ID", "123"),
                patch("scanner.ALERTED_FILE", root / "alerted.json"),
                patch("scanner.FAILURE_FILE", root / "failure.json"),
                patch("scanner.HISTORY_FILE", root / "history.csv"),
                patch("scanner.load_supreme_values", return_value=values),
                patch("scanner.fetch_bloxswaps", return_value=page),
                patch("scanner.send_telegram", side_effect=lambda message: sent.append(message)),
                patch("scanner.compare_secondary_values", return_value=""),
                patch("scanner.MAX_ALERTS_PER_RUN", 2),
                patch("scanner.ALERT_DELAY_SECONDS", 0),
            ):
                scanner.main()
                scanner.main()
                scanner.main()
                alerted = scanner.load_alerted()
            self.assertEqual(len(sent), 5)
            self.assertEqual(len(alerted), 5)
            self.assertEqual(len(set(alerted)), 5)
            self.assertEqual(len((root / "history.csv").read_text().splitlines()), 6)

    def test_main_alerts_visible_qualifying_offers_once(self):
        def html(ad_ids):
            cards = []
            for ad in ad_ids:
                cards.append(f"""
                <article>
                  <div>Propose <img alt="Knife B"></div>
                  <div>Recherche <img alt="Knife A"></div>
                  <a href="/fr/trade-mm2?ad={ad}">Discussion</a>
                  <a href="/fr/users/123">Owner</a>
                </article>""")
            return "<html><body>" + "".join(cards) + "</body></html>"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sent = []
            sequence = [html(["old"]), html(["old", "new"]), html(["old", "new"])]
            with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), \
                 patch("scanner.ALERTED_FILE", root / "alerted.json"), \
                 patch("scanner.FAILURE_FILE", root / "failure.json"), \
                 patch("scanner.HISTORY_FILE", root / "history.csv"), \
                 patch("scanner.load_supreme_values", return_value={
                     "knife a": {"name": "Knife A", "value": 10},
                     "knife b": {"name": "Knife B", "value": 20},
                 }), patch("scanner.fetch_bloxswaps", side_effect=sequence), \
                 patch("scanner.send_telegram", side_effect=lambda message: sent.append(message)), \
                 patch("scanner.compare_secondary_values", return_value=""):
                scanner.main()
                scanner.main()
                scanner.main()
            self.assertEqual(len(sent), 2)
            self.assertTrue((root / "history.csv").exists())
            self.assertTrue((root / "alerted.json").exists())


if __name__ == "__main__":
    unittest.main()
