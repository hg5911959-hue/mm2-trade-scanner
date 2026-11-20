# MM2 Trade Scanner — Reference Architecture Review

**Status:** Phase 0 complete; Phase 1 alert-gate hardening validated; PR #85 reconciliation in progress  
**Baseline:** `main` at `1f6bd2332524de684a0fce2286256a71fa5b2229`  
**Review date:** 2026-11-20

## Product boundary — do not change

The product remains an automated scanner for public BloxSwaps MM2 P2P listings. Its primary job is to discover actionable trading opportunities, qualify them conservatively, and deliver concise Telegram alerts. It is not being replaced by a manual trade calculator, an in-game overlay, or a generic price tracker.

Existing authoritative behaviour must remain intact unless a separately tested change explicitly proves it is safer and more correct:
- BloxSwaps listing discovery and item/quantity parsing.
- Supreme-based game-value qualification and existing fail-closed rules.
- Optional cash-market analysis, disabled by default.
- Telegram delivery, deduplication, retry/uncertain-delivery handling, and state persistence.
- GitHub Actions scheduling and watchdog operations.
- Existing security, privacy, and data-tree immutability guarantees.

## Reference projects reviewed

### MM2 TradeLens

Repository: https://github.com/ShadowPanther123/mm2-tradelens  
License: MIT (as published in its repository).

Useful patterns to adapt, not copy wholesale:
- A canonical item/value model at data boundaries.
- Provider adapters that normalize external data into one internal schema.
- Explicit source provenance, retrieval/update timestamps, adapter versions, and validation states.
- Confidence and source-disagreement as visible risk signals rather than silently averaging incompatible readings.
- Frozen calculation snapshots so historical decisions remain reproducible after source values or algorithms change.
- Signed/versioned snapshots and fail-closed adoption of untrusted updates.

Important boundary: TradeLens documents that Supreme Values integration requires an authorized feed/written permission. This project must not copy protected datasets or adopt an undocumented endpoint merely because another project contains an adapter template. Existing integrations require their own terms/permission review before any expansion.

### MM2 Trade Charts

Repository: https://github.com/yanaa-12/mm2-trading

Useful patterns:
- Timestamped per-item history, not just a latest-value file.
- Explicit metadata describing snapshot time and item coverage.
- Detecting unexpectedly small or malformed snapshots before publishing them.
- A clear separation between collection, storage, and read-only visualization.

Do not copy its scraper or deployment assumptions without checking source terms, rate limits, and this repository's no-paid-services / standard-library constraints.

### MM2 Trade Overlay

Repository: https://github.com/DepravitiesFinest/MM2TradeOverlay

Useful patterns:
- Treat same-name items as ambiguous until origin/type/image evidence resolves them.
- Preserve per-card identity and quantity instead of merging by display name alone.
- Make uncertain recognition visible and require correction/review rather than silently selecting a candidate.
- Keep fixture-based regression cases for real-world lookalikes and parsing edge cases.

Its Windows OCR/UI stack is not part of this scanner's product scope.

## Baseline observations

- The default branch is `main`; the repository is Python and the active project constraint is Python 3.12 with the standard library only.
- `scanner.py` remains a large orchestration and domain module. It contains feed parsing, source/value resolution, Telegram transport, persistent state, opportunity qualification, and the scan loop. This is the clearest maintainability pressure point, but splitting it is high-risk because tests and workflows import or depend on its existing interfaces.
- The codebase already has extracted modules under `tools/` and `market_intelligence/`; future extraction should extend these boundaries instead of introducing a second competing architecture.
- The test workflow runs the complete unittest suite twice, checks that `data/` remains unchanged, pins Ruff, and runs repository-wide undefined/unused-name checks. These protections are acceptance criteria, not cleanup targets.
- Cash mode is documented as off by default. No work in this review enables it or changes live alert behaviour.

## Branch / PR safety finding

PR #85 (`before-market-book`) is open and unmerged. The current comparison of `main` to `before-market-book` reports a diverged history (388 commits ahead and 2 behind) and a broad 40+ file change set. It must not be blindly merged, rebased, or treated as a clean incremental patch. Its features should be reviewed and reconciled against current `main` in small, independently tested slices. No commit from that PR is assumed to be present in `main` unless verified there.

## Target architecture (incremental, compatibility-first)

