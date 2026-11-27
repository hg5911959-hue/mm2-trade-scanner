# Scanner configuration

This guide documents the environment-backed defaults parsed by `tools/scanner_config.py`.
The scanner loads them once at startup and exposes the established module-level constants
for compatibility. Invalid values fail startup with the name of the affected variable;
runtime-only switches are still read by the component that uses them.

## Parsing rules

- Boolean values: `true/false`, `1/0`, `yes/no`, or `on/off` (case-insensitive).
- Integer and decimal settings must be syntactically valid. Floating-point values must be finite.
- Safety bounds are preserved: values outside a documented minimum/maximum are clamped where noted.
- Credentials are read from environment variables and are never written into this document or source control.

## Environment variables

| Variable | Default | Behavior |
|---|---:|---|
| `BLOXSWAPS_URL` | `https://bloxswaps.com/trade-mm2?trade-type=P2P` | Feed page URL |
| `RBLXVALUE_API_KEY` | empty | API credential; keep in a GitHub Actions secret |
| `TELEGRAM_BOT_TOKEN` | empty | Telegram credential; keep in a GitHub Actions secret |
| `TELEGRAM_CHAT_ID` | empty | Telegram destination |
| `MIN_VALUE_DIFFERENCE` | `0` | Minimum value difference |
| `MIN_PROFIT_PERCENT` | `5` | Minimum value-mode profit percentage |
| `MIN_RECEIVE_DEMAND_SCORE` | `4.0` | Value-mode demand threshold |
| `MIN_RECEIVE_STABILITY_SCORE` | `3.0` | Value-mode stability threshold |
| `FAILURE_ALERT_THRESHOLD` | `3` | Consecutive failures before alerting |
| `MAX_ALERTS_PER_RUN` | `0` | Zero means unlimited; negative values clamp to zero |
| `MAX_ALERTS_PER_SCAN` | `0` | Per-scan alert cap; zero means unlimited. Production workflow uses zero. |
| `TELEGRAM_CHAT_TYPE` | empty | Use `private` for private-chat pacing |
| `ALERT_DELAY_SECONDS` | `3.2` | Requested delay; group pacing retains its 3.2-second floor |
| `MAX_PENDING_RECHECK_PER_SCAN` | `50` | Minimum 1 |
| `MAX_BACKLOG_RECHECK_PAGES` | `25` | Minimum 1 |
| `BACKLOG_RECHECK_EVERY_CYCLES` | `4` | Minimum 1 |
| `MAX_FEED_PAGES` | `1000` | Clamped to 1–1000 |
| `CASH_FILTER` | `false` | Enables the cash-only qualification path |
| `RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY` | `6` | Minimum 0 |
| `RBLXVALUE_META_POLL_ENABLED` | `false` | Enables optional metadata polling |
| `VALUES_TTL_SECONDS` | 12h in value mode; 24h in cash mode | Clamped to 60 seconds–24 hours |
| `BLOXSWAPS_ITEMS_TTL_SECONDS` | `3600` | Minimum 300 seconds |
| `PENDING_TTL_SECONDS` | `604800` (7 days) | Minimum 86400 seconds |
| `MAX_SUCCESS_GAP_SECONDS` | `86400` (24 hours) | Minimum 3600 seconds |
| `SUSPICIOUS_PROFIT_PERCENT` | `100` | Suspicious-profit guard |
| `CASH_MIN_PROFIT_USD` | `2.00` | Minimum conservative cash profit; minimum 1.00 |
| `CASH_MIN_PROFIT_USD_SMALL` | `2.00` | Small-offer threshold; minimum 1.00 |
| `CASH_MIN_VERIFIED_ROWS` | `8` | Preflight minimum; minimum 0 |
| `CASH_MIN_MARGIN_PERCENT` | `0` | Must be finite and non-negative |
| `CASH_MAX_AGE_DAYS` | `1` | Minimum 1 day; production also enforces the stricter freshness gates |
| `CASH_MAX_LISTING_AGE_SECONDS` | `3600` | Minimum 60 seconds |

## Cash pricing invariant

The configuration layer does not select or transform market prices. Production cash valuation
continues to use only **chromas.gg → Price (7d)** and the matching **Sold (7d)** count from
ROBASE. No 30-day price, StarPets price, or All marketplaces figure is a fallback. The
existing minimum of three completed Chroma sales in the 7-day window remains a separate
cash-eligibility requirement.

## Runtime-only switches

Some flags are deliberately read at the point of use rather than frozen in `ScannerConfig`,
including `DRY_RUN`, `BLOXSWAPS_API_ONLY`, `MAX_ALERTS_PER_SCAN`, `MAX_ALERTS_PER_SESSION`,
`MM2_SCAN_DURATION_SECONDS`, `MM2_SCAN_INTERVAL_SECONDS`,
`RBLXVALUE_FORCE_KEY_CHECK`, and `MM2_TELEGRAM_SCAN_SUMMARY`.
