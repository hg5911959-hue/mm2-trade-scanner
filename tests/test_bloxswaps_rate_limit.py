import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch
from urllib.request import Request

import scanner
from test_support import IsolatedDataTestCase


class BloxSwapsRateLimitTests(IsolatedDataTestCase):
    def test_http_429_stops_without_retries_and_reports_retry_after(self):
        calls = []

        def rate_limited(req, timeout):
            calls.append(req)
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                {
                    "Retry-After": "37",
                    "Server": "cloudflare",
                    "CF-Ray": "test-ray",
                    "Content-Type": "text/html",
                },
                BytesIO(b"rate limited"),
            )

        request = Request("https://bloxswaps.com/fr/trade-mm2")
        with (
            patch("scanner.urllib.request.urlopen", side_effect=rate_limited),
            patch("scanner.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError,
                r"HTTP 429.*Retry-After=37s.*Server=cloudflare.*CF-Ray=test-ray",
            ):
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_http_429_without_retry_after_fails_closed_immediately(self):
        calls = []

        def rate_limited(req, timeout):
            calls.append(req)
            raise HTTPError(
                req.full_url, 429, "too many requests", {}, BytesIO(b"rate limited")
            )

        request = Request("https://bloxswaps.com/fr/trade-mm2")
        with patch("scanner.urllib.request.urlopen", side_effect=rate_limited):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError, "no Retry-After header"
            ):
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertEqual(len(calls), 1)

    def test_http_429_with_invalid_retry_after_fails_closed_without_leaking_body(self):
        def rate_limited(req, timeout):
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                {"Retry-After": "not-a-date", "Server": "cloudflare"},
                BytesIO(b"private response body"),
            )

        request = Request("https://bloxswaps.com/fr/trade-mm2")
        with patch("scanner.urllib.request.urlopen", side_effect=rate_limited):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError, "invalid Retry-After header"
            ) as raised:
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertNotIn("private response body", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
