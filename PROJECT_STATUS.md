# Project Status and Resume Checkpoint

Last verified: 2026-11-27 21:49 UTC  
Repository: `hg5911959-hue/mm2-trade-scanner`  
Production branch: `main`  
Main baseline at checkpoint: PR #261 merged as `f7169b2d5bb40f489749e43f37a0d97f6f2e7b21`, PR #262 merged as `89eeec4e9abdeeca3b9d73d44c8eea8c93f16691`, PR #263 merged as `9bf3394196bc9ab3a75e6e9a22a1476419e03b0e`, and PR #264 merged as `41505ac92417e9630a003ba2a57c7a5c481b40ea`, and PR #265 merged as `957f31c866e5ac49b7fafc43d688c1b0c59ce439`, and PR #266 merged as `232dc8ce3c5eef76a362ab80c0f2bf041db9d50f`, and PR #267 merged as `6aa508f0cffb04c661040c0263b846352e04601e`; the RBLXValue reference card, archive policy, 5% cash-margin gate, and single-location Supreme/Stability display are on `main`.

> This file is a durable handoff, not a substitute for live verification. At the start of every new work session, re-check the current `main` SHA, open pull requests, latest GitHub Actions runs, and the `state` branch before editing.

## Production configuration

- Source: BloxSwaps structured API, P2P offers only; no HTML feed scraping.
- Runtime: chained GitHub Actions scans, approximately every 10 minutes; each active scan runs up to 540 seconds and polls the feed every 30 seconds.
- Pagination: continue until the previously-seen boundary is reached; the page safety ceiling is 1,000. Each returned candidate is classified and accounted for.
- Wildcards: exclude `Ajouts` and `Offres`; allow `Surclassements` and `Déclassements`.
- Offer parsing: require both trade sides, exact positive integer quantities, tradable Knife/Gun item identities, and exact BloxSwaps-to-ROBASE mapping. Uncertain quantities or identities do not qualify.
- Cash source: **Chroma.gg Price (7d)** and **Sold (7d)** only. Minimum sample: 3 sales in 7 days. No 30-day price, StarPets, or All Marketplaces fallback in production cash valuation.
- Freshness: listing age at most 60 minutes; each ROBASE item verification at most 20 minutes old.
- Selection: estimated gross spread at least USD 2.00 and gross margin at least 5%; no manual-review or estimate-only rows.
- Delivery: no per-scan alert cap; Telegram pacing is 3.2 seconds between messages. Uncertain sends are quarantined and are never automatically resent.
- Supreme Value: read only from RBLXValue API `value_source_b`; Stability: read from the same API's `stability` field. Both are appended to Telegram in a compact, boxed **REFERENCE INFO ONLY** layout. They never affect cash valuation, filtering, ranking, or alert decisions. The production cash branch explicitly calls `evaluate_cash_listing_robase_only` (BloxSwaps + ROBASE only); the RBLXValue sidecar is passed only to message formatters. If the sidecar is stale or unavailable, cash alerts continue unchanged. No direct Supreme website extraction is used.

## Current data coverage

- ROBASE catalogue: 222/222 item pages present and freshly verified in the latest persisted snapshot.
- Chroma.gg 7-day price: 200/222 items have a price.
- Cash eligibility: 189/222 items also meet the 3+ sales sample threshold. The remaining 22 have no 7-day price; 11 have fewer than 3 sales. They are blocked rather than assigned guessed/fallback prices.
- RBLXValue: 214/214 exact weapon identities and 214/214 numeric `value_source_b` Supreme entries; Stability is available for 212/214 (missing for Seer and Xmas, shown as unavailable rather than inferred). Latest archive snapshot `2026-11-27T21:26:48Z` (dataset `last_updated=2026-11-27 12:02:34`), 2,140 history rows. The manifest limits these fields to optional Telegram reference display, never cash valuation/eligibility/ranking/alert decisions. The authorization metadata is based on the user-provided written statement; its external authenticity has not been independently verified. Scheduled updates remain 11:30 and 23:30 UTC (12:30 and 00:30 Algeria). The rolling request budget is now persisted on the archive branch: 12 requests in the last 24 hours against the hard limit of 60; it records timestamps and workflow labels only, not the API key or its fingerprint.
- Latest persisted ROBASE snapshot after post-PR-264 run `46067478167`: `2026-11-27T21:07:01Z`; all 222 rows were checked, 200 have a Chroma.gg 7D price, and 189 meet the 3+ sales sample.

