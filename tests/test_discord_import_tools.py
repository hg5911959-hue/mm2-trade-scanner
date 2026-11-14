import json
import tempfile
import unittest
from test_support import IsolatedDataTestCase
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tools.merge_discord_imports import merge_imports
from tools.check_discord_import import validate

class DiscordImportToolTests(IsolatedDataTestCase):
    def row(self, mid, age=0):
        stamp = datetime.now(timezone.utc) - timedelta(hours=age)
        return {"message_id": mid, "content": "secret text", "created_at_utc": stamp.isoformat()}
    def test_two_secrets_merge(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"messages.json"
            result=merge_imports(json.dumps({"messages":[self.row("1")]}),
                                 json.dumps({"messages":[self.row("2")]}),p)
            self.assertEqual(result["rows"],2)
            self.assertEqual({x["message_id"] for x in json.loads(p.read_text())["messages"]},{"1","2"})
    def test_invalid_secret_does_not_discard_valid_secret(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"messages.json"
            result=merge_imports("{bad",json.dumps({"messages":[self.row("2")]}),p)
            self.assertEqual(result["invalid_secrets"],1)
            self.assertEqual(result["rows"],1)
    def test_stale_import_detection(self):
        with tempfile.TemporaryDirectory() as d:
            result=merge_imports(json.dumps({"messages":[self.row("old",60)]}),"",Path(d)/"m.json")
            self.assertTrue(result["stale"])
    def test_size_warning_above_45_kb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"import.json"
            p.write_text(json.dumps({"messages":[self.row(str(i)) for i in range(600)]}))
            count,size,warn=validate(p)
            self.assertEqual(count,600)
            self.assertGreater(size,45)
            self.assertTrue(warn)
if __name__=="__main__": unittest.main()
