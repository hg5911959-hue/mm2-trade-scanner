import json
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from test_support import IsolatedDataTestCase

import tools.rblxvalue_daily as daily
from rblxvalue_guard import RblxValueBudgetExceeded, reserve_request
from tools.rblxvalue_daily import RBLXValueClient, candidate_matches, build_snapshot, normalize_name, parse_number, EXACT_IDENTITY_ALIASES

class RBLXValueDailyTests(IsolatedDataTestCase):
    def identity(self, name="Bat", slug="bat", rarity="godly", kind="knife", chroma=False):
        return {"robase_slug":slug,"robase_name":name,"bloxswaps_slug":slug,"bloxswaps_rarity":rarity,"bloxswaps_type":kind,"bloxswaps_chroma":chroma}
    def source(self, name="Bat", slug="bat", category="godly", kind="Knife", **extra):
        return {"name":name,"slug":slug,"category":category,"type":kind,"value":155,"value_source_a":150,"value_source_b":160,"stability":"Stable","demand":5,"rarity":4,"range":"150-165","last_updated":"2026-11-25 05:00:00",**extra}
    def test_exact_name_category_and_type_match(self):
        self.assertEqual(len(candidate_matches(self.identity(),[self.source()])),1)

    def test_explicit_aliases_require_exact_slug_name_category_and_type(self):
        self.assertEqual(len(EXACT_IDENTITY_ALIASES), 9)
        for robase_slug, alias in EXACT_IDENTITY_ALIASES.items():
            identity = {
                "robase_slug": robase_slug,
                "robase_name": alias["robase_name"],
                "bloxswaps_slug": alias["bloxswaps_slug"],
                "bloxswaps_type": alias["type"],
                "bloxswaps_rarity": alias["category"],
                "bloxswaps_chroma": False,
            }
            source = {
                "name": alias["name"],
                "slug": alias["slug"],
                "category": alias["category"],
                "type": alias["type"].title(),
            }
            self.assertEqual(candidate_matches(identity, [source]), [source], robase_slug)
            wrong_slug = dict(source, slug="not-the-reviewed-slug")
            self.assertEqual(candidate_matches(identity, [wrong_slug]), [], robase_slug)
            wrong_type = dict(source, type="Gun" if alias["type"] == "knife" else "Knife")
            self.assertEqual(candidate_matches(identity, [wrong_type]), [], robase_slug)
            wrong_category = dict(source, category="ancient")
            self.assertEqual(candidate_matches(identity, [wrong_category]), [], robase_slug)

    def test_zero_supreme_source_is_not_counted_as_a_supreme_value(self):
        identity = {
            "ghost-g-2026": {
                "robase_slug": "ghost-1149",
                "robase_name": "Ghost",
                "bloxswaps_slug": "ghost-g-2026",
                "bloxswaps_type": "gun",
                "bloxswaps_rarity": "uncommon",
                "bloxswaps_chroma": False,
            }
        }
        source = [{
            "name": "Ghost Gun",
            "slug": "ghost-gun",
            "category": "uncommon",
            "type": "Gun",
            "value": 10,
            "value_source_a": 10,
            "value_source_b": 0,
            "stability": "Stable",
            "demand": 3,
            "rarity": 3,
            "last_updated": "2026-11-25 12:00:00",
        }]
        snapshot = build_snapshot(
            identity, source, {"total_items": 1, "last_updated": "2026-11-25 12:00:00"},
            snapshot_date="2026-11-25", fetched_at_utc="2026-11-25T12:00:00+00:00", expected_count=1,
        )
        row = snapshot["items"][0]
        self.assertEqual(row["status"], "matched_missing_supreme_value")
        self.assertEqual(snapshot["counts"]["with_supreme_value"], 0)
        self.assertEqual(snapshot["counts"]["with_stability"], 1)
        self.assertEqual(row["identity_method"], "explicit_verified_alias")
        self.assertIn("/item/ghost-gun", row["identity_source_url"])
    def test_slug_match_still_requires_category_and_type(self):
        self.assertEqual(len(candidate_matches(self.identity(name="Renamed Bat"),[self.source()])),1)
        self.assertEqual(len(candidate_matches(self.identity(),[self.source(category="ancient")])),0)
        self.assertEqual(len(candidate_matches(self.identity(),[self.source(kind="Gun")])),0)
    def test_ambiguous_candidates_are_not_guessed(self):
        self.assertEqual(len(candidate_matches(self.identity(),[self.source(slug="bat-a"),self.source(slug="bat-b")])),2)
    def test_chroma_requires_chroma_category(self):
        ident=self.identity(name="Chroma Bat",slug="chroma-bat",chroma=True)
        self.assertEqual(len(candidate_matches(ident,[self.source(name="Chroma Bat",slug="chroma-bat",category="chroma")])),1)
        self.assertEqual(len(candidate_matches(ident,[self.source(name="Chroma Bat",slug="chroma-bat",category="godly")])),0)
    def test_name_normalization_is_not_fuzzy(self):
        self.assertEqual(normalize_name("Vampire's Gun"),"vampire gun")
        self.assertNotEqual(normalize_name("Bat"),normalize_name("Batwing"))
    def test_number_parser_does_not_coerce_invalid_values(self):
        self.assertEqual(parse_number("1,250"),1250); self.assertIsNone(parse_number("x3")); self.assertIsNone(parse_number(-1)); self.assertIsNone(parse_number("155.5"))
    def test_api_key_is_required(self):
        with self.assertRaisesRegex(ValueError,"RBLXVALUE_API_KEY"): RBLXValueClient("")
    def test_client_persists_every_http_attempt_in_the_rolling_budget(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"meta":{"last_updated":"today"}}'

        with tempfile.TemporaryDirectory() as directory:
            budget = Path(directory) / "rblxvalue_request_budget.json"
            blocked = Path(directory) / "api_key_blocked.json"
            retry_after = Message()
            first_error = HTTPError(
                "https://api.rblxvalue.com/v2/meta",
                429,
                "Too Many Requests",
                retry_after,
                None,
            )
            client = RBLXValueClient("rbx_test_key", sleep=lambda _seconds: None)
            with (
                patch.object(daily.rblxvalue_guard, "BUDGET_FILE", budget),
                patch.object(daily.rblxvalue_guard, "BLOCK_FILE", blocked),
                patch.object(daily, "urlopen", side_effect=[first_error, Response()]) as request,
            ):
                self.assertEqual(client._get_json("/meta")["meta"]["last_updated"], "today")
            self.assertEqual(request.call_count, 2)
            ledger = json.loads(budget.read_text(encoding="utf-8"))
            self.assertEqual(len(ledger["request_timestamps_epoch"]), 2)
            self.assertEqual(ledger["request_counts_by_workflow"], {
                "RBLXValue Daily Weapon History": 2,
            })

    def test_exhausted_rolling_budget_stops_before_network_io(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = Path(directory) / "rblxvalue_request_budget.json"
            blocked = Path(directory) / "api_key_blocked.json"
            now = time.time()
            with (
                patch.object(daily.rblxvalue_guard, "BUDGET_FILE", budget),
                patch.object(daily.rblxvalue_guard, "BLOCK_FILE", blocked),
            ):
                for _ in range(60):
                    reserve_request(
                        now=now,
                        key="rbx_test_key",
                        budget_path=budget,
                        block_path=blocked,
                        workflow="prior archive run",
                    )
                client = RBLXValueClient("rbx_test_key", sleep=lambda _seconds: None)
                with patch.object(daily, "urlopen") as request:
                    with self.assertRaises(RblxValueBudgetExceeded):
                        client._get_json("/meta")
                    request.assert_not_called()

    def test_full_214_weapon_snapshot_keeps_both_value_sources(self):
        identities={f"weapon-{i}":self.identity(name=f"Weapon {i}",slug=f"weapon-{i}") for i in range(214)}
        items=[self.source(name=f"Weapon {i}",slug=f"weapon-{i}") for i in range(214)]
        result=build_snapshot(identities,items,{"last_updated":"2026-11-25 05:00:00","total_items":214},snapshot_date="2026-11-25",fetched_at_utc="2026-11-25T04:00:00+00:00")
        self.assertEqual(result["counts"]["matched"],214); self.assertEqual(result["items"][0]["value"],155); self.assertEqual(result["items"][0]["value_source_b"],160); self.assertEqual(result["items"][0]["stability"],"Stable"); self.assertEqual(result["policy"]["production"],"optional Telegram reference display only; never used for cash valuation, eligibility, ranking, or alert decisions")
    def test_missing_values_remain_null(self):
        result=build_snapshot({"bat":self.identity()},[self.source(value=None,value_source_b=None,stability=None)],{"last_updated":"update","total_items":1},snapshot_date="2026-11-25",fetched_at_utc="2026-11-25T04:00:00+00:00",expected_count=1)
        row=result["items"][0]; self.assertIsNone(row["value"]); self.assertIsNone(row["value_source_b"]); self.assertEqual(row["status"],"matched_missing_blended_value")
    def test_duplicate_source_assignment_is_rejected(self):
        identities={"bat":self.identity(),"bat-alt":self.identity(slug="bat-alt")}
        result=build_snapshot(identities,[self.source()],{"last_updated":"update","total_items":1},snapshot_date="2026-11-25",fetched_at_utc="2026-11-25T04:00:00+00:00",expected_count=2)
        self.assertEqual(result["counts"]["ambiguous"],2); self.assertIsNone(result["items"][0]["value"])
    def test_source_count_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"count"):
            build_snapshot({"bat":self.identity()},[self.source()],{"last_updated":"update","total_items":2},snapshot_date="2026-11-25",fetched_at_utc="2026-11-25T04:00:00+00:00",expected_count=1)

if __name__ == "__main__": unittest.main()
