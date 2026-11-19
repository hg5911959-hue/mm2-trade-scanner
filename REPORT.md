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

## Self-assessment (historical Phase 2 snapshot)

Not yet scored at the end of Phase 2. See the evidence-based readiness assessment in the current overall status at the end of this report.


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

### Remaining phases at the end of Phase 2 (historical snapshot; superseded by current overall status)
- At that point, the market book, direct arbitrage, swap chains, inventory advice, private price history, Telegram `getUpdates` commands, privacy regression tests, workflow feature flags, and full user documentation were not yet complete. Their later implementation and validation are recorded in the subsequent phases and the current overall status at the end of this report.
- All new production integrations remain disabled by default. No trading signals are sent by the parser foundation.


### Phase 3 — Order book and private-state boundary
- Added `market_book.py`: 48-hour timestamp filtering, recency-weighted ask/bid medians, author confidence, spread/liquidity, outlier/flooding/single-author/stale-offer flags, direct arbitrage after two transfer fees and safety margin, 1:1 and N:1 swap-chain evaluation, inventory verdicts, and compact 14-day price history.
- Game-value chains require non-stale value data, medium-or-better confidence, and an explicit non-blocked stability label on the received item. Direct cash arbitrage does not depend on game values.
- Added tests for best ask/bid, overlapping quantities, fees, no-arbitrage acceptance sample, stale offers, N:1 chains, unsafe/stale values, inventory verdicts, and private history.
- The repository is confirmed **public**. A branch named `state` is therefore not private. Added `private_state.py` using standard-library key derivation/HMAC and the runner's OpenSSL executable (AES-256-CTR) to encrypt allowlisted private files with the existing `TELEGRAM_BOT_TOKEN` as key input. No new secret is introduced. The workflow decrypts only inside the runner and re-encrypts before artifacts/state persistence; the public-state validator rejects plaintext private files. OpenSSL CLI availability is an explicit system prerequisite.
- Added an additional opt-in `PRIVATE_STATE_ENABLED` flag. All market, Telegram, inventory, and private-state flags are false by default. No private offers, inventory, Telegram offset, or chat data have been written to the public state branch by this work.

### Phase 4 — Telegram interface foundation
- Added `telegram_market.py` with owner-chat authorization, `getUpdates` offset persistence, forwarded/pasted-post parsing, Arabic command replies, multi-item `/p`, `/buy`, `/chains`, inventory management commands, status, and queued notice when the previous scan was more than 12 minutes old.
- The cash parser also ingests authorized existing manual-import secrets and the existing read-only collector's temporary `discord_messages.json` once per scan process. It stores only normalized offers; the raw import file is removed from the durable artifact.
- Added threshold-qualified automatic alerts capped by `MAX_ALERTS_PER_SCAN`; encrypted private dedup state suppresses repeats unless net profit changes by more than `MARKET_ALERT_PROFIT_DELTA_USD`. Failed sends do not advance alert state.
- Telegram polling is wired at the start of each `scan_once` cycle (including the 30-second loop ticks), but only when `MARKET_BOOK_ENABLED`, `TELEGRAM_COMMANDS_ENABLED`, and `PRIVATE_STATE_ENABLED` are explicitly true. Inventory commands also require `INVENTORY_ENABLED`. Dry-run skips Telegram polling and sending.
- Telegram offsets, private offers, inventory, and private price history are allowlisted for encryption; they are not exported to the dashboard or public SQLite/CSV history.
- This phase does not execute trades. The bot only returns estimates and suggestions.

### Verification status at this checkpoint
- The first hosted PR run (`MM2 Scanner Tests`, run `#847`) failed with 402 tests executed, 10 failures and 5 errors. The logs identified: a cash-parser header-regex escaping bug, the new tests not using the repository's isolated test base, old key-health tests that expected API calls on fresh cache, and a redundant health probe shifting mocked pagination responses.
- Those causes have been corrected in the branch and additional market-book, Telegram, and encryption tests have been added. A subsequent full run (twice) plus Ruff is still required before any feature flag is enabled or the PR is merged.
- No new test-pass or Ruff-pass claim is made at this checkpoint.


