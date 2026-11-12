# MM2 Trade Scanner

Automated scanner for a **configured public MM2 trade feed**, using a configurable item-value feed and Telegram alerts. Runs with GitHub Actions every 15 minutes.

## Current status

The GitHub Actions workflow and scanner engine are included. **A compatible live trade-feed URL is not bundled or assumed.** MM2 does not provide a documented official public endpoint for every live player trade, so the scanner will not discover live trades until you configure a trustworthy feed that returns the JSON format below.

## Configure GitHub

In this repository, open **Settings → Secrets and variables → Actions**.

Add these **Variables**:
- `MM2_VALUES_URL`: direct URL returning item values as either `{"values":{"Chroma Seer":100}}` or `[{"name":"Chroma Seer","value":100}]`.
- `MM2_TRADES_URL`: direct URL returning trades as a JSON array, or an object with a `trades`, `offers`, `data`, or `results` array.

Add these **Secrets**:
- `TELEGRAM_BOT_TOKEN`: token from BotFather. Never commit it to the repository.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.

## Trade feed format

The trade endpoint should return records similar to:

```json
{
  "trades": [
    {
      "id": "unique-trade-123",
      "give": [{"name": "Item A"}],
      "receive": [{"name": "Item B"}],
      "url": "https://example.com/trade/123"
    }
  ]
}
```

The scanner calculates:

`value difference = value(receive) - value(give)`

With the configured minimum difference of **0**, trades with a difference of zero or more are sent to Telegram. Already processed trade IDs are saved in `data/seen.json` to avoid repeated alerts.

## Start a test

After adding both variables and both secrets, open **Actions → MM2 Trade Scanner → Run workflow**. Scheduled runs then occur every 15 minutes (GitHub Actions cron uses UTC).

## Important

- Use only feeds you are authorized to access and that permit automated requests.
- The values are estimates and may differ from actual player demand or accepted trades.
- The scanner skips trades whose item names are missing from the value feed.
- No Roblox password, browser cookie, or account session token is needed.