## Reliability and live verification

- Latest chained production scan: run `46071799804` on post-PR-266 main code. Scanner and state-persistence jobs succeeded across 14/14 cycles and 91 pagination batches / 1,820 API posts; every batch reported `complete=True` and `seen_boundary=True`. Candidate accounting reconciled exactly: 615 candidate cards = 49 Ajouts + 127 Offres + 104 single-sided posts + 335 parser-eligible listings. All 335 parsed listings received one terminal outcome. Two cash alerts were accepted by Telegram; no scanner errors occurred. One newly first-seen alert was sent 0.57s after scanner first-seen; feed-refresh-to-alert times were 21.34–27.70s. State validation and state-branch persistence succeeded.
- Previous post-PR-266 live scan `46070296286` also completed successfully: 14 cycles, 70 complete pages / 1,400 API posts, 548 candidates reconciled into 39 Ajouts + 85 Offres + 77 single-sided + 347 parsed listings, and two Telegram-accepted cash alerts. First-seen-to-alert was 0.48–2.91s; no scanner errors.
- Additional chained production scan `46070110960` completed successfully on the same scanner code: 14 cycles, 61 complete pages / 1,220 API posts, and 677 candidates reconciled into 44 Ajouts + 164 Offres + 67 single-sided + 402 parsed listings. All 402 parsed listings had one terminal outcome; two trade alerts were reported and Telegram accepted three messages total including the summary. New-listing first-seen-to-alert was 0.70–2.05s; no scanner errors.
- In chained run `46071799804`, the RBLXValue snapshot loaded on all 14 cycles: 214/214 Supreme `value_source_b`, 212/214 Stability, age 0.23–0.34 hours, `informational_only=true`. The cash scanner used archived reference data only for Telegram display; cash eligibility remained ROBASE + BloxSwaps. No direct Supreme-site scraping is present in production.
- Latest persisted ROBASE snapshot after run `46071799804`: `2026-11-27T21:40:46Z`; 222 catalogue rows, 200 with Chroma.gg 7D price, 189 meeting the 3+ sales sample. State: 2,773 watched listings, 0 pending rechecks, 598 unmatched-name entries, 4,968 alerted IDs, and 2 quarantined Telegram delivery IDs with 2 context records.
- The production scan uses RBLXValue only for optional Telegram reference display; Supreme `value_source_b` and Stability are not loaded into cash eligibility. The 5% margin gate and ROBASE + BloxSwaps-only cash path are confirmed in live logs.
- Previous completed scan: run `46047079955`; 14 cycles, complete pagination, no scanner errors, and no new alert met the selection rules during that run.
- Earlier live validation: run `46046592893`; 14 cycles and two alerts sent. Telegram API response times for those sends were approximately 388 ms and 452 ms. One already-alerted listing was correctly blocked by the duplicate guard.
- Follow-up scan `46054424527` restored the cleaned state branch, completed scanner and state persistence successfully, and processed 5 complete pages / 100 API posts. One trade alert was accepted 1.45 seconds after feed refresh; no new uncertain delivery or scanner error occurred. Its alert ID was confirmed persisted in `alerted.json`.
- Pre-merge chain run `46059810691` completed scanner and state persistence on SHA `45bfebf5`. Post-merge runs `46060043022` and `46061640498` both completed live scans and state persistence with the 5% margin gate active. Earlier pre-PR-264 live run `46062726916` on SHA `9bf33941` remains a historical comparison sample; post-PR-264 production validation is now complete in run `46067478167`.
- Watch recheck queue: 2,773 tracked listings; pending rechecks: 0. The watch TTL is 24 hours, capacity is 50,000, and the recheck batch is 50 listings every 4 cycles.
- Unmatched-name registry: 598 entries after removing five historical generic evaluator/rejection strings. Audit found 578 entries have not recurred since Nov 25; recent unmatched names were checked against the live catalog and correspond to unpriced variants, items without a ROBASE row, or items below the 3-sales sample. No current exact-priced catalog mapping miss was identified. Main filters legacy generic rejection reasons.
- Telegram quarantine: 2 unresolved delivery IDs and 2 matching context records. One context was reconstructed from run `45996910385` logs with its BloxSwaps source post ID and exact attempt timestamp; its listing/owner URLs were not present in logs and remain blank. Do not resend either message without manual confirmation.
- ROBASE refresh safety: PR #257 preserves the last complete snapshot after a failed refresh. Cached prices remain usable only while their individual verification timestamps are within the 20-minute freshness limit.
- Feed and quarantine hardening: PR #258 added candidate accounting, wildcard-policy coverage, and reconciliation of confirmed/ orphaned Telegram quarantine state.
- Public dashboard: PR #259 added a strict item-data whitelist; the dashboard publish workflow succeeded.
- PR #260 filters generic evaluator rejection messages out of the unmatched-item registry and normalizes BloxSwaps API source aliases when preserving uncertain Telegram context.
- The direct Supreme scraping workflow was removed; PR #205 is closed. PR #261 is merged: the unused direct-site collector and its tests are deleted from `main`. Production reference info uses RBLXValue API v2 only, and the refreshed archive metadata explicitly limits production use to optional Telegram reference display. Post-merge CI passed: 750 tests twice plus Ruff (`46055994728`, `46057274129`, `46057700312`, and PR #262 head run `46059632924`); ROBASE Pricing Tests passed (`46055994775`, `46059632847`); dashboard publish succeeded (`46055994780`, `46060043033`); RBLXValue archive refresh succeeded (`46055994741`). Post-merge main test runs `46060043099`, `46060336683`, `46060449672`, `46060856792`, and `46062726930` passed (latest run: 751 tests twice plus Ruff). PR #263 head tests `46062401339` also passed 751 tests twice plus Ruff; ROBASE Pricing Tests passed (`46062401745`, `46062726960`). Dashboard publish succeeded on merged SHAs `89eeec4e` (`46060043033`) and `9bf33941` (`46062726949`). Live scan `46060043022` on `89eeec4e` and follow-up scan `46061640498` on `de86a884` both completed with the 5% margin gate active and state persistence successful; each sent three cash alerts. The latest fully inspected pre-PR-264 live run `46062726916` on `9bf33941` completed successfully: 14/14 cycles, 81 pages / 1,620 API posts, every page batch `complete=True` and `seen_boundary=True`, 801 candidate cards reconciled exactly into 93 Ajouts + 113 Offres + 124 single-sided posts + 471 parsed listings, and all 471 parsed listings received one terminal outcome. Five qualified trade alerts were accepted; first-seen-to-alert latency was 0.57–1.59s and feed-refresh-to-alert was 4.72–85.69s. The scanner and state-persistence jobs succeeded. The latest durable scan timestamp observed on `state` is `2026-11-27T20:55:40Z`; the ROBASE snapshot is `2026-11-27T20:49:06Z` (222/222 catalogue rows, 200 Chroma 7D prices, 189 meeting the 3+ sales threshold). RBLXValue daily archive refresh `46061212591` succeeded with 214 Supreme values, 212 Stability values, and 1,926 history rows.

