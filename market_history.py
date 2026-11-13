"""SQLite history for MM2 market snapshots, listings and qualified opportunities."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data/market_history.sqlite3")
LISTING_RETENTION_DAYS = 90
HISTORY_RETENTION_DAYS = 180


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_items(items: Any) -> list[dict[str, Any]]:
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            "name": str(item.get("name") or "")[:100],
            "quantity": item.get("quantity"),
            "quantity_min": item.get("quantity_min"),
            "quantity_max": item.get("quantity_max"),
            "quantity_source": str(item.get("quantity_source") or "")[:40],
        })
    return result


class MarketHistory:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at_utc TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL UNIQUE,
                    item_count INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'Supreme + secondary'
                );
                CREATE TABLE IF NOT EXISTS market_values (
                    snapshot_id INTEGER NOT NULL REFERENCES market_snapshots(id) ON DELETE CASCADE,
                    item_key TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    slug TEXT,
                    category TEXT,
                    item_type TEXT,
                    supreme_value REAL,
                    secondary_value REAL,
                    difference_percent REAL,
                    confidence TEXT NOT NULL,
                    stability TEXT,
                    PRIMARY KEY (snapshot_id, item_key)
                );
                CREATE INDEX IF NOT EXISTS idx_market_values_item
                    ON market_values(item_key, snapshot_id);
                CREATE TABLE IF NOT EXISTS listings (
                    listing_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    owner_name TEXT,
                    owner_url TEXT,
                    listing_url TEXT,
                    first_seen_at_utc TEXT,
                    last_seen_at_utc TEXT NOT NULL,
                    give_items_json TEXT NOT NULL,
                    receive_items_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_listings_last_seen
                    ON listings(last_seen_at_utc);
                CREATE TABLE IF NOT EXISTS opportunities (
                    opportunity_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    detected_at_utc TEXT NOT NULL,
                    give_total REAL,
                    receive_total REAL,
                    profit REAL,
                    margin_percent REAL,
                    listing_url TEXT,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_opportunities_detected
                    ON opportunities(detected_at_utc);
                """
            )

    @staticmethod
    def _value_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for key, record in (values or {}).items():
            if not isinstance(record, dict):
                continue
            variants = record.get("variants") or [record]
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                name = str(variant.get("name") or record.get("name") or key)[:120]
                item_key = str(
                    variant.get("id") or variant.get("slug")
                    or f"{name.casefold()}:{variant.get('category', '')}:{variant.get('type', '')}"
                )
                supreme = variant.get("value")
                secondary = variant.get("secondary_value")
                try:
                    supreme = float(supreme) if supreme is not None else None
                except (TypeError, ValueError):
                    supreme = None
                try:
                    secondary = float(secondary) if secondary is not None else None
                except (TypeError, ValueError):
                    secondary = None
                if supreme is not None and supreme > 0 and secondary is not None and secondary > 0:
                    difference = abs(supreme - secondary) / max(supreme, secondary) * 100
                    confidence = "cross_checked" if difference <= 15 else "conflict"
                elif (supreme is not None and supreme > 0) or (secondary is not None and secondary > 0):
                    difference = None
                    confidence = "estimated"
                else:
                    difference = None
                    confidence = "unrated"
                rows.append({
                    "item_key": item_key,
                    "item_name": name,
                    "slug": str(variant.get("slug") or "")[:160],
                    "category": str(variant.get("category") or "")[:60],
                    "item_type": str(variant.get("type") or "")[:60],
                    "supreme_value": supreme,
                    "secondary_value": secondary,
                    "difference_percent": difference,
                    "confidence": confidence,
                    "stability": str(variant.get("stability") or "")[:60],
                })
        rows.sort(key=lambda row: row["item_key"])
        return rows

    def record_values_snapshot(self, values: dict[str, Any]) -> bool:
        rows = self._value_rows(values)
        if not rows:
            return False
        digest = _canonical_hash(rows)
        captured = _utc_now()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO market_snapshots(captured_at_utc, snapshot_hash, item_count) VALUES (?, ?, ?)",
                (captured, digest, len(rows)),
            )
            if cursor.rowcount == 0:
                return False
            snapshot_id = cursor.lastrowid
            db.executemany(
                """
                INSERT INTO market_values(
                    snapshot_id, item_key, item_name, slug, category, item_type,
                    supreme_value, secondary_value, difference_percent, confidence, stability
                ) VALUES (
                    :snapshot_id, :item_key, :item_name, :slug, :category, :item_type,
                    :supreme_value, :secondary_value, :difference_percent, :confidence, :stability
                )
                """,
                [{"snapshot_id": snapshot_id, **row} for row in rows],
            )
            self._cleanup(db)
        return True

    def record_listings(self, listings: list[dict[str, Any]]) -> int:
        now = _utc_now()
        saved = 0
        with self._connect() as db:
            for listing in listings or []:
                if not isinstance(listing, dict):
                    continue
                listing_id = str(listing.get("id") or "")[:128]
                if not listing_id:
                    continue
                source = str(listing.get("source") or "bloxswaps")[:32].casefold()
                if source == "discord":
                    continue  # Private-server content is not written to the public state branch.
                source_id = str(
                    listing.get("discord_message_id") or listing.get("source_ad_id") or listing_id
                )[:160]
                give_items = _safe_items(listing.get("give_items"))
                receive_items = _safe_items(listing.get("receive_items"))
                content = {
                    "source": source,
                    "source_id": source_id,
                    "give": give_items,
                    "receive": receive_items,
                    "url": str(listing.get("url") or "")[:1000],
                }
                digest = _canonical_hash(content)
                db.execute(
                    """
                    INSERT INTO listings(
                        listing_id, source, source_id, owner_name, owner_url, listing_url,
                        first_seen_at_utc, last_seen_at_utc, give_items_json, receive_items_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(listing_id) DO UPDATE SET
                        source=excluded.source,
                        source_id=excluded.source_id,
                        owner_name=COALESCE(excluded.owner_name, listings.owner_name),
                        owner_url=COALESCE(excluded.owner_url, listings.owner_url),
                        listing_url=COALESCE(excluded.listing_url, listings.listing_url),
                        last_seen_at_utc=excluded.last_seen_at_utc,
                        give_items_json=excluded.give_items_json,
                        receive_items_json=excluded.receive_items_json,
                        content_hash=excluded.content_hash
                    """,
                    (
                        listing_id, source, source_id,
                        str(listing.get("source_author") or listing.get("owner_name") or "")[:100] or None,
                        str(listing.get("owner_url") or "")[:1000] or None,
                        str(listing.get("url") or "")[:1000] or None,
                        str(listing.get("first_seen_at_utc") or now),
                        now, json.dumps(give_items, ensure_ascii=False),
                        json.dumps(receive_items, ensure_ascii=False), digest,
                    ),
                )
                saved += 1
            self._cleanup(db)
        return saved

    def record_opportunity(self, opportunity: dict[str, Any]) -> bool:
        listing = opportunity.get("listing") or {}
        if not isinstance(listing, dict) or not listing.get("id"):
            return False
        source = str(listing.get("source") or "bloxswaps")[:32].casefold()
        if source == "discord":
            return False  # Never persist private Discord opportunities in public GitHub state.
        details = {
            "give_items": _safe_items(listing.get("give_items")),
            "receive_items": _safe_items(listing.get("receive_items")),
            "give_lines": opportunity.get("give_lines") or [],
            "receive_lines": opportunity.get("receive_lines") or [],
            "manual_review": bool(opportunity.get("manual_review")),
        }
        identity = {
            "listing_id": str(listing["id"]),
            "source": source,
            "details": details,
            "give_total": opportunity.get("give_total"),
            "receive_total": opportunity.get("receive_total"),
            "profit": opportunity.get("difference"),
            "margin": opportunity.get("margin"),
        }
        opportunity_id = _canonical_hash(identity)
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO opportunities(
                    opportunity_id, listing_id, source, detected_at_utc, give_total,
                    receive_total, profit, margin_percent, listing_url, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id, str(listing["id"]), source, _utc_now(),
                    opportunity.get("give_total"), opportunity.get("receive_total"),
                    opportunity.get("difference"), opportunity.get("margin"),
                    str(listing.get("url") or "")[:1000],
                    json.dumps(details, ensure_ascii=False),
                ),
            )
            self._cleanup(db)
            return cursor.rowcount > 0

    @staticmethod
    def _cleanup(db: sqlite3.Connection) -> None:
        listing_cutoff = (datetime.now(timezone.utc) - timedelta(days=LISTING_RETENTION_DAYS)).isoformat()
        history_cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
        db.execute("DELETE FROM listings WHERE last_seen_at_utc < ?", (listing_cutoff,))
        db.execute("DELETE FROM opportunities WHERE detected_at_utc < ?", (history_cutoff,))
        db.execute("DELETE FROM market_snapshots WHERE captured_at_utc < ?", (history_cutoff,))
