import json
import re
import urllib.request
from datetime import date

BASE = "https://robase.gg"
DAY = "2026-11-24"
EXPECTED = {
    "starpets": (3.86, 126),
    "chromas": (2.67, 9),
    "bhopp": (2.74, 7),
}
EXPECTED_TOTAL = (3.73, 142)

def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ROBASEChartVerifier/1.0)"})
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        return response.read().decode("utf-8")

page = get(f"{BASE}/item/bat?days=365")
match = re.search(r'data-fees=["\']([^"\']+)["\']', page)
if not match:
    raise RuntimeError("Could not read ROBASE data-fees from Bat page")
fees = json.loads(match.group(1).replace("&quot;", '"').replace("&#39;", "'"))
api = json.loads(get(f"{BASE}/api.php?item=bat&days=365"))
if api.get("item", {}).get("slug") != "bat":
    raise RuntimeError("ROBASE API returned an unexpected item")

market_rows = []
total_sold = 0
weighted_amount = 0.0
priced_sold = 0
for code, points in api["markets"].items():
    point = next((p for p in points if p.get("day") == DAY), None)
    sold = (point or {}).get("sold", 0) or 0
    raw_price = (point or {}).get("price")
    fee = fees.get(code, 0)
    after_fee = raw_price * (1 - fee) if raw_price is not None else None
    total_sold += sold
    if after_fee is not None and sold:
        weighted_amount += after_fee * sold
        priced_sold += sold
    market_rows.append({
        "code": code,
        "fee_percent": round(fee * 100, 4),
        "raw_api_price": raw_price,
        "after_fee_price": round(after_fee, 8) if after_fee is not None else None,
        "after_fee_display": round(after_fee + 1e-12, 2) if after_fee is not None else None,
        "sold": sold,
    })

market_rows.sort(key=lambda x: x["code"])
total_price = weighted_amount / priced_sold if priced_sold else None
display_map = {row["code"]: row for row in market_rows}
market_checks = {}
for code, (price, sold) in EXPECTED.items():
    row = display_map.get(code)
    market_checks[code] = bool(row and row["sold"] == sold and row["after_fee_display"] == price)
total_check = total_sold == EXPECTED_TOTAL[1] and round(total_price + 1e-12, 2) == EXPECTED_TOTAL[0] if total_price is not None else False

result = {
    "item": api["item"],
    "date": DAY,
    "marketplace_fees": fees,
    "markets": market_rows,
    "all_marketplaces": {
        "sold": total_sold,
        "price_after_fee_exact": round(total_price, 8) if total_price is not None else None,
        "price_after_fee_display_2dp": round(total_price + 1e-12, 2) if total_price is not None else None,
        "priced_sold_denominator": priced_sold,
    },
    "expected": {
        "markets": {k: {"price_after_fee": v[0], "sold": v[1]} for k, v in EXPECTED.items()},
        "all_marketplaces": {"price_after_fee": EXPECTED_TOTAL[0], "sold": EXPECTED_TOTAL[1]},
    },
    "market_checks": market_checks,
    "all_marketplaces_check": total_check,
    "all_checks_pass": all(market_checks.values()) and total_check,
    "method": "ROBASE's chart total: sum sold across markets; weighted average of each marketplace's daily price after its fee, weighted by sold copies with a priced observation.",
}
with open("bat-verification.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result["all_checks_pass"]:
    raise SystemExit("Bat Nov 24 tooltip verification failed")
