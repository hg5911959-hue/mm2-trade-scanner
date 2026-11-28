from __future__ import annotations

from test_support import IsolatedDataTestCase
from tools.scan_loop import BloxSwapsRateLimitError, run_scan_loop


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


class FakeSignals:
    SIGTERM = 15
    SIGINT = 2

    def __init__(self):
        self.handlers = {self.SIGTERM: "term-handler", self.SIGINT: "int-handler"}

    def getsignal(self, signum):
        return self.handlers[signum]

    def signal(self, signum, handler):
        self.handlers[signum] = handler


class FakeGuard:
    def __init__(self):
        self.logged = 0

    def log_request_count(self):
        self.logged += 1

    def requests_this_run(self):
        return 7


class ScanLoopTests(IsolatedDataTestCase):
    def test_successful_cycle_preserves_budget_metrics_and_shutdown_handlers(self):
        clock = FakeClock()
        signals = FakeSignals()
        guard = FakeGuard()
        scans = []
        resets = []
        metrics = []
        start_checks = iter([True, False])

        result = run_scan_loop(
            duration_seconds=120,
            interval_seconds=30,
            scan_once=lambda **kwargs: scans.append(kwargs),
            should_start_scan_cycle=lambda remaining, duration, observed_max: next(start_checks),
            should_run_backlog_recheck=lambda cycle: cycle == 4,
            reset_alert_budget=lambda: resets.append(True),
            get_alert_budget=lambda: (2, 5),
            record_failure=lambda exc: self.fail("record_failure should not run"),
            redact=str,
            rblxvalue_guard=guard,
            update_metrics=lambda **kwargs: metrics.append(kwargs),
            time_module=clock,
            signal_module=signals,
        )

        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["successful_cycles"], 1)
        self.assertEqual(result["elapsed_seconds"], 30.0)
        self.assertEqual(scans, [{"include_backlog_rechecks": False}])
        self.assertEqual(resets, [True])
        self.assertEqual(guard.logged, 1)
        self.assertEqual(metrics, [{"increments": {"rblxvalue_requests": 7}}])
        self.assertEqual(signals.handlers[signals.SIGTERM], "term-handler")
        self.assertEqual(signals.handlers[signals.SIGINT], "int-handler")

    def test_loop_passes_slowest_completed_cycle_to_adaptive_policy(self):
        clock = FakeClock()
        signals = FakeSignals()
        guard = FakeGuard()
        observed = []

        def scan_once(**kwargs):
            clock.now += 80.0

        def should_start(remaining, duration, observed_max):
            observed.append(observed_max)
            return len(observed) == 1

        result = run_scan_loop(
            duration_seconds=120,
            interval_seconds=30,
            scan_once=scan_once,
            should_start_scan_cycle=should_start,
            should_run_backlog_recheck=lambda cycle: False,
            reset_alert_budget=lambda: None,
            get_alert_budget=lambda: (0, 0),
            record_failure=lambda exc: self.fail("record_failure should not run"),
            redact=str,
            rblxvalue_guard=guard,
            update_metrics=lambda **kwargs: None,
            time_module=clock,
            signal_module=signals,
        )

        self.assertEqual(observed, [0.0, 80.0])
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["elapsed_seconds"], 80.0)

    def test_rate_limit_exception_stops_after_one_cycle_and_is_reraised(self):
        clock = FakeClock()
        signals = FakeSignals()
        guard = FakeGuard()
        failure = BloxSwapsRateLimitError("429")
        scans = []

        def scan_once(**kwargs):
            scans.append(kwargs)
            raise failure

        with self.assertRaisesRegex(RuntimeError, "429"):
            run_scan_loop(
                duration_seconds=120,
                interval_seconds=30,
                scan_once=scan_once,
                should_start_scan_cycle=lambda remaining, duration, observed_max: True,
                should_run_backlog_recheck=lambda cycle: False,
                reset_alert_budget=lambda: None,
                get_alert_budget=lambda: (0, 0),
                record_failure=lambda exc: self.fail("rate limits must not be recorded as ordinary failures"),
                redact=str,
                rblxvalue_guard=guard,
                update_metrics=lambda **kwargs: None,
                time_module=clock,
                signal_module=signals,
            )

        self.assertEqual(len(scans), 1)
        self.assertEqual(guard.logged, 1)
        self.assertEqual(clock.now, 0.0)


if __name__ == "__main__":
    import unittest

    unittest.main()
