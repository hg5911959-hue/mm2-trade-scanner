# MM2 Trade Scanner — Operations Runbook

This document contains operational configuration and deployment procedures. The project overview and development setup are in [README.md](../README.md).

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share these secrets. The scanner does not print API keys or Telegram bot tokens in logs.

### RBLXValue free-plan compliance and key safety

- The scanner displays the exact `Data from rblxvalue.com` credit in every Telegram message and on the public dashboard. It is shown unconditionally, so the project remains compliant if the API response requires free-plan attribution.
- RBLXValue documents one free API key per account. Do not create alternate accounts or spread requests across keys to bypass limits. If a key is compromised or needs replacement, rotate the existing key through the official developer dashboard and update the `RBLXVALUE_API_KEY` repository secret.
- HTTP 429 is a rate-limit response, not a key suspension; the scanner respects retry guidance and its own persistent rolling request budget. HTTP 401 indicates a missing/wrong/rotated key; HTTP 403 may indicate a key that is not activated, expired, or suspended.
- The scanner never stores the key itself in repository state. The private state branch stores only a truncated SHA-256 fingerprint and timestamps for health checks and circuit-breaker recovery.

- `data/supreme_values_audit.json` records the complete internal API snapshot with completeness counts, missing metadata and same-name identity collisions. It is retained for scanner diagnostics only; do not export or republish the Supreme-derived value list. Missing or unrecognized received-side Stability causes cash offers to fail closed; Demand is informational only in cash-first mode.
- The RBLXValue API explicitly documents `value_source_b` as the Supreme Values numeric value, but documents `stability` as a general API field rather than a source-specific field. Telegram therefore labels it `Stability (RBLXValue)`; this is not a claim of direct Supreme website verification. Its `/v2/history/{slug}?source=supremevalues` endpoint can be used for source-specific historical value movement where API budget permits.
- Up to six records with missing type, or numeric-valued weapons missing Stability/Demand/Rarity, are checked per UTC day through the documented `GET /v2/items/{slug}` endpoint. The response is accepted only when ID/slug match the catalogue record; successful details fill missing metadata without overwriting known values. Attempts are persisted and unchanged records are not queried repeatedly. This keeps enrichment within the 60-request/day safety budget.
- **Manual Supreme-data editing is intentionally not enabled.** Supreme Values' Terms of Service explicitly prohibit scraping, manually inputting, or copying its value-list data into apps, websites and bots without authorization. We can build an editor for independently sourced, user-owned valuations or corrections, but not a tool for manually copying Supreme's published values unless written permission is obtained.


## Runtime, deduplication, and persistence

