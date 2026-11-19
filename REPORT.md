# MM2 Market Intelligence — Engineering Report

## Baseline and phase log

- Baseline main commit: `47ef8b93346d156d9af34aa53c7f84b754bd1998`
- Baseline test suite: **387 tests**, passed twice on Python 3.12 in GitHub Actions run [#43745530514](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43745530514).
- Both runs checked `git status --porcelain data`; the workflow passed.
- Standard library only; no paid services or new secrets introduced.
- Phase 0: baseline recorded. **Open operational limitation:** the available GitHub integration can create/update files and branches, but exposes no tag-object/ref creation operation. I will not create a branch named `before-v13` and misrepresent it as a tag. The required pushed tag `before-v13` remains uncreated and needs a GitHub tag-write capability or maintainer action.

## Phases

| Phase | Status | Commit |
|---|---|---|
| 0 — Safety/baseline | Baseline recorded; tag blocked by missing tag-write operation | `c16165ba07f33b20348201e4ed2729b12a08f401` (report); test assertion `ef452658e30cff20fa0a29f1bed316dc9c5f4039` |\n| 1 — Pause USD feature | Complete: default-off code audited; added integration assertions for mode summary, no catalogue/snapshot access, no maintenance notice, and value-only alert. | `9807dc54ec0ee32b2c59a68561a6527883076528`; report `e698c91970d86c1ac91b62620cb8f2ecdd380c38` |

- Phase 1 verification: **388 tests passed twice** (Python 3.12, full Ruff checks green) in Actions run [#43746330397](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43746330397).\n- Cash remains disabled by default. The Supreme/game-value path remains active and does not consult cash prices.\n\n- Phase 2 verification: **390 tests passed twice**; data-tree hashes remained identical and `git status --porcelain data` was empty in both passes. Actions run [#43746531576](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43746531576).\n\n## Environment variables

- `CASH_FILTER=false` by default; accepted true values: `1`, `true`, `yes`, `on` (case-insensitive).

## Changed files

- Phase 0: `REPORT.md`, `tests/test_workflow_config.py`.\n- Phase 1: `tests/test_cash_filter_modes.py` (existing default-off integration coverage strengthened).\n- Phase 2: `.github/workflows/tests.yml`, `tests/test_no_data_pollution.py`, `tests/test_workflow_config.py`.

## Open questions / blockers

1. Who or what can create and push the annotated/lightweight Git tag `before-v13` at the baseline commit? Current integration has no tag creation operation.
2. The user requests Telegram command search with bounded freshness and a live scan process. This repository currently runs bounded GitHub Actions jobs; command responsiveness outside an active job cannot be guaranteed without changing the hosting/transport model. No alternative service will be introduced without an explicit supported architecture.

## Self-assessment

Not yet scored. Scores will be evidence-based after the corresponding phases and tests are complete.


## MM2 cash-market upgrade — 2026-11-19

### Phase 0 — Safety baseline
- Created safety branch `before-market-book` from the current `main` head (PR base SHA: `d26ddf4205aff952754a825d741b0bc590260238`).
- The GitHub integration exposes branch creation but no Git tag/ref creation operation. The requested `before-market-book` tag is therefore **not** claimed as created; the branch is the available safety checkpoint.
- The latest baseline evidence already recorded on main is 390 tests passing twice (Actions run `#43746531576`). A new baseline workflow dispatch is not exposed by the available GitHub integration; the draft PR's pull-request test workflow is the verification path for these changes.

### Phase 1 — Cash-offer parser foundation
- Added `cash_offer_parser.py`, `cash_item_aliases.json`, `tests/fixtures/discord_cash_posts_sample.txt`, and `tests/test_cash_offer_parser.py`.
- Handles seller/buyer headers (including `QUICK SELL` as seller), inline headers, pipe-separated lines, per-unit markers, inverse `1/price` notation, decimal commas, min quantities, USDT BEP20 hints, and no-currency flags.
- Unknown/ambiguous names and multi-quantity prices without per-unit markers are rejected. The parser does not connect to Discord and is not yet wired into the production workflow.
- Verification is pending the PR's CI run; no test-pass claim is made until the hosted suite reports green.

### Phase 2 — RBLXValue request safety
- Audited the repository's RBLXValue request paths in `scanner.py`: paginated `/v2/items` conditional requests, `/meta`, per-item `/items/{slug}` metadata enrichment, the periodic one-item credential health check, and the explicit manual key-check path. These pass through `request_with_retry` and `rblxvalue_guard.reserve_request`, which reserves budget before network I/O and counts retry attempts.
- The connection-test workflow was an exception: it made a direct credential-bearing request and had a `push` trigger. It now runs only on manual dispatch, shares the scanner's concurrency group, restores the state-branch API ledger, reserves from the same rolling 24-hour budget, and persists the ledger back to the private state branch.
- Added rolling per-workflow request accounting to the shared budget ledger. The hard cap remains 60 requests per rolling 24 hours.
- Reduced metadata polling from hourly to six-hourly. The full item catalogue retains a 12-hour TTL and per-page ETag/If-None-Match support.
- Changed the fresh-cache path so a scan-loop tick performs **no RBLXValue request**, including metadata, credential-health, or detail-enrichment calls. Health/metadata checks and enrichment are confined to an expired-cache refresh. A fresh cached snapshot remains usable while the key breaker is active; an expired snapshot with a blocked key fails closed without network access.
- Volume audit: at a 100-row page size, each full refresh can require one conditional request per catalogue page; normal refreshes are limited by the 12-hour cache, while metadata and health checks are each bounded to six hours and detail enrichment has its existing six-per-day cap. Every retry consumes budget. The current code and stored ledger do **not** prove exactly how the historical 1,045 requests were distributed; repeated page refreshes, retries, and requests from older/uncoordinated workflow versions are plausible contributors, not verified attribution.
- Weekly reporting reads metrics and does not call RBLXValue. Dashboard export reads persisted market history and does not call RBLXValue. The connection-test workflow was the only separately identified direct API caller outside the scanner and is now budgeted/serialized.
- Verification is pending PR CI. The live API key remains an owner/provider-side operational issue; code changes cannot reactivate a suspended credential.

### Remaining phases
- Market book, direct arbitrage, swap chains, inventory advice, private price history, Telegram `getUpdates` commands, privacy regression tests, workflow feature flags, and full user documentation are not yet complete.
- All new production integrations remain disabled by default. No trading signals are sent by the parser foundation.


### Phase 3 — Order book and private-state boundary
- Added `market_book.py`: 48-hour timestamp filtering, recency-weighted ask/bid medians, author confidence, spread/liquidity, outlier/flooding/single-author/stale-offer flags, direct arbitrage after two transfer fees and safety margin, 1:1 and N:1 swap-chain evaluation, inventory verdicts, and compact 14-day price history.
- Game-value chains require non-stale value data, medium-or-better confidence, and an explicit non-blocked stability label on the received item. Direct cash arbitrage does not depend on game values.
- Added tests for best ask/bid, overlapping quantities, fees, no-arbitrage acceptance sample, stale offers, N:1 chains, unsafe/stale values, inventory verdicts, and private history.
- The repository is confirmed **public**. A branch named `state` is therefore not private. Added `private_state.py` using standard-library key derivation/HMAC and the runner's OpenSSL executable (AES-256-CTR) to encrypt allowlisted private files with the existing `TELEGRAM_BOT_TOKEN` as key input. No new secret is introduced. The workflow decrypts only inside the runner and re-encrypts before artifacts/state persistence; the public-state validator rejects plaintext private files. OpenSSL CLI availability is an explicit system prerequisite.
- Added an additional opt-in `PRIVATE_STATE_ENABLED` flag. All market, Telegram, inventory, and private-state flags are false by default. No private offers, inventory, Telegram offset, or chat data have been written to the public state branch by this work.

### Phase 4 — Telegram interface foundation
- Added `telegram_market.py` with owner-chat authorization, `getUpdates` offset persistence, forwarded/pasted-post parsing, Arabic command replies, multi-item `/p`, `/buy`, `/chains`, inventory management commands, status, and queued notice when the previous scan was more than 12 minutes old.
- Telegram polling is wired at the start of each `scan_once` cycle (including the 30-second loop ticks), but only when `MARKET_BOOK_ENABLED`, `TELEGRAM_COMMANDS_ENABLED`, and `PRIVATE_STATE_ENABLED` are explicitly true. Inventory commands also require `INVENTORY_ENABLED`. Dry-run skips Telegram polling and sending.
- Telegram offsets, private offers, inventory, and private price history are allowlisted for encryption; they are not exported to the dashboard or public SQLite/CSV history.
- This phase does not execute trades. The bot only returns estimates and suggestions.

### Verification status at this checkpoint
- The first hosted PR run (`MM2 Scanner Tests`, run `#847`) failed with 402 tests executed, 10 failures and 5 errors. The logs identified: a cash-parser header-regex escaping bug, the new tests not using the repository's isolated test base, old key-health tests that expected API calls on fresh cache, and a redundant health probe shifting mocked pagination responses.
- Those causes have been corrected in the branch and additional market-book, Telegram, and encryption tests have been added. A subsequent full run (twice) plus Ruff is still required before any feature flag is enabled or the PR is merged.
- No new test-pass or Ruff-pass claim is made at this checkpoint.
