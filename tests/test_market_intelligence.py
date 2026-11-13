import unittest

from market_intelligence.ai_advisor import build_analysis_payload, build_deepseek_manual_prompt
from market_intelligence.public_sources import parse_rss, validate_reddit_feed_url


class PublicSourceTests(unittest.TestCase):
    def test_rejects_non_reddit_and_non_https(self):
        urls = (
            "http://www.reddit.com/r/test/.rss",
            "https://discord.com/channels/1/2",
            "https://example.com/feed.rss",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_reddit_feed_url(url)

    def test_accepts_reddit_rss(self):
        url = "https://www.reddit.com/r/MurderMystery2/new/.rss"
        self.assertEqual(validate_reddit_feed_url(url), url)

    def test_parse_rss_and_filter_external_links(self):
        xml = """<?xml version='1.0'?><rss><channel>
        <item><title>Trading: 2 weapons</title><link>https://www.reddit.com/r/test/comments/123/post/</link><description>Looking for offers</description><pubDate>Thu, 13 Nov 2026 10:00:00 GMT</pubDate></item>
        <item><title>Bad link</title><link>https://example.com/post</link></item>
        </channel></rss>"""
        rows = parse_rss(xml, "https://www.reddit.com/r/test/new/.rss")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Trading: 2 weapons")
        self.assertEqual(rows[0]["source"], "reddit_rss")

    def test_parse_atom(self):
        xml = """<feed xmlns='http://www.w3.org/2005/Atom'><entry>
        <title>MM2 trade</title><link href='https://www.reddit.com/r/test/comments/1/a/'/>
        <summary>Offer</summary></entry></feed>"""
        rows = parse_rss(xml, "https://www.reddit.com/r/test/new/.rss")
        self.assertEqual(rows[0]["url"], "https://www.reddit.com/r/test/comments/1/a/")


class AIAdvisorTests(unittest.TestCase):
    def test_payload_is_bounded_and_allowlisted(self):
        rows = [{"id": str(i), "profit": 5, "secret": "never"} for i in range(40)]
        payload = build_analysis_payload(rows)
        self.assertEqual(len(payload["listings"]), 30)
        self.assertNotIn("secret", payload["listings"][0])

    def test_deepseek_prompt_contains_safety_rule_and_json(self):
        prompt = build_deepseek_manual_prompt({"listings": [{"id": "x"}]})
        self.assertIn("deterministic", prompt)
        self.assertIn('"id": "x"', prompt)


if __name__ == "__main__":
    unittest.main()
