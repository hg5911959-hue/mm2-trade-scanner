"""Pure scheduling policy for the scanner's bounded polling loop."""


def minimum_cycle_start_window(duration_seconds):
    """Reserve a bounded finish buffer before starting another scan cycle.

    Short sessions reserve 25% with a 0.1-second floor. Medium sessions reserve
    25% with a 30-second floor. Long sessions reserve between 120 and 180 seconds.
    """
    duration = max(0.1, float(duration_seconds))
    if duration <= 30:
        return max(0.1, duration * 0.25)
    if duration <= 300:
        return max(30.0, duration * 0.25)
    return min(180.0, max(120.0, duration * 0.25))


def should_start_scan_cycle(remaining_seconds, duration_seconds):
    """Do not start a cycle when there is insufficient time to finish it."""
    return float(remaining_seconds) >= minimum_cycle_start_window(duration_seconds)


def should_run_backlog_recheck(cycle_number, every):
    """Run expensive pending/watch work at a fixed, positive cycle cadence."""
    cadence = max(1, int(every))
    return int(cycle_number) > 0 and int(cycle_number) % cadence == 0
