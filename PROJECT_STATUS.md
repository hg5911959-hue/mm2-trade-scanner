# Project Status and Resume Checkpoint

Last verified: 2026-11-27 20:02 UTC  
Repository: `hg5911959-hue/mm2-trade-scanner`  
Production branch: `main`  
Main baseline at checkpoint: PR #261 merged as `f7169b2d5bb40f489749e43f37a0d97f6f2e7b21` and PR #262 merged as `89eeec4e9abdeeca3b9d73d44c8eea8c93f16691`; the RBLXValue reference card, archive policy, and 5% cash-margin gate are on `main`.

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
- RBLXValue: 214/214 exact weapon identities and 214/214 numeric `value_source_b` Supreme entries; Stability is available for 212/214. Post-merge archive run `46055994741` fetched the latest snapshot at `2026-11-27T19:20:59Z` (dataset `last_updated=2026-11-27 12:02:34`). The manifest records optional Telegram reference display only, never cash valuation/eligibility/ranking/alert decisions. Daily history contains 1,712 rows; scheduled updates remain 11:30 and 23:30 UTC (12:30 and 00:30 Algeria). Missing Stability is displayed as unavailable, not inferred.
- Latest persisted ROBASE snapshot: `2026-11-27T19:43:40Z`; all 222 rows were checked, 200 have a Chroma.gg 7D price, and 189 meet the 3+ sales sample.

## Reliability and live verification

