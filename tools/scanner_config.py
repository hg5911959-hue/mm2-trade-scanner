"""Typed, validated environment configuration for the MM2 scanner.

Runtime-only switches that are intentionally read at the point of use remain
in their owning modules. Values that determine scanner defaults are parsed
once here so invalid numeric settings fail with an actionable variable name.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from collections.abc import Mapping


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = str(env.get(name, "true" if default else "false")).strip().casefold()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: true/false, 1/0, yes/no, on/off")


def _int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = env.get(name, str(default))
    if raw is None or str(raw).strip() == "":
        raw = str(default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    raw = env.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None:
        value = max(minimum, value)
    return value


def _nonnegative_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and non-negative") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    bloxswaps_url: str
    rblxvalue_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    min_value_difference: float
    min_profit_percent: float
    min_receive_demand_score: float
    min_receive_stability_score: float
    failure_alert_threshold: int
    max_alerts_per_run: int
    max_alerts_per_scan: int
    max_alerts_per_session: int
    scan_duration_seconds: int
    scan_interval_seconds: int
    telegram_chat_type: str
    alert_delay_seconds: float
    max_pending_recheck_per_scan: int
    max_backlog_recheck_pages: int
    backlog_recheck_every_cycles: int
    max_feed_pages: int
    cash_filter: bool
    rblxvalue_max_detail_lookups_per_day: int
    rblxvalue_daily_request_limit: int
    rblxvalue_meta_poll_enabled: bool
    default_values_ttl_seconds: int
    values_ttl_seconds: int
    bloxswaps_items_ttl_seconds: int
    pending_ttl_seconds: int
    max_success_gap_seconds: int
    suspicious_profit_percent: float
    cash_min_profit_usd: float
    cash_min_profit_usd_small: float
    cash_min_verified_rows: int
    cash_min_margin_percent: float
    cash_max_age_days: int
    cash_max_listing_age_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ScannerConfig:
        source = os.environ if env is None else env
        cash_filter = _bool(source, "CASH_FILTER")
        default_values_ttl = 24 * 60 * 60 if cash_filter else 12 * 60 * 60
        max_alerts_per_run = _int(source, "MAX_ALERTS_PER_RUN", 0, minimum=0)
        return cls(
            bloxswaps_url=str(source.get(
                "BLOXSWAPS_URL",
                "https://bloxswaps.com/trade-mm2?trade-type=P2P",
            )).strip(),
            rblxvalue_api_key=str(source.get("RBLXVALUE_API_KEY", "")).strip(),
            telegram_bot_token=str(source.get("TELEGRAM_BOT_TOKEN", "")).strip(),
            telegram_chat_id=str(source.get("TELEGRAM_CHAT_ID", "")).strip(),
            min_value_difference=_float(source, "MIN_VALUE_DIFFERENCE", 0.0),
            min_profit_percent=_float(source, "MIN_PROFIT_PERCENT", 5.0),
            min_receive_demand_score=_float(source, "MIN_RECEIVE_DEMAND_SCORE", 4.0),
            min_receive_stability_score=_float(source, "MIN_RECEIVE_STABILITY_SCORE", 3.0),
            failure_alert_threshold=_int(source, "FAILURE_ALERT_THRESHOLD", 3),
            max_alerts_per_run=max_alerts_per_run,
            max_alerts_per_scan=_int(source, "MAX_ALERTS_PER_SCAN", 0, minimum=0),
            max_alerts_per_session=_int(
                source,
                "MAX_ALERTS_PER_SESSION",
                max_alerts_per_run,
                minimum=0,
            ),
            scan_duration_seconds=_int(source, "MM2_SCAN_DURATION_SECONDS", 0, minimum=0),
            scan_interval_seconds=_int(source, "MM2_SCAN_INTERVAL_SECONDS", 45, minimum=1),
            telegram_chat_type=str(source.get("TELEGRAM_CHAT_TYPE", "")).strip().casefold(),
            alert_delay_seconds=_float(source, "ALERT_DELAY_SECONDS", 3.2, minimum=0.0),
            max_pending_recheck_per_scan=_int(source, "MAX_PENDING_RECHECK_PER_SCAN", 50, minimum=1),
            max_backlog_recheck_pages=_int(source, "MAX_BACKLOG_RECHECK_PAGES", 25, minimum=1),
            backlog_recheck_every_cycles=_int(source, "BACKLOG_RECHECK_EVERY_CYCLES", 4, minimum=1),
            max_feed_pages=_int(source, "MAX_FEED_PAGES", 1000, minimum=1, maximum=1000),
            cash_filter=cash_filter,
            rblxvalue_max_detail_lookups_per_day=_int(
                source, "RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY", 6, minimum=0
            ),
            rblxvalue_daily_request_limit=_int(
                source, "RBLXVALUE_DAILY_REQUEST_LIMIT", 60, minimum=1, maximum=60
            ),
            rblxvalue_meta_poll_enabled=_bool(source, "RBLXVALUE_META_POLL_ENABLED"),
            default_values_ttl_seconds=default_values_ttl,
            values_ttl_seconds=min(
                24 * 60 * 60,
                max(60, _int(source, "VALUES_TTL_SECONDS", default_values_ttl)),
            ),
            bloxswaps_items_ttl_seconds=_int(
                source, "BLOXSWAPS_ITEMS_TTL_SECONDS", 3600, minimum=300
            ),
            pending_ttl_seconds=_int(
                source, "PENDING_TTL_SECONDS", 7 * 24 * 60 * 60, minimum=24 * 60 * 60
            ),
            max_success_gap_seconds=_int(
                source, "MAX_SUCCESS_GAP_SECONDS", 24 * 60 * 60, minimum=60 * 60
            ),
            suspicious_profit_percent=_float(source, "SUSPICIOUS_PROFIT_PERCENT", 100.0),
            cash_min_profit_usd=_float(source, "CASH_MIN_PROFIT_USD", 2.0, minimum=1.0),
            cash_min_profit_usd_small=_float(
                source, "CASH_MIN_PROFIT_USD_SMALL", 2.0, minimum=1.0
            ),
            cash_min_verified_rows=_int(source, "CASH_MIN_VERIFIED_ROWS", 8, minimum=0),
            cash_min_margin_percent=_nonnegative_float(
                source, "CASH_MIN_MARGIN_PERCENT", 5.0
            ),
            cash_max_age_days=_int(source, "CASH_MAX_AGE_DAYS", 1, minimum=1),
            cash_max_listing_age_seconds=_int(
                source, "CASH_MAX_LISTING_AGE_SECONDS", 3600, minimum=60
            ),
        )


CONFIG = ScannerConfig.from_env()
