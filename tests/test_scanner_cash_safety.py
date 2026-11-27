"""Regression tests for strict ROBASE cash-alert safety gates."""

import unittest
from unittest.mock import patch
from datetime import UTC, datetime

from test_support import IsolatedDataTestCase

import scanner


class ScannerCashSafetyTests(IsolatedDataTestCase):
    def test_value_alert_gate_rejects_non_dictionary_results(self):
        for result in (None, [], "profit", 10):
            with self.subTest(result=result):
                self.assertFalse(scanner.qualifies(result))

    def test_value_alert_gate_rejects_non_finite_metrics(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for field in ("profit_worst", "margin"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value):
                    self.assertFalse(scanner.qualifies({**base, field: value}))

    def test_value_alert_gate_rejects_malformed_metrics(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for field, value in (("profit_worst", None), ("profit_worst", "bad"),
                             ("margin", None), ("margin", "bad")):
            with self.subTest(field=field, value=value):
                self.assertFalse(scanner.qualifies({**base, field: value}))

    def test_value_alert_gate_rejects_invalid_thresholds(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for kwargs in ({"min_diff": float("nan")}, {"min_diff": float("inf")},
                       {"min_percent": float("nan")}, {"min_percent": float("inf")},
                       {"min_diff": -1.0}, {"min_percent": -1.0}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(scanner.qualifies(base, **kwargs))

    def test_value_alert_gate_accepts_finite_verified_positive_trade(self):
        result = {"manual_review": False, "cash_estimate_only": False,
                  "profit_worst": 10.0, "margin": 5.0}
        self.assertTrue(scanner.qualifies(result, min_diff=1.0, min_percent=5.0))

    def test_legacy_estimate_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": True, "manual_review": False,
            "cash_profit_worst": 10000.0, "margin": 500.0, "give_total_max": 10.0,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_manual_review_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False, "manual_review": True,
            "cash_profit_worst": 10000.0, "margin": 500.0, "give_total_max": 10.0,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_non_finite_cash_profit_is_never_alert_eligible(self):
        for profit in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(profit=profit):
                result = {
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "chromas.gg Price (7d)",
                    "listing": {"posted_at": datetime.now(UTC).isoformat()},
                    "cash_profit_worst": profit, "margin": 1000.0, "give_total_max": 10.0,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_non_finite_cash_margin_is_never_alert_eligible(self):
        for margin in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(margin=margin):
                result = {
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "chromas.gg Price (7d)",
                    "listing": {"posted_at": datetime.now(UTC).isoformat()},
                    "cash_profit_worst": 1000.0, "margin": margin, "give_total_max": 10.0,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_negative_cash_profit_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False, "manual_review": False,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
            "cash_profit_worst": -1000.0, "margin": 1000.0, "give_total_max": 10.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_verified_robase_cash_profit_can_pass_alert_gate(self):
        result = {
            "cash_estimate_only": False, "manual_review": False,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
            "cash_profit_worst": 2.0, "margin": 5.0, "give_total_max": 10000.0,
        }
        self.assertTrue(scanner.qualifies_cash(result))

    def test_cash_minimum_margin_threshold_is_enforced(self):
        base = {
            "cash_estimate_only": False, "manual_review": False,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
            "cash_profit_worst": 2.0, "margin": 0.01, "give_total_max": 10000.0,
        }
        with patch.object(scanner, "CASH_MIN_MARGIN_PERCENT", 0.5):
            self.assertFalse(scanner.qualifies_cash(base))
            self.assertTrue(scanner.qualifies_cash({**base, "margin": 0.5}))

    def test_robase_items_resolve_through_exact_rblxvalue_slugs(self):
        names = (
            "Spirit", "Harvester", "Bat", "Immortal", "Icebreaker", "Bloom",
            "Snowstorm", "Candy", "Watergun", "Evergun", "Celestial",
        )
        now = datetime.now(UTC).isoformat()
        values = {}
        cash_items_by_slug = {}
        cash_items_by_name = {}
        rblx_exact_map = {}
        give_items = []
        receive_items = []
        for index, name in enumerate(names):
            slug = name.casefold().replace(" ", "-")
            values[name.casefold()] = {
                "name": name, "slug": slug, "value_source_b": index + 1,
                "stability": "Stable", "category": "Godly", "type": "Knife",
            }
            cash_row = {
                "name": name, "cash_price_usd": 5.0, "price_low_usd": 5.0,
                "price_high_usd": 5.0, "price_display": "$5.00",
                "status": "verified", "verified_at_utc": now,
                "source": "https://robase.gg/item/" + slug, "robase_slug": slug,
                "robase_category": "Godly",
            }
            cash_items_by_slug[slug] = cash_row
            cash_items_by_name[scanner.normalize_cash_name(name)] = cash_row
            rblx_exact_map[slug] = {
                "bloxswaps_slug": slug, "robase_slug": slug,
                "robase_name": name, "robase_identity_source": "test",
                "rblx_slug": slug, "category": "godly", "type": "knife",
            }
            item = {
                "name": name, "catalog_slug": slug, "catalog_rarity": "Godly",
                "catalog_type": "Knife", "catalog_chroma": False,
                "quantity": 1, "quantity_min": 1, "quantity_max": 1,
                "quantity_source": "structured",
            }
            give_items.append(dict(item))
            receive_items.append(dict(item))
        listing = {
            "id": "robase-catalog-integration", "source": "bloxswaps",
            "give_items": give_items, "receive_items": receive_items,
        }
        result, missing = scanner.evaluate_cash_listing(
            listing, values, {}, {
                "items": cash_items_by_name,
                "items_by_slug": cash_items_by_slug,
                "rblx_exact_map": rblx_exact_map,
            },
        )
        self.assertEqual(missing, [])
        self.assertIsNotNone(result)
        self.assertEqual(
            result["cash_price_basis"],
            "chromas.gg Price (7d)",
        )
        self.assertEqual(result["cash_sell_haircut_pct"], 0.0)

        # Cash profit is based on current ROBASE seller-proceeds prices.
        # Non-tradability Stability labels are informational here; only an
        # explicit Untradeable label blocks a cash offer.
        for stability in (
            None, "unrecognized-status", "Decreasing", "Receding", "Unsafe",
            "Unstable", "Volatile", "Fluctuating", "Stable",
        ):
            with self.subTest(received_stability=stability):
                case_values = {key: dict(value) for key, value in values.items()}
                for name in names:
                    case_values[name.casefold()]["stability"] = stability
                accepted, reasons = scanner.evaluate_cash_listing(
                    listing, case_values, {}, {
                        "items": cash_items_by_name,
                        "items_by_slug": cash_items_by_slug,
                        "rblx_exact_map": rblx_exact_map,
                    },
                )
                self.assertIsNotNone(accepted)
                self.assertEqual(reasons, [])

        # A missing Stability field is displayed as N/A, not treated as a
        # missing cash price or a reason to discard a verified cash trade.
        case_values = {key: dict(value) for key, value in values.items()}
        for name in names:
            case_values[name.casefold()].pop("stability", None)
        accepted, reasons = scanner.evaluate_cash_listing(
            listing, case_values, {}, {
                "items": cash_items_by_name,
                "items_by_slug": cash_items_by_slug,
                "rblx_exact_map": rblx_exact_map,
            },
        )
        self.assertIsNotNone(accepted)
        self.assertEqual(reasons, [])

        # Explicitly untradeable items are rejected on either side.
        case_values = {key: dict(value) for key, value in values.items()}
        for name in names:
            case_values[name.casefold()]["stability"] = "Untradeable"
        rejected, reasons = scanner.evaluate_cash_listing(
            listing, case_values, {}, {
                "items": cash_items_by_name,
                "items_by_slug": cash_items_by_slug,
                "rblx_exact_map": rblx_exact_map,
            },
        )
        self.assertIsNone(rejected)
        self.assertTrue(any("untradeable" in reason.casefold() for reason in reasons))

    def test_malformed_cash_profit_or_margin_fails_closed(self):
        for field, value in (
            ("cash_profit_worst", None),
            ("cash_profit_worst", "not-a-number"),
            ("cash_profit_worst", True),
            ("margin", None),
            ("margin", "not-a-number"),
            ("margin", False),
        ):
            with self.subTest(field=field, value=value):
                result = {
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "chromas.gg Price (7d)",
                    "listing": {"posted_at": datetime.now(UTC).isoformat()},
                    "cash_profit_worst": 100.0, "margin": 20.0, "give_total_max": 10.0,
                }
                result[field] = value
                self.assertFalse(scanner.qualifies_cash(result))

    def test_profit_threshold_is_exactly_two_dollars_for_every_trade_size(self):
        base = {
            "manual_review": False, "cash_estimate_only": False,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
            "margin": 5.0, "give_total_max": 4.99,
        }
        self.assertTrue(scanner.qualifies_cash({**base, "cash_profit_worst": 2.00}))
        self.assertFalse(scanner.qualifies_cash({**base, "cash_profit_worst": 1.99}))
        standard = {**base, "give_total_max": 500.0}
        self.assertTrue(scanner.qualifies_cash({**standard, "cash_profit_worst": 2.00}))
        self.assertFalse(scanner.qualifies_cash({**standard, "cash_profit_worst": 1.999}))

    def test_non_dictionary_cash_result_fails_closed(self):
        for result in (None, [], "profit", 10):
            with self.subTest(result=result):
                self.assertFalse(scanner.qualifies_cash(result))

    def test_missing_or_untrusted_cash_provenance_fails_closed(self):
        base = {
            "manual_review": False, "cash_estimate_only": False,
            "cash_price_basis": "chromas.gg Price (7d)",
            "listing": {"posted_at": datetime.now(UTC).isoformat()},
            "cash_profit_worst": 100.0, "margin": 20.0, "give_total_max": 10.0,
        }
        self.assertTrue(scanner.qualifies_cash(base))
        for field in (
            "manual_review", "cash_estimate_only", "cash_price_basis",
            "cash_profit_worst", "margin",
        ):
            result = dict(base)
            result.pop(field)
            with self.subTest(field=field):
                self.assertFalse(scanner.qualifies_cash(result))
        wrong_basis = dict(base, cash_price_basis="unverified legacy estimate only")
        self.assertFalse(scanner.qualifies_cash(wrong_basis))
        generic_only = dict(base, profit_worst=100.0)
        generic_only.pop("cash_profit_worst")
        self.assertFalse(scanner.qualifies_cash(generic_only))


    def test_cash_digest_displays_the_configured_minimum_profit(self):
        opportunity = {
            "listing": {
                "valuation_mode": "cash",
                "source": "bloxswaps",
                "url": "https://bloxswaps.com/trade/example",
            },
            "cash_profit_worst": 2.0,
            "give_lines": [],
            "receive_lines": [],
        }
        with patch.object(scanner, "CASH_MIN_PROFIT_USD", 2.0), patch.object(
            scanner, "CASH_MIN_PROFIT_USD_SMALL", 2.0
        ):
            message = str(scanner.format_opportunity_digest([opportunity]))
        self.assertIn("profit $2.00+", message)

    def test_digest_delivery_quarantines_only_uncertain_offer_and_continues(self):
        items = ["uncertain", "delivered"]
        prepared = []
        sent = []

        def prepare(item):
            prepared.append(item)

        def send(item):
            sent.append(item)
            if item == "uncertain":
                raise scanner.TelegramDeliveryUncertain("network response missing")

        outcomes = scanner.deliver_digest_items_individually(items, prepare, send)
        self.assertEqual(prepared, items)
        self.assertEqual(sent, items)
        self.assertEqual([row[0] for row in outcomes], ["uncertain", "sent"])
        self.assertEqual([row[1] for row in outcomes], items)

    def test_digest_delivery_stops_after_definite_rejection_and_queues_rest(self):
        items = ["delivered", "rejected", "not-attempted"]
        prepared = []
        sent = []

        def prepare(item):
            prepared.append(item)

        def send(item):
            sent.append(item)
            if item == "rejected":
                raise RuntimeError("Telegram rejected request")

        outcomes = scanner.deliver_digest_items_individually(items, prepare, send)
        self.assertEqual(prepared, ["delivered", "rejected"])
        self.assertEqual(sent, ["delivered", "rejected"])
        self.assertEqual(
            [(status, item) for status, item, _error in outcomes],
            [("sent", "delivered"), ("failed", "rejected"), ("not_attempted", "not-attempted")],
        )

    def test_digest_delivery_prepares_each_offer_before_its_send(self):
        events = []
        items = ["a", "b"]

        def prepare(item):
            events.append(("prepare", item))

        def send(item):
            events.append(("send", item))

        outcomes = scanner.deliver_digest_items_individually(items, prepare, send)
        self.assertEqual(
            events,
            [("prepare", "a"), ("send", "a"), ("prepare", "b"), ("send", "b")],
        )
        self.assertTrue(all(status == "sent" for status, _item, _error in outcomes))


    def test_production_cash_evaluator_uses_only_robase_and_bloxswaps_metadata(self):
        now = datetime.now(UTC).isoformat()
        rows, mapping = {}, {}
        for name, price in (("Bat", 3.0), ("Laser", 10.0)):
            slug = name.casefold()
            rows[slug] = {
                "name": name, "cash_price_usd": price, "price_low_usd": price,
                "price_high_usd": price, "price_display": f"${price:.2f}",
                "status": "verified", "verified_at_utc": now,
                "source": "ROBASE completed sales", "robase_slug": slug,
            }
            mapping[slug] = {
                "bloxswaps_slug": slug, "robase_slug": slug, "robase_name": name,
                "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
                "bloxswaps_chroma": False, "bloxswaps_tradable": True,
            }
        def item(name):
            return {
                "name": name, "catalog_slug": name.casefold(),
                "catalog_type": "knife", "catalog_rarity": "godly",
                "catalog_chroma": False, "quantity": 1,
                "quantity_min": 1, "quantity_max": 1,
                "quantity_source": "structured",
            }
        listing = {
            "id": "source-independent-cash-test", "source": "bloxswaps",
            "give_items": [item("Bat")], "receive_items": [item("Laser")],
        }
        result, missing = scanner.evaluate_cash_listing_robase_only(
            listing, {"items_by_slug": rows, "bloxswaps_exact_map": mapping}
        )
        self.assertEqual(missing, [])
        self.assertEqual(result["cash_profit_worst"], 7.0)
        self.assertEqual(result["cash_price_basis"], "chromas.gg Price (7d)")
        self.assertFalse(any("Supreme" in line or "Stability" in line
                             for line in result["give_lines"] + result["receive_lines"]))


    def test_cash_evaluator_allows_surclassements_and_declassements_but_rejects_ajouts_and_offres(self):
        now = datetime.now(UTC).isoformat()
        rows = {
            "bat": {
                "name": "Bat", "cash_price_usd": 5.0, "price_low_usd": 5.0,
                "price_high_usd": 5.0, "price_display": "$5.00",
                "status": "verified", "verified_at_utc": now,
                "source": "https://robase.gg/item/bat", "robase_slug": "bat",
            }
        }
        mapping = {
            "bat": {
                "bloxswaps_slug": "bat", "robase_slug": "bat",
                "robase_name": "Bat", "bloxswaps_type": "knife",
                "bloxswaps_rarity": "godly", "bloxswaps_chroma": False,
                "bloxswaps_tradable": True,
            }
        }
        item = {
            "name": "Bat", "catalog_slug": "bat", "catalog_type": "knife",
            "catalog_rarity": "godly", "catalog_chroma": False,
            "quantity": 1, "quantity_min": 1, "quantity_max": 1,
            "quantity_source": "structured",
        }
        catalog = {"items_by_slug": rows, "bloxswaps_exact_map": mapping}
        base = {
            "id": "wildcard-filter-regression", "source": "bloxswaps",
            "give_items": [dict(item)], "receive_items": [dict(item)],
        }

        for wildcard in ("Surclassements", "Déclassements"):
            with self.subTest(allowed=wildcard):
                result, missing = scanner.evaluate_cash_listing_robase_only(
                    {**base, "requested_wildcards": [wildcard]}, catalog
                )
                self.assertIsNotNone(result)
                self.assertEqual(missing, [])

        for wildcard in ("Ajouts", "Ajout", "Offres", "Offers"):
            with self.subTest(excluded=wildcard):
                result, missing = scanner.evaluate_cash_listing_robase_only(
                    {**base, "requested_wildcards": [wildcard]}, catalog
                )
                self.assertIsNone(result)
                self.assertIn("Ajouts/Offres", missing[0])
        body_only, body_missing = scanner.evaluate_cash_listing_robase_only(
            {**base, "source_text": "Looking for Offers in general; no wildcard metadata"},
            catalog,
        )
        self.assertIsNotNone(body_only)
        self.assertEqual(body_missing, [])

    def test_production_cash_evaluator_rejects_bloxswaps_metadata_mismatch(self):
        now = datetime.now(UTC).isoformat()
        row = {
            "name": "Laser", "cash_price_usd": 10.0, "price_low_usd": 10.0,
            "price_high_usd": 10.0, "price_display": "$10.00",
            "status": "verified", "verified_at_utc": now,
            "source": "ROBASE completed sales", "robase_slug": "laser",
        }
        mapping = {"laser": {
            "robase_slug": "laser", "robase_name": "Laser",
            "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
            "bloxswaps_chroma": False, "bloxswaps_tradable": True,
        }}
        bad_item = {
            "name": "Laser", "catalog_slug": "laser", "catalog_type": "gun",
            "catalog_rarity": "godly", "catalog_chroma": False,
            "quantity": 1, "quantity_min": 1, "quantity_max": 1,
            "quantity_source": "structured",
        }
        listing = {
            "id": "metadata-mismatch", "source": "bloxswaps",
            "give_items": [bad_item], "receive_items": [dict(bad_item)],
        }
        result, missing = scanner.evaluate_cash_listing_robase_only(
            listing, {"items_by_slug": {"laser": row}, "bloxswaps_exact_map": mapping}
        )
        self.assertIsNone(result)
        self.assertTrue(any("weapon type mismatch" in reason for reason in missing))


    def test_production_cash_evaluator_rejects_nontradable_item(self):
        now = datetime.now(UTC).isoformat()
        row = {
            "name": "Laser", "cash_price_usd": 10.0, "price_low_usd": 10.0,
            "price_high_usd": 10.0, "price_display": "$10.00",
            "status": "verified", "verified_at_utc": now,
            "source": "ROBASE completed sales", "robase_slug": "laser",
        }
        mapping = {"laser": {
            "robase_slug": "laser", "robase_name": "Laser",
            "bloxswaps_type": "knife", "bloxswaps_rarity": "godly",
            "bloxswaps_chroma": False, "bloxswaps_tradable": False,
        }}
        item = {
            "name": "Laser", "catalog_slug": "laser", "catalog_type": "knife",
            "catalog_rarity": "godly", "catalog_chroma": False,
            "quantity": 1, "quantity_min": 1, "quantity_max": 1,
            "quantity_source": "structured",
        }
        listing = {
            "id": "nontradable-item", "source": "bloxswaps",
            "give_items": [item], "receive_items": [dict(item)],
        }
        result, missing = scanner.evaluate_cash_listing_robase_only(
            listing, {"items_by_slug": {"laser": row}, "bloxswaps_exact_map": mapping}
        )
        self.assertIsNone(result)
        self.assertTrue(any("does not confirm" in reason for reason in missing))


if __name__ == "__main__":
    unittest.main()


class ConfirmedTelegramDeliveryFinalizationTests(IsolatedDataTestCase):
    def test_alerted_checkpoint_precedes_quarantine_release(self):
        uncertain = {"trade-1"}
        events = []

        scanner.finalize_confirmed_telegram_delivery(
            "trade-1",
            uncertain,
            lambda trade_id: events.append(("alerted", trade_id)),
            lambda: events.append(("watch", None)),
            lambda ids: events.append(("persist_quarantine", set(ids))),
            lambda trade_id: events.append(("clear_context", trade_id)),
        )

        self.assertEqual(
            events,
            [
                ("alerted", "trade-1"),
                ("watch", None),
                ("persist_quarantine", set()),
                ("clear_context", "trade-1"),
            ],
        )
        self.assertNotIn("trade-1", uncertain)

    def test_failed_checkpoint_keeps_quarantine_intact(self):
        uncertain = {"trade-1"}
        events = []

        def fail_watch():
            events.append(("watch_failed", None))
            raise OSError("simulated state write failure")

        with self.assertRaisesRegex(OSError, "simulated state write failure"):
            scanner.finalize_confirmed_telegram_delivery(
                "trade-1",
                uncertain,
                lambda trade_id: events.append(("alerted", trade_id)),
                fail_watch,
                lambda ids: events.append(("persist_quarantine", set(ids))),
                lambda trade_id: events.append(("clear_context", trade_id)),
            )

        self.assertIn("trade-1", uncertain)
        self.assertEqual(events, [("alerted", "trade-1"), ("watch_failed", None)])
