#!/usr/bin/env python3
"""Daily RBLXValue API snapshot for the 214 ROBASE weapons (research-only)."""
from __future__ import annotations
import argparse, csv, json, math, os, re, time, unicodedata
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import rblxvalue_guard

API_BASE = "https://api.rblxvalue.com/v2"
PAGE_SIZE = 100
MIN_REQUEST_INTERVAL_SECONDS = 2.1
CSV_FIELDS = ("snapshot_date","fetched_at_utc","dataset_last_updated","item_last_updated","robase_slug","robase_name","bloxswaps_slug","item_type","item_rarity","is_chroma","rblxvalue_slug","rblxvalue_name","category","api_type","value","value_source_a","value_source_b","stability","demand","rarity","range","status")
CATEGORY_MAP = {"godly":"godly","ancient":"ancient","vintage":"vintage","classic":"vintage","legendary":"legendary","rare":"rare","uncommon":"uncommon","common":"common","unique":"unique","chroma":"chroma"}

# These are explicit identity aliases, not fuzzy guesses. Each entry was
# cross-checked against the public RBLXValue item page and the ROBASE/BloxSwaps
# exact weapon identity (type + category + catalogue slug). The API response
# is still revalidated on every daily collection.
EXACT_IDENTITY_ALIASES = {
    "bat": {"robase_name":"Bat","bloxswaps_slug":"bat","slug":"bat-knife","name":"Bat Knife","category":"godly","type":"knife","url":"https://rblxvalue.com/item/bat-knife"},
    "cane-562": {"robase_name":"Cane","bloxswaps_slug":"cane-k-2018","slug":"cane-knife-2018","name":"Cane Knife 2018","category":"rare","type":"knife","url":"https://rblxvalue.com/item/cane-knife-2018"},
    "ghost-1149": {"robase_name":"Ghost","bloxswaps_slug":"ghost-g-2026","slug":"ghost-gun","name":"Ghost Gun","category":"uncommon","type":"gun","url":"https://rblxvalue.com/item/ghost-gun"},
    "laser": {"robase_name":"Laser","bloxswaps_slug":"disint","slug":"vintage-laser","name":"Vintage Laser","category":"vintage","type":"gun","url":"https://rblxvalue.com/item/vintage-laser"},
    "laser-1148": {"robase_name":"Laser","bloxswaps_slug":"laser-k-2026","slug":"laser-knife","name":"Laser Knife","category":"rare","type":"knife","url":"https://rblxvalue.com/item/laser-knife"},
    "latte": {"robase_name":"Latte","bloxswaps_slug":"latte","slug":"latte-gun","name":"Latte gun","category":"legendary","type":"gun","url":"https://rblxvalue.com/item/latte-gun"},
    "latte-915": {"robase_name":"Latte","bloxswaps_slug":"latte-k-2023","slug":"latte-knife","name":"Latte Knife","category":"legendary","type":"knife","url":"https://rblxvalue.com/item/latte-knife"},
    "phaser-1151": {"robase_name":"Phaser","bloxswaps_slug":"phaser-k-2026","slug":"phaser-knife","name":"Phaser Knife","category":"rare","type":"knife","url":"https://rblxvalue.com/item/phaser-knife"},
    "prince-1152": {"robase_name":"Prince","bloxswaps_slug":"prince-g-2026","slug":"prince-gun","name":"Prince Gun","category":"rare","type":"gun","url":"https://rblxvalue.com/item/prince-gun"},
}

def normalize_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()

