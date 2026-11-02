"""Tests for the real HTTP probe, report writing and spec validation."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gameday.models import (
    Result,
    Sample,
    SpecError,
    SteadyState,
    load_experiment,
)
from gameday.probes import http_probe, is_healthy, percentile
from gameday.report import to_markdown, write_reports


class _Handler(BaseHTTPRequestHandler):
    """Serves 200 on /ok and 503 on every other path."""

    def do_GET(self):
        self.send_response(200 if self.path == "/ok" else 503)
        self.end_headers()

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_http_probe_success_and_failure(server):
    ok, latency = http_probe(SteadyState(url=f"{server}/ok"))()
    assert ok and latency >= 0
    ok, _ = http_probe(SteadyState(url=f"{server}/down"))()
    assert not ok


def test_http_probe_connection_refused():
    # Port 1 is reserved and nothing listens there.
    ok, _ = http_probe(SteadyState(url="http://127.0.0.1:1/", timeout_seconds=0.5))()
    assert not ok


def test_percentile_nearest_rank():
    assert percentile([], 95) == 0.0
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile(list(range(1, 101)), 95) == 95


def test_is_healthy_needs_full_window_and_ratio():
    spec = SteadyState(url="x", window=4, min_success_ratio=0.75, max_p95_ms=100)
    good = [Sample(i, True, 10) for i in range(4)]
    assert is_healthy(good, spec)
    assert not is_healthy(good[:3], spec)                       # window not full
    one_bad = good[:3] + [Sample(3, False, 10)]
    assert is_healthy(one_bad, spec)                            # 75% >= 75%
    two_bad = good[:2] + [Sample(2, False, 10), Sample(3, False, 10)]
    assert not is_healthy(two_bad, spec)
    slow = [Sample(i, True, 500) for i in range(4)]
    assert not is_healthy(slow, spec)                           # p95 too high


def _result(**kw):
    base = {"experiment": "demo", "verdict": "PASS", "reason": "ok",
            "recovery_seconds": 4.2, "max_recovery_seconds": 60,
            "availability_during_fault": 0.5,
            "samples": [Sample(0, True, 5), Sample(1, False, 5)],
            "phases": {"fault": (0.0, 1.0)}}
    base.update(kw)
    return Result(**base)


def test_markdown_contains_key_metrics():
    md = to_markdown(_result())
    assert "PASS" in md and "4.2s" in md and "50.0%" in md and "1 / 2" in md
    assert "not recovered" in to_markdown(_result(recovery_seconds=None, verdict="FAIL"))


def test_write_reports_creates_valid_json(tmp_path):
    json_path, md_path = write_reports(_result(), str(tmp_path / "out"))
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["experiment"] == "demo" and len(data["samples"]) == 2
    with open(md_path, encoding="utf-8") as fh:
        assert fh.read().startswith("# GameDay report")


def _write(tmp_path, text):
    p = tmp_path / "e.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


VALID = """
name: t
steady_state: {url: "http://x"}
fault: {type: pod-kill, params: {namespace: a, selector: app=b}}
"""


def test_load_valid_experiment_applies_defaults(tmp_path):
    exp = load_experiment(_write(tmp_path, VALID))
    assert exp.max_recovery_seconds == 300 and exp.steady_state.window == 10


@pytest.mark.parametrize("text,msg", [
    ("- just\n- a list\n", "mapping"),
    ("name: t\nfault: {type: pod-kill}\n", "steady_state"),
    ("name: t\nsteady_state: {url: x}\n", "fault"),
    ("steady_state: {url: x}\nfault: {type: pod-kill}\n", "name"),
    ("name: t\nsteady_state: {window: 3}\nfault: {type: pod-kill}\n", "url"),
    ("name: t\nsteady_state: {url: x, window: 0}\nfault: {type: pod-kill}\n", "window"),
    (("name: t\nsteady_state: {url: x, min_success_ratio: 1.5}\n"
      "fault: {type: pod-kill}\n"), "min_success_ratio"),
])
def test_invalid_specs_raise(tmp_path, text, msg):
    with pytest.raises(SpecError, match=msg):
        load_experiment(_write(tmp_path, text))