- Latest completed production scan: run `46059810691` on pre-merge SHA `45bfebf5` (before PR #262's 5% margin change). Scanner and state-persistence jobs succeeded across 14 cycles and 84 pagination pages / 1,680 API posts; every page batch reported `complete=True` and `seen_boundary=True`. It parsed 398 listings, evaluated 93, and qualified 2 candidates; one new cash alert was sent, with no scanner errors. Telegram accepted trade message `9504` for trade ID `10b97092c37d360d2b472154`, and the ID was confirmed persisted in `alerted.json`. First-seen-to-alert was 0.81s, feed-refresh-to-alert 25.65s, and Telegram confirmed the request in 540.5ms. Maintenance summary message `9505` was also accepted (429.8ms). The scan and state-persistence jobs succeeded; the chain's ten-minute wait/continue step remains active.
- Previous post-merge scan `46057491594` also completed successfully: 14 cycles, 92 pages / 1,840 API posts, 422 parsed listings, 91 evaluations, and two Telegram-accepted cash alerts (`9502`, `9503`); no scanner errors. Their feed-refresh-to-alert times were 55.64s and 26.22s; confirmed Telegram request times were 332ms and 346ms.
- In run `46059810691`, the RBLXValue snapshot was loaded on all 14 cycles: 214/214 Supreme `value_source_b`, 212/214 Stability, age 0.55–0.65 hours, `informational_only=true`; the scanner made zero RBLXValue API requests. Cash eligibility logs explicitly confirm ROBASE + BloxSwaps only.
- Latest persisted ROBASE snapshot after run `46059810691`: `2026-11-27T19:53:45Z`; 222 catalogue rows, 200 with Chroma.gg 7D price, 189 meeting the 3+ sales sample. State: 2,318 watched listings, 0 pending rechecks, 4,936 alerted IDs, 598 unmatched-name entries, and 2 quarantined Telegram delivery IDs with 2 context records.
- The production scan loaded RBLXValue reference data on all 14 cycles: 214/214 Supreme `value_source_b`, 212/214 Stability, snapshot age 0.08–0.19 hours, `informational_only=true`. The scanner made zero RBLXValue API requests; it used the archived snapshot. Logs explicitly confirm third-party valuation sources are disabled for cash eligibility and that cash decisions use ROBASE + BloxSwaps only.
- Previous completed scan: run `46047079955`; 14 cycles, complete pagination, no scanner errors, and no new alert met the selection rules during that run.
- Earlier live validation: run `46046592893`; 14 cycles and two alerts sent. Telegram API response times for those sends were approximately 388 ms and 452 ms. One already-alerted listing was correctly blocked by the duplicate guard.
- Follow-up scan `46054424527` restored the cleaned state branch, completed scanner and state persistence successfully, and processed 5 complete pages / 100 API posts. One trade alert was accepted 1.45 seconds after feed refresh; no new uncertain delivery or scanner error occurred. Its alert ID was confirmed persisted in `alerted.json`.
- Pre-merge chain run `46055635362` completed scanner and state persistence on old SHA `eef62970`. Run `46059810691` on pre-merge SHA `45bfebf5` has completed its scanner and state-persistence jobs; only its ten-minute wait/continue step remains. Post-merge live run `46060043022` on SHA `89eeec4e` is queued behind it and will be the first production scan using the 5% cash-margin gate.
- Watch recheck queue: 2,272 tracked listings; pending rechecks: 0. The watch TTL is 24 hours, capacity is 50,000, and the recheck batch is 50 listings every 4 cycles.
- Unmatched-name registry: 598 entries after removing five historical generic evaluator/rejection strings. Audit found 578 entries have not recurred since Nov 25; the 11 newest names checked against the live ROBASE/BloxSwaps catalog were unpriced in Chroma.gg 7D or had fewer than 3 sales, so they were correctly held rather than mispriced. Main filters legacy generic rejection reasons.
- Telegram quarantine: 2 unresolved delivery IDs and 2 matching context records. One context was reconstructed from run `45996910385` logs with its BloxSwaps source post ID and exact attempt timestamp; its listing/owner URLs were not present in logs and remain blank. Do not resend either message without manual confirmation.
- ROBASE refresh safety: PR #257 preserves the last complete snapshot after a failed refresh. Cached prices remain usable only while their individual verification timestamps are within the 20-minute freshness limit.
- Feed and quarantine hardening: PR #258 added candidate accounting, wildcard-policy coverage, and reconciliation of confirmed/ orphaned Telegram quarantine state.
- Public dashboard: PR #259 added a strict item-data whitelist; the dashboard publish workflow succeeded.
- PR #260 filters generic evaluator rejection messages out of the unmatched-item registry and normalizes BloxSwaps API source aliases when preserving uncertain Telegram context.
- The direct Supreme scraping workflow was removed; PR #205 is closed. PR #261 is merged: the unused direct-site collector and its tests are deleted from `main`. Production reference info uses RBLXValue API v2 only, and the refreshed archive metadata explicitly limits production use to optional Telegram reference display. Post-merge CI passed: 750 tests twice plus Ruff (`46055994728`, `46057274129`, `46057700312`, and PR #262 head run `46059632924`); ROBASE Pricing Tests passed (`46055994775`, `46059632847`); dashboard publish succeeded (`46055994780`, `46060043033`); RBLXValue archive refresh succeeded (`46055994741`). Post-merge main test run `46060043099` passed 750 tests twice and Ruff; follow-up status-checkpoint run `46060336683` also passed. Dashboard publish on merged SHA `89eeec4e` succeeded (`46060043033`). The first live scheduler run on merged SHA `89eeec4e` (`46060043022`) is queued behind pre-merge chain run `46059810691`; verify its cash margin is 5% before considering the change fully live.

## Merged changes

- PR #257 — preserve last complete ROBASE snapshot on failed refresh.
- PR #258 — wildcard handling, feed accounting, and Telegram quarantine reconciliation.
- PR #259 — public dashboard item-data whitelist.
- PR #260 — unmatched-name diagnostics and uncertain Telegram context hardening.
- PR #261 — compact Telegram reference card, RBLXValue-only Supreme/ Stability display, direct collector removal, archive policy correction, gross estimate labels, and legacy rejection filtering.
- PR #262 — restored the requested 5% minimum gross cash margin in both production workflow entry points and ScannerConfig defaults; retained the $2 gross-profit floor. Merged as `89eeec4e`.

## Open or experimental work

- PR #159 — draft hardening work; do not merge wholesale without a fresh review against current `main`.
- PR #203 — isolated RBLXValue value-profit shadow analysis; automatically runs after the daily archive in `dry_run` with `CASH_FILTER=false`, an isolated empty state, read-only repository permissions, no Telegram alerts, and zero RBLXValue API requests. Run `46056098581` completed with zero alerts and no production state writes. It can apply separate shadow-analysis thresholds but cannot affect production cash alerts or state. Its proposed `value`-only change is not the source for the reference card and must not replace `value_source_b` for Supreme Value.
- PR #100 — experimental Market Book work; not part of the production scanner.


## Remaining release checks

1. Manually confirm the two quarantined Telegram messages; the system intentionally will not auto-resend them.
2. Monitor the next scheduled RBLXValue archive update at 23:30 UTC / 00:30 Algeria time.
3. Continue routine checks of price freshness, feed pagination, Telegram latency, and state persistence on subsequent chained scans.

## Resume protocol for future conversations

1. Read this file first.
2. Fetch the latest `main` SHA and inspect open PRs; do not assume the branch or work from the previous chat is still current.
3. Inspect the latest scanner, tests, ROBASE pricing, watchdog, and dashboard workflow runs.
4. Inspect the `state` branch files: `robase_prices.json`, `watch_ads.json`, `pending_recheck.json`, `unmatched_names.json`, `telegram_uncertain.json`, and `telegram_uncertain_context.json`.
5. Preserve the production decisions above: Chroma.gg Price (7d) only, RBLXValue Supreme/ Stability informational only, and no automatic resend of uncertain Telegram deliveries.
6. Update this file whenever a material production decision, merged fix, unresolved blocker, or verified run changes.