def normalize_slug(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

def parse_number(value):
    if value is None or isinstance(value, bool): return None
    if isinstance(value, int): return value if value >= 0 else None
    if isinstance(value, float): return int(value) if math.isfinite(value) and value >= 0 and value.is_integer() else None
    value = str(value).strip().replace(",", "")
    return int(value) if re.fullmatch(r"\d+", value) else None

class RBLXValueClient:
    def __init__(self, api_key, *, timeout=20, sleep=time.sleep):
        if not api_key or not api_key.strip():
            raise ValueError("RBLXVALUE_API_KEY is required; no anonymous or scraped fallback is allowed")
        self.api_key, self.timeout, self.sleep = api_key.strip(), timeout, sleep
        self.last_request_at = 0.0

    def _get_json(self, path, params=None):
        url = API_BASE + path + (("?" + urlencode(params)) if params else "")
        headers = {"X-Api-Key": self.api_key, "Accept": "application/json", "User-Agent": "MM2-Trade-Scanner-RBLXValue-Research/1.0"}
        last_error = None
        for attempt in range(4):
            elapsed = time.monotonic() - self.last_request_at
            if self.last_request_at and elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                self.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            # Reserve every HTTP attempt (including retries) before network I/O.
            # The rolling ledger is restored from and persisted to the archive branch.
            rblxvalue_guard.reserve_request(
                key=self.api_key,
                workflow="RBLXValue Daily Weapon History",
            )
            self.last_request_at = time.monotonic()
            try:
                with urlopen(Request(url, headers=headers), timeout=self.timeout) as response:
                    if response.status != 200: raise RuntimeError(f"RBLXValue API returned HTTP {response.status}")
                    body = response.read(4_000_001)
                    if len(body) > 4_000_000: raise ValueError("RBLXValue response exceeded 4 MB")
                self.last_request_at = time.monotonic()
                result = json.loads(body.decode("utf-8"))
                if not isinstance(result, dict): raise ValueError("RBLXValue response is not a JSON object")
                return result
            except HTTPError as exc:
                self.last_request_at = time.monotonic()
                if exc.code == 429 and attempt < 3:
                    try: delay = min(60.0, max(2.1, float(exc.headers.get("Retry-After", ""))))
                    except (ValueError, TypeError): delay = 10.0 * (attempt + 1)
                    self.sleep(delay); last_error = exc; continue
                if exc.code in (401,403):
                    raise RuntimeError(f"RBLXValue API authentication/access failed (HTTP {exc.code}); check key activation") from exc
                raise RuntimeError(f"RBLXValue API returned HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                self.last_request_at = time.monotonic()
                last_error = exc
                if attempt < 3: self.sleep(2 ** attempt)
        raise RuntimeError(f"RBLXValue API request failed: {type(last_error).__name__}: {last_error}")

    def fetch_dataset(self):
        response = self._get_json("/meta")
        meta = response.get("meta", response)
        if not isinstance(meta, dict): raise ValueError("RBLXValue /meta metadata is invalid")
        total = parse_number((meta.get("totals") or {}).get("items"))
        updated = str(meta.get("last_updated") or "").strip()
        if total is None or total < 214 or not updated:
            raise ValueError("RBLXValue /meta is incomplete or reports fewer than 214 items")
        items = []
        for offset in range(0, total, PAGE_SIZE):
            page_response = self._get_json("/items", {"limit": PAGE_SIZE, "offset": offset})
            page = page_response.get("items")
            if not isinstance(page, list) or (not page and offset < total):
                raise ValueError(f"Invalid or prematurely empty RBLXValue page at offset {offset}")
            if any(not isinstance(item, dict) for item in page): raise ValueError("Malformed RBLXValue item")
            items.extend(page)
        if len(items) != total: raise ValueError(f"Pagination mismatch: expected {total}, received {len(items)}")
        slugs = [str(x.get("slug") or "").casefold() for x in items]
        if any(not x for x in slugs) or len(slugs) != len(set(slugs)): raise ValueError("Missing or duplicate API item slugs")
        return items, {"last_updated": updated, "total_items": total}

def expected_category(identity):
    return "chroma" if identity.get("bloxswaps_chroma") is True else CATEGORY_MAP.get(str(identity.get("bloxswaps_rarity") or "").casefold())

def candidate_matches(identity, source_items):
    target_name = normalize_name(identity.get("robase_name"))
    target_slugs = {normalize_slug(identity.get("robase_slug")), normalize_slug(identity.get("bloxswaps_slug"))} - {""}
    category = expected_category(identity)
    kind = str(identity.get("bloxswaps_type") or "").casefold()
    if not target_name or not category or kind not in {"knife","gun"}: return []
    # Prefer a normal exact match first. This preserves legacy fixtures/items
    # whose current API display name or slug already matches ROBASE directly.
    matches = []
    for item in source_items:
        if str(item.get("category") or "").casefold() != category or str(item.get("type") or "").casefold() != kind: continue
        if normalize_name(item.get("name")) == target_name or normalize_slug(item.get("slug")) in target_slugs:
            matches.append(item)
    matches = list({str(x.get("slug") or "").casefold(): x for x in matches}.values())
    if matches:
        return matches

    alias = EXACT_IDENTITY_ALIASES.get(str(identity.get("robase_slug") or "").casefold())
    if not alias:
        return []
    # An alias is valid only for this exact ROBASE row, exact BloxSwaps slug,
    # exact API slug/name, and exact category/type. Any mismatch fails closed.
    if (
        str(identity.get("robase_name") or "").casefold() != alias["robase_name"].casefold()
        or str(identity.get("bloxswaps_slug") or "").casefold() != alias["bloxswaps_slug"]
        or category != alias["category"]
        or kind != alias["type"]
    ):
        return []
    alias_matches = [
        item for item in source_items
        if normalize_slug(item.get("slug")) == normalize_slug(alias["slug"])
        and normalize_name(item.get("name")) == normalize_name(alias["name"])
        and str(item.get("category") or "").casefold() == alias["category"]
        and str(item.get("type") or "").casefold() == alias["type"]
    ]
    return alias_matches if len(alias_matches) == 1 else []

def build_snapshot(identity_map, source_items, meta, *, snapshot_date, fetched_at_utc, expected_count=214):
    if not isinstance(identity_map, dict) or len(identity_map) != expected_count: raise ValueError(f"Expected exactly {expected_count} ROBASE weapon identities")
    if not isinstance(source_items, list) or len(source_items) != meta.get("total_items"): raise ValueError("Source item count does not match metadata")
    if not meta.get("last_updated") or not snapshot_date or not fetched_at_utc: raise ValueError("Snapshot timestamps are required")
    rows, claims = [], {}
    for blox_slug, identity in sorted(identity_map.items()):
        if not isinstance(identity, dict): raise ValueError(f"Invalid identity for {blox_slug}")
        candidates, category = candidate_matches(identity, source_items), expected_category(identity)
        row = {"snapshot_date":snapshot_date,"fetched_at_utc":fetched_at_utc,"dataset_last_updated":meta["last_updated"],"item_last_updated":None,"robase_slug":str(identity.get("robase_slug") or ""),"robase_name":str(identity.get("robase_name") or ""),"bloxswaps_slug":str(blox_slug),"item_type":str(identity.get("bloxswaps_type") or "").casefold(),"item_rarity":str(identity.get("bloxswaps_rarity") or ""),"is_chroma":identity.get("bloxswaps_chroma") is True,"identity_method":"explicit_verified_alias" if str(identity.get("robase_slug") or "").casefold() in EXACT_IDENTITY_ALIASES else "exact_name_or_slug","identity_source_url":EXACT_IDENTITY_ALIASES.get(str(identity.get("robase_slug") or "").casefold(),{}).get("url"),"rblxvalue_slug":None,"rblxvalue_name":None,"category":category,"api_type":None,"value":None,"value_source_a":None,"value_source_b":None,"stability":None,"demand":None,"rarity":None,"range":None,"status":"missing" if category else "unsupported_category_or_type"}
        if len(candidates) == 1:
            item = candidates[0]; slug = str(item.get("slug") or "").casefold()
            row.update({"item_last_updated":item.get("last_updated"),"rblxvalue_slug":slug,"rblxvalue_name":item.get("name"),"category":item.get("category"),"api_type":item.get("type"),"value":parse_number(item.get("value")),"value_source_a":parse_number(item.get("value_source_a")),"value_source_b":parse_number(item.get("value_source_b")),"stability":str(item.get("stability") or "").strip() or None,"demand":parse_number(item.get("demand")),"rarity":parse_number(item.get("rarity")),"range":str(item.get("range") or "").strip() or None,"status":"matched"})
            if row["value"] is None: row["status"] = "matched_missing_blended_value"
            elif row["value_source_b"] is None or row["value_source_b"] <= 0: row["status"] = "matched_missing_supreme_value"
            claims.setdefault(slug, []).append(len(rows))
        elif len(candidates) > 1: row["status"] = "ambiguous"
        rows.append(row)
    for slug, indexes in claims.items():
        if len(indexes) > 1:
            for i in indexes:
                rows[i]["status"] = "ambiguous_identity_collision"
                for field in ("value","value_source_a","value_source_b","stability","demand","rarity","range"): rows[i][field] = None
    counts = {"weapons":len(rows),"exactly_mapped":sum(bool(x["rblxvalue_slug"]) for x in rows),"explicit_verified_aliases":sum(x["identity_method"]=="explicit_verified_alias" and bool(x["rblxvalue_slug"]) for x in rows),"matched":sum(x["status"]=="matched" for x in rows),"matched_missing_blended_value":sum(x["status"]=="matched_missing_blended_value" for x in rows),"matched_missing_supreme_value":sum(x["status"]=="matched_missing_supreme_value" for x in rows),"missing":sum(x["status"]=="missing" for x in rows),"ambiguous":sum(x["status"] in {"ambiguous","ambiguous_identity_collision"} for x in rows),"unsupported_category_or_type":sum(x["status"]=="unsupported_category_or_type" for x in rows),"with_blended_value":sum(x["value"] is not None and x["value"] > 0 for x in rows),"with_supreme_value":sum(x["value_source_b"] is not None and x["value_source_b"] > 0 for x in rows),"with_stability":sum(x["stability"] is not None for x in rows)}
    return {"schema_version":1,"source":"RBLXValue official API v2","source_url":API_BASE,"source_fields":{"value":"RBLXValue blended estimate (not used for trade pricing)","value_source_a":"MM2Values (not used for trade pricing)","value_source_b":"Supreme Values (exclusive trade-price source)","stability":"RBLXValue","demand":"RBLXValue","rarity":"RBLXValue","range":"RBLXValue metadata; not used for trade-price bounds"},"third_party_authorization":{"source":"Supreme Values","authorization_date":"2026-11-26","authorized_scope":"Extract, process, and display published price-list, Demand, Rarity, and Stability data for the MM2 project, subject to accurate attribution and no ownership claim","attribution":"Supreme Values (supremevalues.com)"},"snapshot_date":snapshot_date,"fetched_at_utc":fetched_at_utc,"dataset_last_updated":meta["last_updated"],"source_total_items":meta["total_items"],"expected_weapons":expected_count,"counts":counts,"policy":{"identity":"exact normalized ROBASE display name or exact ROBASE/BloxSwaps slug within matching category/type, plus the explicit verified aliases listed in identity_method","ambiguous_identity":"rejected; no fuzzy matching","missing_values":"null; never treated as zero","production":"optional Telegram reference display only; never used for cash valuation, eligibility, ranking, or alert decisions"},"items":rows}

def collect_snapshot(identity_path, api_key, *, now=None):
    identities = json.loads(Path(identity_path).read_text(encoding="utf-8"))
    if not isinstance(identities, dict): raise ValueError("ROBASE identity map must be a JSON object")
    items, meta = RBLXValueClient(api_key).fetch_dataset()
    local_now = now or datetime.now(ZoneInfo("Africa/Algiers"))
    if local_now.tzinfo is None or local_now.utcoffset() is None: raise ValueError("Snapshot time must be timezone-aware")
    snapshot = build_snapshot(identities, items, meta, snapshot_date=local_now.astimezone(ZoneInfo("Africa/Algiers")).date().isoformat(), fetched_at_utc=local_now.astimezone(ZoneInfo("UTC")).isoformat())
    print("[rblxvalue-daily] coverage=" + json.dumps(snapshot["counts"], sort_keys=True))
    return snapshot

def write_outputs(snapshot, json_path, csv_path):
    Path(json_path).parent.mkdir(parents=True, exist_ok=True); Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    with Path(csv_path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore"); writer.writeheader(); writer.writerows(snapshot["items"])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-map", required=True); parser.add_argument("--json-output", required=True); parser.add_argument("--csv-output", required=True); parser.add_argument("--api-key-env", default="RBLXVALUE_API_KEY")
    args = parser.parse_args()
    try:
        snapshot = collect_snapshot(args.identity_map, os.environ.get(args.api_key_env, ""))
    finally:
        rblxvalue_guard.log_request_count()
    write_outputs(snapshot, args.json_output, args.csv_output)
    return 0

if __name__ == "__main__": raise SystemExit(main())
