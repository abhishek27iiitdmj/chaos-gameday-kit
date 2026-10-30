"""Fault injectors. Each one is reversible via `rollback()`.

All cluster access goes through `Kubectl`, which shells out to the kubectl
already configured on the machine (no credentials are handled here).
"""
from __future__ import annotations

import json
import random
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from .models import FaultSpec, SpecError


class Kubectl:
    """Thin, injectable wrapper around the kubectl binary."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._run = runner

    def run(self, *args: str, stdin: str | None = None) -> str:
        proc = self._run(["kubectl", *args], input=stdin, capture_output=True,
                         text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout


class Fault(ABC):
    @abstractmethod
    def inject(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None:
        """Must be idempotent: it is also called from the error path."""


class PodKill(Fault):
    """Delete `count` random pods matching a label selector."""

    def __init__(self, kubectl: Kubectl, namespace: str, selector: str, count: int = 1,
                 rng: random.Random | None = None):
        self.k, self.ns, self.selector, self.count = kubectl, namespace, selector, count
        self.rng = rng or random.Random()

    def inject(self) -> None:
        out = self.k.run("get", "pods", "-n", self.ns, "-l", self.selector,
                         "--field-selector=status.phase=Running", "-o", "json")
        names = [i["metadata"]["name"] for i in json.loads(out)["items"]]
        if len(names) < self.count:
            raise RuntimeError(f"only {len(names)} running pods match '{self.selector}'")
        for name in self.rng.sample(names, self.count):
            # --wait=false returns immediately so recovery timing starts now.
            self.k.run("delete", "pod", name, "-n", self.ns, "--wait=false")

    def rollback(self) -> None:
        """Nothing to undo: Kubernetes reschedules deleted pods by itself."""


class ZoneDrain(Fault):
    """Simulate losing an availability zone: cordon and drain all its nodes."""

    ZONE_LABEL = "topology.kubernetes.io/zone"

    def __init__(self, kubectl: Kubectl, zone: str):
        self.k, self.zone = kubectl, zone
        self._nodes: list[str] = []

    def inject(self) -> None:
        out = self.k.run("get", "nodes", "-l", f"{self.ZONE_LABEL}={self.zone}",
                         "-o", "jsonpath={.items[*].metadata.name}")
        self._nodes = out.split()
        if not self._nodes:
            raise RuntimeError(f"no nodes found in zone '{self.zone}'")
        for node in self._nodes:  # cordon everything first so pods cannot reland
            self.k.run("cordon", node)
        for node in self._nodes:
            self.k.run("drain", node, "--ignore-daemonsets", "--delete-emptydir-data",
                       "--force", "--timeout=120s")

    def rollback(self) -> None:
        for node in self._nodes:
            self.k.run("uncordon", node)
        self._nodes = []


class NetworkLatency(Fault):
    """Add latency to matching pods through a Chaos Mesh NetworkChaos resource."""

    def __init__(self, kubectl: Kubectl, namespace: str, selector: dict[str, str],
                 latency_ms: int, jitter_ms: int = 0, name: str = "gameday-latency"):
        self.k, self.ns, self.name = kubectl, namespace, name
        self.manifest: dict[str, Any] = {
            "apiVersion": "chaos-mesh.org/v1alpha1",
            "kind": "NetworkChaos",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {
                "action": "delay",
                "mode": "all",
                "selector": {"namespaces": [namespace], "labelSelectors": selector},
                "delay": {"latency": f"{latency_ms}ms", "jitter": f"{jitter_ms}ms"},
            },
        }

    def inject(self) -> None:
        # kubectl accepts JSON, which is a subset of YAML.
        self.k.run("apply", "-f", "-", stdin=json.dumps(self.manifest))

    def rollback(self) -> None:
        self.k.run("delete", "networkchaos", self.name, "-n", self.ns,
                   "--ignore-not-found=true")


def build_fault(spec: FaultSpec, kubectl: Kubectl) -> Fault:
    p = spec.params
    try:
        if spec.type == "pod-kill":
            return PodKill(kubectl, p["namespace"], p["selector"], int(p.get("count", 1)))
        if spec.type == "zone-drain":
            return ZoneDrain(kubectl, p["zone"])
        if spec.type == "network-latency":
            return NetworkLatency(kubectl, p["namespace"], p["selector"],
                                  int(p["latency_ms"]), int(p.get("jitter_ms", 0)))
    except KeyError as exc:
        raise SpecError(f"fault '{spec.type}' is missing param {exc}") from exc
    raise SpecError(f"unknown fault type '{spec.type}'")
