"""Bounded public-only history for the MM2 cash dashboard.

This store is deliberately separate from market_history.sqlite3: it accepts only
public BloxSwaps listings and cash-qualified opportunities, and has no tables for
Supreme/RBLXValue snapshots or private Discord content.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DB_PATH = Path("data/public_dashboard_history.sqlite3")
ALLOWED_HOSTS = {"bloxswaps.com", "www.bloxswaps.com"}
LISTING_RETENTION_DAYS = 90
OPPORTUNITY_RETENTION_DAYS = 180
MAX_LISTINGS = 3_000
MAX_OPPORTUNITIES = 1_000
MAX_ITEMS_PER_SIDE = 50


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_link(value: object) -> str:
    raw = _safe_text(value, 1000)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in ALLOWED_HOSTS:
        return ""
    return raw


def _safe_items(value: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in (value[:MAX_ITEMS_PER_SIDE] if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("name"), 100)
        if not name:
            continue
        quantity = item.get("quantity")
        if type(quantity) is not int or not 1 <= quantity <= 9999:
            quantity = None
        result.append({"name": name, "quantity": quantity})
    return result


def _finite_number(value: object, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= minimum else None


def _timestamp(value: object, fallback: str) -> str:
    raw = _safe_text(value, 64)
    if not raw:
        return fallback
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return raw


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PublicDashboardHistory:
    """Persist only sanitized, public BloxSwaps cash-market history."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.is_symlink():
            raise ValueError("public dashboard history path must not be a symlink")
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    listing_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL CHECK (source = 'bloxswaps'),
                    source_id TEXT NOT NULL,
                    owner_name TEXT,
                    owner_url TEXT,
                    listing_url TEXT NOT NULL,
                    first_seen_at_utc TEXT NOT NULL,
                    last_seen_at_utc TEXT NOT NULL,
                    give_items_json TEXT NOT NULL,
                    receive_items_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_public_listings_last_seen
                    ON listings(last_seen_at_utc);
                CREATE TABLE IF NOT EXISTS opportunities (
                    opportunity_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source = 'bloxswaps'),
                    detected_at_utc TEXT NOT NULL,
                    give_total REAL,
                    receive_total REAL,
                    profit REAL NOT NULL,
                    margin_percent REAL,
                    listing_url TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_public_opportunities_detected
                    ON opportunities(detected_at_utc);
                PRAGMA user_version = 1;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        if self.db_path.is_symlink():
            raise ValueError("public dashboard history path must not be a symlink")
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_listings(self, listings: list[dict[str, object]]) -> int:
        now = _now()
        saved = 0
        with self._connect() as db:
            for listing in listings or []:
                if not isinstance(listing, dict):
                    continue
                if _safe_text(listing.get("source"), 32).casefold() != "bloxswaps":
                    continue
                listing_id = _safe_text(listing.get("id"), 128)
                source_id = _safe_text(listing.get("source_ad_id"), 160)
                listing_url = _safe_link(listing.get("url"))
                if not listing_id or not source_id or not listing_url:
                    continue
                owner_url = _safe_link(listing.get("owner_url"))
                owner_name = _safe_text(listing.get("source_author") or listing.get("owner_name"), 100)
                give_items = _safe_items(listing.get("give_items"))
                receive_items = _safe_items(listing.get("receive_items"))
                content = {
                    "source_id": source_id,
                    "give": give_items,
                    "receive": receive_items,
                    "url": listing_url,
                }
                db.execute(
                    """
                    INSERT INTO listings(
                        listing_id, source, source_id, owner_name, owner_url, listing_url,
                        first_seen_at_utc, last_seen_at_utc, give_items_json,
                        receive_items_json, content_hash
                    ) VALUES (?, 'bloxswaps', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(listing_id) DO UPDATE SET
                        source_id=excluded.source_id,
                        owner_name=COALESCE(excluded.owner_name, listings.owner_name),
                        owner_url=COALESCE(excluded.owner_url, listings.owner_url),
                        listing_url=excluded.listing_url,
                        last_seen_at_utc=excluded.last_seen_at_utc,
                        give_items_json=excluded.give_items_json,
                        receive_items_json=excluded.receive_items_json,
                        content_hash=excluded.content_hash
                    """,
                    (
                        listing_id, source_id, owner_name or None, owner_url or None,
                        listing_url, _timestamp(listing.get("first_seen_at_utc"), now),
                        now, json.dumps(give_items, ensure_ascii=False),
                        json.dumps(receive_items, ensure_ascii=False), _digest(content),
                    ),
                )
                saved += 1
            self._cleanup(db)
        return saved

    def record_opportunity(self, opportunity: dict[str, object]) -> bool:
        listing = opportunity.get("listing")
        if not isinstance(listing, dict):
            return False
        if _safe_text(listing.get("source"), 32).casefold() != "bloxswaps":
            return False
        if _safe_text(listing.get("valuation_mode"), 24).casefold() != "cash":
            return False
        listing_id = _safe_text(listing.get("id"), 128)
        listing_url = _safe_link(listing.get("url"))
        profit = _finite_number(opportunity.get("cash_profit_worst"))
        if not listing_id or not listing_url or profit is None:
            return False
        give_total = _finite_number(opportunity.get("give_total"))
        receive_total = _finite_number(opportunity.get("receive_total"))
        margin = _finite_number(opportunity.get("margin"), minimum=-1_000_000.0)
        give_items = _safe_items(listing.get("give_items"))
        receive_items = _safe_items(listing.get("receive_items"))
        details = {
            "give_items": give_items,
            "receive_items": receive_items,
            "manual_review": bool(opportunity.get("manual_review")),
            "valuation_mode": "cash",
        }
        identity = {
            "listing_id": listing_id,
            "details": details,
            "profit": profit,
            "margin": margin,
        }
        opportunity_id = _digest(identity)
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO opportunities(
                    opportunity_id, listing_id, source, detected_at_utc,
                    give_total, receive_total, profit, margin_percent,
                    listing_url, details_json
                ) VALUES (?, ?, 'bloxswaps', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id, listing_id, _now(), give_total, receive_total,
                    profit, margin, listing_url, json.dumps(details, ensure_ascii=False),
                ),
            )
            self._cleanup(db)
            return cursor.rowcount > 0

    @staticmethod
    def _cleanup(db: sqlite3.Connection) -> None:
        listing_cutoff = (datetime.now(UTC) - timedelta(days=LISTING_RETENTION_DAYS)).isoformat()
        opportunity_cutoff = (datetime.now(UTC) - timedelta(days=OPPORTUNITY_RETENTION_DAYS)).isoformat()
        db.execute("DELETE FROM listings WHERE last_seen_at_utc < ?", (listing_cutoff,))
        db.execute("DELETE FROM opportunities WHERE detected_at_utc < ?", (opportunity_cutoff,))
        db.execute(
            "DELETE FROM listings WHERE listing_id NOT IN "
            "(SELECT listing_id FROM listings ORDER BY last_seen_at_utc DESC LIMIT ?)",
            (MAX_LISTINGS,),
        )
        db.execute(
            "DELETE FROM opportunities WHERE opportunity_id NOT IN "
            "(SELECT opportunity_id FROM opportunities ORDER BY detected_at_utc DESC LIMIT ?)",
            (MAX_OPPORTUNITIES,),
        )
