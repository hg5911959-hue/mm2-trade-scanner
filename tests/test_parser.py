import unittest

from scanner import parse_listings


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


if __name__ == "__main__":
    unittest.main()
