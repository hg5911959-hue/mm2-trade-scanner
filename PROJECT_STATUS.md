# MM2 Trade Scanner — Current Project Status

**Last verified:** 2026-11-28 19:30 UTC  
**Production branch:** `main`  
**Current main commit:** `4ce51c5e9c188b3b682f50ac41b7324554d63659`

This file is the current hand-off checkpoint. It records verified production behavior and the next safe work items. Do not treat an open draft PR as production code.

## Production invariants — do not change casually

- **Feed:** structured BloxSwaps API v2; do not silently fall back to HTML scraping.
- **Cash price source:** ROBASE snapshot, selecting **Chroma.gg Price (7d)** and **completed sales (7d)** only. No 30-day price, StarPets, or all-marketplaces fallback in production.
- **Minimum sample:** at least 3 completed Chroma.gg sales in the 7-day window. Unverified, stale, missing, and low-sample rows remain blocked.
- **Trade decision:** exact BloxSwaps/ROBASE weapon identity; cash filter enabled; minimum estimated profit USD 2; minimum margin 5%. Supreme Value and Stability are informational only and never cash filters.
- **Supreme/ Stability reference:** sourced through the RBLXValue official API archive. Telegram display uses Supreme `value_source_b` and RBLXValue Stability. The production cash scan does not call the RBLXValue API.
- **Delivery:** individual Telegram alerts, persisted delivery guard before send, uncertain deliveries quarantined, definite failures queued for retry. Never automatically resend an uncertain delivery.
- **Pagination:** preserve the 1,000-page overall safety ceiling and 25-page pending-cursor fallback. Do not lower page limits without evidence that cursor/boundary handling remains complete.
- **State:** preserve the separate `state` branch and validated persistence workflow. Do not reset dedupe, seen, pending, watch, or quarantine state during refactors.

## Changes merged into main

- **PR #289:** stricter Ruff checks and mypy for the typed configuration modules.
- **PR #290:** unmatched-name retention policy (14 days), preserving active and invalid-timestamp records.
- **PR #291:** prune the unmatched registry at scan start, including scans with no new unmatched names. Registry read/write failures are non-blocking for live scanning.
- **PR #292:** dashboard now displays cash-eligible catalogue items and every blocked item with its reason. It distinguishes missing Chroma data, zero 7-day sales, and low sample. Also corrected README price/fee wording, pagination documentation, and centrally parsed configuration documentation. Added a Node syntax check for dashboard JavaScript to PR CI.
- **PR #293:** suppress only the transient `ROBASE price not freshly verified` message from identity diagnostics in cash mode. Genuine missing/low-sample cash-price gaps remain visible.
- **PR #294:** extracted feed diagnostics and listing deduplication/history persistence from `scan_once`, preserving private-history fail-open and public-history fail-closed behavior.
- **PR #295:** extracted `mark_alerted`, `mark_pending`, and `mark_seen_ad` into typed, tested helpers in `tools/scan_state_actions.py`. The new module is included in strict Ruff and mypy checks.
- Old PR #288 was closed as superseded by the current-main version in PR #294.

## Latest verified production data

### ROBASE cash catalogue

- Snapshot fetched: **2026-11-28 19:20:59 UTC**.
- **222/222** catalogue items; snapshot complete; **0** item-page failures.
- **202** rows have a verified Chroma.gg 7-day price.
- **190** are cash-eligible (price present and at least 3 completed 7-day sales).
- **32** are blocked: **12** low-sample, **6** with zero 7-day sales, **14** without Chroma.gg 7-day data.
- Dashboard deployment for the current dashboard change completed successfully.

### RBLXValue reference archive

- Latest archive fetched: **2026-11-28 19:00:58 UTC**.
- 214/214 exact weapon identities; 214 Supreme values; 212 Stability values; 156 blended estimates.
- Latest collection used **12/60** permitted requests. The production scan itself used **0** RBLXValue API requests.
- Telegram market-info snapshot was about 0.3 hours old during the latest completed live scan; informational only.

### Durable scanner state

