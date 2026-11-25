"""Regression guards for the daily ROBASE archive workflow ordering and timezone."""

from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "robase-daily-history.yml"


class DailyArchiveWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = WORKFLOW.read_text(encoding="utf-8")

    def test_archive_append_precedes_previous_day_verification(self):
        append = self.content.index("- name: Append validated daily weapon observations")
        verify = self.content.index("- name: Verify previous completed-day selector against persistent archive")
        upload = self.content.index("- name: Upload diagnostic report")
        self.assertLess(append, verify)
        self.assertLess(verify, upload)

    def test_archive_uses_algeria_local_calendar_day(self):
        self.assertIn('datetime.now(ZoneInfo("Africa/Algiers"))', self.content)
        self.assertNotIn("today_utc = datetime.now(timezone.utc).date()", self.content)

    def test_append_requires_previous_completed_day_in_incoming_matrix(self):
        self.assertIn("max(incoming_days) != yesterday_key", self.content)
        self.assertIn("yesterday_key not in incoming_days", self.content)
        self.assertIn("Expected exactly 214 exact weapon identities", self.content)

    def test_post_append_verification_requires_full_weapon_coverage(self):
        verify_start = self.content.index("- name: Verify previous completed-day selector")
        verify_end = self.content.index("- name: Upload diagnostic report", verify_start)
        verify_block = self.content[verify_start:verify_end]
        self.assertIn("load_exact_previous_day_prices", verify_block)
        self.assertIn("len(prices) != 214", verify_block)


if __name__ == "__main__":
    unittest.main()
