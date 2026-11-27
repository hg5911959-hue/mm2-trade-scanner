# Project Status and Resume Checkpoint

Last verified: 2026-11-27 19:33 UTC  
Repository: `hg5911959-hue/mm2-trade-scanner`  
Production branch: `main`  
Main baseline at checkpoint: PR #261 merged as `f7169b2d5bb40f489749e43f37a0d97f6f2e7b21`; the RBLXValue reference card and archive-policy changes are now live on `main`.

> This file is a durable handoff, not a substitute for live verification. At the start of every new work session, re-check the current `main` SHA, open pull requests, latest GitHub Actions runs, and the `state` branch before editing.

## Production configuration

- Source: BloxSwaps structured API, P2P offers only; no HTML feed scraping.
- Runtime: chained GitHub Actions scans, approximately every 10 minutes; each active scan runs up to 540 seconds and polls the feed every 30 seconds.
- Pagination: continue until the previously-seen boundary is reached; the page safety ceiling is 1,000. Each returned candidate is classified and accounted for.
- Wildcards: exclude `Ajouts` and `Offres`; allow `Surclassements` and `Déclassements`.
- Offer parsing: require both trade sides, exact positive integer quantities, tradable Knife/Gun item identities, and exact BloxSwaps-to-ROBASE mapping. Uncertain quantities or identities do not qualify.
- Cash source: **Chroma.gg Price (7d)** and **Sold (7d)** only. Minimum sample: 3 sales in 7 days. No 30-day price, StarPets, or All Marketplaces fallback in production cash valuation.
- Freshness: listing age at most 60 minutes; each ROBASE item verification at most 20 minutes old.
- Selection: estimated gross spread at least USD 2.00 and gross margin at least 0%; no manual-review or estimate-only rows.
- Delivery: no per-scan alert cap; Telegram pacing is 3.2 seconds between messages. Uncertain sends are quarantined and are never automatically resent.
- Supreme Value: read only from RBLXValue API `value_source_b`; Stability: read from the same API's `stability` field. Both are appended to Telegram in a compact, boxed **REFERENCE INFO ONLY** layout. They never affect cash valuation, filtering, ranking, or alert decisions. The production cash branch explicitly calls `evaluate_cash_listing_robase_only` (BloxSwaps + ROBASE only); the RBLXValue sidecar is passed only to message formatters. If the sidecar is stale or unavailable, cash alerts continue unchanged. No direct Supreme website extraction is used.

## Current data coverage

- ROBASE catalogue: 222/222 item pages present and freshly verified in the latest persisted snapshot.
- Chroma.gg 7-day price: 200/222 items have a price.
- Cash eligibility: 189/222 items also meet the 3+ sales sample threshold. The remaining 22 have no 7-day price; 11 have fewer than 3 sales. They are blocked rather than assigned guessed/fallback prices.
- RBLXValue: 214/214 exact weapon identities and 214/214 numeric `value_source_b` Supreme entries; Stability is available for 212/214. Post-merge archive run `46055994741` fetched the latest snapshot at `2026-11-27T19:20:59Z` (dataset `last_updated=2026-11-27 12:02:34`). The manifest records optional Telegram reference display only, never cash valuation/eligibility/ranking/alert decisions. Daily history contains 1,712 rows; scheduled updates remain 11:30 and 23:30 UTC (12:30 and 00:30 Algeria). Missing Stability is displayed as unavailable, not inferred.
- Latest persisted ROBASE snapshot: `2026-11-27T19:25:36Z`.

## Reliability and live verification

- Latest completed production scan: run `46055994878` on merge SHA `f7169b2d`. Scanner and state-persistence jobs succeeded across 14 cycles and 225 pagination pages / 4,500 API posts; every page batch reported `complete=True` and `seen_boundary=True`. It parsed 453 listings, evaluated 91, and sent one qualified trade alert; no scanner errors occurred. Telegram accepted trade message `9500` and scan-summary maintenance message `9501`. The trade alert request took 302 ms and was sent 33.57 seconds after feed refresh. Trade ID `531dc1f9eb507a4911228507` was confirmed persisted in `alerted.json`.
- The production scan loaded RBLXValue reference data on all 14 cycles: 214/214 Supreme `value_source_b`, 212/214 Stability, snapshot age 0.08–0.19 hours, `informational_only=true`. The scanner made zero RBLXValue API requests; it used the archived snapshot. Logs explicitly confirm third-party valuation sources are disabled for cash eligibility and that cash decisions use ROBASE + BloxSwaps only.
- Previous completed scan: run `46047079955`; 14 cycles, complete pagination, no scanner errors, and no new alert met the selection rules during that run.
- Earlier live validation: run `46046592893`; 14 cycles and two alerts sent. Telegram API response times for those sends were approximately 388 ms and 452 ms. One already-alerted listing was correctly blocked by the duplicate guard.
- Follow-up scan `46054424527` restored the cleaned state branch, completed scanner and state persistence successfully, and processed 5 complete pages / 100 API posts. One trade alert was accepted 1.45 seconds after feed refresh; no new uncertain delivery or scanner error occurred. Its alert ID was confirmed persisted in `alerted.json`.
- Pre-merge chain run `46055635362` completed scanner and state persistence on old SHA `eef62970`; its wait/continue job remains active for the next chained scan.
- Watch recheck queue: 2,163 tracked listings; pending rechecks: 0. The watch TTL is 24 hours, capacity is 50,000, and the recheck batch is 50 listings every 4 cycles.
- Unmatched-name registry: 598 entries after removing five historical generic evaluator/rejection strings. Main now filters legacy `no numeric Supreme value` and `value-weighted Demand is low` reasons; no such generic keys reappeared after the merge.
- Telegram quarantine: 2 unresolved delivery IDs and 2 matching context records. One context was reconstructed from run `45996910385` logs with its BloxSwaps source post ID and exact attempt timestamp; its listing/owner URLs were not present in logs and remain blank. Do not resend either message without manual confirmation.
- ROBASE refresh safety: PR #257 preserves the last complete snapshot after a failed refresh. Cached prices remain usable only while their individual verification timestamps are within the 20-minute freshness limit.
- Feed and quarantine hardening: PR #258 added candidate accounting, wildcard-policy coverage, and reconciliation of confirmed/ orphaned Telegram quarantine state.
- Public dashboard: PR #259 added a strict item-data whitelist; the dashboard publish workflow succeeded.
- PR #260 filters generic evaluator rejection messages out of the unmatched-item registry and normalizes BloxSwaps API source aliases when preserving uncertain Telegram context.
- The direct Supreme scraping workflow was removed; PR #205 is closed. PR #261 is merged: the unused direct-site collector and its tests are deleted from `main`. Production reference info uses RBLXValue API v2 only, and the refreshed archive metadata explicitly limits production use to optional Telegram reference display. Post-merge CI passed: 750 tests twice plus Ruff (`46055994728` and `46057274129`), ROBASE Pricing Tests (`46055994775`), dashboard publish (`46055994780`), and RBLXValue archive refresh (`46055994741`).

## Merged changes

- PR #257 — preserve last complete ROBASE snapshot on failed refresh.
- PR #258 — wildcard handling, feed accounting, and Telegram quarantine reconciliation.
- PR #259 — public dashboard item-data whitelist.
- PR #260 — unmatched-name diagnostics and uncertain Telegram context hardening.
- PR #261 — compact Telegram reference card, RBLXValue-only Supreme/ Stability display, direct collector removal, archive policy correction, gross estimate labels, and legacy rejection filtering.

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
