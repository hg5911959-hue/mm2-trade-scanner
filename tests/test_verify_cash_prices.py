import csv
import tempfile
import unittest
from pathlib import Path

from tools.verify_cash_prices import verify


class VerifyCashPricesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv_path = self.root / "cash.csv"
        self.input_path = self.root / "prices.txt"
        self.names = [
            "Spirit", "Harvester", "Bat", "Immortal", "Icebreaker", "Bloom",
            "Snowstorm", "Candy", "Watergun", "Evergun", "Celestial",
        ]
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "name", "cash_price_usd", "tier", "status",
                "verified_at_utc", "source", "notes",
            ])
            writer.writeheader()
            for name in self.names:
                writer.writerow({
                    "name": name, "cash_price_usd": "1.00", "tier": "test",
                    "status": "unverified", "verified_at_utc": "",
                    "source": "", "notes": "",
                })

    def test_user_formats_verify_eleven_rows_and_skip_unknown_and_x2_total(self):
        self.input_path.write_text("\n".join([
            "x1 spirit knife 8$",
            "harvester 5.5$",
            "bat 2.8$/1",
            "immortal gun 2.8$",
            "icebreaker 1.1$",
            "bloom 7$",
            "snowstorm 4$",
            "candy 2.2$",
            "watergun 2.6$",
            "Evergun 65$",
            "Celestial 34$",
            "unknown sword 99$",
            "x2 spirit knife 16$",
        ]), encoding="utf-8")
        result = verify(self.input_path, self.csv_path, max_change_pct=10000)
        self.assertEqual(result["verified"], 11)
        self.assertEqual(len(result["skipped"]), 2)
        with self.csv_path.open(encoding="utf-8", newline="") as handle:
            rows = {row["name"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["Spirit"]["cash_price_usd"], "8.00")
        self.assertEqual(rows["Spirit"]["status"], "verified")
        self.assertEqual(rows["Bat"]["cash_price_usd"], "2.80")
        self.assertEqual(rows["Celestial"]["cash_price_usd"], "34.00")

    def test_large_price_change_refuses_to_write(self):
        self.input_path.write_text("spirit 100$", encoding="utf-8")
        before = self.csv_path.read_bytes()
        with self.assertRaises(ValueError):
            verify(self.input_path, self.csv_path, max_change_pct=60)
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_atomic_write_leaves_no_temporary_file(self):
        self.input_path.write_text("spirit 8$", encoding="utf-8")
        verify(self.input_path, self.csv_path, max_change_pct=1000)
        self.assertFalse(list(self.root.glob("cash.csv.*.tmp")))

    def test_dry_run_does_not_change_catalogue(self):
        self.input_path.write_text("spirit 8$", encoding="utf-8")
        before = self.csv_path.read_bytes()
        result = verify(self.input_path, self.csv_path, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.csv_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