1. **Ingestion adapters:** BloxSwaps and any explicitly authorized supplemental feeds return normalized raw records plus source metadata. Adapters do not decide whether an opportunity is profitable.
2. **Canonical listing model:** preserve stable listing identity, item identity, quantity certainty, timestamps, author/source provenance, and original URL. Ambiguity is represented explicitly.
3. **Valuation layer:** resolve values through isolated source adapters/cache validators. Never substitute a guessed value for a missing reading; preserve per-source readings and age.
4. **Market intelligence:** build books, liquidity/spread indicators, confidence, risk flags, and candidate swap chains from qualified observations only. A demand hint is not a bid.
5. **Opportunity policy:** one auditable policy layer applies fees, safety margins, freshness, stability, quantity, confidence, and minimum-profit thresholds before alert eligibility.
6. **Delivery and state:** keep Telegram transport separate from opportunity calculation; make delivery retry-safe and state writes atomic, validated, and secret-safe.
7. **Observability:** expose counts, freshness, parser coverage, rejection reasons, source health, and latency without leaking credentials, private listings, or sensitive item names.
8. **Historical reproducibility:** persist versioned calculation inputs/results where permitted, so old decisions are not silently recomputed using today's prices.

## Required invariants for every implementation phase

- No feature-flag defaults are changed without explicit tests and a separate safety review.
- No unverified/stale/ambiguous price, item, quantity, author, or listing may silently become a qualified opportunity.
- Never treat a platform's own Win/Loss label or a seller's asking price as authoritative item value.
- Preserve source URLs, IDs, and timestamps for traceability.
- Do not expose secrets or private market data in logs, public artifacts, or repository history.
- No Discord user token, self-bot, or unauthorized collection. Any Discord ingestion must use an authorized bot with read-only permissions or explicit owner-provided exports.
- No paid services, new secrets, or dependencies outside the project's approved constraints.
- Do not weaken or delete existing assertions to make a change pass.

## Execution plan

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Baseline and reference review | This document; branch starts from current `main`; PR #85 preserved |
| 1 | PR #85 reconciliation | File-by-file overlap map; identify safe, missing, conflicting, and stale changes; no blind merge |
| 2 | Contract tests | Explicit schemas/contracts for listing identity, quantity certainty, source provenance, freshness, and alert eligibility |
| 3 | Low-risk modularization | Extract one cohesive boundary at a time with compatibility wrappers and regression tests |
| 4 | Market intelligence hardening | Test order-book confidence, independent authors, minimum quantities, fees, stale offers, and chain risk |
| 5 | Reliability and security | Validate persistence, retry/redirect behaviour, state recovery, rate limits, redaction, and workflow permissions |
| 6 | Performance and observability | Bounded work, deterministic metrics, coverage/latency reporting, and no data pollution |
| 7 | Final acceptance | Full suite twice, data immutability, Ruff checks, workflow checks, and documented unresolved external blockers |

## Current phase result

### Phase 0 — baseline and reference review

Complete. The baseline review is documentation-only and does not change feature flags, secrets, datasets, or runtime workflows.

### Phase 1 — alert qualification hardening

Implemented on `improvement/reference-architecture-review`:
- `scanner.qualifies()` now fails closed for non-dictionary results, missing/malformed numeric metrics, booleans, NaN/infinite values, and invalid thresholds.
- Negative minimum-difference and minimum-margin thresholds are rejected rather than silently weakening the alert gate.
- Regression tests cover malformed metrics, non-finite profit/margin values, non-finite/negative thresholds, and a valid finite positive trade.
- No feature flag, source policy, alert threshold default, or production workflow was enabled or changed.

Validation:
- Latest branch head: `43adb6dc1b05698f4bfa54df77f127512dcb0dcf`.

- GitHub Actions run [44814950719](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44814950719) completed successfully on the latest branch head.
- The full suite ran **394 tests twice**; both runs passed. Data immutability checks and every configured Ruff/lint step passed.
- PR #99 remains open as Draft and has not been merged.

### Phase 1 — PR #85 reconciliation (selective migration underway)

Current PR metadata:
- PR #85 remains open and unmerged; head `98f922cb7c51eb204dbbb681033b6c920dc5dfb7`, base `main`.
- Current comparison: 388 commits ahead, 2 behind, 42 changed files; merge base `d26ddf4205aff952754a825d741b0bc590260238`, while current main is `1f6bd2332524de684a0fce2286256a71fa5b2229`.
- PR #85's latest associated test run [44643900064](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44643900064) succeeded: 504 tests ran twice, data immutability passed, Ruff checks passed, and the history secret scan passed. This is evidence of test success, not a substitute for code review.

