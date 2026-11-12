import io
import os
import unittest
from unittest.mock import patch

import scanner


class SecretRedactionTests(unittest.TestCase):
    def test_redact_replaces_configured_token_key_and_chat_id(self):
        secrets = {
            "TELEGRAM_BOT_TOKEN": "telegram-token-123456",
            "TELEGRAM_CHAT_ID": "-100987654321",
            "RBLXVALUE_API_KEY": "rbx_secret_key_123",
        }
        with patch.dict(os.environ, secrets, clear=False):
            output = scanner.redact("token=telegram-token-123456 chat=-100987654321 key=rbx_secret_key_123")
        self.assertEqual(output, "token=[REDACTED] chat=[REDACTED] key=[REDACTED]")
        for secret in secrets.values():
            self.assertNotIn(secret, output)

    def test_redacting_stream_filters_printed_output(self):
        secrets = {
            "TELEGRAM_BOT_TOKEN": "telegram-token-123456",
            "TELEGRAM_CHAT_ID": "-100987654321",
            "RBLXVALUE_API_KEY": "rbx_secret_key_123",
        }
        target = io.StringIO()
        with patch.dict(os.environ, secrets, clear=False):
            stream = scanner.RedactingStream(target)
            print("telegram-token-123456 -100987654321 rbx_secret_key_123", file=stream)
            stream.flush()
        self.assertEqual(target.getvalue(), "[REDACTED] [REDACTED] [REDACTED]\n")

    def test_normal_text_is_unchanged(self):
        value = "Scanner cycle completed: 3 listings evaluated."
        self.assertEqual(scanner.redact(value), value)

    def test_existing_credential_patterns_are_redacted(self):
        value = "https://api.telegram.org/botabc123456/sendMessage rbx_AbCdEf123 Authorization: Bearer token-value-123"
        output = scanner.redact(value)
        self.assertNotIn("abc123456", output)
        self.assertNotIn("AbCdEf123", output)
        self.assertNotIn("token-value-123", output)


if __name__ == "__main__":
    unittest.main()
