# Architecture and module boundaries

The scanner is being decomposed incrementally. `scanner.py` remains the compatibility
entry point and composition root so existing workflows, tests and callers do not need
a coordinated rewrite. Extracted modules own narrow responsibilities and are imported
back into `scanner.py` where legacy symbol names must remain available.

## Current boundaries

| Module | Responsibility |
|---|---|
| `tools/scanner_config.py` | Typed, immutable startup configuration; validates environment booleans, integers and finite thresholds |
| `tools/scan_loop_policy.py` | Pure scan-window and backlog-cadence decisions |
| `tools/scan_loop.py` | Bounded polling orchestration, graceful signal handling, rate-limit stop behavior and metrics |
| `tools/state_store.py` | Durable same-directory temporary writes with fsync and atomic replacement |
| `tools/html_tree.py` | Small event-based HTML tree, traversal, text extraction and trade-side matching |
| `tools/item_parser.py` | Item-name normalization, Arabic digit handling, DOM-scoped quantity evidence and trade-side item extraction |
| `tools/bloxswaps_api.py` | Structured trade-post side classification and catalog-backed listing adaptation |
| `tools/listing_identity.py` | Durable source listing IDs, normalized item identity and legacy alert-state migration |
| `tools/telegram_transport.py` | Telegram redirect protection and conservative per-chat pacing |
| `tools/telegram_delivery.py` | Trusted HTML formatting, attribution, multipart delivery and non-idempotent send safety |
| `tools/redaction.py` | Secret discovery, credential scrubbing and line-buffered safe log streams |
| `tools/retry_after.py` | Shared delta-seconds and HTTP-date parsing for upstream retry responses |
| `tools/http_retry.py` | Bounded response reads, transient HTTP retries, credential-origin allowlisting and safe 429 handling |
| `tools/robase_prices.py` | ROBASE price snapshot parsing and Chroma.gg 7-day source validation |
| `tools/cash_market.py` | Conservative cash-market valuation and qualification helpers |
| `tools/robase_identity.py` | Identity matching between the feed and ROBASE catalog |
| `tools/alert_policy.py` | Ranking and alert-budget policy |
| `tools/rblxvalue_guard.py` | RBLXValue request budget, key health and safe request accounting |
| `tools/validate_state.py` | Persisted-state schema and integrity checks |

## Compatibility and refactoring rules

- Keep established imports such as `scanner.run_scan_loop`, `scanner.PageTree`,
  `scanner.atomic_write_text` and `scanner.telegram_alert_delay` working while their
  implementations live in focused modules.
- Extract one responsibility at a time. Do not rewrite the 6k-line scanner in one change.
- Keep tests for both the focused module and its compatibility surface.
- Do not weaken parsing, price-source, alert, or state-integrity checks to make a refactor pass.

## Pricing invariant

Production cash valuation uses **Chroma.gg → Price (7d)** and the corresponding **Sold (7d)**
count only. The 30-day price, StarPets and All marketplaces values are not cash-price
fallbacks. Cash eligibility also requires the configured minimum completed-sale count.

## Remaining extraction candidates

The remaining large sections of `scanner.py` are intentionally not moved in this pass.
Good future boundaries are feed retrieval/pagination, opportunity evaluation, and state
serialization/reconciliation. Each requires dependency
injection and dedicated regression tests before extraction; avoid copying those blocks
without their source and failure-mode tests.
