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

    def test_item_detail_uses_chromas_gg_price_7d_only_even_when_starpets_is_cheaper(self):
        html = """
        <main>
          <table>
            <thead><tr><th>#</th><th>Marketplace</th><th>Price (7d)</th><th>Sold (7d)</th>
            <th>Volume (7d)</th><th>Volume (30d)</th><th>Share</th><th>Fee</th><th>Earned (30d)</th><th>Last sale</th></tr></thead>
            <tbody>
              <tr><td>1</td><td><i></i>StarPets</td><td>$1.90</td><td>761</td><td>$4,053.65</td><td>$18,404.80</td><td>91.0%</td><td>32%</td><td>$5,889.54</td><td>yesterday</td></tr>
              <tr><td>2</td><td><i></i>chromas.gg</td><td>$2.80</td><td>86</td><td>$253.42</td><td>$1,449.32</td><td>7.2%</td><td>5%</td><td>$72.47</td><td>yesterday</td></tr>
            </tbody>
          </table>
          <h2>About Sweet</h2>
          <p>Sweet is a Godly item in Roblox Murder Mystery 2 (MM2).</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/sweet", html, "2026-11-26T12:00:00Z"
        )
        self.assertEqual(item["cash_price_usd"], 2.80)
        self.assertEqual(item["price_display"], "$2.80")
        self.assertEqual(item["selected_marketplace"], "chromas.gg")
        self.assertEqual(item["selected_marketplace_sold_7d"], 86)
        self.assertEqual(set(item["marketplace_prices"]), {"chromas.gg"})
        self.assertEqual(item["price_basis"], "chromas_gg_7d_price")

    def test_item_detail_does_not_fall_back_to_starpets_when_chromas_has_no_7d_sales(self):
        html = """
        <main>
          <table>
            <tr><td>1</td><td>StarPets</td><td>$1.90</td><td>761</td><td>$4,053.65</td><td>$18,404.80</td><td>91.0%</td><td>32%</td><td>$5,889.54</td><td>yesterday</td></tr>
            <tr><td>2</td><td>chromas.gg</td><td>$2.80</td><td>0</td><td>$0</td><td>$0</td><td>0%</td><td>5%</td><td>$0</td><td>—</td></tr>
          </table>
          <h2>About Sweet</h2>
          <p>Sweet is a Godly item in Roblox Murder Mystery 2 (MM2).</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/sweet", html, "2026-11-26T12:00:00Z"
        )
        self.assertIsNone(item["cash_price_usd"])
        self.assertEqual(item["price_display"], "")
        self.assertIsNone(item["selected_marketplace"])
        self.assertEqual(item["marketplace_prices"]["chromas.gg"]["sold_7d"], 0)

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
        self.assertEqual(item["price_display"], "")
        self.assertEqual(item["all_marketplaces_price_display"], "$4.99")
        self.assertIsNone(item["cash_price_usd"])

    def test_item_detail_parses_multiword_chroma_heading_without_splitting_name(self):
        html = """
        <main>
          <h2>About Chroma Alienbeam</h2>
          <p>Chroma Alienbeam is a Chroma item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $332.50 on average after the marketplace fee.</p>
          <p>1 copy changed hands in the last 7 days for $350 (5 in 30 days).</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/chroma-alienbeam", html, "2026-11-22T10:00:00Z"
        )
        self.assertEqual(item["name"], "Chroma Alienbeam")
        self.assertEqual(item["robase_category"], "Chroma")
        self.assertEqual(item["price_display"], "")
        self.assertEqual(item["all_marketplaces_price_display"], "$332.50")
        self.assertEqual(item["sold_7d"], 1)
        self.assertEqual(item["sold_30d"], 5)

    def test_item_detail_prefers_explicit_sold_period_stats_over_about_copy(self):
        html = """
        <main>
          <h2>About Chroma Constellation</h2>
          <div>Sold (7d) 1</div>
          <div>Sold (30d) 4</div>
          <p>Chroma Constellation is a Chroma item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $446.50 on average after the marketplace fee.</p>
          <p>1 copy changed hands in the last 7 days for $470.00.</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/chroma-constellation", html, "2026-11-23T22:00:00Z"
        )
        self.assertIsNone(item["price_window_days"])
        self.assertEqual(item["all_marketplaces_price_window_days"], 7)
        self.assertEqual(item["sold_7d"], 1)
        self.assertEqual(item["sold_30d"], 4)

    def test_item_detail_uses_30_day_window_when_7_day_stats_show_no_sales(self):
        html = """
        <main>
          <h2>About Chroma Vampire's Gun</h2>
          <div>Sold (7d) 0</div>
          <div>Sold (30d) 1</div>
          <p>Chroma Vampire's Gun is a Chroma item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $392.99 on average after the marketplace fee.</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/chroma-vampires-gun", html, "2026-11-23T22:00:00Z"
        )
        self.assertEqual(item["sold_7d"], 0)
        self.assertEqual(item["sold_30d"], 1)
        self.assertIsNone(item["price_window_days"])
        self.assertEqual(item["all_marketplaces_price_window_days"], 30)

    def test_item_detail_falls_back_to_about_sales_counts_when_stats_are_absent(self):
        html = """
        <main>
          <h2>About Harvester</h2>
          <p>Harvester is an Ancient item in Roblox Murder Mystery 2 (MM2).
          Over the last 7 days it sold for $4.99 on average after the marketplace fee.</p>
          <p>1,403 copies changed hands in the last 7 days for $9,611 (7,722 in 30 days).</p>
        </main>
        """
        item = _parse_item_detail(
            "https://robase.gg/item/harvester", html, "2026-11-23T22:00:00Z"
        )
        self.assertEqual(item["sold_7d"], 1403)
        self.assertEqual(item["sold_30d"], 7722)

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
        self.assertIsNone(item["price_window_days"])
        self.assertEqual(item["all_marketplaces_price_window_days"], 30)
        self.assertEqual(item["sold_7d"], 0)
        self.assertEqual(item["sold_30d"], 15)


    def test_2026_variants_without_og_images_use_exact_page_slug_identities(self):
        expected = {
            "cowboy-1150": ("Cowboy", "Rare", "cowboy-k-2026"),
            "ghost-1149": ("Ghost", "Uncommon", "ghost-g-2026"),
            "golden-1146": ("Golden", "Legendary", "golden-k-2026"),
            "laser-1148": ("Laser", "Rare", "laser-k-2026"),
            "phaser-1151": ("Phaser", "Rare", "phaser-k-2026"),
            "prince-1152": ("Prince", "Rare", "prince-g-2026"),
            "shadow-1147": ("Shadow", "Legendary", "shadow-g-2026"),
        }
        for page_slug, (name, category, bloxswaps_slug) in expected.items():
            with self.subTest(page_slug=page_slug):
                html = f"""
                <main>
                  <h2>About {name}</h2>
                  <p>{name} is a {category} item in Roblox Murder Mystery 2 (MM2).
                  Over the last 7 days it sold for $0.34 on average after the marketplace fee.</p>
                </main>
                """
                item = _parse_item_detail(
                    f"https://robase.gg/item/{page_slug}",
                    html,
                    "2026-11-22T12:00:00Z",
                )
                self.assertEqual(item["og_image_slug"], "")
                self.assertEqual(item["bloxswaps_slug"], bloxswaps_slug)


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
        self.assertEqual(item["price_display"], "")
        self.assertIsNone(item["cash_price_usd"])
        self.assertEqual(item["all_marketplaces_price_usd"], 4.99)
        self.assertEqual(item["sold_7d"], 1403)
        self.assertEqual(item["sold_30d"], 7722)
        self.assertEqual(item["bloxswaps_value"], 250)
        self.assertEqual(item["bloxswaps_copies"], 99868)
        self.assertEqual(item["bloxswaps_holders"], 70527)
        self.assertEqual(item["status"], "verified")
