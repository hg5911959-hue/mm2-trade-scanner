import unittest
import scanner
import watchdog
import json
import re
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from scanner import parse_listings, parse_page, calculate_side, load_supreme_values

def with_rsc(html):
    """Attach explicit RSC item names to HTML fixtures so tests exercise identity matching."""
    listings, _ = parse_page(html)
    posts = []
    for listing in listings:
        rows = []
        for field, role in (("receive_items", "OFFERED"), ("give_items", "REQUESTED")):
            for item in listing.get(field, []):
                quantity = item.get("quantity")
                if quantity is None:
                    quantity = 1
                rows.append({
                    "itemDetailsId": normalize_fixture_id(item["name"]),
                    "displayName": item["name"],
                    "quantity": quantity,
                    "tradeRole": role,
                })
        posts.append({
            "id": listing["source_ad_id"],
            "tradePostItems": rows,
            "requestedWildcards": [],
            "lastRefreshedAt": datetime.now(timezone.utc).isoformat(),
            "user": {"robloxUserId": "123", "robloxUsername": "fixture", "robloxDisplayName": "Fixture"},
        })
    page = {"items": posts, "meta": {"cursor": None}}
    payload = json.dumps({"initialPage": page}, ensure_ascii=False, separators=(",", ":"))
    return '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>' + html


