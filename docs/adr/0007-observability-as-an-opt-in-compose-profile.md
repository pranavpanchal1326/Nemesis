# 0007 — Observability runs as an opt-in compose profile

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** SRE
- **Blueprint:** §24.3, §27.1, §41

## Context

Phase 1a adds five observability services — OpenTelemetry collector, Tempo,
Prometheus, Alertmanager, Grafana — to a stack that already runs six.

The constraint is arithmetic, and it is not close enough to ignore. The
application services sum to 6336 MB inside an 8192 MB WSL2 cap (`docs/HARDWARE.md`).
The observability services add 992 MB, landing at 7328 MB. That fits, but it
leaves under 900 MB of headroom on a 16 GB machine that is also expected to run
host-side Ollama (~5.5 GB of VRAM but real system RAM too), a browser with a
WebGL context, and an editor.

RAM, not compute, is the binding constraint on this machine — that is the first
finding in the hardware profile, and every allocation decision follows from it.

The failure this creates is not a warning. `worker-ml` is the largest consumer
at 3 GB and the one holding CLIP, Whisper, and e5 weights simultaneously; under
pressure it is what the OOM killer reaches for, and the symptom is inference
failing mid-pipeline for reasons that look like a model problem.

## Decision

The five observability services are declared with `profiles: [obs]` and are
**not started by `docker compose up` or `nem up`**. They start with `nem obs`,
which also sets `NEMESIS_OTEL__ENABLED` and `NEMESIS_OTEL__ENDPOINT` in the
compose invocation so the backend services are recreated with trace export on.
`nem obs-down` reverses both halves.

Trace export defaults to off for a second, independent reason: a
`BatchSpanProcessor` pointed at an absent collector retries on a background
thread and writes an export error every few seconds forever — a log full of
noise about the tool that was supposed to reduce noise.

## Alternatives considered

**Run observability by default.** Rejected on the arithmetic above. It would
mean every `nem up` competes with the demo for memory, and it would make the
default developer experience slower for a capability most sessions do not use.

**A second compose file (`docker-compose.observability.yml`) with `-f`.**
Rejected: it splits the volume and network definitions across two files and
makes every command longer. Profiles are the mechanism compose provides for
exactly this, and `nem obs` hides the flag anyway.

**Drop Tempo and keep metrics only.** Tempo is the largest single addition at
256 MB. Rejected because Phase 0 deliberately instrumented context propagation
across the HTTP → Celery → agent boundary, and the §27.1 question — "why did
this complaint take 90 seconds" — is a trace question that no metric can answer.
Instrumenting the hard part and then discarding the output would make the Phase
0 work decorative.

**Hosted observability (Grafana Cloud, Honeycomb).** Rejected as
provider-shaped: it is a Phase 1b decision coupled to the deploy target, and the
whole premise of the 1a/1b split is not to make provider decisions early.
It also breaks the air-gap property the Phase 0 gate verified.

## Consequences

**Easier:** the default stack stays inside a comfortable memory envelope; the
observability stack is reproducible from a clean checkout; a developer who does
not need dashboards never pays for them.

**Harder:** signals are only visible when someone opts in, so a problem
occurring during a default run leaves no metric history. Prometheus retention is
7 days *of the time the profile was running*, which is not the same as 7 days.

**Committed to:** `nem obs-verify` as the gate that proves the chain works, since
an opt-in stack is one that can rot unnoticed between uses. And to revisiting
this the moment there is an always-on environment — in Phase 1b, "opt-in
observability" stops being a trade-off and becomes a bug.

## Revisit when

A deploy target is chosen (Phase 1b), or the reference machine gains RAM. In a
deployed environment the observability stack is not optional and this ADR is
superseded rather than amended.
