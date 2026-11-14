import json, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from tools.metrics import update_metrics
from tools.weekly_report import build_report

class MetricsReportTests(unittest.TestCase):
    def test_metrics_atomic_counter_and_14_day_retention(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"metrics.json"
            update_metrics(p,increments={"scans":1,"alerts_sent":2},now=datetime(2026,11,13,tzinfo=timezone.utc))
            data=json.loads(p.read_text())
            self.assertEqual(data["2026-11-13"]["alerts_sent"],2)
            self.assertFalse(p.with_name("metrics.json.tmp").exists())
    def test_report_from_fixture_metrics(self):
        report=build_report({"2026-11-13":{"scans":10,"scan_failures":1,"alerts_sent":8,"alerts_deferred":3,"rblxvalue_requests":12}},
            {"stale":False},{"permanent":False},60,datetime(2026,11,13,tzinfo=timezone.utc))
        self.assertIn("Scan success: 90.0%",report)
        self.assertIn("Alerts sent: 8 | deferred: 3",report)
        self.assertIn("12/60",report)
        self.assertIn("fresh",report)
    def test_report_with_empty_metrics(self):
        self.assertIn("Scan success: 0.0%",build_report({},now=datetime(2026,11,13,tzinfo=timezone.utc)))
    def test_report_missing_metrics_file_is_represented_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            missing=Path(d)/"missing.json"
            self.assertFalse(missing.exists())
            self.assertIn("Scan success: 0.0%",build_report({}))
if __name__=="__main__": unittest.main()