def normalize_fixture_id(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-")


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
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in listing["receive_items"]],
            [("Chroma Gemstone", 2)],
        )

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
            [(item["name"], item["quantity"]) for item in listing["receive_items"]],
            [("Harvester", 2), ("Spirit", 3)],
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
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in listing["receive_items"]],
            [("Snowcannon", 2)],
        )

    def test_quantity_from_data_attribute_is_parsed(self):
        html = """
        <article>
          <div>Propose <img alt="Harvester" data-quantity="2"></div>
          <div>Recherche <img alt="Spirit"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 2)

    def test_live_bloxswaps_layout_reads_x2_after_coin_icon_and_price(self):
        # Mirrors the live feed's visible order: weapon image, item name,
        # coin icon, numeric listing price, then the x2 stack badge.
        html = """
        <article>
          <div>Propose
            <a href="/item/snowcannon">
              <img alt="Image de Snowcannon dans Murder Mystery 2">
            </a>
            Snowcannon
            <img alt="coin">
            750.0
            <span>x2</span>
          </div>
          <div>Recherche
            <img alt="Image de Spirit dans Murder Mystery 2">
            Spirit
            <img alt="coin">
            540.0
          </div>
          <a href="/fr/users/12345">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in listing["receive_items"]],
            [("Snowcannon", 2)],
        )
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in listing["give_items"]],
            [("Spirit", 1)],
        )

    def test_quantity_badge_does_not_leak_from_next_item(self):
        html = """
        <article>
          <div>Propose
            <div class="item-wrap"><img alt="Knife A"></div>
            <div class="item-wrap"><img alt="Knife B"><span>x2</span></div>
          </div>
          <div>Recherche <img alt="Knife C"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(
            [(item["name"], item["quantity"]) for item in listing["receive_items"]],
            [("Knife A", 1), ("Knife B", 2)],
        )

    def test_displayed_price_is_never_used_as_stack_quantity(self):
        html = """
        <article>
          <div>Propose
            <img alt="Image de Snowcannon dans Murder Mystery 2">
            Snowcannon <img alt="coin"> 750.0
          </div>
          <div>Recherche <img alt="Image de Spirit dans Murder Mystery 2"> Spirit</div>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 1)

    def test_stale_supreme_cache_is_never_used_when_api_fails(self):
        values = {
            f"item {i}": {
                "name": f"Item {i}", "value": i + 1, "secondary_value": None,
                "variants": [{"name": f"Item {i}", "value": i + 1, "secondary_value": None}],
            }
            for i in range(100)
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "supreme_values.json"
            cache_path.write_text(json.dumps({"schema_version": 2, "fetched_at": 1, "values": values}), encoding="utf-8")
            with patch("scanner.VALUES_FILE", cache_path), patch(
                "scanner.RBLXVALUE_API_KEY", "test-key"
            ), patch(
                "scanner.api_get_json_conditional", side_effect=RuntimeError("API unavailable")
            ):
                with self.assertRaisesRegex(RuntimeError, "refusing stale-cache calculations"):
                    load_supreme_values()



class ReliabilityTests(unittest.TestCase):
    def test_duplicate_normalized_names_use_conservative_value_by_trade_side(self):
        # Same normalized display names can describe different items. Preserve
        # both source_b records: give uses the higher value; receive the lower.
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
                    patch("scanner.RBLXVALUE_API_KEY", "test-key"),
                    patch("scanner.api_get_json_conditional", return_value=({"items": items, "total": len(items)}, None, False)),
                    patch("scanner.time.sleep"),
                ):
                    values = load_supreme_values()
            self.assertEqual(values["rainbow gun"]["value"], 400)
            self.assertEqual(
                sorted(variant["value"] for variant in values["rainbow gun"]["variants"]),
                [4, 400],
            )
            item = [{"name": "Rainbow Gun", "quantity": 1}]
            give_total, _, _ = scanner.calculate_side(item, values, side="give")
            receive_total, _, _ = scanner.calculate_side(item, values, side="receive")
            self.assertEqual(give_total, 400)
            self.assertEqual(receive_total, 4)
            give_min, give_max, _, _, _ = scanner.calculate_side_bounds(item, values, side="give")
            receive_min, receive_max, _, _, _ = scanner.calculate_side_bounds(item, values, side="receive")
            self.assertEqual((give_min, give_max), (400, 400))
            self.assertEqual((receive_min, receive_max), (4, 4))

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
                patch("scanner.RBLXVALUE_API_KEY", "test-key"),
                patch("scanner.api_get_json_conditional", return_value=({"items": items, "total": len(items)}, None, False)),
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

    def test_corrupt_alerted_state_fails_closed_instead_of_resending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerted.json"
            history = Path(directory) / "history.csv"
            path.write_text("{broken json", encoding="utf-8")
            with patch("scanner.ALERTED_FILE", path), patch("scanner.HISTORY_FILE", history):
                with self.assertRaisesRegex(RuntimeError, "refusing to send potentially duplicated alerts"):
                    scanner.load_alerted()

    def test_missing_first_run_state_files_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerted.json"
            history = Path(directory) / "history.csv"
            with patch("scanner.ALERTED_FILE", path), patch("scanner.HISTORY_FILE", history):
                self.assertEqual(scanner.load_alerted(), [])

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

    def test_uncertain_give_quantity_uses_best_and_worst_case_and_requires_manual_review(self):
        listing = {
            "give_items": [{
                "name": "Harvester", "quantity": None,
                "quantity_source": "uncertain", "quantity_min": 1, "quantity_max": 3,
                "raw_excerpt": "Harvester x?",
            }],
            "receive_items": [{"name": "Spirit", "quantity": 1, "quantity_source": "structured"}],
        }
        values = {
            "harvester": {"name": "Harvester", "value": 265},
            "spirit": {"name": "Spirit", "value": 540},
        }
        result, missing = scanner.evaluate_listing(listing, values)
        self.assertEqual(missing, [])
        self.assertTrue(result["manual_review"])
        self.assertEqual(result["profit_best"], 275)
        self.assertEqual(result["profit_worst"], -255)
        self.assertTrue(scanner.qualifies(result, 0, 0))

    def test_uncertain_give_quantity_with_positive_worst_case_is_normal_alert(self):
        listing = {
            "give_items": [{
                "name": "Harvester", "quantity": None,
                "quantity_source": "uncertain", "quantity_min": 1, "quantity_max": 2,
            }],
            "receive_items": [{"name": "Spirit", "quantity": 1, "quantity_source": "structured"}],
        }
        values = {
            "harvester": {"name": "Harvester", "value": 265},
            "spirit": {"name": "Spirit", "value": 540},
        }
        result, missing = scanner.evaluate_listing(listing, values)
        self.assertEqual(missing, [])
        self.assertFalse(result["manual_review"])
        self.assertEqual(result["profit_worst"], 10)
        self.assertTrue(scanner.qualifies(result, 0, 0))

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
        self.assertFalse(watchdog.should_alert(24))
        self.assertTrue(watchdog.should_alert(25))
        self.assertTrue(watchdog.should_alert(29))
        self.assertFalse(watchdog.should_alert(30))
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
        def fake_open(req, timeout):
            captured["payload"] = json.loads(req.data.decode())
            from io import BytesIO
            return BytesIO(b'{"ok": true}')
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.urllib.request.urlopen", side_effect=fake_open
        ):
            scanner.send_telegram("x" * 5000)
        self.assertLessEqual(len(captured["payload"]["text"]), scanner.TELEGRAM_MAX_CHARS)
        self.assertTrue(captured["payload"]["text"].endswith("[message truncated]"))

    def test_telegram_send_does_not_retry_uncertain_network_failure(self):
        calls = []
        def fail_open(req, timeout):
            calls.append(req)
            from urllib.error import URLError
            raise URLError("connection reset after request")
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.urllib.request.urlopen", side_effect=fail_open
        ):
            with self.assertRaises(scanner.TelegramDeliveryUncertain):
                scanner.send_telegram("test")
        self.assertEqual(len(calls), 1)

    def test_uncertain_delivery_quarantine_expires_without_auto_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uncertain.json"
            with patch("scanner.UNCERTAIN_FILE", path), patch("scanner.time.time", return_value=1000):
                scanner.save_uncertain_ids({"old-listing"})
            with patch(
                "scanner.UNCERTAIN_FILE", path
            ), patch(
                "scanner.time.time", return_value=1000 + scanner.UNCERTAIN_TTL_SECONDS + 1
            ):
                state, expired = scanner.load_uncertain_state()
            self.assertEqual(state, {})
            self.assertEqual(expired, ["old-listing"])
            self.assertEqual(scanner.load_uncertain_ids(), set())


    def test_uncertain_telegram_delivery_ids_are_persisted_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram_uncertain.json"
            with patch("scanner.UNCERTAIN_FILE", path):
                scanner.save_uncertain_ids({"listing-a", "listing-b"})
                self.assertEqual(
                    scanner.load_uncertain_ids(), {"listing-a", "listing-b"}
                )

    def test_first_seen_state_round_trips_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "first_seen_ads.json"
            with patch("scanner.FIRST_SEEN_FILE", path):
                timestamp = "2026-11-12T19:00:00+00:00"
                scanner.save_first_seen_ads({"ad-1": timestamp})
                self.assertEqual(scanner.load_first_seen_ads(), {"ad-1": timestamp})
                path.write_text("{bad", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "First-seen"):
                    scanner.load_first_seen_ads()

    def test_history_migrates_legacy_rows_and_records_first_seen_and_alert_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            path.write_text(
                "detected_at_utc,listing_id,profit_supreme,profit_margin_percent,you_give,you_receive,listing_url,owner_url\n"
                "2026-11-11T10:00:00+00:00,old-id,5,50,10,15,https://example.test/old,\n",
                encoding="utf-8",
            )
            listing = {
                "id": "new-id", "url": "https://example.test/new", "owner_url": "",
                "first_seen_at_utc": "2026-11-12T19:00:00+00:00",
                "alert_attempted_at_utc": "2026-11-12T19:01:00+00:00",
                "alerted_at_utc": "2026-11-12T19:01:02+00:00",
            }
            result = {"difference": 5, "margin": 50, "give_total": 10, "receive_total": 15}
            with patch("scanner.HISTORY_FILE", path):
                scanner.append_history(listing, result)
            rows = list(__import__("csv").DictReader(path.open(encoding="utf-8")))
            self.assertEqual([row["listing_id"] for row in rows], ["old-id", "new-id"])
            self.assertEqual(rows[0]["alerted_at_utc"], "2026-11-11T10:00:00+00:00")
            self.assertEqual(rows[1]["first_seen_at_utc"], listing["first_seen_at_utc"])
            self.assertEqual(rows[1]["alert_attempted_at_utc"], listing["alert_attempted_at_utc"])
            self.assertEqual(rows[1]["alerted_at_utc"], listing["alerted_at_utc"])

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
        self.assertIn("👤 https://bloxswaps.com/fr/users/12345", message)
        self.assertLess(message.index("👤 "), message.index("🔗 "))
        self.assertLess(message.index("👤 "), message.index("📤 GIVE"))
        self.assertNotIn("QUANTITY PROVENANCE", message)
        self.assertNotIn("raw=", message)

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
        self.assertIn("👤 https://bloxswaps.com/fr/users/123", message)

    def test_owner_link_survives_telegram_truncation(self):
        listing = {
            "url": "https://bloxswaps.com/fr/trade-mm2?ad=abc",
            "owner_url": "https://bloxswaps.com/fr/users/12345?tab=trade-ads",
        }
        many_items = [f"• Item {i} — 1 each = 1" for i in range(500)]
        message = scanner.format_trade_message(listing, 500, many_items, 500, many_items, 0)
        captured = {}
        def fake_open(req, timeout):
            captured["text"] = json.loads(req.data.decode())["text"]
            from io import BytesIO
            return BytesIO(b'{"ok": true}')
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.urllib.request.urlopen", side_effect=fake_open
        ):
            scanner.send_telegram(message)
        self.assertIn("👤 https://bloxswaps.com/fr/users/12345", captured["text"])
        self.assertIn("and 492 more items", captured["text"])

    def test_one_definite_telegram_failure_does_not_stop_later_alerts(self):
        html = """
        <article>
          <div>Propose <img alt="Knife B x5"></div>
          <div>Recherche <img alt="Knife A"></div>
          <a href="/fr/trade-mm2?ad=offer-one">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        <article>
          <div>Propose <img alt="Knife B x5"></div>
          <div>Recherche <img alt="Knife A"></div>
          <a href="/fr/trade-mm2?ad=offer-two">Discussion</a>
          <a href="/fr/users/456">Owner</a>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "knife a": {"name": "Knife A", "value": 10},
                "knife b": {"name": "Knife B", "value": 20},
            }
            with (
                patch("scanner.BOT_TOKEN", "test-token"),
                patch("scanner.CHAT_ID", "123"),
                patch("scanner.ALERTED_FILE", root / "alerted.json"),
                patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.FAILURE_FILE", root / "failure.json"),
                patch("scanner.HISTORY_FILE", root / "history.csv"),
                patch("scanner.SEEN_FILE", root / "seen_ads.json"),
                 patch("scanner.PENDING_FILE", root / "pending.json"),
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.load_supreme_values", return_value=values),
                patch("scanner.fetch_bloxswaps", return_value=with_rsc(html)),
                patch("scanner.send_telegram", side_effect=[RuntimeError("rejected"), None]) as send,
                patch("scanner.compare_secondary_values", return_value=""),
            ):
                scanner.main()
            self.assertEqual(send.call_count, 2)
            second_message = send.call_args_list[1].args[0]
            self.assertIn("WARNING: quantity 5+", second_message)
            self.assertIn("WARNING: unusually high profit", second_message)
            self.assertIn("Knife B x5", second_message)
            alerted = json.loads((root / "alerted.json").read_text(encoding="utf-8"))["alerted"]
            self.assertEqual(len(alerted), 1)
            seen = json.loads((root / "seen_ads.json").read_text(encoding="utf-8"))["ad_ids"]
            self.assertEqual(seen, ["offer-two"])


    def test_best_case_only_profit_is_sent_as_manual_verification(self):
        html = """
        <article>
          <div>Propose <img alt="Spirit"></div>
          <div>Recherche <span class="quantity-badge">x?</span><img alt="Harvester"></div>
          <a href="/fr/trade-mm2?ad=manual-review-test">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "harvester": {"name": "Harvester", "value": 265},
                "spirit": {"name": "Spirit", "value": 540},
            }
            with (
                patch("scanner.BOT_TOKEN", "test-token"),
                patch("scanner.CHAT_ID", "123"),
                patch("scanner.ALERTED_FILE", root / "alerted.json"),
                patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.FAILURE_FILE", root / "failure.json"),
                patch("scanner.HISTORY_FILE", root / "history.csv"),
                patch("scanner.SEEN_FILE", root / "seen_ads.json"),
                 patch("scanner.PENDING_FILE", root / "pending.json"),
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.load_supreme_values", return_value=values),
                patch("scanner.fetch_bloxswaps", return_value=with_rsc(html)),
                patch("scanner.send_telegram") as send,
                patch("scanner.compare_secondary_values", return_value=""),
                patch("scanner.MIN_PROFIT_PERCENT", 0),
            ):
                scanner.main()
            trade_messages = [call.args[0] for call in send.call_args_list if "💎 MM2 TRADE" in call.args[0]]
            self.assertEqual(len(trade_messages), 1)
            message = trade_messages[0]
            self.assertIn("MANUAL CHECK", message)
            self.assertIn("POSSIBLE PROFIT RANGE: -∞ to 275", message)
            self.assertIn("Harvester x? (1..∞)", message)
            self.assertNotIn("raw=", message)


    def test_main_quarantines_uncertain_telegram_delivery_on_next_scan(self):
        html = """
        <article>
          <div>Propose <img alt="Knife B"></div>
          <div>Recherche <img alt="Knife A"></div>
          <a href="/fr/trade-mm2?ad=uncertain-test">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "knife a": {"name": "Knife A", "value": 10},
                "knife b": {"name": "Knife B", "value": 20},
            }
            with (
                patch("scanner.BOT_TOKEN", "test-token"),
                patch("scanner.CHAT_ID", "123"),
                patch("scanner.ALERTED_FILE", root / "alerted.json"),
                patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.FAILURE_FILE", root / "failure.json"),
                patch("scanner.HISTORY_FILE", root / "history.csv"),
                patch("scanner.SEEN_FILE", root / "seen_ads.json"),
                 patch("scanner.PENDING_FILE", root / "pending.json"),
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.load_supreme_values", return_value=values),
                patch("scanner.fetch_bloxswaps", return_value=with_rsc(html)),
                patch("scanner.send_telegram", side_effect=scanner.TelegramDeliveryUncertain("unknown")),
                patch("scanner.compare_secondary_values", return_value=""),
            ):
                scanner.main()
                self.assertTrue((root / "uncertain.json").exists())
                scanner.send_telegram.reset_mock()
                scanner.send_telegram.side_effect = None
                scanner.send_telegram.return_value = None
                scanner.main()
                self.assertEqual(scanner.send_telegram.call_count, 0)
                self.assertTrue((root / "uncertain.json").exists())

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
                 patch("scanner.SEEN_FILE", root / "seen_ads.json"), \
                 patch("scanner.PENDING_FILE", root / "pending.json"), \
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"), \
                 patch("scanner.load_supreme_values", side_effect=value_sets), \
                 patch("scanner.fetch_bloxswaps", return_value=with_rsc(html)), \
                 patch("scanner.send_telegram", side_effect=lambda message: sent.append(message)), \
                 patch("scanner.compare_secondary_values", return_value=""):
                scanner.main()
                scanner.main()
            # Unknown-valued offers generate a maintenance notice, remain pending, and are re-evaluated.
            trade_messages = [message for message in sent if "💎 MM2 TRADE" in message]
            self.assertEqual(len(trade_messages), 1)
            self.assertNotIn("MANUAL VALUE CHECK", "".join(trade_messages))
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
                patch("scanner.SEEN_FILE", root / "seen_ads.json"),
                 patch("scanner.PENDING_FILE", root / "pending.json"),
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"),
                patch("scanner.load_supreme_values", return_value=values),
                patch("scanner.fetch_bloxswaps", return_value=with_rsc(page)),
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
                 patch("scanner.SEEN_FILE", root / "seen_ads.json"), \
                 patch("scanner.PENDING_FILE", root / "pending.json"), \
                 patch("scanner.UNCERTAIN_FILE", root / "uncertain.json"), \
                 patch("scanner.load_supreme_values", return_value={
                     "knife a": {"name": "Knife A", "value": 10},
                     "knife b": {"name": "Knife B", "value": 20},
                 }), patch("scanner.fetch_bloxswaps", side_effect=[with_rsc(item) for item in sequence]), \
                 patch("scanner.send_telegram", side_effect=lambda message: sent.append(message)), \
                 patch("scanner.compare_secondary_values", return_value=""):
                scanner.main()
                scanner.main()
                scanner.main()
            self.assertEqual(len(sent), 2)
            self.assertTrue((root / "history.csv").exists())
            self.assertTrue((root / "alerted.json").exists())



class CloudflareDetectionTests(unittest.TestCase):
    def test_http_200_cloudflare_challenge_is_not_treated_as_empty_feed(self):
        challenge = b"<html><title>Just a moment...</title><div id='challenge-platform'></div></html>"
        with patch("scanner.request_with_retry", return_value=challenge):
            with self.assertRaisesRegex(RuntimeError, "(?i)cloudflare"):
                scanner.http_get("https://bloxswaps.com/fr/trade-mm2")


class CacheTTLTests(unittest.TestCase):
    def test_fresh_supreme_snapshot_is_reused_within_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supreme.json"
            values = {
                f"item {i}": {
                    "name": f"Item {i}", "value": i + 1,
                    "variants": [{"name": f"Item {i}", "value": i + 1}],
                }
                for i in range(100)
            }
            path.write_text(json.dumps({"schema_version": 2, "fetched_at": 990, "values": values}), encoding="utf-8")
            with (
                patch("scanner.VALUES_FILE", path),
                patch("scanner.VALUES_TTL_SECONDS", 300),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.RBLXVALUE_API_KEY", ""),
                patch("scanner.api_get_json_conditional") as api,
            ):
                loaded = scanner.load_supreme_values()
            self.assertEqual(len(loaded), 100)
            api.assert_not_called()

    def test_legacy_highest_only_supreme_cache_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supreme.json"
            values = {f"item {i}": {"name": f"Item {i}", "value": i + 1} for i in range(100)}
            path.write_text(json.dumps({"fetched_at": 990, "values": values}), encoding="utf-8")
            with (
                patch("scanner.VALUES_FILE", path),
                patch("scanner.VALUES_TTL_SECONDS", 300),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.RBLXVALUE_API_KEY", ""),
            ):
                with self.assertRaisesRegex(RuntimeError, "fresh cached source_b snapshot"):
                    scanner.load_supreme_values()

    def test_expired_supreme_snapshot_is_never_used_without_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supreme.json"
            values = {f"item {i}": {"name": f"Item {i}", "value": i + 1} for i in range(100)}
            path.write_text(json.dumps({"schema_version": 2, "fetched_at": 100, "values": values}), encoding="utf-8")
            with (
                patch("scanner.VALUES_FILE", path),
                patch("scanner.VALUES_TTL_SECONDS", 300),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.RBLXVALUE_API_KEY", ""),
            ):
                with self.assertRaisesRegex(RuntimeError, "refusing stale values"):
                    scanner.load_supreme_values()

    def test_fresh_bloxswaps_item_catalogue_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "items.json"
            items = {"item-id": {"id": "item-id", "displayName": "Snowcannon"}}
            path.write_text(json.dumps({"fetched_at": 990, "items": items}), encoding="utf-8")
            with (
                patch("scanner.BLOXSWAPS_ITEMS_FILE", path),
                patch("scanner.BLOXSWAPS_ITEMS_TTL_SECONDS", 3600),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.bloxswaps_api_get_json") as api,
            ):
                loaded = scanner.fetch_bloxswaps_items_map()
            self.assertEqual(loaded, items)
            api.assert_not_called()


class PendingRecheckStateTests(unittest.TestCase):
    def test_expired_pending_ids_are_reported_before_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            with patch("scanner.PENDING_FILE", path), patch("scanner.time.time", return_value=1000):
                scanner.save_pending_ids({"old-ad": 100.0})
            with (
                patch("scanner.PENDING_FILE", path),
                patch("scanner.time.time", return_value=1000 + scanner.PENDING_TTL_SECONDS + 1),
                patch("scanner.send_maintenance_alert") as alert,
            ):
                self.assertEqual(scanner.load_pending_ids(), {})
            alert.assert_called_once()
            self.assertIn("old-ad", alert.call_args.args[1])


    def test_pending_ids_round_trip_with_first_seen_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            pending = {"ad-one": 100.0, "ad-two": 200.0}
            with patch("scanner.PENDING_FILE", path), patch("scanner.time.time", return_value=200):
                scanner.save_pending_ids(pending)
                self.assertEqual(scanner.load_pending_ids(), pending)


class SeenAdStateTests(unittest.TestCase):
    def test_seen_state_round_trips_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen_ads.json"
            with patch("scanner.SEEN_FILE", path):
                self.assertIsNone(scanner.load_seen_ads())
                scanner.save_seen_ads({"ad-one", "ad-two"})
                self.assertEqual(scanner.load_seen_ads(), {"ad-one", "ad-two"})
                path.write_text("{bad", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "refusing to replay old feed pages"):
                    scanner.load_seen_ads()


class MaintenanceAndUnmatchedNameTests(unittest.TestCase):
    def test_unmatched_names_are_alerted_once_and_kept_for_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("scanner.UNMATCHED_FILE", root / "unmatched.json"),
                patch("scanner.MAINTENANCE_FILE", root / "maintenance.json"),
                patch("scanner.send_telegram") as send,
            ):
                first = scanner.record_unmatched_names(["Mystery Blade", "Mystery Blade", "Odd Gun"])
                second = scanner.record_unmatched_names(["Mystery Blade", "Odd Gun"])
            self.assertEqual(set(first), {"Mystery Blade", "Odd Gun"})
            self.assertEqual(second, [])
            self.assertEqual(send.call_count, 1)
            saved = json.loads((root / "unmatched.json").read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {"mystery blade", "odd gun"})

    def test_maintenance_alerts_are_throttled(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("scanner.MAINTENANCE_FILE", Path(directory) / "maintenance.json"),
                patch("scanner.send_telegram") as send,
                patch("scanner.time.time", return_value=1000),
            ):
                self.assertTrue(scanner.send_maintenance_alert("parser", "bad card", 3600))
                self.assertFalse(scanner.send_maintenance_alert("parser", "bad card", 3600))
            self.assertEqual(send.call_count, 1)


class SafeNameResolutionTests(unittest.TestCase):
    def test_irregular_plural_matches_unique_singular_item(self):
        values = {"knife": {"name": "Knife", "value": 100}}
        record, source, candidates = scanner.resolve_value_record("Knives", values)
        self.assertEqual(record["name"], "Knife")
        self.assertEqual(source, "plural")
        self.assertEqual(candidates, [])

    def test_ambiguous_singular_plural_candidates_are_not_auto_matched(self):
        values = {
            "knife": {"name": "Knife", "value": 100},
            "knif": {"name": "Knif", "value": 120},
        }
        record, source, candidates = scanner.resolve_value_record("Knives", values)
        self.assertIsNone(record)
        self.assertEqual(source, "ambiguous")
        self.assertEqual(set(candidates), {"Knife", "Knif"})

    def test_reviewed_alias_maps_to_exact_supreme_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aliases.json"
            path.write_text(json.dumps({"Snowcannon": "Snow Cannon"}), encoding="utf-8")
            with patch("scanner.ALIASES_FILE", path):
                aliases = scanner.load_aliases()
        values = {"snow cannon": {"name": "Snow Cannon", "value": 750}}
        record, source, _ = scanner.resolve_value_record("Snowcannon", values, aliases)
        self.assertEqual(record["value"], 750)
        self.assertEqual(source, "alias")

    def test_low_confidence_fuzzy_match_is_rejected(self):
        values = {"harvester": {"name": "Harvester", "value": 265}}
        record, source, _ = scanner.resolve_value_record("Harvaster", values)
        self.assertIsNone(record)
        self.assertEqual(source, "unmatched")


class StructuredQuantityCrossCheckTests(unittest.TestCase):
    def test_rsc_quantity_two_conflicts_with_implicit_html_single(self):
        listing = {
            "receive_items": [{
                "name": "Snowcannon", "quantity": 1,
                "quantity_source": "implicit_single", "quantity_min": 1, "quantity_max": 1,
            }],
            "give_items": [{
                "name": "Spirit", "quantity": 1,
                "quantity_source": "implicit_single", "quantity_min": 1, "quantity_max": 1,
            }],
        }
        structured = {"id": "ad", "tradePostItems": [
            {"itemDetailsId": "snow", "displayName": "Snowcannon", "quantity": 2, "tradeRole": "OFFERED"},
            {"itemDetailsId": "spirit", "displayName": "Spirit", "quantity": 1, "tradeRole": "REQUESTED"},
        ]}
        scanner._apply_structured_quantities(listing, structured)
        item = listing["receive_items"][0]
        self.assertIsNone(item["quantity"])
        self.assertEqual(item["quantity_source"], "uncertain")
        self.assertEqual((item["quantity_min"], item["quantity_max"]), (1, 2))

    def test_rsc_and_explicit_html_quantity_match_as_structured(self):
        listing = {
            "receive_items": [{
                "name": "Snowcannon", "quantity": 2,
                "quantity_source": "explicit", "quantity_min": 2, "quantity_max": 2,
            }],
            "give_items": [{
                "name": "Spirit", "quantity": 1,
                "quantity_source": "implicit_single", "quantity_min": 1, "quantity_max": 1,
            }],
        }
        structured = {"id": "ad", "tradePostItems": [
            {"itemDetailsId": "snow", "displayName": "Snowcannon", "quantity": 2, "tradeRole": "OFFERED"},
            {"itemDetailsId": "spirit", "displayName": "Spirit", "quantity": 1, "tradeRole": "REQUESTED"},
        ]}
        scanner._apply_structured_quantities(listing, structured)
        self.assertEqual(listing["receive_items"][0]["quantity"], 2)
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "structured")


class StructuredFirstPageRecoveryTests(unittest.TestCase):
    def test_rsc_recovers_offer_when_html_card_parser_misses_it(self):
        post = {
            "id": "rsc-only-post",
            "user": {"robloxUserId": "321", "robloxUsername": "fixture", "robloxDisplayName": "Fixture"},
            "tradePostItems": [
                {"itemDetailsId": "b-id", "quantity": 2, "tradeRole": "OFFERED"},
                {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": [],
        }
        page = {"items": [post], "meta": {"cursor": None}}
        payload = json.dumps({"initialPage": page}, separators=(",", ":"))
        html = '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
        with (
            patch("scanner.fetch_bloxswaps", return_value=html),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"id": "a-id", "displayName": "Knife A"},
                "b-id": {"id": "b-id", "displayName": "Knife B"},
            }),
        ):
            listings, stats, _ = scanner.fetch_bloxswaps_all(set())
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["source_ad_id"], "rsc-only-post")
        self.assertEqual(listings[0]["receive_items"][0]["quantity"], 2)
        self.assertEqual(listings[0]["receive_items"][0]["quantity_source"], "structured")
        self.assertEqual(stats["html_parse_warning_count"], 1)
        self.assertTrue(listings[0]["html_card_parse_warning"])

    def test_html_rsc_quantity_disagreement_becomes_uncertain(self):
        api_listing = {
            "id": "id", "owner_url": "owner", "source_text": "api",
            "receive_items": [{
                "name": "Snowcannon", "quantity": 2, "quantity_source": "structured",
                "quantity_min": 2, "quantity_max": 2,
            }],
            "give_items": [{
                "name": "Spirit", "quantity": 1, "quantity_source": "structured",
                "quantity_min": 1, "quantity_max": 1,
            }],
        }
        html_listing = {
            "id": "html-id", "owner_url": "html-owner", "source_text": "html",
            "receive_items": [{
                "name": "Snowcannon", "quantity": 1, "quantity_source": "implicit_single",
                "listed_price": 750.0, "raw_excerpt": "Snowcannon 750.0",
            }],
            "give_items": [{
                "name": "Spirit", "quantity": 1, "quantity_source": "explicit",
                "listed_price": 540.0, "raw_excerpt": "Spirit x1",
            }],
        }
        merged = scanner._merge_html_quantity_diagnostics(api_listing, html_listing)
        item = merged["receive_items"][0]
        self.assertIsNone(item["quantity"])
        self.assertEqual(item["quantity_source"], "uncertain")
        self.assertEqual((item["quantity_min"], item["quantity_max"]), (1, 2))
        self.assertEqual(item["listed_price"], 750.0)
        self.assertEqual(merged["id"], "html-id")


class CursorPaginationTests(unittest.TestCase):
    @staticmethod
    def _first_page_html():
        page = {
            "items": [{
                "id": "first-post",
                "tradePostItems": [
                    {"itemDetailsId": "b-id", "quantity": 1, "tradeRole": "OFFERED"},
                    {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
                ],
                "requestedWildcards": [],
            }],
            "meta": {"cursor": "cursor-1"},
        }
        payload = json.dumps({"initialPage": page}, separators=(",", ":"))
        return (
            '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
            '<article><div>Propose <img alt="Knife B"></div>'
            '<div>Recherche <img alt="Knife A"></div>'
            '<a href="/fr/trade-mm2?ad=first-post">Discussion</a>'
            '<a href="/fr/users/123">Owner</a></article>'
        )

    @staticmethod
    def _post(post_id):
        return {
            "id": post_id,
            "user": {
                "robloxUserId": "123",
                "robloxUsername": "fixture_user",
                "robloxDisplayName": "Fixture Owner",
            },
            "tradePostItems": [
                {"itemDetailsId": "b-id", "quantity": 2, "tradeRole": "OFFERED"},
                {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": [],
            "lastRefreshedAt": "2026-11-12T00:00:00Z",
        }

    def test_cursor_pages_are_scanned_until_first_seen_ad(self):
        responses = [
            {"items": [self._post("post-2"), self._post("post-3")], "meta": {"cursor": "cursor-2"}},
            {"items": [self._post("post-4")] + [self._post(f"seen-old-{i}") for i in range(25)], "meta": {"cursor": None}},
        ]
        with (
            patch("scanner.fetch_bloxswaps", return_value=self._first_page_html()),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"id": "a-id", "displayName": "Knife A"},
                "b-id": {"id": "b-id", "displayName": "Knife B"},
            }),
            patch("scanner._last_successful_scan_timestamp", return_value=datetime.now(timezone.utc).timestamp() - 60),
            patch("scanner.bloxswaps_api_get_json", side_effect=responses) as api,
        ):
            listings, stats, discovered = scanner.fetch_bloxswaps_all({"seen-old"} | {f"seen-old-{i}" for i in range(25)})
        self.assertEqual(api.call_count, 2)
        self.assertEqual([x["source_ad_id"] for x in listings], ["first-post", "post-2", "post-3", "post-4"])
        self.assertEqual(stats["pagination_pages"], 3)
        self.assertTrue(stats["pagination_complete"])
        self.assertTrue(stats["seen_boundary_reached"])
        self.assertEqual(len([x for x in listings if x["source_ad_id"].startswith("seen-old-")]), 0)
        self.assertEqual(listings[1]["receive_items"][0]["quantity_source"], "structured")
        self.assertEqual(listings[1]["receive_items"][0]["quantity"], 2)
        self.assertEqual(listings[1]["owner_url"], "https://bloxswaps.com/fr/users/123")
        self.assertIn("ad=post-2", listings[1]["url"])

    def test_pending_listing_is_found_after_an_already_seen_boundary(self):
        pending_post = self._post("pending-post")
        responses = [{
            "items": [pending_post, self._post("seen-boundary")],
            "meta": {"cursor": None},
        }]
        with (
            patch("scanner.fetch_bloxswaps", return_value=self._first_page_html()),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"id": "a-id", "displayName": "Knife A"},
                "b-id": {"id": "b-id", "displayName": "Knife B"},
            }),
            patch("scanner.bloxswaps_api_get_json", side_effect=responses),
        ):
            listings, stats, _ = scanner.fetch_bloxswaps_all(
                {"first-post", "seen-boundary"}, {"pending-post"}
            )
        self.assertEqual([item["source_ad_id"] for item in listings], ["pending-post"])
        self.assertEqual(stats["pending_found_ids"], ["pending-post"])
        self.assertEqual(stats["pending_remaining_ids"], [])
        self.assertFalse(stats["seen_boundary_reached"])
        self.assertTrue(stats["pagination_complete"])
        self.assertEqual(stats["pagination_pages"], 2)


    def test_missing_seen_state_seeds_current_page_without_replaying_history(self):
        with patch("scanner.fetch_bloxswaps", return_value=self._first_page_html()):
            listings, stats, seed_ids = scanner.fetch_bloxswaps_all(None)
        self.assertEqual(listings, [])
        self.assertTrue(stats["cold_start"])
        self.assertEqual(seed_ids, ["first-post"])


class CardScopedAjoutFilterTests(unittest.TestCase):
    def test_ajout_marker_is_scoped_to_its_own_card_not_header_or_neighbor(self):
        html = """
        <header>Ajout guide and filters</header>
        <article>
          <div>Propose <img alt="Knife B"></div>
          <div>Recherche <img alt="Knife A"></div>
          <span>Ajouts</span>
          <a href="/fr/trade-mm2?ad=with-add">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        <article>
          <div>Propose <img alt="Knife D"></div>
          <div>Recherche <img alt="Knife C"></div>
          <a href="/fr/trade-mm2?ad=clean-card">Discussion</a>
          <a href="/fr/users/456">Owner</a>
        </article>
        """
        listings, stats = parse_page(html)
        self.assertEqual([item["source_ad_id"] for item in listings], ["clean-card"])
        self.assertEqual(stats["addition_count"], 1)
        self.assertEqual(stats["addition_ids"], ["with-add"])


class LiveBloxSwapsFixtureTests(unittest.TestCase):
    def test_redacted_real_bloxswaps_card_matches_rsc_quantities(self):
        fixture = Path(__file__).parent / "fixtures" / "bloxswaps-live-sample.html"
        html = fixture.read_text(encoding="utf-8")
        listings, stats = parse_page(html, {
            "136e4b17-a761-4c2e-aa17-cb5ef7153148": {"displayName": "Emberbow"},
            "cmazobvc2001enu708o9j9vtg": {"displayName": "Harvester"},
            "cmazobw1m00linu70am6x8ca3": {"displayName": "Icepiercer"},
        })
        self.assertEqual(len(listings), 1)
        self.assertEqual(stats["structured_matched_count"], 1)
        listing = listings[0]
        self.assertTrue(listing["source_ad_id"])
        self.assertEqual(listing["structured_trade_post_id"], listing["source_ad_id"])
        all_items = listing["receive_items"] + listing["give_items"]
        for item in all_items:
            self.assertEqual(item["quantity_source"], "structured")
            self.assertGreaterEqual(item["quantity"], 1)
            self.assertLessEqual(item["quantity"], 100)
        self.assertTrue(any(item["quantity"] > 1 for item in all_items))
        self.assertTrue(any(item.get("listed_price") is not None for item in all_items))
        self.assertTrue(any(re.search(r"\d+\.\d{1,2}", item.get("raw_excerpt", "")) for item in all_items))


class StructuredQuantityTests(unittest.TestCase):
    @staticmethod
    def _html_with_rsc(trade_items, wildcards=None, visible_badge=""):
        page = {
            "items": [{
                "id": "structured-ad-1",
                "tradePostItems": trade_items,
                "requestedWildcards": wildcards or [],
            }],
            "meta": {"cursor": None},
        }
        payload = json.dumps({"initialPage": page}, ensure_ascii=False, separators=(",", ":"))
        return (
            '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
            '<article><div>Propose <img alt="Knife B">' + visible_badge + '</div>'
            '<div>Recherche <img alt="Knife A"></div>'
            '<a href="/fr/trade-mm2?ad=structured-ad-1">Discussion</a>'
            '<a href="/fr/users/123">Owner</a></article>'
        )

    def test_rsc_quantity_disagreement_with_implicit_html_single_is_uncertain(self):
        html = self._html_with_rsc([
            {"itemDetailsId": "b-id", "quantity": 2, "tradeRole": "OFFERED"},
            {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
        ])
        listing = parse_page(html, {"b-id": {"displayName": "Knife B"}, "a-id": {"displayName": "Knife A"}})[0][0]
        self.assertIsNone(listing["receive_items"][0]["quantity"])
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "uncertain")
        self.assertEqual(
            (listing["receive_items"][0]["quantity_min"], listing["receive_items"][0]["quantity_max"]),
            (1, 2),
        )
        self.assertEqual(listing["give_items"][0]["quantity"], 1)
        self.assertEqual(listing["give_items"][0]["quantity_source"], "structured")

    def test_rsc_and_explicit_html_quantity_disagreement_becomes_uncertain(self):
        html = self._html_with_rsc([
            {"itemDetailsId": "b-id", "quantity": 1, "tradeRole": "OFFERED"},
            {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
        ], visible_badge='<span>x2</span>')
        listing = parse_listings(html)[0]
        self.assertIsNone(listing["receive_items"][0]["quantity"])
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "uncertain")

    def test_structured_add_wildcard_excludes_card_even_without_visible_ajout_text(self):
        html = self._html_with_rsc([
            {"itemDetailsId": "b-id", "quantity": 1, "tradeRole": "OFFERED"},
            {"itemDetailsId": "a-id", "quantity": 1, "tradeRole": "REQUESTED"},
        ], wildcards=["ADD"])
        listings, stats = parse_page(html)
        self.assertEqual(listings, [])
        self.assertEqual(stats["addition_count"], 1)


class QuantityIncidentReproductionTests(unittest.TestCase):
    def test_explicit_quantity_regex_accepts_split_text_nodes(self):
        self.assertEqual(scanner._explicit_stack_quantities("Snowcannon 750.0 x 2"), [2])
        self.assertEqual(scanner._explicit_stack_quantities("Snowcannon 750.0 x" + chr(160) + "2"), [2])
        self.assertEqual(scanner._explicit_stack_quantities("Snowcannon 750.0 x٢"), [2])

    def test_split_x_and_digit_after_price_is_quantity_two(self):
        html = """
        <article>
          <div>Propose
            <div class="item-card">
              <img alt="Snowcannon">
              <span class="item-name">Snowcannon</span>
              <img alt="coin">
              <span class="price">750.0</span>
              <span class="badge-part-a">x</span><span class="badge-part-b">2</span>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
          <a href="/fr/trade-mm2?ad=split-x2">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        item = listing["receive_items"][0]
        self.assertIn("x 2", item["raw_excerpt"])
        self.assertEqual(item["quantity"], 2, repr(item))
        self.assertEqual(item["quantity_source"], "explicit")
        self.assertEqual(item["listed_price"], 750.0)

    def test_flat_shared_container_badge_never_leaks_to_opposite_side(self):
        html = """
        <article>
          <div class="flat-trade-card">
            <span>Propose</span>
            <img alt="Knife A">
            <span class="quantity-badge">x2</span>
            <span>Recherche</span>
            <img alt="Knife B">
          </div>
          <a href="/fr/trade-mm2?ad=flat-isolation">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 2)
        self.assertEqual(listing["give_items"][0]["quantity"], 1)
        self.assertEqual(listing["give_items"][0]["quantity_source"], "implicit_single")

    def test_unreadable_quantity_marker_is_uncertain_not_silently_one(self):
        html = """
        <article>
          <div>Propose
            <div class="item-card">
              <img alt="Harvester">
              <span class="quantity-badge">x?</span>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        item = listing["receive_items"][0]
        self.assertIsNone(item["quantity"])
        self.assertEqual(item["quantity_source"], "uncertain")

    def test_decimal_price_is_diagnostic_only_and_single_is_explicitly_implicit(self):
        html = """
        <article>
          <div>Propose
            <div class="item-card">
              <img alt="Snowcannon">
              <span class="price">750.0</span>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
        </article>
        """
        listing = parse_listings(html)[0]
        item = listing["receive_items"][0]
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(item["quantity_source"], "implicit_single")
        self.assertEqual(item["listed_price"], 750.0)

    def test_harvester_two_for_spirit_profit_is_ten_when_badge_is_split(self):
        html = """
        <article>
          <div>Propose <img alt="Spirit"></div>
          <div>Recherche
            <div class="item-card">
              <img alt="Harvester">
              <span class="quantity-prefix">x</span><span class="quantity-value">2</span>
            </div>
          </div>
        </article>
        """
        listing = parse_listings(html)[0]
        values = {
            "harvester": {"name": "Harvester", "value": 265},
            "spirit": {"name": "Spirit", "value": 540},
        }
        result, missing = scanner.evaluate_listing(listing, values)
        self.assertEqual(missing, [])
        self.assertEqual(result["give_total"], 530)
        self.assertEqual(result["receive_total"], 540)
        self.assertEqual(result["difference"], 10)


