"""Safe reconciliation for Telegram sends with durable, confirmed delivery evidence."""


def reconcile_uncertain_quarantine(uncertain_state, alerted_ids, context):
    """Clear only quarantines whose IDs are already confirmed in sent history.

    A confirmed ID is safe to remove because the scanner writes it to alerted
    state only after Telegram confirms the send. Unknown IDs remain quarantined.
    Context rows without a matching quarantine are stale metadata and are pruned.
    """
    state = dict(uncertain_state or {})
    original_ids = set(state)
    alerted = {str(value) for value in (alerted_ids or []) if str(value)}
    confirmed = sorted(original_ids & alerted)
    for trade_id in confirmed:
        state.pop(trade_id, None)

    details = dict(context or {})
    orphaned = sorted(set(details) - original_ids)
    for trade_id in set(confirmed) | set(orphaned):
        details.pop(trade_id, None)

    return state, details, {
        "confirmed_quarantines_cleared": confirmed,
        "orphan_contexts_removed": orphaned,
    }
