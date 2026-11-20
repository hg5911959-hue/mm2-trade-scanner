import json
import os
import tempfile
import unittest
from unittest.mock import patch
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
    def test_existing_merge_state_rejects_symlink_without_reading_target(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.json"
            target.write_text('{"messages":[]}', encoding="utf-8")
            link = root / "messages.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "cannot be opened safely"):
                merge_imports("", "", link)

    def test_existing_merge_state_rejects_fifo_without_blocking(self):
        if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
            self.skipTest("FIFO/nonblocking open is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "messages.pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                merge_imports("", "", fifo)

    def test_existing_merge_state_enforces_read_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            path.write_text('{"messages":[]}', encoding="utf-8")
            with patch("tools.merge_discord_imports.MAX_INPUT_BYTES", 4):
                with self.assertRaisesRegex(RuntimeError, "exceeds the size limit"):
                    merge_imports("", "", path)

    def test_size_warning_above_45_kb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"import.json"
            p.write_text(json.dumps({"messages":[self.row(str(i)) for i in range(600)]}))
            count,size,warn=validate(p)
            self.assertEqual(count,600)
            self.assertGreater(size,45)
            self.assertTrue(warn)
if __name__=="__main__": unittest.main()
