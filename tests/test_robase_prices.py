from tools.robase_prices import parse_robase_html


class RobasePriceParserTests(unittest.TestCase):
    def test_parses_public_table_and_marks_low_sample_unverified(self):
        html = """
        <table><tr><th>#</th><th>Item</th><th>Price</th><th>7d</th><th>30d</th>
        <th>Value</th><th>Cap</th><th>Volume</th><th>Supply</th></tr>
        <tr><td>1</td><td><a>Harvester Ancient</a></td><td>$5.01</td><td>4%</td>
        <td>16%</td><td>$20</td><td>$500k</td><td>$10k 1,510 sold</td><td>99k</td></tr>
        <tr><td>2</td><td>Chroma Bauble Chroma</td><td>$451.25</td><td>—</td>
        <td>—</td><td>$15</td><td>$87k</td><td>$475 1 sold</td><td>193</td></tr>
        </table>
        """
        snapshot = parse_robase_html(html, "2026-11-21T12:00:00Z")
        self.assertEqual(len(snapshot["items"]), 2)
        by_name = {item["name"]: item for item in snapshot["items"]}
        self.assertEqual(by_name["Harvester"]["cash_price_usd"], 5.01)
        self.assertEqual(by_name["Harvester"]["sold_7d"], 1510)
        self.assertEqual(by_name["Harvester"]["status"], "verified")
        self.assertEqual(by_name["Chroma Bauble"]["status"], "unverified")
        self.assertEqual(snapshot["price_basis"], "seller_proceeds_after_marketplace_fees")

    def test_rejects_incomplete_or_duplicate_table(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_robase_html("<table><tr><td>1</td><td>Harvester</td><td>$5</td></tr></table>")
        duplicate = """
        <table>
        <tr><td>1</td><td>Harvester Ancient</td><td>$5</td><td>4</td><td>4</td><td>1</td><td>1</td><td>10 sold</td><td>1</td></tr>
        <tr><td>2</td><td>Harvester Ancient</td><td>$5</td><td>4</td><td>4</td><td>1</td><td>1</td><td>10 sold</td><td>1</td></tr>
        </table>
        """
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_robase_html(duplicate)


