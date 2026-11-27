# Project Status and Resume Checkpoint

Last verified: 2026-11-27 18:30 UTC  
Repository: `hg5911959-hue/mm2-trade-scanner`  
Production branch: `main`  
Main baseline at checkpoint: PR #260 merged; the next production scan is running on that baseline.

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
- Supreme Value and Stability: read from the RBLXValue daily snapshot and appended to Telegram as **reference information only**. They do not affect cash valuation, filtering, ranking, or alert decisions.

## Current data coverage

- ROBASE catalogue: 222/222 item pages present and freshly verified in the latest persisted snapshot.
- Chroma.gg 7-day price: 200/222 items have a price.
- Cash eligibility: 189/222 items also meet the 3+ sales sample threshold. The remaining 22 have no 7-day price; 11 have fewer than 3 sales. They are blocked rather than assigned guessed/fallback prices.
- RBLXValue: 214/214 Supreme Value entries; Stability is available for 212/214. Missing Stability is displayed as unavailable, not inferred.
- Latest persisted ROBASE snapshot: `2026-11-27T18:21:37Z`.

## Reliability and live verification

- Latest completed scan job: run `46048822099` (started before PR #260 was merged). Its scanner job and state persistence completed successfully; 14 cycles were observed, with two Telegram alerts sent. Feed pagination reported complete pages and the seen boundary on each observed cycle.
- Previous completed scan: run `46047079955`; 14 cycles, complete pagination, no scanner errors, and no new alert met the selection rules during that run.
- Earlier live validation: run `46046592893`; 14 cycles and two alerts sent. Telegram API response times for those sends were approximately 388 ms and 452 ms. One already-alerted listing was correctly blocked by the duplicate guard.
- A scan on the post-PR-260 baseline is currently running; verify its final result before declaring the release fully operational.
- Watch recheck queue: 1,784 tracked listings; pending rechecks: 0. Recheck batch is 50 listings every 4 cycles.
- Unmatched-name registry: 601 actual item-name entries after removing two historical generic rejection-reason entries.
- Telegram quarantine: 2 unresolved delivery IDs and 2 corresponding context records. One context was reconstructed from the workflow log with its BloxSwaps source post ID, timestamp, and estimated gross spread. Do not resend either message without manual confirmation.
- ROBASE refresh safety: PR #257 preserves the last complete snapshot after a failed refresh. Cached prices remain usable only while their individual verification timestamps are within the 20-minute freshness limit.
- Feed and quarantine hardening: PR #258 added candidate accounting, wildcard-policy coverage, and reconciliation of confirmed/ orphaned Telegram quarantine state.
- Public dashboard: PR #259 added a strict item-data whitelist; the dashboard publish workflow succeeded.
- PR #260 filters generic evaluator rejection messages out of the unmatched-item registry and normalizes BloxSwaps API source aliases when preserving uncertain Telegram context.
- The direct Supreme scraping workflow was removed; PR #205 is closed. RBLXValue's scheduled archive is the source for Telegram reference information.

## Merged changes

- PR #257 — preserve last complete ROBASE snapshot on failed refresh.
- PR #258 — wildcard handling, feed accounting, and Telegram quarantine reconciliation.
- PR #259 — public dashboard item-data whitelist.
- PR #260 — unmatched-name diagnostics and uncertain Telegram context hardening.

## Open or experimental work

- PR #159 — draft hardening work; do not merge wholesale without a fresh review against current `main`.
- PR #203 — RBLXValue shadow analysis; no production Telegram alerts.
- PR #100 — experimental Market Book work; not part of the production scanner.
- Current documentation branch: `docs/gross-cash-estimate-and-project-handoff`. It clarifies that the USD 2 threshold and displayed profit are **gross estimates before fees/slippage**, updates Telegram/dashboard labels, and adds this handoff file. It is not merged yet.

## Remaining release checks

1. Finish and inspect the post-PR-260 live scan.
2. Complete review and CI for the gross-estimate label/handoff branch, then merge only if all checks pass.
3. Confirm the two quarantined Telegram messages manually; the system intentionally will not auto-resend them.
4. Monitor the next RBLXValue archive update (scheduled for 23:30 UTC / 00:30 Algeria time).
5. Re-check price freshness, feed pagination, Telegram latency, and state persistence on the first scan after the final merge.

## Resume protocol for future conversations

1. Read this file first.
2. Fetch the latest `main` SHA and inspect open PRs; do not assume the branch or work from the previous chat is still current.
3. Inspect the latest scanner, tests, ROBASE pricing, watchdog, and dashboard workflow runs.
4. Inspect the `state` branch files: `robase_prices.json`, `watch_ads.json`, `pending_recheck.json`, `unmatched_names.json`, `telegram_uncertain.json`, and `telegram_uncertain_context.json`.
5. Preserve the production decisions above: Chroma.gg Price (7d) only, RBLXValue Supreme/ Stability informational only, and no automatic resend of uncertain Telegram deliveries.
6. Update this file whenever a material production decision, merged fix, unresolved blocker, or verified run changes.