class StructuredHtmlConflictRegressionTests(unittest.TestCase):
    def test_data_quantity_conflicting_with_visible_badge_is_uncertain_not_crash(self):
        html = """
        <article>
          <div>Propose
            <div class="item-card" data-quantity="2">
              <img alt="Snowcannon">
              <span class="quantity-badge">x3</span>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
          <a href="/fr/trade-mm2?ad=structured-html-conflict">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        item = listing["receive_items"][0]
        self.assertIsNone(item["quantity"])
        self.assertEqual(item["quantity_source"], "uncertain")
        self.assertEqual((item["quantity_min"], item["quantity_max"]), (2, 3))




class QuantityBadgeAttributeBoundaryTests(unittest.TestCase):
    def _listing(self, badge):
        html = f"""
        <article>
          <div>Propose
            <div class="item-tile">
              <div class="weapon-image"><img alt="Snowcannon"></div>
              <div class="details">
                <span class="price">750.0</span>
                {badge}
              </div>
            </div>
          </div>
          <div>Recherche <img alt="Spirit"></div>
          <a href="/fr/trade-mm2?ad=badge-attribute">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        return parse_listings(html)[0]["receive_items"][0]

    def test_aria_label_on_badge_sibling_after_decimal_price_is_explicit(self):
        item = self._listing('<span class="stack-badge" aria-label="x2"></span>')
        self.assertEqual(item["quantity"], 2, repr(item))
        self.assertEqual(item["quantity_source"], "explicit")
        self.assertEqual(item["listed_price"], 750.0)

    def test_title_on_badge_sibling_after_decimal_price_is_explicit(self):
        item = self._listing('<span class="stack-badge" title="×2"></span>')
        self.assertEqual(item["quantity"], 2, repr(item))
        self.assertEqual(item["quantity_source"], "explicit")

    def test_data_quantity_on_badge_sibling_is_not_ignored(self):
        item = self._listing('<span class="stack-badge" data-quantity="2"></span>')
        self.assertEqual(item["quantity"], 2, repr(item))
        self.assertEqual(item["quantity_source"], "structured")

    def test_data_quantity_badge_does_not_leak_across_propose_recherche_boundary(self):
        html = """
        <article>
          <div class="flat-trade-card">
            <span>Propose</span><img alt="Knife A">
            <span class="stack-badge" data-quantity="2"></span>
            <span>Recherche</span><img alt="Knife B">
          </div>
          <a href="/fr/trade-mm2?ad=data-boundary">Discussion</a>
          <a href="/fr/users/123">Owner</a>
        </article>
        """
        listing = parse_listings(html)[0]
        self.assertEqual(listing["receive_items"][0]["quantity"], 2)
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "structured")
        self.assertEqual(listing["give_items"][0]["quantity"], 1)
        self.assertEqual(listing["give_items"][0]["quantity_source"], "implicit_single")

    def test_nested_split_badge_after_price_with_comment_is_two_not_one(self):
        item = self._listing(
            '<span class="stack-badge"><b>x</b><!-- split --><i>2</i></span>'
        )
        self.assertEqual(item["quantity"], 2, repr(item))
        self.assertEqual(item["quantity_source"], "explicit")




