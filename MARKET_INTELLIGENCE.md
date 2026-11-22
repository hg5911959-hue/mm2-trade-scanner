# Optional Market Intelligence Extensions

This document describes optional research and import utilities. **None of these extensions is part of the production trade-qualification path unless a workflow explicitly invokes it.** The production scanner remains deterministic: BloxSwaps listings are parsed, ROBASE cash prices are refreshed, conservative calculations are applied, and Telegram handles alerts.

## Current production status

- The production scheduler calls the reusable scanner workflow.
- The production scan workflow does not invoke discord_collector.py, tools/collect_public_feeds.py, or the market_intelligence package.
- No Discord bot token is passed to the production scanner workflow. The Discord collector is not an automatic source for current alerts.
- Gemini and DeepSeek integrations are advisory/manual utilities only. They do not supply prices, determine profitability, or approve offers.
- Reddit/RSS collection is a separate optional utility and is not part of the scheduled scanner.

## Optional Discord collection

discord_collector.py is a standalone, read-only collector designed for a Discord bot installed in servers where the operator has permission to read the relevant channels. It must use an official bot token, not a personal account token or self-bot.

To use it outside the production workflow:

1. Create/configure an official Discord bot and enable the required Message Content Intent.
2. Invite it only to servers where you have authorization, granting only View Channels and Read Message History on the intended channels.
3. Set DISCORD_BOT_TOKEN and an explicit DISCORD_GUILD_IDS allowlist in the local execution environment.
4. Install the discord.py dependency in that environment and run python discord_collector.py --help to inspect the supported options before collecting.
5. Keep raw message exports private. Do not commit private channel contents or upload them to the public dashboard.

The collector does not send messages, moderate, join voice, bypass permissions, or use personal account sessions. Channels the bot cannot read are not collected.

## Manual import and parsing utilities

- discord_parser.py parses supplied Discord message records into normalized listing candidates.
- tools/check_discord_import.py validates a prepared import.
- tools/merge_discord_imports.py merges explicitly supplied import files.
- tools/collect_public_feeds.py is a separate public-feed/RSS utility.

These are operator-run helpers. They are not invoked by the scheduled production scanner, and their output must not be treated as verified price data without the normal identity, freshness, and valuation checks.

## AI adapters

The market_intelligence package contains experimental advisory adapters. They are not called by scanner.py or the production GitHub Actions workflow. If enabled in a future change, model output must remain advisory-only and must never:

- invent or substitute ROBASE prices;
- calculate or override trade profitability;
- bypass Ajout/Ajouts, quantity, identity, stability, freshness, or API-budget gates;
- mark an unverified offer as eligible.

Never send API keys, bot tokens, account credentials, private DMs, or unrelated personal information to an AI provider.

## Maintenance rule

Keep optional integrations isolated from the scanner's critical path. A future integration must have a separate reviewed workflow, explicit secret mapping, bounded data retention, source-permission checks, and regression tests. Until then, the core scanner must operate correctly with these extensions unused.
