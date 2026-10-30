"""Render a Result as JSON (machines) and Markdown (humans)."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone

from .models import Result


def to_markdown(r: Result) -> str:
    rec = f"{r.recovery_seconds:.1f}s" if r.recovery_seconds is not None else "not recovered"
    avail = f"{r.availability_during_fault:.1%}" if r.availability_during_fault is not None else "n/a"
    failed = sum(not s.ok for s in r.samples)
    lines = [
        f"# GameDay report: {r.experiment}",
        "",
        f"**Verdict:** {r.verdict}  ",
        f"**Reason:** {r.reason}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Measured recovery time (RTO) | {rec} |",
        f"| RTO target | {r.max_recovery_seconds:.0f}s |",
        f"| Availability while fault active | {avail} |",
        f"| Samples (failed / total) | {failed} / {len(r.samples)} |",
        "",
        "## Phases (seconds since start)",
        "",
    ]
    lines += [f"- {name}: {a:.1f} -> {b:.1f}" for name, (a, b) in r.phases.items()]
    return "\n".join(lines) + "\n"


def write_reports(r: Result, directory: str) -> tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(directory, f"{r.experiment}-{stamp}")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(dataclasses.asdict(r), fh, indent=2)
    with open(base + ".md", "w", encoding="utf-8") as fh:
        fh.write(to_markdown(r))
    return base + ".json", base + ".md"