class QuantityDomFuzzRegressionTests(unittest.TestCase):
    def test_seeded_dom_layout_variants_keep_each_badge_with_its_own_weapon(self):
        import random
        rng = random.Random(20261112)
        wrappers = [
            ("<div class='item-wrap'>", "</div>"),
            ("<section class='tile'><div class='inner'>", "</div></section>"),
            ("<div><span class='content'>", "</span></div>"),
            ("<div class='relative'><div class='absolute'>", "</div></div>"),
        ]
        badge_variants = [
            '<span class="quantity-badge">x2</span>',
            '<span class="quantity-badge"><b>x</b><!-- split --><i>2</i></span>',
            '<span class="quantity-badge" title="×2"></span>',
            '<span class="quantity-badge" data-quantity="2"></span>',
        ]
        for case in range(80):
            open_a, close_a = rng.choice(wrappers)
            open_b, close_b = rng.choice(wrappers)
            badge_a = rng.choice(badge_variants)
            badge_b = rng.choice([
                "",
                '<span class="quantity-badge">x4</span>',
                '<span class="quantity-badge" aria-label="4x"></span>',
            ])
            before_a = rng.choice([True, False])
            before_b = rng.choice([True, False])
            tile_a = (
                (badge_a if before_a else "")
                + open_a + '<img alt="Knife A"><img alt="coin">'
                + '<span class="price">750.0</span>'
                + ("" if before_a else badge_a) + close_a
            )
            tile_b = (
                (badge_b if before_b else "")
                + open_b + '<img alt="Knife B">'
                + ("" if before_b else badge_b) + close_b
            )
            html = f"""
            <article>
              <div>Propose {tile_a}</div>
              <div>Recherche {tile_b}</div>
              <a href="/fr/trade-mm2?ad=fuzz-{case}">Discussion</a>
              <a href="/fr/users/123">Owner</a>
            </article>
            """
            with self.subTest(case=case, before_a=before_a, before_b=before_b):
                listing = parse_listings(html)[0]
                self.assertEqual(listing["receive_items"][0]["quantity"], 2)
                self.assertIn(
                    listing["receive_items"][0]["quantity_source"],
                    {"explicit", "structured"},
                )
                expected_b = 4 if badge_b else 1
                self.assertEqual(listing["give_items"][0]["quantity"], expected_b)
                self.assertEqual(
                    listing["give_items"][0]["quantity_source"],
                    "explicit" if badge_b and "data-quantity" not in badge_b else (
                        "structured" if badge_b else "implicit_single"
                    ),
                )
                self.assertEqual(listing["receive_items"][0]["listed_price"], 750.0)


