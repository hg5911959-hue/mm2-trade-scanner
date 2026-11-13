"""Deterministic ranking and per-scan alert budget."""
def rank_and_split(opportunities, limit=15):
    ranked = sorted(opportunities, key=lambda item: (
        float(item.get("profit_worst", item.get("difference", 0))),
        float(item.get("margin", 0))), reverse=True)
    cap = max(0, int(limit))
    return (ranked if cap == 0 else ranked[:cap],
            [] if cap == 0 else ranked[cap:])