Preliminary file-level findings:
- New, currently absent from main: `cash_offer_parser.py`, `cash_item_aliases.json`, `market_book.py`, `private_state.py`, `telegram_market.py`, and their associated fixtures/tests. These are candidates for isolated review and selective migration, not automatic acceptance.
- Existing files modified by PR #85 include `scanner.py`, `discord_collector.py`, `rblxvalue_guard.py`, `tools/preflight.py`, `tools/validate_state.py`, `tools/weekly_report.py`, tests, and GitHub Actions workflows. These require three-way review against current main.
- The PR changes workflow triggers and permissions, including a push trigger on `before-market-book`, inherited secrets, and write permissions for state persistence. The workflow changes are a security-sensitive blocker and must not be imported as a bundle.
- The Discord collector supports an explicit `DISCORD_CHANNEL_IDS` allowlist, but its history is deliberately bounded to at most six pages of 100 messages per channel and reports truncation. It must not be described as a complete-history exporter.
- The reviewed market-book core had concrete false-positive paths: owner-supplied author labels could inflate independence confidence, flagged outliers/flooding/sell-before-drop offers could remain actionable, and missing author identities could appear in candidate legs.
- These paths were hardened in a separate branch and PR #100; the original PR #85 branch remains unchanged.
- PR #100 contains only `market_book.py` and `tests/test_market_book.py`; it does not wire the module into the live scanner or change workflows, secrets, defaults, or price data.
- Workflow changes in PR #85 remain a security-sensitive blocker. No PR #85 workflow changes or commits have been merged into `main`.

### Selective market-book hardening — PR #100

Implemented on `fix/market-book-trust-and-risk-gates`, based directly on current `main`:
- Ignore owner-supplied and unidentified author labels; use stable `author_id` values rather than mutable display names for independent-author confidence and actionable depth.
- Exclude `price_far_from_median`, `market_flooding`, and `sell_before_drop` offers from actionable price discovery and opportunity legs.
- Require verified, distinct stable author IDs on both sides of direct arbitrage and swap-chain candidates.
- Extend the existing read-only Discord collector to preserve validated author snowflakes and label their provenance as `discord_api`.
- Add the isolated cash-offer parser and explicit alias catalogue; carry validated author IDs into parsed offers and demand hints, and reject malformed alias schemas or normalized alias collisions.
- Add a pure normalized-Discord ingestion adapter that validates source snowflakes, matching permalinks and timestamps, deduplicates messages, preserves channel provenance, and keeps demand hints separate from bids. An end-to-end regression test verifies that independent, verified offers can form a qualified opportunity.
- Require an explicit `DISCORD_CHANNEL_IDS` allowlist in the existing collector and refuse server-wide collection if it is absent; this avoids accidental collection of every readable channel in an allowed guild.
- Filter previously retained messages against the current guild/channel allowlists before merging and persisting, so changing the allowlist cannot carry old out-of-scope channel content forward.
- Normalize malformed optional risk-flag containers defensively.
- Add regression tests for all the above cases.

Validation:
- PR #100 GitHub Actions run [44819738814](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44819738814) succeeded on the latest code head.
- **442 tests passed twice**, data immutability passed, and all configured Ruff/lint checks passed.
- PR #100 is open as Draft; no code has been merged into `main`.

### Private-state encryption hardening — PR #101

A separate security slice was ported from PR #85 onto a fresh branch based on current `main`:
- Retain the allowlisted AES-256-CTR plus HMAC-SHA256 encrypt-then-MAC format, with authentication verified before decryption.
- Check plaintext file sizes with `stat()` before reading them during encryption.
- Bound encrypted bundle size before reading it during decryption, accounting for base64 expansion and bounded JSON metadata.
- Add sparse-file tests to prove oversized inputs are rejected without allocating or restoring the payload.

Validation:
- GitHub Actions run [44818273130](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/44818273130) passed **396 tests twice**, data immutability, and all configured Ruff/lint checks.
- PR #101 is open as Draft; no workflows or secrets were changed.

Remaining security review before enabling private state:
- The current key derivation uses the Telegram bot token as its secret input. Token rotation therefore requires a documented re-encryption/key-recovery plan before production activation.
- PR #85 workflow changes still require an independent least-privilege review. Do not import its push trigger, inherited secrets, write permissions, or hard-coded artifact recovery workflow as a bundle.

Next acceptance work:
1. Complete the remaining 42-file PR #85 overlap/security review; keep its workflow changes excluded until separately redesigned and reviewed.
2. Review parser identity, quantity certainty, provenance, and duplicate-message handling before wiring the market book into the scanner.
3. Review and harden the normalized Discord import adapter's edge cases and data-retention boundary; ensure only collector-provenanced author IDs reach actionable offers.
4. Design the live-scanner integration boundary separately, keeping cash mode disabled by default and excluding private state from public artifacts.
5. Run the full suite twice, data immutability, Ruff, and relevant workflow checks after each isolated integration slice.