class DuplicateBatchRegressionTests(unittest.TestCase):
    def test_duplicate_rows_with_same_listing_id_are_collapsed(self):
        first = {"id": "stable-ad-1", "source_ad_id": "ad-1", "receive_items": [], "give_items": []}
        duplicate = dict(first)
        self.assertEqual(scanner.deduplicate_listings([first, duplicate]), [first])

    def test_ad_id_stays_stable_when_item_quantity_or_details_change(self):
        first_receive = [{"name": "Snowcannon", "quantity": 1, "quantity_source": "structured"}]
        second_receive = [{"name": "Snowcannon", "quantity": 2, "quantity_source": "structured"}]
        give = [{"name": "Spirit", "quantity": 1, "quantity_source": "structured"}]
        url = "https://bloxswaps.com/fr/trade-mm2?ad=stable-ad-123"
        owner = "https://bloxswaps.com/fr/users/123"
        first_id = scanner.stable_listing_id(owner, first_receive, give, url)
        second_id = scanner.stable_listing_id(owner, second_receive, give, url)
        self.assertEqual(first_id, second_id)


class SupremeETagCacheTests(unittest.TestCase):
    def test_expired_cache_reuses_page_only_after_matching_etag_304(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supreme.json"
            rows = [
                {"name": f"ETag Item {i}", "value_source_b": i + 1, "value_source_a": None}
                for i in range(100)
            ]
            cached_values = {
                scanner.normalize_name(row["name"]): {
                    "name": row["name"], "value": row["value_source_b"],
                    "secondary_value": None,
                    "variants": [{
                        "name": row["name"], "value": row["value_source_b"],
                        "secondary_value": None,
                    }],
                }
                for row in rows
            }
            etag = '"supreme-page-v1"'
            path.write_text(json.dumps({
                "schema_version": 2,
                "fetched_at": 1,
                "values": cached_values,
                "pages": {"0": {"etag": etag, "total": 100, "items": rows}},
            }), encoding="utf-8")
            captured = {}

            def not_modified(request, timeout, label, **kwargs):
                captured["request"] = request
                self.assertTrue(kwargs.get("return_headers"))
                self.assertTrue(kwargs.get("allow_not_modified"))
                return None, {"ETag": etag}

            with (
                patch("scanner.VALUES_FILE", path),
                patch("scanner.VALUES_TTL_SECONDS", 300),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.time.sleep"),
                patch("scanner.RBLXVALUE_API_KEY", "test-key"),
                patch("scanner.request_with_retry", side_effect=not_modified),
            ):
                loaded = scanner.load_supreme_values()

            self.assertEqual(len(loaded), 100)
            self.assertEqual(
                captured["request"].get_header("If-none-match"), etag
            )

    def test_conditional_helper_reports_304_for_caller_cache_validation(self):
        with patch("scanner.RBLXVALUE_API_KEY", "test-key"), patch(
            "scanner.request_with_retry", return_value=(None, {"ETag": '"missing"'})
        ):
            payload, etag, not_modified = scanner.api_get_json_conditional(
                "/items", {"limit": 100, "offset": 0}, '"missing"'
            )
        self.assertIsNone(payload)
        self.assertEqual(etag, '"missing"')
        self.assertTrue(not_modified)


class AdditionalHardeningRegressionTests(unittest.TestCase):
    def test_single_malformed_card_is_reportable(self):
        self.assertTrue(scanner.should_alert_malformed_cards(1))
        self.assertTrue(scanner.should_alert_malformed_cards(3))
        self.assertFalse(scanner.should_alert_malformed_cards(0))
        self.assertFalse(scanner.should_alert_malformed_cards(None))

    def test_initial_supreme_fetch_persists_server_etag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supreme.json"
            rows = [
                {"name": f"Initial ETag Item {i}", "value_source_b": i + 1, "value_source_a": None}
                for i in range(100)
            ]
            etag = '"supreme-initial-v1"'
            captured = {}

            def first_page(request, timeout, label, **kwargs):
                captured["request"] = request
                self.assertIsNone(request.get_header("If-none-match"))
                return json.dumps({"items": rows, "total": 100}).encode(), {"ETag": etag}

            with (
                patch("scanner.VALUES_FILE", path),
                patch("scanner.RBLXVALUE_API_KEY", "test-key"),
                patch("scanner.request_with_retry", side_effect=first_page),
                patch("scanner.time.sleep"),
            ):
                values = scanner.load_supreme_values()

            cache = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(values), 100)
            self.assertEqual(cache["pages"]["0"]["etag"], etag)
            self.assertEqual(len(cache["pages"]["0"]["items"]), 100)