- **Trigger cadence:** GitHub cron starts the scanner every 10 minutes. Manual `workflow_dispatch` and `repository_dispatch` event type `mm2-scan` remain available.
- **Polling inside each job:** one process runs `scan_once()` every 30 seconds (configurable within 30–60 seconds) for 540 seconds (9 minutes). This leaves an approximately 1-minute scheduled gap; GitHub may delay scheduled runs, so this reduces but cannot eliminate missed-post risk. It handles SIGTERM/SIGINT by completing the current scan cycle before exiting.
- **Concurrency:** `cancel-in-progress: false`; a running scan is not cancelled while it is sending Telegram messages or writing state. GitHub keeps at most one pending run for the concurrency group.
- **Alert budget:** qualifying offers are ranked by conservative profit; the top 15 are sent per scan by default, and remaining offers stay eligible for the next scan.
- **Detection timing:** the scanner logs first-observed-to-alert latency for every sent trade. The 30-second poll interval is designed to keep detection near 30 seconds when a post appears between cycles; GitHub scheduling and remote API delays are outside that guarantee.
- **Cold start:** if durable seen state is missing, the scanner paginates the feed rather than baselining only the first page. It evaluates posts verifiably refreshed within the last 60 minutes and seeds older or undated posts as historical baseline. It stops after 25 consecutive timestamped posts older than the cutoff; pinned and undated posts do not advance that boundary. Reaching the 1,000-page cap leaves pagination incomplete and triggers maintenance.
- **Watch window:** the latest 6 hours or 300 watched posts (whichever is smaller) are retained. Non-profitable posts remain eligible for re-evaluation, and pagination searches for watched IDs before stopping at the seen boundary. A changed item/quantity, Stability, or verified cash-price fingerprint causes the same ad to be evaluated again; a newly profitable version can alert again without replaying an unchanged alert. Supreme numeric value changes alone do not change the cash ranking.
- **State compatibility:** existing `alerted.json`, seen-ad, pending-recheck, and uncertain-Telegram state formats are retained and migrated in place. New watch-window and last-success checkpoints are additive. A corrupt or unreadable state file fails closed instead of silently resetting deduplication.
- **Durable state source:** the `state` branch is the sole durable source. The scan job has `contents: read`; it restores the branch and uploads a state artifact. A separate `persist-state` job has `contents: write` and updates only the state branch. The legacy Actions Cache restore path has been removed.
- **State checkpoints:** state files are written atomically after each scan cycle; the final state artifact is committed after the polling job. The 2.5-minute timeout margin allows artifact upload to finish.
- **Telegram delivery:** a definite Telegram API rejection clears its pre-send guard so the offer can be retried. A timeout, connection failure, invalid response, or HTTP 5xx is treated as **uncertain delivery**: the listing is quarantined and is not automatically resent. Messages to the same chat are spaced by 1.1 seconds to respect Telegram's per-chat rate limit. An explicit 429 is retried once after `retry_after`; if it is still rejected, the listing is queued for a later scan instead of blocking the polling session with repeated long waits. Check Telegram before manually clearing its entry in the uncertain state file.
- **Maintenance alerts:** malformed/unreadable cards, missing RSC data, incomplete pagination, unmatched item names, unresolved pending ads after a complete pagination pass, and expired Telegram quarantines use persistent cooldowns. They are not gated by a loop-mode environment flag. Missing RSC data is a scan error; HTML-only partial data is never trusted.
- **Watchdog:** runs every 5 minutes, suppresses stale-success alerts while a scanner job is active, and raises a warning after 20 minutes without a successful scanner run. The repository name is read from `GITHUB_REPOSITORY`.
- **Action security:** GitHub Actions dependencies are pinned to verified immutable commit SHAs with release comments. Dependabot checks GitHub Actions weekly. The scanner job does not have repository-content write permission; only the state-persistence job can write contents.
- **External monitoring:** optionally configure Healthchecks.io and store its ping URL as `HEALTHCHECKS_PING_URL`. The workflow pings on completion and reports failed runs.

## Item identity and Supreme-value safety

- BloxSwaps catalogue metadata (slug, rarity, weapon type, and chroma flag) is carried with each item ID. A missing catalogue ID is never rescued by a similarly named weapon.
- Supreme records retain their own source slug, category, type, ID, scalar value, explicit numeric range, and published stability label. In cash-first mode, Supreme numeric values are not used in the profit arithmetic; the received-side Stability label remains a safety gate and is displayed in Telegram. The cache schema is versioned so an older snapshot without stability is refreshed before it can be used. Same-display-name records are never merged into a synthetic high/low range.
- Resolution uses exact source slug, exact normalized name plus catalogue identity, or a unique base-name match constrained by rarity and type (for catalogue labels such as Latte). Reviewed aliases are allowed; automatic fuzzy and plural matching are disabled.
- If more than one Supreme identity remains possible, the item is rejected. A missing Supreme numeric value alone does not block cash evaluation when the identity is unique and a verified USD price exists.
- RBLXValue encodes some Common/Uncommon/Rare xN T1-item notation as a small integer. Values below 10 in those categories are treated as tier-item counts, not ordinary Supreme value units. Higher numeric exceptions remain eligible.
- Give/receive bounds use only the selected item's own published range. When the API has no range, the scalar value_source_b is used identically on both sides; another item with the same name is never used as a range.

## Workflow hardening and external scheduling

- **Action integrity:** every third-party GitHub Action is pinned to a verified full commit SHA with a trailing release-version comment. Dependabot checks `github-actions` weekly.
- **Test isolation:** `tests.yml` runs `python -m unittest discover -s tests` on pushes to `main` and on pull requests. The production scan job no longer runs tests, so a test failure cannot prevent a scheduled scan. The old `source-diagnostics.yml` workflow has been removed.
- **Global log redaction:** `scanner.py` redacts the exact configured Telegram bot token, Telegram chat ID, and RBLXValue API key (only when at least six characters), plus Telegram bot URLs, `rbx_` keys, and Bearer credentials. Both stdout and stderr are filtered before output reaches `scanner.log` or the public Actions artifact.
- **Durable state replacement:** when scanner data changes, `persist-state` compares staged `data/` with `origin/state` and skips an unchanged tree. Otherwise it creates `state-new` as an orphan branch and force-pushes it to `state`, leaving a single-commit state history. Empty or missing state artifacts are ignored so they cannot erase durable data. The workflow-level concurrency group covers both scan and persistence jobs, preventing overlapping state writes.
- **Faster coverage:** the scanner cron targets every 10 minutes; each job polls for up to 540 seconds at 30-second intervals and has a 20-minute timeout. Fast new-feed scans run every cycle, while the bounded backlog pass runs once every four cycles. The watchdog default stale threshold is 20 minutes.
- **Optional cron-job.org trigger:** configure a scheduled HTTP POST to `https://api.github.com/repos/hg5911959-hue/mm2-trade-scanner/dispatches` with JSON body `{"event_type":"mm2-scan"}` and GitHub's JSON accept/content headers. Use a fine-grained personal access token limited to this repository with the minimum `Contents: read and write` permission required for repository dispatch. Store the token only in cron-job.org's secret configuration; never put it in the repository, workflow YAML, URL, or logs. GitHub Actions cron remains the primary schedule.

