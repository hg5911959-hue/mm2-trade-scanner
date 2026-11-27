"""Fail-closed accounting for identified BloxSwaps feed candidates."""


def validate_candidate_accounting(
    *,
    candidate_count,
    additions,
    single_sided,
    offers_excluded,
    malformed,
    parsed,
    context="feed",
):
    """Require every identified candidate to have exactly one parser outcome."""
    fields = {
        "Ajouts": additions,
        "single-sided": single_sided,
        "Offres": offers_excluded,
        "malformed": malformed,
        "parsed": parsed,
    }
    normalized = {}
    for name, value in fields.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(
                f"[feed-accounting] Invalid {name} count in {context}: {value!r}"
            )
        normalized[name] = value
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 0:
        raise RuntimeError(
            f"[feed-accounting] Invalid candidate count in {context}: {candidate_count!r}"
        )
    classified = sum(normalized.values())
    if classified != candidate_count:
        gaps = candidate_count - classified
        raise RuntimeError(
            f"[feed-accounting] Candidate coverage mismatch in {context}: "
            f"candidates={candidate_count}, classified={classified}, unexplained={gaps}; "
            + ", ".join(f"{name}={count}" for name, count in normalized.items())
        )
    return {
        "candidate_count": candidate_count,
        "classified_count": classified,
        "classification_complete": True,
        "classification_counts": normalized,
    }
