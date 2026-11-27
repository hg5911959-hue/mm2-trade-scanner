"""Per-scan terminal outcome accounting for parsed trade listings."""


class ScanOutcomeLedger:
    """Require exactly one terminal outcome for every parsed listing."""

    PENDING = "qualified_pending_delivery"
    ALLOWED = frozenset({
        PENDING,
        "already_alerted",
        "delivery_quarantined",
        "watch_unchanged",
        "watch_missing_cash_price",
        "watch_unmapped",
        "dry_run_estimate",
        "watch_not_qualified",
        "alert_sent",
        "dry_run_preview",
        "delivery_failed_queued",
        "delivery_guard_skipped",
    })

    def __init__(self, listing_ids):
        self.expected = {str(value) for value in listing_ids}
        if "" in self.expected:
            raise RuntimeError("[outcome-ledger] A parsed listing has no stable ID.")
        self.outcomes = {}

    def record(self, listing_id, outcome):
        key = str(listing_id)
        if key not in self.expected:
            raise RuntimeError(
                f"[outcome-ledger] Outcome references an unknown listing ID: {key}"
            )
        if outcome not in self.ALLOWED or outcome == self.PENDING and key in self.outcomes:
            raise RuntimeError(
                f"[outcome-ledger] Invalid or duplicate outcome for listing {key}: {outcome}"
            )
        previous = self.outcomes.get(key)
        if previous is None:
            self.outcomes[key] = outcome
            return
        if previous == self.PENDING and outcome != self.PENDING:
            self.outcomes[key] = outcome
            return
        raise RuntimeError(
            f"[outcome-ledger] Listing {key} already has outcome {previous}; "
            f"refusing second outcome {outcome}."
        )

    def finalize(self):
        missing = sorted(self.expected - set(self.outcomes))
        pending = sorted(
            key for key, value in self.outcomes.items() if value == self.PENDING
        )
        if missing or pending:
            raise RuntimeError(
                "[outcome-ledger] Scan ended without terminal outcomes: "
                f"missing={len(missing)} pending_delivery={len(pending)}; "
                f"missing_ids={missing[:8]} pending_ids={pending[:8]}"
            )
        counts = {}
        for outcome in self.outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1
        return dict(sorted(counts.items()))
