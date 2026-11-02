"""End-to-end CLI test: real HTTP server, real subprocess calls, fake kubectl.

The fake `kubectl` is a tiny script placed first on PATH. When asked to delete
a pod it makes the HTTP server return 503 for a short period, which mimics a
service that is briefly unavailable while Kubernetes reschedules the pod.
"""
import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gameday.cli import main

OUTAGE_SECONDS = 1.5

FAKE_KUBECTL = """#!{python}
import json, sys, time
args = sys.argv[1:]
if args[0] == "get":
    print(json.dumps({{"items": [{{"metadata": {{"name": "pod-a"}}}}]}}))
elif args[0] == "delete":
    open({flag!r}, "w").write(str(time.time() + {outage}))
"""

EXPERIMENT = """
name: e2e-pod-kill
steady_state:
  url: {url}
  window: 3
  interval_seconds: 0.2
  timeout_seconds: 1
fault:
  type: pod-kill
  params: {{namespace: shop, selector: app=checkout}}
max_recovery_seconds: {max_rec}
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    flag = tmp_path / "down_until"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            down = flag.exists() and time.time() < float(flag.read_text())
            self.send_response(503 if down else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    bindir = tmp_path / "bin"
    bindir.mkdir()
    kubectl = bindir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL.format(python=sys.executable, flag=str(flag),
                                           outage=OUTAGE_SECONDS))
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    yield f"http://127.0.0.1:{httpd.server_port}/", tmp_path
    httpd.shutdown()


def _run(env, max_rec, tmp_path_name="exp.yaml"):
    url, tmp = env
    spec = tmp / tmp_path_name
    spec.write_text(EXPERIMENT.format(url=url, max_rec=max_rec), encoding="utf-8")
    code = main(["run", str(spec), "--yes", "--report-dir", str(tmp / "reports")])
    reports = sorted((tmp / "reports").glob("*.json"))
    return code, json.loads(reports[-1].read_text(encoding="utf-8"))


def test_pass_measures_recovery_after_outage(env):
    code, report = _run(env, max_rec=20)
    assert code == 0 and report["verdict"] == "PASS"
    # Outage is 1.5s plus a 3-sample healthy window, so recovery must be
    # noticeable but well under the target.
    assert 0.5 <= report["recovery_seconds"] <= 6
    assert any(not s["ok"] for s in report["samples"])


def test_fail_when_target_too_tight(env):
    # Window of 3 x 0.2s cannot fit inside a 0.3s target after a 1.5s outage.
    code, report = _run(env, max_rec=0.3)
    assert code == 1 and report["verdict"] == "FAIL"


def test_invalid_spec_returns_exit_code_2(env, capsys):
    _, tmp = env
    bad = tmp / "bad.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    assert main(["validate", str(bad)]) == 2
    assert "invalid experiment" in capsys.readouterr().err


def test_confirmation_prompt_can_cancel(env, monkeypatch):
    url, tmp = env
    spec = tmp / "exp.yaml"
    spec.write_text(EXPERIMENT.format(url=url, max_rec=5), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["run", str(spec)]) == 2
    assert not (tmp / "reports").exists()
