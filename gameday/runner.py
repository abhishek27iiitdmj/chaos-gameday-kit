"""Experiment orchestration and recovery-time measurement."""
from __future__ import annotations

import contextlib
import time
from collections import deque
from collections.abc import Callable

from .faults import Fault
from .models import Experiment, Result, Sample
from .probes import Probe, is_healthy


class Runner:
    def __init__(self, exp: Experiment, fault: Fault, probe: Probe,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.exp, self.fault, self.probe = exp, fault, probe
        self._sleep, self._clock = sleep, clock
        self._t0 = 0.0
        self.samples: list[Sample] = []
        self._window: deque[Sample] = deque(maxlen=exp.steady_state.window)

    def _sample(self) -> Sample:
        ok, latency = self.probe()
        s = Sample(t=self._clock() - self._t0, ok=ok, latency_ms=latency)
        self.samples.append(s)
        self._window.append(s)
        self._sleep(self.exp.steady_state.interval_seconds)
        return s

    def _healthy(self) -> bool:
        return is_healthy(list(self._window), self.exp.steady_state)

    def run(self) -> Result:
        exp, ss = self.exp, self.exp.steady_state
        self._t0 = self._clock()
        phases: dict[str, tuple[float, float]] = {}

        def result(verdict, reason, rec=None, avail=None) -> Result:
            return Result(exp.name, verdict, reason, rec, exp.max_recovery_seconds,
                          avail, self.samples, phases)

        # 1. Baseline: never inject into a system that is already unhealthy.
        start = self._clock() - self._t0
        for _ in range(ss.window):
            self._sample()
        phases["baseline"] = (start, self._clock() - self._t0)
        if not self._healthy():
            return result("ABORTED", "steady state not met before injection")

        # 2-4. Inject, hold, release. Rollback is guaranteed by `finally`.
        inject_at = self._clock() - self._t0
        released = False
        try:
            self.fault.inject()
            hold_end = self._clock() - self._t0 + exp.hold_seconds
            while self._clock() - self._t0 < hold_end:
                self._sample()
            self.fault.rollback()
            released = True
        except Exception as exc:  # noqa: BLE001 - report any injector failure
            return result("ABORTED", f"fault injection failed: {exc}")
        finally:
            if not released:
                with contextlib.suppress(Exception):  # best effort on the error path
                    self.fault.rollback()
        release_at = self._clock() - self._t0
        phases["fault"] = (inject_at, release_at)

        during = [s for s in self.samples if inject_at <= s.t < release_at]
        avail = (sum(s.ok for s in during) / len(during)) if during else None

        # 5. Recovery: the RTO is measured from release to the first healthy window.
        self._window.clear()
        while self._clock() - self._t0 - release_at < exp.max_recovery_seconds:
            self._sample()
            if self._healthy():
                # The window is full of healthy samples, so recovery happened
                # when its oldest sample was taken.
                recovered = self._window[0].t - release_at
                phases["recovery"] = (release_at, self._clock() - self._t0)
                rec = max(0.0, recovered)
                return result("PASS", f"recovered in {rec:.1f}s "
                              f"(target {exp.max_recovery_seconds:.0f}s)", rec, avail)

        phases["recovery"] = (release_at, self._clock() - self._t0)
        return result("FAIL", f"no recovery within {exp.max_recovery_seconds:.0f}s",
                      None, avail)