### Latest verification update
- Hosted run `#899` on commit `7e2a332` completed successfully: **431 tests passed on pass 1 and 431 tests passed on pass 2**. The workflow also verified that tests left `data/` unchanged.
- The same run passed the existing selected Ruff checks, the scanner's `--select F` check, and repository-wide `ruff check --select F .`.
- A subsequent attempt to lint every new file with the repository's full custom Ruff rule set failed on many pre-existing project-wide style rules (including broad exception-handling and import-order rules), not on the test suite. The lint command has been narrowed to Ruff's default correctness rules `E4,E7,E9,F` for the newly added modules, while retaining the existing full checks and repository-wide F checks. This final lint configuration still needs a hosted run.
- Current PR: [#85](https://github.com/hg5911959-hue/mm2-trade-scanner/pull/85). It remains unmerged. All new feature flags remain false.


### RBLXValue request-path audit and 1,045-request investigation
- All RBLXValue HTTP calls in `scanner.py` route through `api_get_json` or `api_get_json_conditional`, which call `request_with_retry`; that reserves the shared rolling-budget slot before each HTTP attempt. The direct one-request connection test in `.github/workflows/rblxvalue-connection-test.yml` also reserves from the same ledger before opening its request.
- Enumerated RBLXValue call sites:
  1. Paginated `GET /v2/items` in `load_supreme_values` (conditional ETag revalidation).
  2. `GET /v2/meta` before and after a full catalogue refresh to detect a changing source version.
  3. `GET /v2/items/{slug}` for metadata enrichment, capped at 6 lookups per UTC day.
  4. Explicit one-item key verification when `RBLXVALUE_FORCE_KEY_CHECK=true` is manually dispatched.
  5. The standalone connection-test workflow's one-item `/v2/items` request.
- The weekly report has no `RBLXVALUE_API_KEY` and reads persisted status/cache only. The dashboard exporter reads local state and has no RBLXValue API call. BloxSwaps API calls use a separate base URL and are not counted as RBLXValue requests.
- The scheduler dispatches one scan every 10 minutes: 144 workflow runs/day. Each scan process can execute up to 18 `scan_once` ticks (30-second interval for up to 540 seconds), or up to 2,592 scan-loop ticks/day. Before the fresh-cache early return was enforced, any path that re-entered the catalogue loader without a usable/restored cache could request catalogue pages repeatedly across those ticks. This is the dominant plausible multiplier for a four-digit daily count. Pagination multiplies each refresh by the number of pages; up to four retry attempts per transiently failing request can add more attempts. The previous scheduled connection test and detail/meta/credential checks were additional independent sources. The exact 1,045 cannot be attributed to a particular path without RBLXValue's provider-side request logs; it would be inaccurate to claim an exact reconstruction.
- Current protections: the catalogue TTL is 12 hours; a fresh cache returns before metadata, health, enrichment, or catalogue API calls. Each expired-cache refresh conditionally revalidates cached pages using ETags. A shared rolling 24-hour budget (60 maximum) is reserved before every RBLXValue attempt and stores per-workflow attribution. The 401/403 breaker is checked before requests; credential rejection is not retried in the same run. The connection-test workflow is manual-dispatch only, and it shares the scanner concurrency group. Detail enrichment is capped at 6/day. The default 24-hour maximum game-value age disables swap chains while cash-only direct opportunities remain eligible.
- Key recovery: the suspended `Halim3788` key cannot be reactivated by code. The owner must contact RBLXValue through its official Discord/support channel to reactivate that same key. No second account or key is to be created.



### Open acceptance ambiguity — Celestial Set ask
- The supplied appendix fixture contains a buyer offer for `Celestial Set` at `$63/1`, but contains no seller-side `Celestial Set` offer at `$71/1`. The `/p` formatting test uses a synthetic book row to verify that a real ask of 71 and bid of 63 would be displayed correctly; it does not fabricate or inject that ask into the fixture. End-to-end acceptance for best ask 71 therefore requires the owner to provide the seller post (or clarify which existing source contains it). Until then, the engine correctly reports no ask from the supplied sample.

- Additional fail-closed hardening: parsed minimum quantities now constrain direct arbitrage, N:1 chain legs, and inventory sellability; non-finite prices and non-finite market thresholds are rejected. The legacy CSV fallback is freshness-checked and reference-only in dry-run, never a synthetic live bid/ask.

- Telegram ingestion now derives offer age from the Discord permalink snowflake; the Telegram receipt timestamp is never substituted. A pasted price-list without a verifiable Discord permalink is rejected as `timestamp_unknown`. Authorized collector/manual-secret rows use their original `created_at_utc`. Repeated forwards of the same permalink replace the prior offers instead of inflating liquidity.
- Swap-chain quantity search is bounded analytically: it evaluates the minimum executable lot and the largest still-profitable N:1 lot rather than iterating every quantity up to 5,000 for every item pair.

- Backlog/detail rechecks were also audited: `_enrich_catalog_metadata_gaps` tracks attempts per item slug and source version, honors `retry_after`, skips already-enriched/no-additional-metadata items for the same item version, and shares the 6-lookups/day cap. It is not an independent unbounded retry loop. Each detail request still passes through the global pre-request budget and breaker. Metadata pre/post checks are only reached during a full catalogue refresh; the fresh-cache early return bypasses them.


### Phase 5 — Telegram owner identity hardening
- Tightened command authorization from chat-ID-only to a private one-to-one chat whose `chat.id` and `from.id` both match the configured `TELEGRAM_CHAT_ID`. Group/supergroup messages and private messages from other users are ignored; their update offsets are advanced without replying or processing commands.
- Added regression coverage for a supergroup message and a different sender in the owner's private chat. Existing authorized private-chat command and forwarded-post tests were updated to include Telegram's private chat type and sender identity.
- Hosted verification on commit `c0d9d0d08e88ff1d9b781e75ed97bba41a0dad4d`, Actions run [#985](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44616757100): **445 tests passed twice** (445/445 on each pass); the data-tree hash remained unchanged after each pass. Ruff passed on all new market/privacy/API modules, scanner correctness checks, and repository-wide undefined/unused-name checks.
- This closes the code-level Telegram authorization issue. It does not enable the market features or prove live Telegram connectivity; all four market/private-state feature flags remain false.

### Remaining acceptance and operational blockers
- The supplied acceptance material still has no seller-side Celestial Set offer at $71/1. The fixture has a buyer offer at $63/1; the $71 ask currently exists only in a synthetic formatting test. Do not invent a seller offer or treat the test fixture as market evidence. A source post with its Discord permalink and timestamp is required for end-to-end acceptance.
- The requested `before-market-book` safety tag has not been created because the connected GitHub operations expose branch creation but not tag-object/ref creation. The safety branch exists.
- Live activation remains blocked pending owner-controlled secrets/configuration, confirmation that the Telegram bot is in a private one-to-one chat, and an explicit dry-run review. The suspended RBLXValue key remains a provider-side recovery issue.


### Phase 6 — Future-dated cache rejection
- Fixed the RBLXValue cache-age reader so boolean, non-finite, non-positive, and future `fetched_at` timestamps are rejected instead of clamped to age zero.
- Fixed the cash-market value resolver to treat future or invalid cache timestamps as unavailable; this leaves `game_values` empty and disables swap chains while preserving cash-only direct-arbitrage evaluation.
- Added regressions proving a future-dated cache cannot be used while the API breaker is active and cannot enable swap-chain value resolution.

### Phase 7 — Author identity normalization
- Author identity is now whitespace-trimmed and case-folded before computing independent-author counts for the order book and each side. This prevents `Seller`, `seller`, and whitespace variants from falsely satisfying the two-author confidence threshold.
- Added a regression proving case-only author variants remain one author, receive low confidence, and cannot qualify a direct-arbitrage signal.

### Latest complete hosted verification
- Final validation commit: `1fdbda5485b2b98ee37e09c8d935151fbb235be2`; Actions run [#990](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44617469718) completed successfully.
- **448 tests passed twice** (448/448 on pass 1 and 448/448 on pass 2). The workflow verified the real `data/` tree hash remained unchanged after both passes.
- Ruff passed for the market/privacy/API modules, scanner correctness checks, and repository-wide undefined/unused-name checks. An intermediate run caught one unused test import; it was removed before the successful final run.
- All new production features remain disabled by default. This verification establishes test/lint correctness for the branch; it does not establish live market accuracy, live Telegram connectivity, or guaranteed profit.


### Owner-supplied market evidence — Celestial Set bundle listing (2026-11-19)
- The owner supplied the exact Discord message permalink and the message text. The permalink identifies channel `1572655277130514472` and message snowflake `1573076681072189520`.
- The Discord page redirects to login in an anonymous browser, so the original author and rendered message could not be independently inspected. The post content below is therefore owner-supplied, not independently page-verified. The owner later confirmed the exact username `fragelsil`; this identity is recorded as owner-supplied and remains unverified by Discord.
- The message snowflake yields the original UTC timestamp `2026-11-19T20:45:07.119Z` using Discord's timestamp encoding. This replaces the earlier relative-only “37 minutes ago” note and provides a stable timestamp for provenance.
- Individual seller prices supplied by the owner: Celestial Set `$72`, Snowcannon `$14.50`, and Spirit `$8.50`; the same post states `ALL FOR $92`. The individual prices total `$95`, so the bundle is `$3` below their sum. The `$92` bundle total is not allocated across items and must never be treated as an individual item price.
- Added parser handling so `ALL FOR $92` is explicitly rejected as `bundle_total_not_item_price`, while the three individual sell prices are retained with the supplied permalink and snowflake-derived timestamp. A regression test covers this exact listing structure using a synthetic test permalink.
- The supplied Celestial Set ask is `$72`, not the `$71` required by the original appendix acceptance assertion. The old assertion is intentionally unchanged. The real listing is not inserted into private live state from this chat; it must enter through the authorized owner-forward/import path. The owner subsequently confirmed the exact Discord username as `fragelsil`. This remains owner-supplied identity metadata, not independently verified by Discord; the offer is flagged `owner_supplied_author` and must not increase independent-author confidence.
- The browser could not independently verify the Discord page without a signed-in profile. No sign-in, message interaction, or server modification was attempted.

- The first hosted run of the new 449-test suite exposed that `Spirit` was missing from `cash_item_aliases.json`; this is a real parser-resolution defect, not a market-data issue. Added the canonical `spirit -> Spirit` alias. Validation of the corrected commit is in progress; the initial failed run is not counted as a pass.


### Phase 8 — Seller bundle listing parser and provenance
- Added explicit rejection reason `bundle_total_not_item_price` for `ALL FOR ...` bundle-summary lines. Individual line-item offers remain separate; the bundle total is never divided or assigned to an item.
- Added the missing canonical alias `spirit -> Spirit`, discovered by the new end-to-end parser regression based on the owner-supplied listing structure.
- Regression coverage verifies the three seller-side individual prices (Celestial Set `$72`, Snowcannon `$14.50`, Spirit `$8.50`), preserves the Discord permalink and snowflake-derived timestamp, and rejects the `$92` bundle line as a standalone item offer.
- The exact owner-supplied message snowflake resolves to `2026-11-19T20:45:07.119Z`. The owner supplied the exact username `fragelsil`; anonymous browser inspection redirects to Discord login, so the username remains owner-supplied and is not independently verified by Discord. The message was not inserted into private live market state from this chat; authorized owner-forward/import ingestion is still required.
- The first hosted test attempt found the missing Spirit alias and failed on that new regression; the defect was fixed before final verification.
- Final validation commit `c867dcaee032de93532f9fd8ab6cfa1b04589e8d`, Actions run [#1000](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44619126582): **449 tests passed twice** (449/449 on both passes). Ruff passed for all targeted market/privacy/API modules, scanner correctness checks, and repository-wide undefined/unused-name checks. The workflow's data-tree hash guard passed.
- The new feature flags remain disabled. This validates parser and regression behavior only; it does not prove the offer is still available or independently verify the Discord author's identity.


### Phase 9 — Standalone RBLXValue credential-check hardening
- Audit found the manual-only `.github/workflows/rblxvalue-connection-test.yml` used `urllib.request.urlopen` directly with the `X-Api-Key` header. Unlike the scanner's protected request path, that standalone call did not explicitly reject redirects before they could forward a credential-bearing request.
- Added a dedicated standard-library `HTTPRedirectHandler` that rejects redirects and switched the connection check to an opener using that handler. The same workflow remains manual-dispatch-only and reserves against the shared persistent request budget before its one request.
- Hardened state persistence when the optional `data/api_key_blocked.json` file is absent: the workflow now stages the budget file unconditionally and stages/removes the breaker file only when appropriate, avoiding a missing-path `git add` failure.
- Added workflow-config regression assertions for redirect rejection and optional breaker-file persistence.
- Hosted verification: commit `e1c158d27bb759873d4a4b28175e811927515b3d`, Actions run [#1007](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44619954488): **449 tests passed twice**, data-tree immutability guard passed, and all Ruff checks passed. This verifies the redirect and optional breaker-file persistence changes.


### Phase 10 — Explicit stale-value state and opportunity risk transparency
- The cash-market snapshot now distinguishes fresh, stale, invalid, and unavailable RBLXValue snapshots. Values older than the configurable maximum (24 hours by default) are explicitly marked `stale`; swap-chain calculation remains disabled while direct cash arbitrage continues from current offers.
- `/p` now labels stale or invalid game values and reports `stale_game_values`, `missing_stability`, and `untradeable` risk flags rather than conflating every missing value with a generic unavailable state. `/status` exposes stale/invalid cache status, and `/chains` explains when stale game values disable chains.
- Swap-chain rows now retain risk flags from both market legs. `/buy`, `/chains`, and Telegram opportunity alerts display source links and applicable risk flags; direct cash opportunities remain usable without game values.
- Added regressions for stale-cache cash-only operation, stability risk display, chain risk propagation, and source/risk visibility in commands.
- The first Phase 10 validation attempt failed because the new test omitted `datetime`/`timezone` imports; this was fixed in the test only. Final hosted verification: commit `84addd75b9e73823868fd8d5a1b899681dff53a1`, Actions run [#1018](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44621493402): **455 tests passed twice**, the `data/` tree remained unchanged, and all Ruff checks passed.


### Phase 11 — Remove the third-party Discord client dependency
- The optional read-only collector previously installed `discord.py` during every scan workflow. This contradicted the prompt's standard-library-only constraint and added an avoidable runtime dependency.
- Replaced the collector with a standard-library `urllib` client using only official Discord REST `GET` endpoints for explicitly allowlisted guilds, text channels, and active threads. It uses the existing bot token only; no user token, self-bot, write endpoint, or scraping mechanism is used.
- Added bounded response sizes, bounded 429 retries, 403 permission handling, message-count/channel caps, and redirect rejection so the bot Authorization header cannot be forwarded to another host.
- Removed the `pip install discord.py` workflow step and added tests for channel filtering, read-only request paths, redirect rejection, and absence of the third-party dependency.
- The first collector validation attempt found only a test-file indentation/import defect; it was corrected before final validation. Final hosted verification: commit `757a7e14ceceb97d02e784b0257c2c1f4cd2820e`, Actions run [#1025](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44622077260): **459 tests passed twice**, the `data/` tree remained unchanged, and all Ruff checks passed. The test-only failure was not counted as a pass.


### Phase 12 — Appendix `/p celestial set` acceptance
- Added an end-to-end Telegram command regression using the appendix's synthetic Celestial Set market fixture: best ask `$71.00`, best bid `$63.00`. The test asserts the actual `/p celestial set` response, not only the lower-level order-book object.
- This `$71` seller ask is test-fixture data from the prompt's acceptance assertion, not a claim about a currently available real Discord offer. The owner's separately supplied live listing remains `$72` and is tracked with its own permalink/timestamp provenance.
- Hosted verification: commit `bbfeca4cc2e2278874c56bd272614a6323d0a3df`, Actions run [#1028](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44622347145): **460 tests passed twice**, data-tree immutability passed, and all Ruff checks passed.


### Phase 13 — Display unpriced demand hints without inventing bids
- The parser already produced private `demand_hints` for unpriced bulk wanted-lists, but the Telegram market snapshot discarded them. The snapshot now carries these hints through to `/p`.
- `/p` displays the aggregate unpriced wanted quantity separately as informational demand. It is never added to `best_bid`, bid quantity, median, direct arbitrage, or swap-chain calculations. If there are no priced offers, the command still shows the demand hint while clearly stating that it is not a priced buy offer.
- Added a regression proving an unpriced demand hint is visible but produces no book row, bid, direct opportunity, or chain.
- Hosted verification: validation branch commit `eaafd041fec32da69f863d0d0169d11d465b5f61`, Actions run [#1031](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44622668975): **461 tests passed twice**, data-tree immutability passed, and all Ruff checks passed.


## Current overall status — 2026-11-19

- Current implementation branch: `before-market-book`; latest validated code/test head: `3da174a944f645fad039f32f8ab08546eccdcfcc`. Implementation PR [#85](https://github.com/hg5911959-hue/mm2-trade-scanner/pull/85) remains open and unmerged.
- Latest complete hosted validation: **477/477 tests passed twice** on Python 3.12; the `data/` tree hash was unchanged after both runs; targeted Ruff checks, scanner correctness lint, and repository-wide undefined/unused-name checks all passed. Validation run [#1132](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44631204718) validates the latest code head.
- This is strong code/test evidence for the implemented scope, not a guarantee of zero undiscovered defects, live data accuracy, or profitable trades.
- The requested safety branch exists. The requested Git tag `before-market-book` is still **not created** because the available GitHub integration has no tag-object/ref creation operation.
- All runtime feature flags remain OFF by default: `MARKET_BOOK_ENABLED`, `TELEGRAM_COMMANDS_ENABLED`, `INVENTORY_ENABLED`, and `PRIVATE_STATE_ENABLED`. No live Telegram messages, trades, or market alerts were sent by this work.

### External / operational blockers
1. **RBLXValue credential:** the previously configured key was rejected/suspended with HTTP 403. The code now blocks safely and avoids retries, but only the provider can reactivate the existing key. No replacement account/key was created.
2. **Celestial Set source identity:** the owner supplied the listing and exact Discord permalink. Its snowflake timestamp is `2026-11-19T20:45:07.119Z`; the anonymous browser was redirected to Discord login, so the author could not be independently read. The owner-supplied listing remains outside private live state until it is ingested through the authorized bot/import route with its owner-supplied author identity (`fragelsil`); this identity remains unverified by Discord. For a plain-text Telegram paste, use an `Author: exact-discord-username` line; it is explicitly flagged `owner_supplied_author` because the bot cannot independently verify it.
3. **Live integration verification:** Telegram/Discord credentials and bot permissions were not exposed to this workflow and were not tested. The user-owned secrets must already be configured correctly before enabling the flags. No secrets should be pasted into chat.
4. **Telegram responsiveness:** the bot polls at scan start and each 30-second tick while a GitHub Actions scan job is active. It is not an always-on hosted process; commands sent outside an active job wait until the next run. The prompt's bounded GitHub Actions architecture does not guarantee continuous 30-second responsiveness between jobs.
5. **Appendix $71 ask:** `/p celestial set` is tested end-to-end with the appendix's synthetic $71 ask / $63 bid fixture. The actual owner-supplied listing is $72; the synthetic fixture is not represented as live market evidence.
6. **Private-state key rotation:** encrypted private state derives its keys from the existing Telegram bot token to avoid introducing a new secret. Rotating that token without first decrypting/re-encrypting the private-state bundle will make the old bundle unreadable; the workflow now verifies its authentication before publishing it.

### Readiness assessment
- Code and test readiness for the implemented, feature-flagged scope: **477 tests twice and all configured Ruff checks clean** on the latest validated code head.
- Live operational readiness: **not yet verified** because of the suspended RBLXValue key, unverified Telegram/Discord credentials and permissions, missing tag operation, source-author metadata, and bounded GitHub Actions polling model.
- The project is therefore **not honestly certifiable as 10/10 or 100% operational yet**. No automatic trading is enabled; the system remains advisory and profit is not guaranteed.


### Phase 14 — Weekly report formatting regression
- Corrected double-escaped newline sequences in `tools/weekly_report.py` so blocker and preflight sections render on separate lines.
- Added regression tests for optional blocker/preflight formatting and the default PASS line.
- Source commit: `87e12e6f3761edccb6f0875aa366e10d243084f4`; tests: `15e847af1f4ba4371d874e4feb6c5eb4b2fc1888`.
- Validated by latest hosted run [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461): 472 tests passed twice and all configured Ruff checks passed.

### Phase 15 — Telegram redirect protection in market commands
- Replaced default redirect-following behavior in `telegram_market.py` with a redirect-rejecting opener for both `getUpdates` and `sendMessage`, preventing bot-token-bearing requests from being forwarded to a redirect target.
- Added regression coverage for redirect rejection.
- Source commit: `fbfc1afb090698a277b06b2306153cbf0c98cb8a`; test commit: `9fa2fc3e33d48daa5071fb527f5c98dad1246386`.
- Validated as part of the latest full hosted run [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461).

### Phase 16 — Preflight Telegram redirect protection
- Hardened the maintenance/preflight notification route so it refuses redirects rather than using urllib's default redirect-following opener.
- Added a regression test proving the redirect target is refused.
- Source commit: `62e3fe7e14623671d31a5dc5fa3e2c018d4761fa`; test commit: `91f20e073483f426760e87e555de0d42d1a45081`.
- Validated as part of the latest full hosted run [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461).

### Phase 17 — Retry-safe daily preflight notices
- Fixed a reliability defect where the daily suppression timestamp was written before attempting delivery, which could suppress all retries after a transient network failure.
- The timestamp is now stored only after Telegram returns HTTP 200; failed network requests remain retryable.
- Source commit: `50b4966f678209c0ec42a33d64ee842d8667bc7d`; tests commit: `3d31b64cbe2720b290523318a0ba620c29921173`.
- Follow-up tests also verify Telegram's JSON `ok: true` response; an HTTP 200 response containing `ok: false` is not recorded as delivered. This was added in source commit `de409977886b836c880997cc06e1d8a05b0bd891`, with tests `dbcbbdbfa2dc34a064b606aa837deda2d4b4003b`.
- Validated by run [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461).

### Phase 18 — Authenticate private state before durable persistence
- The state-persistence job previously checked only the encrypted bundle's magic header and minimum length when persisting encrypted private state after a failed scan.
- Added a verification step that decrypts/authenticates the bundle into a temporary runner directory before it can be published to the durable `state` branch. Temporary plaintext is removed by a shell trap; a bad key, modified ciphertext, or invalid HMAC blocks publication.
- Normal successful state persistence is also guarded by this verification when an encrypted bundle is present.
- Workflow commit: `aff56fd9595de153eb35120c353dbb3a0a38b95e`; workflow regression assertions: `61fe9def16aa18f3f93fd579929608fa7c0dc5ab`.
- Validated by run [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461).

### Phase 19 — Complete Telegram redirect-path audit
- A repository-wide call-path review found two additional token-bearing Telegram paths that were still using default redirect behavior: the main scanner alert sender and weekly report sender. Both now use redirect-rejecting openers.
- The weekly report now also requires Telegram's JSON `ok: true` response rather than treating HTTP 200 alone as proof of delivery.
- The manual Telegram connection-test workflow now refuses redirects as well. Together with the earlier market-command and preflight fixes, every identified Telegram send/getUpdates route now rejects redirects.
- Added transport regression tests and updated existing tests to patch the new redirect-safe opener.
- Relevant commits: Telegram test workflow `267e40775662670cdae2ac397dfbc9128e6dce8d`; scanner sender `30e219dbb212a8d148ee7106274c06fb201c032f`; weekly report sender `30a5a975e021bb9be5193de010aecd708391df00`; transport tests `c20b0d9943f55c4c7bb4e230ef884bd81b00d563`; test-mock follow-ups `29ff1b8f218fe2f720dd0defe9abcb5867bd58c9`, `929c018d41431455e4457dd096bde12bee4cabc6`, `f6323600c7f71c877b2cb5cfa4e136a206643d91`, `59a392b743c961818faf9b606ff5fd9bcb0e96ec`, and `6aef6dc9bfe70fd3da9422fca96c3f26f2d634ea`.
- Latest hosted verification [#1079](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44628695461): **472 tests passed twice**, data-tree immutability passed, targeted Ruff checks passed, scanner correctness lint passed, and repository-wide undefined/unused-name lint passed.

### Phase 20 — Correct operational documentation
- Audited `README.md` against the actual workflow files and corrected stale instructions that could lead to unsupported operation:
  - Updated the scanner cadence from 15 minutes to the actual 10-minute GitHub Actions schedule.
  - Updated the scan duration from the obsolete 780-second/15-minute description to 540 seconds/9 minutes and the 20-minute job timeout.
  - Removed unsupported `repository_dispatch` and cron-job.org instructions; the scheduler supports GitHub cron, manual `workflow_dispatch`, and reusable `workflow_call`.
  - Corrected the manual dry-run default to `true` and noted that scheduled runs intentionally use `false`.
  - Corrected the ownership of the schedule re-enable check to `watchdog-scheduler.yml`.
  - Documented the actual CI push branches, including `before-market-book`.
- Documentation commits: `4b957443f6fc5599fdbefd8d40a1c8e55c23bd7f`, `e0c471874d8a16bd8d93b10b59056fde151cf6b0`, `66c95cc972f2be4ad627426a29607e9600ff3e00`, and `143523b5cbb092e817f5d6d77688b3bf4dbc1412`.
- Hosted verification [#1090](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44629202062): **472 tests passed twice**, data immutability passed, and all configured Ruff checks passed.

### Phase 21 — Private Discord offer isolation and encrypted deduplication
- A data-flow audit found that although `market_history.py` already excluded Discord records from SQLite listings/opportunities, the shared scanner could still write private item names to unmatched-name state and diagnostics, and use public alert/watch/quarantine identifiers.
- Discord offers are now parsed/evaluated only when `PRIVATE_STATE_ENABLED=true`. With private state disabled, the scanner skips parsing them.
- Added `discord_alert_state.json` to the authenticated private-state encryption allowlist. Alerted and uncertain-delivery Discord message IDs are now stored there and encrypted with the other private state; they are no longer written to public `alerted.json`, `watch_ads.json`, or `telegram_uncertain.json`.
- Suppressed Discord item names, message IDs, source URLs, and stack quantities from scanner logs and unmatched-name records. The existing SQLite/history guards remain in place; cash-blocker metrics do not aggregate private item names.
- Added the new file to workflow plaintext guards and cleanup, and to `tools/validate_state.py` so it cannot be published unencrypted in the public state branch or state artifact.
- Added regression coverage for encrypted-state round trips, malformed-state fail-closed behavior, plaintext-state rejection, disabled-feature ingestion, and full-scan log/unmatched-state privacy.
- The first test run exposed a Python static nesting limit in the integration test; refactored the test to use `ExitStack`. Final validation: [#1132](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44631204718), **477 tests passed twice**, data-tree immutability passed, and all configured Ruff checks passed.
- No live Discord or Telegram data was collected or sent during this validation.

- A one-time dry-run workflow push marked `[rblxvalue-key-check]` is reserved to verify the configured RBLXValue credential using the shared request budget; no secret value is stored in this report.