## Logs and manual operations

- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- External scheduler fallback: cron-job.org can call GitHub's repository-dispatch API with `{"event_type":"mm2-scan"}` to trigger the existing `repository_dispatch` workflow. Create a fine-grained personal access token limited to this repository, grant the minimum repository permission required for dispatch (`Contents: read and write`), and store it only in cron-job.org's secret configuration. Never put it in the repository, workflow YAML, URL, logs, or chat. GitHub cron remains the primary schedule.
- `history.csv` records detected opportunities and calculated values; it cannot confirm that a Roblox trade was accepted or completed.

## Limitations and safety

- BloxSwaps' public RSC/API contract and HTML markup can change. The live fixture and regression tests protect known layouts, not every future layout.
- The scanner uses a maximum of 1,000 cursor pages per scan. If there are more than 1,000 new posts before a seen boundary, the maintenance warning indicates that the scan was capped.
- GitHub cron starts can be delayed. A 10-minute schedule can miss short-lived offers; it cannot guarantee detection before another trader accepts them.
- Supreme Values are estimates delivered by a third-party provider. A fresh RBLXValue response does not guarantee Supreme itself has just changed its upstream data.
- No Roblox password, browser cookie, or account session token is needed.

## Market Intelligence extension

See [MARKET_INTELLIGENCE.md](MARKET_INTELLIGENCE.md) for the additive public-feed and optional free-AI architecture. The existing BloxSwaps scanner remains unchanged; new sources and AI review are not enabled in production until their feeds and tests are verified.


## SQLite market history and public dashboard

The scanner keeps a local SQLite history at `data/market_history.sqlite3`. The existing `state` branch workflow restores and persists this database alongside the Supreme cache, request budget, and other durable state.

The database stores:
- Deduplicated Supreme/secondary-source value snapshots (only when the value set changes).
- Public BloxSwap listing observations, first-seen/last-seen timestamps, and item quantities.
- Qualified opportunity records, including totals, profit, margin, and listing links.
- A source-agreement label for display only: `cross_checked` when both sources are within 15%, `conflict` when they differ more, and `estimated` when only one numeric source is available. Cash-first trade calculations use verified manual USD prices only; Supreme numeric values and the secondary source are never blended into cash profit.

Retention is 90 days for listing observations and 180 days for value snapshots/opportunities. SQLite writes are additive and idempotent; history storage failures are logged without changing the existing trade-evaluation or Fail-Closed rules.

**Privacy:** private Discord messages, listings, and opportunities are deliberately excluded from this database and from the public dashboard. The state branch belongs to the public repository, so private-server content must never be written there.

### GitHub Pages dashboard

The static dashboard is in `docs/index.html`. After each successful **MM2 Trade Scanner** workflow, `.github/workflows/dashboard-pages.yml` restores the SQLite database from `state`, exports a public-safe `dashboard-data.json`, and deploys it with GitHub Pages. It shows recent public listings, qualified opportunities, Supreme/secondary value comparisons, and historical value changes.

One-time repository setting required:
1. Open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Save. The next successful scanner run will publish the dashboard. You can also run **Actions → Publish MM2 Market Dashboard → Run workflow** manually.

The dashboard is static and uses no paid API, external analytics, or third-party JavaScript. It never includes API keys, Telegram credentials, or private Discord records.


## Discord trade-message ingestion

The scanner includes a conservative parser for messages collected from the two explicitly allowlisted trading channels in one configured Discord server. It accepts only explicit two-sided trade statements, normalizes offered/requested items into the scanner schema, aggregates repeated item quantities, and rejects ambiguous or malformed messages. Discord message IDs are namespaced so they cannot collide with BloxSwaps IDs or be sent into BloxSwaps cursor/pending queries.

