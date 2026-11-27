"""Pure qualification gate for conservative MM2 trade opportunities."""
import math


def qualifies(result, min_diff, min_percent):
    """Return True only for verified, conservative-positive opportunities.

    Best-case-only opportunities and estimate-only cash results are never
    eligible for automated WIN alerts.
    """
    if not isinstance(result, dict):
        return False
    if result.get("manual_review") or result.get("cash_estimate_only"):
        return False
    metric = result.get("profit_worst", result.get("difference", 0))
    margin = result.get("margin")
    numeric_values = (metric, margin, min_diff, min_percent)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in numeric_values
    ):
        return False
    if min_diff < 0 or min_percent < 0:
        return False
    return metric > 0 and metric >= min_diff and margin >= min_percent
