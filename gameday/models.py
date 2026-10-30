"""Data models for experiments and results.

An experiment follows the classic chaos-engineering loop:
  1. verify the steady-state hypothesis (baseline)
  2. inject a fault
  3. hold it for `hold_seconds`
  4. release the fault
  5. measure how long the system takes to return to steady state
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


class SpecError(ValueError):
    """Raised when an experiment file is malformed."""


@dataclass(frozen=True)
class SteadyState:
    """Thresholds that define 'healthy', evaluated over a sliding window."""

    url: str
    timeout_seconds: float = 2.0
    interval_seconds: float = 1.0
    window: int = 10                 # number of samples per evaluation
    min_success_ratio: float = 0.99  # fraction of 2xx/3xx responses
    max_p95_ms: float = 500.0


@dataclass(frozen=True)
class FaultSpec:
    type: str                        # pod-kill | zone-drain | network-latency
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Experiment:
    name: str
    description: str
    steady_state: SteadyState
    fault: FaultSpec
    hold_seconds: float = 0.0        # 0 for instantaneous faults (pod-kill)
    max_recovery_seconds: float = 300.0  # the RTO target under test


def _require(data: dict, key: str, ctx: str) -> Any:
    if key not in data:
        raise SpecError(f"{ctx}: missing required field '{key}'")
    return data[key]


def load_experiment(path: str) -> Experiment:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: expected a mapping at top level")

    ss = _require(raw, "steady_state", path)
    fault = _require(raw, "fault", path)
    exp = Experiment(
        name=_require(raw, "name", path),
        description=raw.get("description", ""),
        steady_state=SteadyState(
            url=_require(ss, "url", "steady_state"),
            **{k: ss[k] for k in (
                "timeout_seconds", "interval_seconds", "window",
                "min_success_ratio", "max_p95_ms") if k in ss},
        ),
        fault=FaultSpec(type=_require(fault, "type", "fault"),
                        params=fault.get("params", {})),
        hold_seconds=float(raw.get("hold_seconds", 0)),
        max_recovery_seconds=float(raw.get("max_recovery_seconds", 300)),
    )
    if exp.steady_state.window < 1:
        raise SpecError("steady_state.window must be >= 1")
    if not 0 < exp.steady_state.min_success_ratio <= 1:
        raise SpecError("steady_state.min_success_ratio must be in (0, 1]")
    return exp


@dataclass
class Sample:
    t: float            # seconds since experiment start
    ok: bool
    latency_ms: float


@dataclass
class Result:
    experiment: str
    verdict: str                      # PASS | FAIL | ABORTED
    reason: str
    recovery_seconds: float | None    # measured RTO; None if never recovered
    max_recovery_seconds: float
    availability_during_fault: float | None
    samples: list[Sample] = field(default_factory=list)
    phases: dict[str, tuple[float, float]] = field(default_factory=dict)