Discord integration requires an official bot in the single approved server, with read-only channel/history permissions restricted to the two configured channel IDs, and Message Content Intent. If the bot cannot be added, direct collection remains inactive. A private/manual import is supported through the GitHub Actions secrets `DISCORD_MANUAL_MESSAGES_JSON` and `DISCORD_MANUAL_MESSAGES_JSON_2`: add it under **Settings → Secrets and variables → Actions** as a JSON object containing a `messages` array (up to 2,000 rows). Example shape:

```json
{
  "messages": [
    {
      "message_id": "123456789012345",
      "guild_id": "1125462206151143464",
      "channel_id": "123456789012345",
      "author": "Trader",
      "content": "Trading: 2x Icebreaker | Looking for: Candy",
      "url": "https://discord.com/channels/1125462206151143464/123456789012345/123456789012345",
      "created_at_utc": "2026-11-13T12:00:00+00:00"
    }
  ]
}
```

The secret is merged into the temporary collector file for that run; its contents are not printed or written to the public state branch. The parser then validates each message and rejects ambiguous trades.

**Private-data boundary:** raw Discord message JSON is processed only inside the scan job and is removed from the state artifact before persistence. Discord offers and opportunities are also excluded from the public SQLite history, public CSV history, and GitHub Pages dashboard. The bot does not send messages, moderate, or use a user token/self-bot.


## Reliability controls: breaker, alert cap and dry-run

- RBLXValue credential rejections are tracked by the SHA-256 key fingerprint only. Consecutive 401/credential-specific 403 responses escalate the lockout (6h, 12h, then permanent after the third consecutive rejection); a successful API response or key rotation resets the counter. Cloudflare/CAPTCHA challenge 403 responses are not classified as invalid credentials. After three rejections, replace the key or contact RBLXValue; the scanner makes no more requests using that fingerprint.
- `MAX_ALERTS_PER_SCAN` defaults to 15 in production. In cash mode, the scanner ranks qualifying offers by conservative USD cash profit; in value mode it ranks by game-value profit. It sends the best 15 and leaves the remainder eligible for the next scan. A single Telegram notice reports how many offers were deferred. Set it to `0` only when unlimited alert volume is explicitly desired.
- Use **Actions → MM2 Trade Scanner → Run workflow → dry_run=true** to evaluate offers without sending Telegram messages or writing alerted/delivery state. The log prints only listing IDs and the profit for the active mode (game value or cash USD). The default is false. Dry-run suppresses maintenance and failure notifications too.
- Global log redaction includes Discord bot credentials and both manual-import secrets. Never paste a Discord bot token or raw message JSON into an issue, commit, or log.

## Manual Discord import: two-secret workflow

You can combine two JSON imports without a bot. Create or update repository secrets named `DISCORD_MANUAL_MESSAGES_JSON` and `DISCORD_MANUAL_MESSAGES_JSON_2`. Each secret must be a JSON object with a `messages` array; use the row schema shown above. The workflow independently validates both secrets, so one malformed secret does not discard the other. Rows older than seven days are ignored, IDs are deduplicated, and the merged import is capped at 2,000 rows. Only counts and freshness metadata are logged; raw message bodies are removed before the state artifact is uploaded and never enter the public state branch.

Before updating a secret, validate the local JSON file:

```bash
python tools/check_discord_import.py discord-messages.json
```

The checker validates the schema and row cap and reports the file size. It warns above 45 KB to leave headroom below GitHub's 48 KB secret limit. Paste the JSON file contents into either secret in **Settings → Secrets and variables → Actions**, save, and run the scanner. Keep each secret below the size limit. The workflow records only the newest-message timestamp and a stale flag; if the newest imported message is older than 48 hours, it logs `[discord-manual] import is stale` and includes that status in the weekly report without sending a per-scan alert.

## Daily metrics and weekly health report

- `data/metrics.json` stores UTC daily counters: scans, scan failures, sent/deferred alerts, RBLXValue requests, breaker duration and empty parses. It is written atomically and retains the latest 14 days. It is optional during state validation for compatibility with existing state branches.
- **MM2 Weekly Health Report** runs every Sunday at 08:00 UTC or manually from Actions. It reads the `state` branch and sends scan success percentage, alert/deferred totals, today's API request usage against the daily limit, key state, Discord manual-import freshness and a seven-day alert trend.
- The weekly workflow uses only the existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets and grants `contents: read`. No paid service or new secret is required.

## Scheduled scanning and pending rechecks

