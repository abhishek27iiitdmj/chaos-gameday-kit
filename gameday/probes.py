"""Steady-state probing: one HTTP request == one sample."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

from .models import Sample, SteadyState

# A probe returns (ok, latency_ms). Injectable so tests need no network.
Probe = Callable[[], tuple[bool, float]]


def http_probe(spec: SteadyState) -> Probe:
    """Build a probe that GETs `spec.url` and treats 2xx/3xx as success."""

    def probe() -> tuple[bool, float]:
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(spec.url, timeout=spec.timeout_seconds) as r:
                ok = 200 <= r.status < 400
        except (urllib.error.URLError, TimeoutError, OSError):
            ok = False
        return ok, (time.perf_counter() - start) * 1000.0

    return probe


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def is_healthy(window: Sequence[Sample], spec: SteadyState) -> bool:
    """True when a full window meets both the success-ratio and p95 targets."""
    if len(window) < spec.window:
        return False
    ratio = sum(s.ok for s in window) / len(window)
    # A failed request's latency is a timeout, so only successes count for p95.
    p95 = percentile([s.latency_ms for s in window if s.ok], 95)
    return ratio >= spec.min_success_ratio and p95 <= spec.max_p95_ms