- Unmatched-name registry: **538** entries after 60 expired diagnostic entries were pruned.
- Pending rechecks: **0**.
- Telegram uncertain-delivery quarantine: **2** unresolved IDs. One has a BloxSwaps listing and owner URL; the other has a source ad ID but no stored listing/owner URL. Both require manual verification; do not replay automatically.
- Daily metrics after the latest persisted scan: **1,666** scans, **234** alerts sent, **0** scan failures.

## Latest completed live scans

**Run `46180242825`**, commit `534195bb8c146a836bde337dda637d6c8646256c`:

- 14/14 scan cycles succeeded in 420 seconds.
- Every cycle reached the seen boundary and logged `complete=True`; feed depth ranged from 5 to 10 pages.
- One cash trade alert was accepted by Telegram: estimated profit **$6.72**, confirmed in about 0.44 seconds.
- No delivery failure or new uncertain delivery; the two historical quarantine IDs remained quarantined.
- No freshness-only ROBASE message was added to the unmatched identity registry after PR #293.

**Previous run `46179885423`**, commit `f60774b18bdd81df62dd7f3d96acc2efd5ce7b7e`:

- 14/14 scan cycles succeeded in 420 seconds; every cycle logged `complete=True` and reached the seen boundary.
- Two cash trade alerts were accepted by Telegram: estimated profits **$2.23** and **$5.47**, each confirmed in about 0.46 seconds.
- A separate maintenance message was also accepted.

Both runs had zero scan-cycle failures and zero pending rechecks. The latest run on current `main` commit `4ce51c5e9c188b3b682f50ac41b7324554d63659` was in progress at the time of this checkpoint; it includes PR #295's extracted scan-state helpers.

## Code structure and validation

- `scanner.py`: 6,560 lines at this checkpoint.
- `scan_once`: **433 lines**, down from 492 in the immediately preceding baseline; only the delivery coordinator remains nested. Keep its non-idempotent Telegram path intact unless an extraction has explicit regression tests.
- Feed reporting, listing persistence, and scan-state mutations now live in focused helpers.
- CI runs the full isolated test suite twice, checks that tests do not mutate `data/`, runs repository-wide Ruff checks, stricter Ruff checks on typed utility modules, mypy on those modules, and the ROBASE pricing tests.
- The main test and ROBASE workflows passed on current commit `4ce51c5e9c188b3b682f50ac41b7324554d63659`. Its live scan was still running when this file was written.

## Remaining work — continue in this order

1. Verify the current-main CI and live scan on `4ce51c5`; inspect pagination completeness, alert delivery, and state persistence.
2. Continue the `scan_once` refactor only where behavior can be preserved and regression-tested. The remaining nested delivery coordinator is intentionally sensitive because it owns non-idempotent Telegram delivery.
3. Review the two quarantined deliveries manually. Do not auto-resend or delete their state.
4. Monitor the new cash-price freshness suppression and confirm it stops adding transient freshness failures to the unmatched registry while preserving real cash-price gaps.
5. Keep the 32 blocked ROBASE catalogue items visible with their reasons; do not loosen the 3-sale minimum to improve the coverage percentage.
6. Revisit unmatched alias coverage only with exact, verified BloxSwaps/ROBASE identities. Never guess mappings.
7. Consider alert feedback/threshold analysis only after enough reliable historical observations exist; do not change the USD 2 / 5% gates based on a small sample.

## Other open PRs — not production changes

- **PR #203:** isolated RBLXValue blended-value shadow research. It does not alter live cash alerts; do not merge it into production valuation without a separate decision.
- **PR #159:** broad reliability/private-state migration draft. Requires separate security, persistence, and migration review before any merge.
- **PR #100:** separate cash-market/order-book and Discord ingestion work. Experimental and not part of the current BloxSwaps cash scanner path.
- **PR #297:** adaptive scan-loop finish reserve. Draft and not production yet; intended to use more of the 540-second polling deadline during fast cycles while expanding the reserve after slow cycles.

**Do not publish a project score yet.** The requested final evaluation should wait until the current-main live run is verified and the remaining manual quarantine and release checks are resolved.