## Merged changes

- PR #257 — preserve last complete ROBASE snapshot on failed refresh.
- PR #258 — wildcard handling, feed accounting, and Telegram quarantine reconciliation.
- PR #259 — public dashboard item-data whitelist.
- PR #260 — unmatched-name diagnostics and uncertain Telegram context hardening.
- PR #261 — compact Telegram reference card, RBLXValue-only Supreme/ Stability display, direct collector removal, archive policy correction, gross estimate labels, and legacy rejection filtering.
- PR #262 — restored the requested 5% minimum gross cash margin in both production workflow entry points and ScannerConfig defaults; retained the $2 gross-profit floor. Merged as `89eeec4e`.
- PR #263 — corrected cash Telegram wording to state that Chroma.gg 7D prices are seller proceeds after marketplace fees, with additional costs/slippage excluded; added a regression test.
- PR #264 — removed duplicate inline Supreme and Stability copies from Telegram item lines; the RBLXValue-sourced values now appear only once in the boxed `REFERENCE INFO ONLY` card. Regression test passed with 751 tests twice plus Ruff; ROBASE Pricing Tests also passed. Merged as `41505ac9` and verified in post-merge live run `46067478167`.
- PR #265 — test-only regression coverage for `format_opportunity_digest`, ensuring legacy inline Supreme/Stability details are suppressed and the reference card appears once. 752 tests passed twice plus Ruff on PR head and merged main. Merged as `957f31c8`.
- PR #266 — enforced the rolling 60-request / 24-hour RBLXValue budget in the daily archive collector, counting retries before network I/O. Budget is restored and validated before collection, uploaded even on collection failure, then persisted in a separate no-secret job; snapshot publication waits for successful budget persistence. PR-head validation: 760 tests twice plus Ruff. Merged as `232dc8ce`; first real post-merge archive run `46070296288` succeeded in all 3 jobs and used 12/60 requests.
- PR #267 — changed the isolated Supreme value-profit shadow workflow from automatic archive-triggered execution to manual-only. It remains dry-run, read-only, without Telegram secrets or RBLXValue API access. This removes unnecessary BloxSwaps feed polling from the production schedule. PR-head validation: 763 tests twice plus Ruff. Merged as `6aa508f0`. Main push test run `46071814554` also passed 763 tests twice plus Ruff.