- `.github/workflows/scan-scheduler.yml` owns the 10-minute cron and calls the reusable `scan.yml` workflow. `scan.yml` remains available for manual `workflow_dispatch` runs and reusable `workflow_call` execution. The shared concurrency group prevents overlapping scanner sessions.
- Unresolved BloxSwaps listing IDs remain in `data/pending_recheck.json` with their original first-seen/expiry timestamps. A separate `data/pending_recheck_schedule.json` controls when they are rechecked: 5 minutes, 15 minutes, 30 minutes, 1 hour, then 6 hours. New listings continue through the normal recent-feed cursor on every scan; only old unresolved IDs are backoff-limited.
- Legacy pending state without a schedule is immediately eligible for one migration recheck. The schedule file is optional for older state branches and is validated before persistence.

- `.github/workflows/watchdog-scheduler.yml` owns the five-minute watchdog cron and invokes the reusable `watchdog.yml` workflow. The watchdog checks both queued and running scans and reports stale successful-scan timestamps; its alert reminder window is 30 minutes.

## Additional scheduler and dashboard behavior

- The scanner scheduler also starts a scan after a successful `MM2 Scanner Tests` run on `main`. Failed test runs are ignored. This provides a post-deployment verification scan in addition to the 10-minute cron and manual dispatch.

- The watchdog also runs after every completed `MM2 Scanner Scheduler` workflow, regardless of success or failure, so a finished scan is checked immediately as well as by the five-minute cron.

- The scanner scheduler checks whether its own cron workflow or the watchdog cron workflow was disabled by GitHub. With the narrowly scoped `actions: write` token, it attempts to re-enable only those two known workflows; failure is non-fatal to the scan and is logged as a warning.

- Pending rechecks run only on every fourth scan cycle by default (`BACKLOG_RECHECK_EVERY_CYCLES=4`), with at most 50 IDs per backlog pass (`MAX_PENDING_RECHECK_PER_SCAN`) and a 25-page lookahead (`MAX_BACKLOG_RECHECK_PAGES`). The newest due IDs are prioritized because they are closest to the feed head. Low-priority missing-value/stability IDs are seeded into the seen set while remaining in the recheck queue, preventing repeated hot-loop parsing. Deferred offers, definite Telegram send failures, uncertain quantities, and unparsed feed cards retain priority retry reasons and stay visible to the hot path, so they are not hidden by the seen-set optimization. Fast cycles omit the historical backlog entirely, so old unresolved listings cannot delay discovery of new offers. A genuinely changed Supreme snapshot or manual cash-price catalogue also triggers a bounded recheck immediately, so new identities or prices can resolve queued offers without repeatedly reprocessing them on every fast cycle. Unresolved IDs remain queued for later attempts.


## Cash blocker intelligence

The scanner records each listing held for a missing, unverified, or expired cash price in the UTC day's `cash_blockers` map in `data/metrics.json`. Each normalized item is counted once per listing per scan. Co-occurrence groups are retained alongside item totals so the estimated unlock percentage is calculated from actual blocked-listing combinations rather than double-counting listings. Metrics retain the latest 14 UTC days and are written atomically; state validation rejects malformed blocker data.

Run `python tools/next_to_verify.py` to see the 15 most frequent blockers, their current catalogue price and verification age, and the cumulative share of blocked listings that would become evaluable if the top N items were verified. The weekly Telegram report includes the top 10. It prints only the ranked subset, never the full catalogue.

Weekly cash maintenance routine:
1. Paste the current marketplace list into a text file.
2. Run `python tools/verify_cash_prices.py --input marketplace-list.txt` and review skipped/ambiguous lines.
3. Read the preflight message and `python tools/next_to_verify.py` output before enabling cash mode and relying on its alerts.

Supreme numeric values are never converted into cash prices; the USD catalogue remains a separate, manually verified source.


### Test isolation and workflow permissions

Every unittest case uses its own temporary working directory. Tests that need catalogue data use synthetic fixtures under `tests/fixtures`; no test reads or writes the repository's real `data/` directory. CI runs `python -m unittest discover -s tests` twice and checks `git status --porcelain data` after each pass. `tests/test_no_data_pollution.py` hashes every file in the real data directory before and after a mocked scan.

Scanner and tool path defaults are module constants or explicit function arguments so tests can patch them. Workflow permissions default to read-only; write access is scoped to state persistence, schedule enablement, repository keepalive, or Pages deployment. Third-party Actions are pinned to full 40-character commit SHAs, and workflow steps must never echo secrets.

