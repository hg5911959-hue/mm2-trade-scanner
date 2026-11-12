# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with Telegram alerts. GitHub Actions is configured to check every 15 minutes.

## Source and calculation

The scanner reads the BloxSwaps P2P page:

`https://bloxswaps.com/fr/trade-mm2?trade-type=P2P`

It reads the values displayed on each listing itself; a separate item-values API is not required for the current implementation.

Calculation:

`your value difference = offered value - requested value`

From your perspective as the person taking the listing, you give the items the poster requests and receive the items the poster proposes. The configured minimum difference is **0**, so a listing qualifies when the proposed items are worth at least as much as the requested items. The alert includes the listing link and both displayed values.

## Required Telegram setup

In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.

Add:
- `TELEGRAM_BOT_TOKEN`: token created with Telegram BotFather.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.

Never commit the bot token to the repository or share it in chat.

## Run

Open **Actions → MM2 Trade Scanner → Run workflow** to start a manual run. Scheduled runs occur every 15 minutes (GitHub Actions cron uses UTC).

The workflow stores processed listing IDs in `data/seen.json` to reduce duplicate alerts.

## Important limitations

- This adapter reads the public P2P listing page and depends on its HTML structure remaining compatible.
- If BloxSwaps changes the page markup or loads listings only through client-side code, the run will fail clearly rather than send guessed alerts. The first live run must confirm parsing works.
- Only listings visible on the selected P2P page are scanned; this is not a guarantee of every trade on the site.
- Displayed values are estimates and do not guarantee that a trade is accepted or completed.
- No Roblox password, browser cookie, or account session token is needed.
