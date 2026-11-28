from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_support import IsolatedDataTestCase
from tools import weekly_report


class WeeklyReportConfigTests(IsolatedDataTestCase):
    def test_main_uses_the_validated_daily_request_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(weekly_report, "DATA_DIR", Path(temporary)),
                patch.object(
                    weekly_report,
                    "CONFIG",
                    SimpleNamespace(rblxvalue_daily_request_limit=37),
                ),
                patch.object(
                    weekly_report,
                    "collect_preflight_checks",
                    return_value={"checks": []},
                ),
                patch.object(weekly_report, "build_report", return_value="weekly report") as build,
                patch.object(weekly_report, "send_report") as send,
                patch.dict(os.environ, {"CASH_FILTER": "false"}, clear=False),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                weekly_report.main()

        self.assertEqual(build.call_args.args[3], 37)
        send.assert_called_once_with(
            "weekly report",
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )


if __name__ == "__main__":
    unittest.main()