class LegacyAlertStateMigrationTests(unittest.TestCase):
    def test_old_quantity_sensitive_hash_is_recognized_as_already_alerted(self):
        receive = [{"name": "Snowcannon", "quantity": 1, "quantity_source": "structured"}]
        give = [{"name": "Spirit", "quantity": 1, "quantity_source": "structured"}]
        owner = "https://bloxswaps.com/fr/users/123"
        url = "https://bloxswaps.com/fr/trade-mm2?ad=legacy-migration-ad"
        legacy_id = scanner.legacy_stable_listing_id(owner, receive, give, url)
        stable_id = scanner.stable_listing_id(owner, receive, give, url)
        self.assertNotEqual(legacy_id, stable_id)
        listing = {"id": stable_id, "legacy_id": legacy_id}
        self.assertEqual(scanner.legacy_alert_state(listing, {legacy_id}, set()), "alerted")

    def test_old_uncertain_hash_is_quarantined_instead_of_resent(self):
        listing = {"id": "new-stable-id", "legacy_id": "old-quantity-hash"}
        self.assertEqual(
            scanner.legacy_alert_state(listing, set(), {"old-quantity-hash"}),
            "uncertain",
        )
        self.assertEqual(scanner.legacy_alert_state(listing, set(), set()), "")


if __name__ == "__main__":
    unittest.main()


