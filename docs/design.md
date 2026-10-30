# Design notes

## Goal
Turn "we are resilient" into a number: **measured recovery time** after a
specific failure, compared with a declared target (`max_recovery_seconds`).

## Flow
```
baseline window healthy? --no--> ABORTED (never inject into a sick system)
        | yes
     inject fault -> hold (hold_seconds) -> rollback
        |
   sample until a full window is healthy again
        |
   PASS if recovered <= target, else FAIL
```

## Definitions
- **Steady state**: over a sliding window of `window` HTTP probes, the success
  ratio is >= `min_success_ratio` and p95 latency of successes is <= `max_p95_ms`.
- **Recovery time**: seconds from fault release to the *oldest* sample of the
  first fully healthy window (so the sliding window does not inflate the figure).
  For instantaneous faults (pod-kill) `hold_seconds` is 0, so this is time from
  injection.
- **Availability during fault**: share of successful probes while the fault is held.

## Safety
- Rollback runs in a `finally` path, even when injection fails half way.
- The CLI asks for confirmation and names the kubectl context it will use.
- The kit holds no credentials; it relies on the operator's kubeconfig.
- Faults are scoped by namespace, label selector, or zone, never cluster-wide.

## Testability
Clock, sleep, probe and kubectl are injected, so the whole loop is unit-tested
without a cluster or real waiting.

## Limitations
- One HTTP endpoint is the steady-state signal; it does not read Prometheus yet.
- RPO is not measured (needs a data-write probe; see roadmap).
- `zone-drain` simulates AZ loss at the scheduler level, not at the network level.
- Not yet exercised against a real cluster (unit tests use fakes).
