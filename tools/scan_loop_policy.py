"""Pure scheduling policy for the scanner's bounded polling loop."""


def minimum_cycle_start_window(duration_seconds, observed_cycle_max_seconds=0.0):
    """Reserve enough time to finish a cycle, adapting to observed cycle duration.

    Short sessions keep the 25% reserve. Medium sessions keep their existing
    25%/30-second floor. Long sessions use a 60–90 second baseline, then grow
    the reserve to at least twice the slowest completed cycle plus 10 seconds.
    This keeps normal feed polling longer without starting a new cycle when a
    recent slow cycle indicates that more finish time is needed.
    """
    duration = max(0.1, float(duration_seconds))
    observed_max = max(0.0, float(observed_cycle_max_seconds))
    if duration <= 30:
        base_reserve = max(0.1, duration * 0.25)
    elif duration <= 300:
        base_reserve = max(30.0, duration * 0.25)
    else:
        base_reserve = min(90.0, max(60.0, duration * 0.125))
    adaptive_reserve = observed_max * 2.0 + 10.0 if observed_max > 0 else 0.0
    return max(base_reserve, adaptive_reserve)


def should_start_scan_cycle(remaining_seconds, duration_seconds, observed_cycle_max_seconds=0.0):
    """Do not start a cycle without its adaptive finish buffer."""
    return float(remaining_seconds) >= minimum_cycle_start_window(
        duration_seconds, observed_cycle_max_seconds
    )


def should_run_backlog_recheck(cycle_number, every):
    """Run expensive pending/watch work at a fixed, positive cycle cadence."""
    cadence = max(1, int(every))
    return int(cycle_number) > 0 and int(cycle_number) % cadence == 0
