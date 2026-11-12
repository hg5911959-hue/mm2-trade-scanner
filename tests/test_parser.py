import unittest

from scanner import parse_listings, calculate_side


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


if __name__ == "__main__":
    unittest.main()
