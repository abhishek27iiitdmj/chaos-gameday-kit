"""Command line interface: `gameday validate|run <experiment.yaml>`."""
from __future__ import annotations

import argparse
import sys

from .faults import Kubectl, build_fault
from .models import SpecError, load_experiment
from .probes import http_probe
from .report import to_markdown, write_reports
from .runner import Runner

EXIT_CODES = {"PASS": 0, "FAIL": 1, "ABORTED": 2}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gameday", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "run"):
        p = sub.add_parser(name)
        p.add_argument("experiment")
    sub.choices["run"].add_argument("--report-dir", default="reports")
    sub.choices["run"].add_argument("--yes", action="store_true",
                                    help="skip the confirmation prompt")
    args = ap.parse_args(argv)

    try:
        exp = load_experiment(args.experiment)
        fault = build_fault(exp.fault, Kubectl())
    except (SpecError, OSError) as exc:
        print(f"invalid experiment: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "validate":
        print(f"OK: {exp.name} ({exp.fault.type}, RTO target {exp.max_recovery_seconds:.0f}s)")
        return 0

    if not args.yes:
        answer = input(f"Inject '{exp.fault.type}' into the CURRENT kubectl context? [y/N] ")
        if answer.strip().lower() != "y":
            print("cancelled")
            return 2
    result = Runner(exp, fault, http_probe(exp.steady_state)).run()
    json_path, md_path = write_reports(result, args.report_dir)
    print(to_markdown(result))
    print(f"reports: {json_path}, {md_path}")
    return EXIT_CODES[result.verdict]


if __name__ == "__main__":
    sys.exit(main())