class HardeningRegressionTests(unittest.TestCase):
    def test_refreshed_seen_post_at_feed_head_does_not_hide_new_posts_below(self):
        refreshed = CursorPaginationTests._post("seen-refreshed")
        refreshed["lastRefreshedAt"] = datetime.now(timezone.utc).isoformat()
        refreshed["isPinned"] = True
        page = {
            "items": [refreshed],
            "meta": {"cursor": "cursor-after-refreshed"},
        }
        payload = json.dumps({"initialPage": page}, separators=(",", ":"))
        html = (
            '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
        )
        responses = [{
            "items": [
                CursorPaginationTests._post("new-below-1"),
                CursorPaginationTests._post("new-below-2"),
            ],
            "meta": {"cursor": None},
        }]
        with (
            patch("scanner.fetch_bloxswaps", return_value=html),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"id": "a-id", "displayName": "Knife A"},
                "b-id": {"id": "b-id", "displayName": "Knife B"},
            }),
            patch("scanner._last_successful_scan_timestamp", return_value=datetime.now(timezone.utc).timestamp() - 60),
            patch("scanner.bloxswaps_api_get_json", side_effect=responses) as api,
        ):
            listings, stats, discovered = scanner.fetch_bloxswaps_all({"seen-refreshed"})
        self.assertEqual(api.call_count, 1)
        found_ids = [item["source_ad_id"] for item in listings]
        self.assertIn("seen-refreshed", found_ids)
        self.assertIn("new-below-1", found_ids)
        self.assertIn("new-below-2", found_ids)
        self.assertNotIn("seen-refreshed", stats.get("seen_ids_skipped", []))

    def test_html_rsc_merge_matches_by_item_id_not_list_position(self):
        api_listing = {
            "id": "api", "owner_url": "owner", "source_text": "api",
            "receive_items": [
                {"name": "Knife A", "structured_item_id": "a-id", "quantity": 2,
                 "quantity_source": "structured", "quantity_min": 2, "quantity_max": 2},
                {"name": "Knife B", "structured_item_id": "b-id", "quantity": 4,
                 "quantity_source": "structured", "quantity_min": 4, "quantity_max": 4},
            ],
            "give_items": [{"name": "Knife C", "structured_item_id": "c-id", "quantity": 1,
                            "quantity_source": "structured", "quantity_min": 1, "quantity_max": 1}],
        }
        html_listing = {
            "id": "html", "owner_url": "html-owner", "source_text": "html",
            "receive_items": [
                {"name": "Knife B", "structured_item_id": "b-id", "quantity": 4,
                 "quantity_source": "explicit", "listed_price": 4, "raw_excerpt": "B x4"},
                {"name": "Knife A", "structured_item_id": "a-id", "quantity": 2,
                 "quantity_source": "explicit", "listed_price": 2, "raw_excerpt": "A x2"},
            ],
            "give_items": [{"name": "Knife C", "structured_item_id": "c-id", "quantity": 1,
                            "quantity_source": "explicit", "listed_price": 1, "raw_excerpt": "C x1"}],
        }
        merged = scanner._merge_html_quantity_diagnostics(api_listing, html_listing)
        self.assertEqual(
            [(item["name"], item["quantity"], item["quantity_source"])
             for item in merged["receive_items"]],
            [("Knife A", 2, "structured"), ("Knife B", 4, "structured")],
        )

    def test_missing_rsc_data_fails_closed_without_loop_environment_flag(self):
        html = "<html><body><article>Propose <img alt='Knife B'> Recherche <img alt='Knife A'></article></body></html>"
        with patch.dict(os.environ, {}, clear=True), patch(
            "scanner.fetch_bloxswaps", return_value=html
        ):
            with self.assertRaisesRegex(RuntimeError, "RSC"):
                scanner.fetch_bloxswaps_all(set())


class WatchWindowRegressionTests(unittest.TestCase):
    def _listing(self, receive_quantity=1):
        return {
            "source_ad_id": "watch-ad",
            "receive_items": [{"name": "Knife A", "quantity": receive_quantity,
                               "quantity_min": receive_quantity, "quantity_max": receive_quantity,
                               "quantity_source": "structured"}],
            "give_items": [{"name": "Knife B", "quantity": 1, "quantity_min": 1,
                            "quantity_max": 1, "quantity_source": "structured"}],
        }

    def test_supreme_value_change_changes_watch_fingerprint(self):
        listing = self._listing()
        values_a = {
            "knife a": {"name": "Knife A", "value": 100},
            "knife b": {"name": "Knife B", "value": 110},
        }
        values_b = {
            "knife a": {"name": "Knife A", "value": 150},
            "knife b": {"name": "Knife B", "value": 110},
        }
        first = scanner.listing_watch_fingerprints(listing, values_a)
        second = scanner.listing_watch_fingerprints(listing, values_b)
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])

    def test_owner_quantity_edit_changes_watch_fingerprint(self):
        first = scanner.listing_watch_fingerprints(self._listing(1), {})
        second = scanner.listing_watch_fingerprints(self._listing(3), {})
        self.assertNotEqual(first[0], second[0])

    def test_watch_state_round_trips_and_expires_after_six_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            with patch("scanner.WATCH_FILE", path), patch("scanner.time.time", return_value=1000):
                state = {"watch-ad": {"content_hash": "c", "values_hash": "v",
                                      "created_at": 1000, "updated_at": 1000}}
                scanner.save_watch_state(state)
                self.assertEqual(scanner.load_watch_state(), state)
            with patch("scanner.WATCH_FILE", path), patch("scanner.time.time", return_value=1000 + 6 * 3600 + 1):
                self.assertEqual(scanner.load_watch_state(), {})


