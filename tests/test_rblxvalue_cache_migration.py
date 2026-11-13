import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rblxvalue_guard
import scanner


class SupremeCacheMigrationTests(unittest.TestCase):
    def _schema3_cache(self, fetched_at):
        rows = [
            {
                "id": str(i), "slug": f"item-{i}", "name": f"Item {i}",
                "category": "godly", "type": "knife",
                "value_source_b": i + 10, "value_source_a": None,
                "stability": "Stable", "range": "", "last_updated": "2026-11-13",
            }
            for i in range(100)
        ]
        values = {
            row["name"].casefold(): {
                "name": row["name"], "value": row["value_source_b"],
                "value_min": row["value_source_b"], "value_max": row["value_source_b"],
                "secondary_value": None,
                "variants": [{
                    "id": row["id"], "slug": row["slug"], "name": row["name"],
                    "value": row["value_source_b"], "value_min": row["value_source_b"],
                    "value_max": row["value_source_b"], "raw_source_value": row["value_source_b"],
                    "value_status": "numeric", "secondary_value": None,
                    "category": "godly", "type": "knife", "last_updated": row["last_updated"],
                }],
            }
            for row in rows
        }
        return {
            "schema_version": 3, "fetched_at": fetched_at,
            "source": "fixture", "pages": {"0": {"etag": '"fixture-v1"', "total": 100, "items": rows}},
            "values": values,
        }

    def test_fresh_schema3_cache_migrates_and_reuses_without_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "supreme_values.json"
            audit = root / "audit.json"
            health = root / "key-health.json"
            base = 1_800_000_000.0
            cache.write_text(json.dumps(self._schema3_cache(base)), encoding="utf-8")
            now = [base]
            api = []

            def unchanged(path, params=None, etag=None):
                api.append((path, params, etag))
                if params and params.get("limit") == 1:
                    return {"items": [{"id": 1, "name": "Health Check"}]}, None, False
                return None, etag, True

            with (
                patch("scanner.VALUES_FILE", cache),
                patch("scanner.VALUES_AUDIT_FILE", audit),
                patch("scanner.RBLXVALUE_KEY_HEALTH_FILE", health),
                patch("scanner.VALUES_TTL_SECONDS", 43200),
                patch("scanner.time.time", side_effect=lambda: now[0]),
                patch("scanner.RBLXVALUE_API_KEY", "rbx_test_cache_key"),
                patch("rblxvalue_guard.BUDGET_FILE", root / "budget.json"),
                patch("rblxvalue_guard.BLOCK_FILE", root / "blocked.json"),
                patch("scanner.api_get_json_conditional", side_effect=unchanged),
                patch("scanner.time.sleep"),
            ):
                loaded = None
                for run in range(144):
                    now[0] = base + run * 600
                    loaded = scanner.load_supreme_values()

            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], 4)
            self.assertEqual(loaded["item 7"]["variants"][0]["stability"], "Stable")
            self.assertEqual(len(api), 5)

    def test_expired_cache_and_blocked_key_fail_closed_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "supreme_values.json"
            audit = root / "audit.json"
            blocked = root / "blocked.json"
            base = 1_800_000_000.0
            cache.write_text(json.dumps(self._schema3_cache(base - 50000)), encoding="utf-8")
            with (
                patch("scanner.VALUES_FILE", cache),
                patch("scanner.VALUES_AUDIT_FILE", audit),
                patch("scanner.VALUES_TTL_SECONDS", 43200),
                patch("scanner.time.time", return_value=base),
                patch("scanner.RBLXVALUE_API_KEY", "rbx_blocked_key"),
                patch("rblxvalue_guard.BLOCK_FILE", blocked),
                patch("rblxvalue_guard.BUDGET_FILE", root / "budget.json"),
                patch("scanner.api_get_json_conditional") as api,
            ):
                rblxvalue_guard.mark_key_blocked("HTTP 403", now=base - 10,
                                                  key="rbx_blocked_key", block_path=blocked)
                with self.assertRaisesRegex(RuntimeError, "refusing stale-cache calculations"):
                    scanner.load_supreme_values()
                api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