## Open or experimental work

- PR #159 — draft hardening work; do not merge wholesale without a fresh review against current `main`.
- PR #203 — isolated RBLXValue value-profit shadow analysis; now manual-only after PR #267. The previous automatic run `46070405347` used `dry_run` and `CASH_FILTER=false`, had read-only repository permissions, sent no Telegram alerts, made zero RBLXValue API requests, and made no production state writes; it did poll the BloxSwaps feed (45 pages / 900 posts in its first cycle), so automatic archive-triggered execution was removed to avoid competing with the production scanner. Manual dispatch remains available. Its shadow thresholds cannot affect production cash alerts. PR #203 is not safe to merge as-is: it reintroduces automatic archive-triggered scans and replaces Supreme `value_source_b` with blended `value`, which would break the requested reference source.
- PR #100 — experimental Market Book work; not part of the production scanner.


## Remaining release checks

1. Manually confirm the two quarantined Telegram messages; the system intentionally will not auto-resend them.
2. Continue monitoring the next ten-minute chained scan; run `46071799804` completed scanner and state persistence successfully.
3. Post-PR-267 main tests `46071814554` passed 763 tests twice plus Ruff; documentation checkpoint runs `46071955100` and `46072137985` also passed 763 tests twice plus Ruff.
4. Monitor the next scheduled RBLXValue archive update at 23:30 UTC / 00:30 Algeria time and confirm the rolling budget advances from the current 12/60.
5. Continue routine checks of price freshness, feed pagination, Telegram latency, state persistence, and the dashboard/watchdog chain.

## Resume protocol for future conversations

1. Read this file first.
2. Fetch the latest `main` SHA and inspect open PRs; do not assume the branch or work from the previous chat is still current.
3. Inspect the latest scanner, tests, ROBASE pricing, watchdog, and dashboard workflow runs.
4. Inspect the `state` branch files: `robase_prices.json`, `watch_ads.json`, `pending_recheck.json`, `unmatched_names.json`, `telegram_uncertain.json`, and `telegram_uncertain_context.json`.
5. Preserve the production decisions above: Chroma.gg Price (7d) only, RBLXValue Supreme/ Stability informational only, and no automatic resend of uncertain Telegram deliveries.
6. Update this file whenever a material production decision, merged fix, unresolved blocker, or verified run changes.