class CatalogueRefreshRegressionTests(unittest.TestCase):
    def test_unknown_item_catalogue_refresh_bypasses_hour_ttl_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "items.json"
            with (
                patch("scanner.BLOXSWAPS_ITEMS_FILE", path),
                patch("scanner._LAST_ITEMS_FORCE_REFRESH_AT", 0),
                patch("scanner.time.time", return_value=1000),
                patch("scanner.bloxswaps_api_get_json", return_value=[
                    {"id": "old", "displayName": "Old Item"}
                ]) as api,
            ):
                first = scanner.fetch_bloxswaps_items_map()
                with patch("scanner.time.time", return_value=1001), patch(
                    "scanner.bloxswaps_api_get_json",
                    return_value=[
                        {"id": "old", "displayName": "Old Item"},
                        {"id": "new", "displayName": "New Item"},
                    ],
                ):
                    refreshed = scanner.fetch_bloxswaps_items_map(force_refresh=True)
                self.assertIn("old", first)
                self.assertIn("new", refreshed)


class ColdStartRecentOfferTests(unittest.TestCase):
    def test_cold_start_evaluates_recently_refreshed_post(self):
        post = CursorPaginationTests._post("recent-cold-post")
        post["lastRefreshedAt"] = datetime.now(timezone.utc).isoformat()
        page = {"items": [post], "meta": {"cursor": None}}
        payload = json.dumps({"initialPage": page}, separators=(",", ":"))
        html = '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
        with (
            patch("scanner.fetch_bloxswaps", return_value=html),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"id": "a-id", "displayName": "Knife A"},
                "b-id": {"id": "b-id", "displayName": "Knife B"},
            }),
        ):
            listings, stats, discovered = scanner.fetch_bloxswaps_all(None)
        self.assertTrue(stats["cold_start"])
        self.assertEqual([item["source_ad_id"] for item in listings], ["recent-cold-post"])
        self.assertEqual(discovered, ["recent-cold-post"])


class MaintenanceDefaultModeTests(unittest.TestCase):
    def test_maintenance_paths_have_no_loop_environment_gate(self):
        import inspect
        self.assertNotIn("MM2_SCAN_LOOP", inspect.getsource(scanner.fetch_bloxswaps_all))
        self.assertNotIn("MM2_SCAN_LOOP", inspect.getsource(scanner.scan_once))

    def test_default_maintenance_alerts_send_with_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "maintenance.json"
            with patch("scanner.MAINTENANCE_FILE", state_file), \
                 patch("scanner.BOT_TOKEN", "test-token"), \
                 patch("scanner.CHAT_ID", "123"), \
                 patch("scanner.send_telegram") as send:
                keys = [
                    "malformed-feed-cards", "pagination-incomplete",
                    "pending-ads-not-found", "unmatched:test",
                    "telegram-quarantine-expired", "rsc-data-missing",
                ]
                for key in keys:
                    self.assertTrue(scanner.send_maintenance_alert(key, "test notice", cooldown_seconds=3600))
                self.assertFalse(scanner.send_maintenance_alert(keys[0], "duplicate notice", cooldown_seconds=3600))
            self.assertEqual(send.call_count, len(keys))


class PaginationFailureSafetyTests(unittest.TestCase):
    def test_api_failure_does_not_mark_unscanned_posts_seen(self):
        post = CursorPaginationTests._post("first-unseen-post")
        page = {"items": [post], "meta": {"cursor": "cursor-more"}}
        payload = json.dumps({"initialPage": page}, separators=(",", ":"))
        html = '<script>self.__next_f.push([1,' + json.dumps(payload) + ']);</script>'
        with (
            patch("scanner.fetch_bloxswaps", return_value=html),
            patch("scanner.fetch_bloxswaps_items_map", return_value={
                "a-id": {"displayName": "Knife A"}, "b-id": {"displayName": "Knife B"}
            }),
            patch("scanner.bloxswaps_api_get_json", side_effect=RuntimeError("API pagination failed")),
            patch("scanner.save_seen_ads") as save_seen,
        ):
            with self.assertRaisesRegex(RuntimeError, "API pagination failed"):
                scanner.fetch_bloxswaps_all(set())
        save_seen.assert_not_called()


class WatchValueReevaluationTests(unittest.TestCase):
    def test_watched_unprofitable_offer_becomes_profitable_after_supreme_value_change(self):
        listing = {
            "id": "stable-ad-id", "source_ad_id": "same-ad",
            "give_items": [{"name": "Knife A", "quantity": 1, "quantity_source": "structured",
                            "quantity_min": 1, "quantity_max": 1}],
            "receive_items": [{"name": "Knife B", "quantity": 1, "quantity_source": "structured",
                               "quantity_min": 1, "quantity_max": 1}],
        }
        values_before = {
            "knife a": {"name": "Knife A", "value": 20},
            "knife b": {"name": "Knife B", "value": 10},
        }
        values_after = {
            "knife a": {"name": "Knife A", "value": 20},
            "knife b": {"name": "Knife B", "value": 30},
        }
        before, _ = scanner.evaluate_listing(listing, values_before)
        self.assertFalse(scanner.qualifies(before))
        with tempfile.TemporaryDirectory() as directory, patch(
            "scanner.WATCH_FILE", Path(directory) / "watch.json"
        ):
            state = {}
            scanner.update_watch_entry(state, listing, values_before)
            after, _ = scanner.evaluate_listing(listing, values_after)
            self.assertTrue(scanner.qualifies(after))
            self.assertTrue(scanner.update_watch_entry(state, listing, values_after))


class PollingLoopTests(unittest.TestCase):
    def test_loop_runs_a_scan_and_returns_before_job_timeout(self):
        with patch("scanner.scan_once") as scan:
            result = scanner.run_scan_loop(duration_seconds=1, interval_seconds=30)
        scan.assert_called_once()
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["successful_cycles"], 1)


class AliasSuggestionToolTests(unittest.TestCase):
    def test_ambiguous_candidates_are_suggestions_only(self):
        from tools.suggest_aliases import build_suggestions
        report = build_suggestions(
            {"1": {"displayName": "Harvaster"}},
            {"harvester": {"name": "Harvester", "value": 265},
             "harvest": {"name": "Harvest", "value": 100}},
        )
        self.assertEqual(report[0]["status"], "ambiguous_manual_review")

    def test_only_explicitly_approved_exact_catalogue_alias_is_written(self):
        from tools.suggest_aliases import apply_reviewed_aliases
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aliases.json"
            count = apply_reviewed_aliases(
                {"Snowcannon": "Snow Cannon"},
                {"snow-id": {"displayName": "Snowcannon"}},
                {"snow cannon": {"name": "Snow Cannon", "value": 750}},
                path,
            )
            self.assertEqual(count, 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"snowcannon": "Snow Cannon"})
            with self.assertRaisesRegex(ValueError, "exact name"):
                apply_reviewed_aliases(
                    {"Snow Cannon?": "Snow Cannon"},
                    {"snow-id": {"displayName": "Snowcannon"}},
                    {"snow cannon": {"name": "Snow Cannon", "value": 750}},
                    path,
                )


class WorkflowSecurityTests(unittest.TestCase):
    def test_scan_and_state_jobs_have_separate_permissions_and_no_legacy_cache(self):
        workflow = Path(".github/workflows/scan.yml").read_text(encoding="utf-8")
        scan_job, state_job = workflow.split("  persist-state:", 1)
        self.assertIn('cron: "*/15 * * * *"', workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('MM2_SCAN_DURATION_SECONDS: "720"', scan_job)
        self.assertIn('MM2_SCAN_INTERVAL_SECONDS: "45"', scan_job)
        self.assertIn("contents: read", scan_job)
        self.assertNotIn("contents: write", scan_job)
        self.assertIn("contents: write", state_job)
        self.assertNotIn("actions/cache/restore", workflow)

    def test_all_external_actions_are_pinned_to_full_commit_shas(self):
        workflow_dir = Path(".github/workflows")
        for path in workflow_dir.glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if "uses:" not in line:
                    continue
                reference = line.split("uses:", 1)[1].strip()
                self.assertIn("@", reference, msg=f"{path}: {reference}")
                self.assertRegex(reference.rsplit("@", 1)[1], r"^[0-9a-f]{40}$", msg=f"{path}: {reference}")
