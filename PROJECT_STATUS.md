# Project Status and Resume Checkpoint

Last verified: 2026-11-27 19:14 UTC  
Repository: `hg5911959-hue/mm2-trade-scanner`  
Production branch: `main`  
Main baseline at checkpoint: PR #260 merged; latest post-PR-260 production scan completed successfully and the next chained scan is running on that baseline.

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
- Supreme Value: read only from RBLXValue API `value_source_b` (the Supreme Values source indexed by RBLXValue); Stability: read from the same API's `stability` field. Both are appended to Telegram in a compact, boxed **REFERENCE INFO ONLY** layout. They never affect cash valuation, filtering, ranking, or alert decisions. No direct Supreme website extraction is used.

## Current data coverage

- ROBASE catalogue: 222/222 item pages present and freshly verified in the latest persisted snapshot.
- Chroma.gg 7-day price: 200/222 items have a price.
- Cash eligibility: 189/222 items also meet the 3+ sales sample threshold. The remaining 22 have no 7-day price; 11 have fewer than 3 sales. They are blocked rather than assigned guessed/fallback prices.
- RBLXValue: 214/214 exact weapon identities and 214/214 numeric `value_source_b` Supreme entries; Stability is available for 212/214. Latest archived snapshot: `2026-11-27T05:12:48Z` (dataset `last_updated=2026-11-27 00:02:34`); scheduled archive run #10 completed successfully at 05:12 UTC and confirmed 214/214 Supreme values plus 212/214 Stability. The manifest schedules 11:30 and 23:30 UTC (12:30 and 00:30 Algeria); the next scheduled slot still needs live confirmation. Missing Stability is displayed as unavailable, not inferred. Daily history contains 1,498 rows.
- Latest persisted ROBASE snapshot: `2026-11-27T18:57:46Z`.

## Reliability and live verification

- Latest completed production scan: run `46053198590` on `main`. Scanner and state-persistence jobs succeeded across 14 cycles and 66 pagination pages / 1,320 API posts; every page batch reported `complete=True` and `seen_boundary=True`. One actual trade alert was accepted by Telegram. Two other candidates were skipped by the duplicate guard and both IDs were confirmed in `alerted.json`. No new uncertain delivery was created; the two existing quarantines remained blocked. No scanner errors were found. The accepted trade alert was sent 32.50 seconds after the relevant feed refresh.
- Previous completed scan: run `46047079955`; 14 cycles, complete pagination, no scanner errors, and no new alert met the selection rules during that run.
- Earlier live validation: run `46046592893`; 14 cycles and two alerts sent. Telegram API response times for those sends were approximately 388 ms and 452 ms. One already-alerted listing was correctly blocked by the duplicate guard.
- The scan log loaded the optional RBLXValue snapshot on every cycle: 214/214 Supreme `value_source_b`, 212/214 Stability, `informational_only=true`, snapshot age about 13.8 hours. The production scanner made zero RBLXValue API requests; it reads the archived snapshot only. The next chained scan `46054424527` started on `main` and successfully restored the updated state branch; it is still running.
- Watch recheck queue: 1,994 tracked listings; pending rechecks: 0. The watch TTL is 24 hours, capacity is 50,000, and the recheck batch is 50 listings every 4 cycles.
- Unmatched-name registry: 598 entries after removing five historical generic evaluator/rejection strings. PR #261 adds matching filters for legacy `no numeric Supreme value` and `value-weighted Demand is low` reasons so they cannot repollute the registry.
- Telegram quarantine: 2 unresolved delivery IDs and 2 matching context records. One context was reconstructed from run `45996910385` logs with its BloxSwaps source post ID and exact attempt timestamp; its listing/owner URLs were not present in logs and remain blank. Do not resend either message without manual confirmation.
- ROBASE refresh safety: PR #257 preserves the last complete snapshot after a failed refresh. Cached prices remain usable only while their individual verification timestamps are within the 20-minute freshness limit.
- Feed and quarantine hardening: PR #258 added candidate accounting, wildcard-policy coverage, and reconciliation of confirmed/ orphaned Telegram quarantine state.
- Public dashboard: PR #259 added a strict item-data whitelist; the dashboard publish workflow succeeded.
- PR #260 filters generic evaluator rejection messages out of the unmatched-item registry and normalizes BloxSwaps API source aliases when preserving uncertain Telegram context.
- The direct Supreme scraping workflow was removed; PR #205 is closed. The unused direct-site collector and its tests are also deleted in PR #261. Production reference info uses RBLXValue API v2 only. The archive metadata now explicitly says its production use is optional Telegram reference display only, never a cash valuation/filter/alert decision; the archive workflow will refresh this metadata on the PR merge push.

## Merged changes

- PR #257 — preserve last complete ROBASE snapshot on failed refresh.
- PR #258 — wildcard handling, feed accounting, and Telegram quarantine reconciliation.
- PR #259 — public dashboard item-data whitelist.
- PR #260 — unmatched-name diagnostics and uncertain Telegram context hardening.

## Open or experimental work

- PR #159 — draft hardening work; do not merge wholesale without a fresh review against current `main`.
- PR #203 — isolated RBLXValue blended-value shadow analysis; no production Telegram alerts. Its proposed `value`-only change is not the source for the reference card and must not replace `value_source_b` for Supreme Value.
- PR #100 — experimental Market Book work; not part of the production scanner.
- Current PR #261 branch: `docs/gross-cash-estimate-and-project-handoff` (latest code-bearing commit `072c7b9c`; documentation checkpoint commits follow). It clarifies the USD 2 threshold and displayed profit as **gross estimates before fees/slippage**, renders Telegram reference data as a compact labeled card with one clean separator, filters legacy generic rejection reasons, removes the unused direct Supreme website collector and its tests, updates docs, and adds this handoff file. Latest CI on the current code-bearing head: 750 tests passed twice, Ruff passed, and ROBASE Pricing Tests passed (runs `46055063917` and `46055063978`). The preceding workflow-policy commit also passed 750 tests twice and ROBASE Pricing Tests (runs `46055054717` and `46055054734`). PR is mergeable but not merged yet.

## Remaining release checks

1. Verify completion of chained scan `46054424527`; the prior scan `46053198590` and state persistence both succeeded.
2. Review PR #261's final diff and latest passing CI, then merge only if no new failures appear.
3. Confirm the two quarantined Telegram messages manually; the system intentionally will not auto-resend them.
4. Monitor the next RBLXValue archive update (scheduled for 23:30 UTC / 00:30 Algeria time); the latest completed scheduled run observed is #10 at 05:12 UTC.
5. Re-check price freshness, feed pagination, Telegram latency, and state persistence on the first scan after the final merge.

## Resume protocol for future conversations

1. Read this file first.
2. Fetch the latest `main` SHA and inspect open PRs; do not assume the branch or work from the previous chat is still current.
3. Inspect the latest scanner, tests, ROBASE pricing, watchdog, and dashboard workflow runs.
4. Inspect the `state` branch files: `robase_prices.json`, `watch_ads.json`, `pending_recheck.json`, `unmatched_names.json`, `telegram_uncertain.json`, and `telegram_uncertain_context.json`.
5. Preserve the production decisions above: Chroma.gg Price (7d) only, RBLXValue Supreme/ Stability informational only, and no automatic resend of uncertain Telegram deliveries.
6. Update this file whenever a material production decision, merged fix, unresolved blocker, or verified run changes.
