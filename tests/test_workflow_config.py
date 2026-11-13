import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowReliabilityTests(unittest.TestCase):
    def test_scanner_uses_one_serialized_scheduled_trigger(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("  schedule:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("repository_dispatch:", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("python tools/validate_state.py --directory state-artifact/data --require-cache", workflow)
        restore = workflow.split("      - name: Restore durable state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("set -euo pipefail", restore)

    def test_dashboard_skips_deploy_when_pages_is_not_enabled(self):
        workflow = (ROOT / ".github/workflows/dashboard-pages.yml").read_text(encoding="utf-8")
        self.assertIn("pages_enabled: ${{ steps.pages-preflight.outputs.enabled }}", workflow)
        self.assertIn("id: pages-preflight", workflow)
        self.assertIn("pages: read", workflow)
        self.assertIn("if: needs.build.outputs.pages_enabled == 'true'", workflow)
        self.assertIn("Enable Pages in Settings > Pages", workflow)


if __name__ == "__main__":
    unittest.main()
