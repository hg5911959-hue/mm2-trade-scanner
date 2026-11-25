import unittest
from test_support import IsolatedDataTestCase
from tools.rblxvalue_daily import RBLXValueClient, candidate_matches, build_snapshot, normalize_name, parse_number

class RBLXValueDailyTests(IsolatedDataTestCase):
    def identity(self, name="Bat", slug="bat", rarity="godly", kind="knife", chroma=False):
        return {"robase_slug":slug,"robase_name":name,"bloxswaps_slug":slug,"bloxswaps_rarity":rarity,"bloxswaps_type":kind,"bloxswaps_chroma":chroma}
    def source(self, name="Bat", slug="bat", category="godly", kind="Knife", **extra):
        return {"name":name,"slug":slug,"category":category,"type":kind,"value":155,"value_source_a":150,"value_source_b":160,"stability":"Stable","demand":5,"rarity":4,"range":"150-165","last_updated":"2026-11-25 05:00:00",**extra}
    def test_exact_name_category_and_type_match(self):
        self.assertEqual(len(candidate_matches(self.identity(),[self.source()])),1)
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
    def test_full_214_weapon_snapshot_keeps_both_value_sources(self):
        identities={f"weapon-{i}":self.identity(name=f"Weapon {i}",slug=f"weapon-{i}") for i in range(214)}
        items=[self.source(name=f"Weapon {i}",slug=f"weapon-{i}") for i in range(214)]
        result=build_snapshot(identities,items,{"last_updated":"2026-11-25 05:00:00","total_items":214},snapshot_date="2026-11-25",fetched_at_utc="2026-11-25T04:00:00+00:00")
        self.assertEqual(result["counts"]["matched"],214); self.assertEqual(result["items"][0]["value"],155); self.assertEqual(result["items"][0]["value_source_b"],160); self.assertEqual(result["items"][0]["stability"],"Stable")
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
