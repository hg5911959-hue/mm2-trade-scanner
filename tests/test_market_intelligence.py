import json
import unittest
from unittest.mock import MagicMock, patch

from market_intelligence.ai_advisor import (
    build_analysis_payload,
    build_deepseek_manual_prompt,
    review_with_gemini,
)
from market_intelligence.public_sources import (
    _RedditOnlyRedirectHandler,
    parse_rss,
    validate_reddit_feed_url,
)


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

    def test_redirect_handler_rejects_offsite_redirects_before_fetch(self):
        handler = _RedditOnlyRedirectHandler()
        request = __import__("urllib.request", fromlist=["Request"]).Request(
            "https://www.reddit.com/r/test/new/.rss"
        )
        for target in (
            "https://example.com/steal",
            "http://www.reddit.com/r/test/new/.rss",
            "https://attacker.reddit.com/r/test/new/.rss",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_redirect_handler_allows_reddit_rss_redirect(self):
        from urllib.request import Request

        handler = _RedditOnlyRedirectHandler()
        request = Request("https://www.reddit.com/r/test/new/.rss")
        redirected = handler.redirect_request(
            request, None, 302, "Found", {},
            "https://old.reddit.com/r/test/new/.rss",
        )
        self.assertEqual(redirected.full_url, "https://old.reddit.com/r/test/new/.rss")

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

    def test_gemini_key_is_sent_in_header_not_url(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "summary": "Review only",
                            "risks": [],
                            "opportunities": [],
                            "data_gaps": [],
                        })
                    }]
                }
            }]
        }).encode("utf-8")
        response.__enter__.return_value = response
        secret = "test-gemini-secret"
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": secret, "GEMINI_MODEL": "gemini-test"}),
            patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            result = review_with_gemini({"listings": []})
        request = urlopen.call_args.args[0]
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(secret, request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), secret)


if __name__ == "__main__":
    unittest.main()
