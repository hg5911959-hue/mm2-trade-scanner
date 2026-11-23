import unittest
from test_support import IsolatedDataTestCase
from tools.robase_identity import build_robase_bloxswaps_map, match_robase_row

class RobaseBloxswapsIdentityTests(IsolatedDataTestCase):
    def row(self, name="Laser", slug="laser", category="godly"):
        return {"name": name, "robase_slug": slug, "robase_category": category}

    def item(self, name="Laser", slug="laser", rarity="godly", kind="knife", chroma=False):
        return {"displayName": name, "slug": slug, "rarity": rarity, "type": kind, "chroma": chroma}

    def test_exact_slug_and_metadata_match(self):
        details = self.item()
        matched, source = match_robase_row(self.row(), {"one": details})
        self.assertEqual(matched, details)
        self.assertEqual(source, "robase_item_slug")

    def test_unique_name_rarity_type_match(self):
        details = self.item(slug="laser-alt")
        matched, source = match_robase_row(self.row(slug="not-the-same"), {"one": details})
        self.assertEqual(matched, details)
        self.assertEqual(source, "unique_name_rarity_type")

    def test_ambiguous_candidates_are_rejected(self):
        items = {"a": self.item(slug="laser-a"), "b": self.item(slug="laser-b")}
        matched, source = match_robase_row(self.row(slug="other"), items)
        self.assertIsNone(matched)
        self.assertEqual(source, "ambiguous")

    def test_wrong_rarity_is_rejected(self):
        matched, source = match_robase_row(self.row(), {"one": self.item(rarity="legendary")})
        self.assertIsNone(matched)
        self.assertEqual(source, "missing")

    def test_chroma_requires_chroma_metadata(self):
        row = self.row(name="Chroma Laser", slug="chroma-laser", category="chroma")
        accepted, _ = match_robase_row(row, {"one": self.item(name="Laser", slug="chroma-laser", chroma=True)})
        rejected, _ = match_robase_row(row, {"one": self.item(name="Laser", slug="chroma-laser")})
        self.assertIsNotNone(accepted)
        self.assertIsNone(rejected)

    def test_classic_aliases_to_vintage(self):
        matched, _ = match_robase_row(self.row(category="classic"), {"one": self.item(rarity="vintage")})
        self.assertIsNotNone(matched)

    def test_non_weapons_are_not_added(self):
        result = build_robase_bloxswaps_map({"items_by_slug": {"laser": self.row()}}, {"one": self.item(kind="pet")})
        self.assertEqual(result["bloxswaps_exact_map"], {})
        self.assertEqual(result["non_weapons"], ["Laser"])


    def test_image_slug_type_and_year_hints_restrict_candidates(self):
        row = self.row(slug="unmatched")
        row["og_image_slug"] = "laser-g-2026"
        gun = self.item(kind="gun", slug="laser-g-2026")
        gun["year"] = 2026
        knife = self.item(kind="knife", slug="laser-k-2026")
        knife["year"] = 2026
        matched, source = match_robase_row(row, {"gun": gun, "knife": knife})
        self.assertEqual(matched, gun)
        self.assertEqual(source, "robase_image_slug")


    def test_duplicate_slug_collision_is_excluded(self):
        catalog = {"items_by_slug": {"a": self.row(), "b": self.row(name="Laser", slug="laser-copy")}}
        result = build_robase_bloxswaps_map(catalog, {"one": self.item()})
        self.assertEqual(result["bloxswaps_exact_map"], {})
        self.assertTrue(result["ambiguous"])

    def test_2026_catseye_uses_type_hint_to_choose_knife_not_gun(self):
        row = self.row(name="Catseye", slug="catseye-1144", category="rare")
        row["og_image_slug"] = "catseye-k-2026"
        gun = self.item(name="Catseye", slug="catseye", rarity="rare", kind="gun")
        knife = self.item(name="Catseye", slug="catseye-k-2026", rarity="rare", kind="knife")
        knife["year"] = 2026
        matched, source = match_robase_row(row, {"gun": gun, "knife": knife})
        self.assertEqual(matched, knife)
        self.assertEqual(source, "robase_image_slug")

    def test_image_slug_exact_match_does_not_override_conflicting_type_hint(self):
        row = self.row(name="Laser", slug="laser-1148", category="rare")
        row["og_image_slug"] = "laser-k-2026"
        gun = self.item(name="Laser", slug="laser-k-2026", rarity="rare", kind="gun")
        gun["year"] = 2026
        matched, source = match_robase_row(row, {"gun": gun})
        self.assertIsNone(matched)
        self.assertEqual(source, "missing")

    def test_name_only_match_rejects_ambiguous_same_rarity_variants(self):
        row = self.row(name="Predator", slug="predator", category="legendary")
        items = {
            "gun": self.item(name="Predator", slug="predator", rarity="legendary", kind="gun"),
            "knife": self.item(name="Predator", slug="predatorknife", rarity="legendary", kind="knife"),
        }
        matched, source = match_robase_row(row, items)
        self.assertIsNone(matched)
        self.assertEqual(source, "ambiguous")

if __name__ == "__main__":
    unittest.main()
