import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from test_support import IsolatedDataTestCase
from tools.telegram_delivery import TelegramDeliveryUncertain, send_telegram


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    def __init__(self, payload):
        self.payload = payload

    def open(self, request, timeout):
        return _Response(self.payload)


class TelegramResponseShapeTests(IsolatedDataTestCase):
    def _send(self, payload):
        return send_telegram(
            "test alert",
            robase_attribution="ROBASE source",
            rblxvalue_attribution="RBLXValue source",
            max_chars=4000,
            bot_token="placeholder",
            chat_id="123",
            opener=_Opener(payload),
            wait_for_rate_limit=lambda: None,
            send_part=lambda message: None,
        )

    def test_valid_non_object_json_is_quarantined_as_uncertain(self):
        for payload in ([], None, "unexpected", 42):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(TelegramDeliveryUncertain, "not an object"):
                    self._send(json.dumps(payload).encode("utf-8"))

    def test_object_without_boolean_ok_flag_is_quarantined_as_uncertain(self):
        for payload in (
            {"description": "message accepted?"},
            {"ok": "true"},
            {"ok": 1},
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(TelegramDeliveryUncertain, "valid ok flag"):
                    self._send(json.dumps(payload).encode("utf-8"))

    def test_confirmed_success_is_accepted(self):
        self.assertIsNone(self._send(b'{"ok": true, "result": {"message_id": 77}}'))

    def test_confirmed_success_logs_request_round_trip_latency(self):
        output = StringIO()
        with patch("tools.telegram_delivery.time.monotonic", side_effect=[10.0, 10.125]):
            with redirect_stdout(output):
                self._send(b'{"ok": true, "result": {"message_id": 77}}')
        self.assertIn("[telegram-timing] confirmed_request_ms=125.0", output.getvalue())

    def test_explicit_api_rejection_remains_a_definite_failure(self):
        with self.assertRaisesRegex(RuntimeError, "API rejected message: blocked"):
            self._send(b'{"ok": false, "description": "blocked", "error_code": 403}')


if __name__ == "__main__":
    unittest.main()
