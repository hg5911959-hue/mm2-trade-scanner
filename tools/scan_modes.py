"""Pure resolution of scanner execution modes from the environment."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ScanModes:
    daily_shadow_mode: bool
    value_profit_shadow_mode: bool
    dry_run: bool
    testing_mode: bool
    force_dry_run: bool


def resolve_scan_modes(
    environ: Mapping[str, str],
    *,
    cash_filter: bool,
    bot_token: str,
    chat_id: str,
) -> ScanModes:
    """Resolve mutually exclusive shadow modes and safe delivery requirements."""
    daily_shadow_mode = (
        environ.get("ROBASE_DAILY_CHART_SHADOW_ENABLED", "").strip().casefold() == "true"
    )
    value_profit_shadow_mode = (
        environ.get("RBLXVALUE_VALUE_PROFIT_SHADOW_ENABLED", "").strip().casefold() == "true"
    )
    if daily_shadow_mode and value_profit_shadow_mode:
        raise RuntimeError(
            "ROBASE cash-chart shadow and Supreme value-profit shadow must run separately."
        )

    force_dry_run = daily_shadow_mode or value_profit_shadow_mode
    dry_run = force_dry_run or environ.get("DRY_RUN", "").strip().casefold() == "true"
    if not dry_run and (not bot_token or not chat_id):
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets."
        )
    testing_mode = environ.get("MM2_TESTING", "").strip().casefold() == "true"
    if value_profit_shadow_mode and cash_filter:
        raise RuntimeError(
            "Value-profit shadow requires CASH_FILTER=false; refusing mixed valuation modes."
        )
    return ScanModes(
        daily_shadow_mode=daily_shadow_mode,
        value_profit_shadow_mode=value_profit_shadow_mode,
        dry_run=dry_run,
        testing_mode=testing_mode,
        force_dry_run=force_dry_run,
    )
