from test_support import IsolatedDataTestCase
from tools.robase_prices import _TableParser, _parse_item_detail, parse_robase_html


class RobasePriceParserTests(IsolatedDataTestCase):
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
        self.assertEqual(by_name["Harvester"]["price_display"], "$5.01")
        self.assertEqual(by_name["Harvester"]["sold_7d"], 1510)
        self.assertEqual(by_name["Harvester"]["status"], "verified")
        self.assertEqual(by_name["Chroma Bauble"]["status"], "unverified")
        self.assertEqual(snapshot["price_basis"], "seller_proceeds_after_marketplace_fees")

    def test_parses_robase_rendered_rows_with_empty_edge_cells_and_joined_tier(self):
        html = """
        <table><tbody id="rb-rows">
        <tr><td></td><td>1</td><td>HarvesterAncient</td><td>$5.02</td>
        <td>4%</td><td>16%</td><td>$20 value 250</td><td>$499k</td>
        <td>$10k 1,510 sold</td><td>99k</td><td></td></tr>
        </tbody></table>
        """
        snapshot = parse_robase_html(html, "2026-11-21T12:00:00Z")
        self.assertEqual(len(snapshot["items"]), 1)
        item = snapshot["items"][0]
        self.assertEqual(item["name"], "Harvester")
        self.assertEqual(item["cash_price_usd"], 5.02)
        self.assertEqual(item["sold_7d"], 1510)
        self.assertEqual(item["status"], "verified")

    def test_ignores_non_data_rows_and_rejects_duplicate_names(self):
        partial = parse_robase_html("<table><tr><td>1</td><td>Harvester</td><td>$5</td></tr></table>")
        self.assertEqual(partial["items"], [])
        duplicate = """
        <table>
        <tr><td>1</td><td>Harvester Ancient</td><td>$5</td><td>4</td><td>4</td><td>1</td><td>1</td><td>10 sold</td><td>1</td></tr>
        <tr><td>2</td><td>Harvester Ancient</td><td>$5</td><td>4</td><td>4</td><td>1</td><td>1</td><td>10 sold</td><td>1</td></tr>
        </table>
        """
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_robase_html(duplicate)




    def test_extracts_all_public_item_links_from_homepage(self):
        parser = _TableParser()
        parser.feed(
            '<a href="/item/harvester">Harvester</a>'
            '<a href="https://robase.gg/item/chroma-emberscythe">Chroma Emberscythe</a>'
            '<a href="https://example.com/item/fake">ignore</a>'
        )
        self.assertEqual(
            parser.item_links,
            {"https://robase.gg/item/harvester", "https://robase.gg/item/chroma-emberscythe"},
        )

    def test_item_detail_accepts_rendered_about_heading_and_description(self):
        html = """
        <main>
          <h2>About Harvester</h2>
          <p>Harvester is an Ancient item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $4.99 on average after the marketplace fee.</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/harvester", html, "2026-11-22T09:00:00Z"
        )
        self.assertEqual(item["name"], "Harvester")
        self.assertEqual(item["robase_category"], "Ancient")
        self.assertEqual(item["price_display"], "$4.99")

    def test_item_detail_anchors_name_and_extracts_exact_bloxswaps_image_slug(self):
        html = """
        <html><head>
          <title>Cane price | ROBASE Items and Markets</title>
          <meta property="og:image" content="https://cdn.bloxluck.com/item-images/cane-k-2018.png">
        </head><body>
          <nav>Prices after fees Buyer price Cane #217 Rare $9.29</nav>
          <h2>About Cane</h2>
          <p>Cane is a Rare item in Roblox Murder Mystery 2 (MM2).
          It has not sold in the last 7 days; over the last 30 days it sold for
          $9.29 on average after the marketplace fee, and buyers paid $13.66.</p>
          <p>15 copies sold in the last 30 days, none in the last 7.</p>
          <p>Its value is 150. BloxSwaps counts 977 copies in the game,
          held by 535 players.</p>
        </body></html>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/cane-562", html, "2026-11-22T09:00:00Z"
        )
        self.assertEqual(item["name"], "Cane")
        self.assertEqual(item["robase_category"], "Rare")
        self.assertEqual(item["og_image_slug"], "cane-k-2018")
        self.assertEqual(item["bloxswaps_slug"], "cane-k-2018")

    def test_item_detail_preserves_displayed_price_and_bloxswaps_metadata(self):
        html = """
        <main>
          <p>Harvester is an Ancient item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $4.99 on average after the marketplace fee,
          and buyers paid $6.78.</p>
          <p>1,403 copies changed hands in the last 7 days for $9,611 (7,722 in 30 days).</p>
          <p>Its value is 250, so 1,000 value in Harvester costs $19.96.
          BloxSwaps counts 99,868 copies in the game, held by 70,527 players, a market cap of $498.2k.</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/harvester", html, "2026-11-22T09:00:00Z",
            '"etag-1"', "Sat, 22 Nov 2026 09:00:00 GMT",
        )
        self.assertEqual(item["name"], "Harvester")
        self.assertEqual(item["robase_category"], "Ancient")
        self.assertEqual(item["price_display"], "$4.99")
        self.assertEqual(item["cash_price_usd"], 4.99)
        self.assertEqual(item["sold_7d"], 1403)
        self.assertEqual(item["sold_30d"], 7722)
        self.assertEqual(item["bloxswaps_value"], 250)
        self.assertEqual(item["bloxswaps_copies"], 99868)
        self.assertEqual(item["bloxswaps_holders"], 70527)
        self.assertEqual(item["status"], "verified")
