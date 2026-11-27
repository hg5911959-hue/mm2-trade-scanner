"""Bounded scan-loop orchestration, independent of scanner implementation details."""
from __future__ import annotations

import signal as _signal
import time as _time


class BloxSwapsRateLimitError(RuntimeError):
    """A BloxSwaps HTTP 429 stops the current scan without retrying."""


def run_scan_loop(
    duration_seconds=720,
    interval_seconds=45,
    *,
    scan_once,
    should_start_scan_cycle,
    should_run_backlog_recheck,
    reset_alert_budget,
    get_alert_budget,
    record_failure,
    redact,
    rblxvalue_guard,
    update_metrics,
    time_module=None,
    signal_module=None,
):
    """Poll repeatedly while delegating scanner-specific behavior through callbacks."""
    clock = time_module or _time
    signals = signal_module or _signal
    duration_seconds = max(1, int(duration_seconds))
    interval_seconds = max(30, min(60, int(interval_seconds)))
    reset_alert_budget()
    started = clock.monotonic()
    deadline = started + duration_seconds
    stop = {"requested": False}
    previous_handlers = {}

    def request_stop(signum, frame):
        stop["requested"] = True
        print(
            f"[loop] Graceful shutdown requested (signal={signum}); "
            "finishing current scan cycle."
        )

    for sig in (signals.SIGTERM, signals.SIGINT):
        try:
            previous_handlers[sig] = signals.getsignal(sig)
            signals.signal(sig, request_stop)
        except (ValueError, OSError):
            pass

    cycles = 0
    successful_cycles = 0
    last_failure = None
    try:
        while not stop["requested"] and should_start_scan_cycle(
            deadline - clock.monotonic(), duration_seconds
        ):
            cycle_started = clock.monotonic()
            cycles += 1
            print(f"[loop] Starting scan cycle {cycles}; elapsed={cycle_started - started:.1f}s")
            try:
                include_backlog = should_run_backlog_recheck(cycles)
                scan_once(include_backlog_rechecks=include_backlog)
                successful_cycles += 1
            except BloxSwapsRateLimitError as exc:
                last_failure = exc
                print(
                    f"[loop] Upstream rate limit in cycle {cycles}; stopping the polling window: "
                    f"{redact(exc)[:240]}"
                )
                break
            except Exception as exc:
                last_failure = exc
                print(
                    f"[loop] Scan cycle {cycles} failed safely: {type(exc).__name__}; "
                    f"reason={redact(exc)[:240]}"
                )
                record_failure(exc)
            cycle_elapsed = clock.monotonic() - cycle_started
            remaining = deadline - clock.monotonic()
            alerts_sent, alert_limit = get_alert_budget()
            print(
                f"[loop] Cycle {cycles} finished in {cycle_elapsed:.1f}s; "
                f"alerts_this_session={alerts_sent}/{alert_limit}; "
                f"remaining={max(0, remaining):.1f}s"
            )
            sleep_for = min(
                max(0.0, interval_seconds - cycle_elapsed),
                max(0.0, remaining),
            )
            sleep_until = clock.monotonic() + sleep_for
            while not stop["requested"] and clock.monotonic() < sleep_until:
                clock.sleep(min(1.0, sleep_until - clock.monotonic()))
    finally:
        for sig, handler in previous_handlers.items():
            try:
                signals.signal(sig, handler)
            except (ValueError, OSError):
                pass

    elapsed = clock.monotonic() - started
    rblxvalue_guard.log_request_count()
    update_metrics(increments={"rblxvalue_requests": rblxvalue_guard.requests_this_run()})
    print(
        f"[loop] Finished: cycles={cycles}, successful={successful_cycles}, "
        f"elapsed={elapsed:.1f}s"
    )
    if isinstance(last_failure, BloxSwapsRateLimitError):
        raise last_failure
    if successful_cycles == 0:
        if last_failure and (
            "[rblxvalue-key-blocked]" in str(last_failure)
            or "[rblxvalue-budget-exhausted]" in str(last_failure)
        ):
            raise RuntimeError(str(last_failure))
        raise RuntimeError(
            "No scan cycle completed successfully during the scheduled polling window."
        )
    return {
        "cycles": cycles,
        "successful_cycles": successful_cycles,
        "elapsed_seconds": elapsed,
    }
