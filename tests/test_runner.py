"""Runner tests use a fake probe and a fake clock, so they run instantly."""
import pytest

from gameday.faults import Fault, Kubectl, NetworkLatency, PodKill
from gameday.models import Experiment, FaultSpec, SteadyState
from gameday.runner import Runner


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, s):
        self.now += s

    def __call__(self):
        return self.now


class RecordingFault(Fault):
    def __init__(self, fail_inject=False):
        self.calls, self.fail_inject = [], fail_inject

    def inject(self):
        self.calls.append("inject")
        if self.fail_inject:
            raise RuntimeError("boom")

    def rollback(self):
        self.calls.append("rollback")


def make(probe_fn, fault, hold=0, max_rec=30):
    ss = SteadyState(url="http://x", window=3, interval_seconds=1)
    exp = Experiment("t", "", ss, FaultSpec("pod-kill"), hold, max_rec)
    clock = FakeClock()
    return Runner(exp, fault, probe_fn(clock), sleep=clock.sleep, clock=clock)


def outage(clock_start, clock_end):
    """Probe that fails while clock is inside [start, end)."""
    def build(clock):
        return lambda: (not (clock_start <= clock.now < clock_end), 20.0)
    return build


def test_recovers_and_measures_rto():
    fault = RecordingFault()
    # Baseline takes 3s; outage lasts until t=10; healthy window forms by ~t=13.
    res = make(outage(3, 10), fault).run()
    assert res.verdict == "PASS"
    assert 5 <= res.recovery_seconds <= 8
    assert fault.calls == ["inject", "rollback"]


def test_fails_when_never_recovers():
    res = make(outage(3, 10_000), RecordingFault()).run()
    assert res.verdict == "FAIL" and res.recovery_seconds is None


def test_aborts_if_baseline_unhealthy():
    fault = RecordingFault()
    res = make(outage(0, 10_000), fault).run()
    assert res.verdict == "ABORTED" and fault.calls == []


def test_rollback_called_when_injection_fails():
    fault = RecordingFault(fail_inject=True)
    res = make(lambda c: (lambda: (True, 5.0)), fault).run()
    assert res.verdict == "ABORTED"
    assert fault.calls == ["inject", "rollback"]


def test_hold_phase_reports_availability():
    res = make(outage(3, 8), RecordingFault(), hold=5).run()
    assert res.availability_during_fault == 0.0


class FakeKubectl(Kubectl):
    def __init__(self, out=""):
        self.cmds, self.out = [], out

    def run(self, *args, stdin=None):
        self.cmds.append((args, stdin))
        return self.out


def test_pod_kill_deletes_requested_count():
    out = '{"items":[{"metadata":{"name":"a"}},{"metadata":{"name":"b"}}]}'
    k = FakeKubectl(out)
    PodKill(k, "shop", "app=x", count=2).inject()
    deletes = [c for c, _ in k.cmds if c[0] == "delete"]
    assert len(deletes) == 2


def test_pod_kill_refuses_when_too_few_pods():
    with pytest.raises(RuntimeError):
        PodKill(FakeKubectl('{"items":[]}'), "shop", "app=x").inject()


def test_network_latency_applies_and_removes_manifest():
    k = FakeKubectl()
    f = NetworkLatency(k, "shop", {"app": "p"}, 500)
    f.inject()
    f.rollback()
    assert k.cmds[0][0][0] == "apply" and "NetworkChaos" in k.cmds[0][1]
    assert k.cmds[1][0][0] == "delete"
