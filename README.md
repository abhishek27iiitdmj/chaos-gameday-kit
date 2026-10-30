# chaos-gameday-kit

Reproducible failure experiments for Kubernetes that end with a **measured
recovery time (RTO)** instead of an opinion.

Each experiment is a YAML file: a steady-state definition, a fault, and a
recovery target. The runner verifies the baseline, injects the fault, releases
it, and reports how long the service took to become healthy again.

## Experiments included

| File | Fault | Needs |
|---|---|---|
| `experiments/pod-kill.yaml` | Delete random pods by label | kubectl |
| `experiments/zone-drain.yaml` | Cordon and drain every node in an AZ | kubectl, cluster admin |
| `experiments/network-latency.yaml` | Add latency to pods | kubectl, [Chaos Mesh](https://chaos-mesh.org) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # no cluster needed

gameday validate experiments/pod-kill.yaml
gameday run experiments/pod-kill.yaml    # uses your CURRENT kubectl context
```

Edit the `url`, `namespace` and `selector` values first; the bundled values
are placeholders. Try it on a kind cluster or a staging environment, never
first on production.

## Example report

```
Verdict: PASS
Reason: recovered in 7.0s (target 60s)
| Measured recovery time (RTO) | 7.0s |
| Availability while fault active | 100.0% |
```

Reports are written to `reports/` as Markdown and JSON. Exit codes:
`0` PASS, `1` FAIL, `2` ABORTED, so it works in CI and scheduled GameDays.

## Status

The runner logic and the pod-kill / network-latency command construction are
unit-tested with a fake clock, probe and kubectl. The faults have **not yet
been run against a real cluster**; validate on kind or staging first.

## Layout

```
gameday/models.py   experiment schema and loader
gameday/probes.py   HTTP probe and steady-state evaluation
gameday/faults.py   pod-kill, zone-drain, network-latency
gameday/runner.py   baseline -> inject -> release -> measure recovery
gameday/report.py   Markdown and JSON output
gameday/cli.py      `gameday validate | run`
```

See [docs/design.md](docs/design.md) for definitions, safety and limitations.

## Security

No credentials, tokens or account IDs are stored. The tool only uses the
kubeconfig already on your machine.

## Roadmap

- Prometheus-based steady state (SLO burn rate)
- Write-probe to estimate RPO for stateful services
- Scheduled GameDay workflow with report upload
- End-to-end test on a kind cluster in CI

Independent reference implementation; it contains no employer code or data.

## License

MIT
