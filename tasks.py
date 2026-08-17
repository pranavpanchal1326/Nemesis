#!/usr/bin/env python3
"""NEMESIS task runner.

Pure standard library, no install step. `make` is not present on Windows and
`just`/`task` would be another prerequisite between a new contributor and a
working checkout — the point of this file is that `git clone` is the only setup.

    ./nem <task>        (bash / git-bash)
    nem <task>          (cmd / PowerShell)
    python tasks.py <task>

`nem help` lists everything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def _init_console() -> tuple[str, str, str]:
    """Return (ok, fail, rule) glyphs the current console can actually encode.

    The Windows console defaults to cp1252, which cannot encode U+2713. Left
    unhandled, the task runner crashes on its own success message — so try UTF-8
    first and fall back to ASCII rather than assuming a modern terminal.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass

    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return "✓", "✗", "━"
    return "OK", "XX", "-"


OK, FAIL, RULE = _init_console()

# Executed inside the api container: it has the dev toolchain and the same
# interpreter and dependency set as production, so a green local run means the
# same thing a green CI run does.
COMPOSE = ["docker", "compose"]
IN_API = [*COMPOSE, "exec", "-T", "api"]
TEST_DSN = "NEMESIS_TEST_ADMIN_DSN=postgresql://nemesis:nemesis@postgres:5432/postgres"

_TASKS: dict[str, tuple[str, Callable[[list[str]], int]]] = {}


def task(name: str, description: str) -> Callable[..., Callable[[list[str]], int]]:
    def decorator(fn: Callable[[list[str]], int]) -> Callable[[list[str]], int]:
        _TASKS[name] = (description, fn)
        return fn

    return decorator


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> int:
    printable = " ".join(cmd)
    print(f"\033[36m→ {printable}\033[0m", flush=True)
    result = subprocess.run(cmd, cwd=cwd or ROOT, env=env)
    if check and result.returncode != 0:
        print(f"\033[31m{FAIL} failed ({result.returncode}): {printable}\033[0m", file=sys.stderr)
    return result.returncode


def _fetch(url: str, *, timeout: float = 5.0, auth: tuple[str, str] | None = None) -> str | None:
    """GET a URL, returning None on any failure.

    Deliberately swallowing: every caller is polling a service that is expected
    to be unavailable for the first few seconds, and a traceback per attempt
    would bury the one line that matters.
    """
    request = urllib.request.Request(url)
    if auth is not None:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: str = response.read().decode("utf-8", "replace")
            return body
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def _poll(label: str, url: str, predicate: Callable[[str], bool], *, seconds: int = 90) -> bool:
    """Poll until `predicate` accepts the response body, or time out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = _fetch(url)
        if body is not None and predicate(body):
            print(f"  \033[32m{OK}\033[0m {label}")
            return True
        time.sleep(3)
    print(f"  \033[31m{FAIL}\033[0m {label} (timed out after {seconds}s)", file=sys.stderr)
    return False


def run_all(steps: list[tuple[str, list[str]]]) -> int:
    """Run every step even if one fails, then report a combined result.

    Stopping at the first failure hides the other three problems and turns one
    fix-and-rerun cycle into four.
    """
    failures: list[str] = []
    for label, cmd in steps:
        print(f"\n\033[1m{RULE}{RULE} {label}\033[0m")
        if run(cmd, check=False) != 0:
            failures.append(label)

    print()
    if failures:
        for label in failures:
            print(f"\033[31m{FAIL} {label}\033[0m")
        return 1
    print(f"\033[32m{OK} all checks passed\033[0m")
    return 0


# --------------------------------------------------------------------------
# Stack lifecycle
# --------------------------------------------------------------------------


@task("up", "Build and start the full stack, waiting until every service is healthy")
def _up(_: list[str]) -> int:
    if run([*COMPOSE, "up", "-d", "--build"]) != 0:
        return 1
    return _wait([])


@task("down", "Stop the stack (volumes preserved)")
def _down(_: list[str]) -> int:
    return run([*COMPOSE, "down"])


@task("nuke", "Stop the stack and DELETE all volumes (destroys local data)")
def _nuke(_: list[str]) -> int:
    print("\033[33mThis deletes the database, uploads, and cached model weights.\033[0m")
    if input("Type 'destroy' to confirm: ").strip() != "destroy":
        print("aborted")
        return 1
    return run([*COMPOSE, "down", "-v"])


@task("wait", "Block until every service reports healthy")
def _wait(_: list[str]) -> int:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        out = subprocess.run(
            [*COMPOSE, "ps", "--format", "{{.Service}}:{{.Health}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        states = [line for line in out.stdout.strip().splitlines() if line]
        if states and not any("starting" in s for s in states):
            # A service with no healthcheck reports an EMPTY health field, which
            # is not the same as unhealthy — the collector image is distroless
            # and has no shell to probe with, by design. Treating "not asserted"
            # as "failing" would make `nem wait` fail on a working stack.
            unasserted = [s for s in states if s.endswith(":")]
            unhealthy = [s for s in states if "healthy" not in s and not s.endswith(":")]
            if unhealthy:
                print(f"\033[31m{FAIL} unhealthy: {', '.join(unhealthy)}\033[0m", file=sys.stderr)
                return 1
            print(f"\033[32m{OK} healthy: {' '.join(s for s in states if s not in unasserted)}\033[0m")
            if unasserted:
                names = ", ".join(s.rstrip(":") for s in unasserted)
                print(f"\033[33m·  no healthcheck declared: {names}\033[0m")
            return 0
        time.sleep(3)
    print(f"\033[31m{FAIL} timed out waiting for health\033[0m", file=sys.stderr)
    return 1


@task("logs", "Tail service logs — `nem logs worker-ml`")
def _logs(args: list[str]) -> int:
    return run([*COMPOSE, "logs", "-f", "--tail=100", *args])


@task("shell", "Open a shell in a service — `nem shell worker-ml`")
def _shell(args: list[str]) -> int:
    return run([*COMPOSE, "exec", args[0] if args else "api", "bash"])


@task("psql", "Open psql against the local database")
def _psql(_: list[str]) -> int:
    return run([*COMPOSE, "exec", "postgres", "psql", "-U", "nemesis", "-d", "nemesis"])


# --------------------------------------------------------------------------
# Quality gates
# --------------------------------------------------------------------------


@task("lint", "Run ruff check")
def _lint(_: list[str]) -> int:
    return run([*IN_API, "ruff", "check", "."])


@task("format", "Apply ruff formatting and import sorting")
def _format(_: list[str]) -> int:
    rc = run([*IN_API, "ruff", "check", "--fix", "."], check=False)
    return run([*IN_API, "ruff", "format", "."]) or rc


@task("types", "Run mypy in strict mode")
def _types(_: list[str]) -> int:
    return run([*IN_API, "mypy", "nemesis"])


@task("test", "Run the test suite with coverage")
def _test(args: list[str]) -> int:
    return run([*COMPOSE, "exec", "-T", "-e", TEST_DSN, "api", "pytest", *args])


@task("check", "Run every quality gate — the same set CI runs")
def _check(_: list[str]) -> int:
    return run_all(
        [
            ("lint", [*IN_API, "ruff", "check", "."]),
            ("format", [*IN_API, "ruff", "format", "--check", "."]),
            ("types", [*IN_API, "mypy", "nemesis"]),
            ("test", [*COMPOSE, "exec", "-T", "-e", TEST_DSN, "api", "pytest", "--cov"]),
            ("migrations", [*IN_API, "alembic", "check"]),
            # Host-side and dependency-free, so they run even when the stack is
            # down — and they fail for reasons that are cheap to fix.
            ("runbooks", [sys.executable, "scripts/check_runbooks.py"]),
            ("parity", [sys.executable, "scripts/check_env_parity.py"]),
            ("observability", [sys.executable, "scripts/check_observability.py"]),
            # Phase 2 gates. `tenant scoping` and `event catalog` are host-side
            # AST checks; the schema *fingerprint* half needs Pydantic and
            # therefore lives in the test suite above (test_event_registry.py),
            # because the api container mounts only ./backend and cannot read
            # the blueprint that the catalog check parses.
            ("tenant scoping", [sys.executable, "scripts/check_tenant_scoping.py"]),
            ("event catalog", [sys.executable, "scripts/check_event_catalog.py"]),
        ]
    )


# --------------------------------------------------------------------------
# Observability (Phase 1a)
# --------------------------------------------------------------------------

OBS_SERVICES = ["otel-collector", "tempo", "prometheus", "alertmanager", "grafana"]

# The exporter posts to the collector's OTLP/HTTP trace endpoint. The path is
# explicit because the Python HTTP exporter uses the endpoint verbatim rather
# than appending /v1/traces to a base URL.
OTLP_TRACES_ENDPOINT = "http://otel-collector:4318/v1/traces"


def _obs_env(*, tracing: bool) -> dict[str, str]:
    """Compose invocation environment.

    Setting these here rather than in `.env` means starting the observability
    stack does not mutate a file the user owns — and `nem obs-down` genuinely
    restores the previous state instead of leaving a half-edited config behind.
    """
    return os.environ | {
        "NEMESIS_OTEL__ENABLED": "true" if tracing else "false",
        "NEMESIS_OTEL__ENDPOINT": OTLP_TRACES_ENDPOINT if tracing else "",
    }


@task("obs", "Start the observability stack and turn on trace export")
def _obs(_: list[str]) -> int:
    # `up -d` (not `start`) so the backend services are RECREATED with the OTel
    # variables set. Without that, the collector runs and receives nothing,
    # which looks exactly like a broken collector.
    if run([*COMPOSE, "--profile", "obs", "up", "-d"], env=_obs_env(tracing=True)) != 0:
        return 1
    if _wait([]) != 0:
        return 1
    print()
    print("  Grafana       http://localhost:3001   (anonymous viewer)")
    print("  Prometheus    http://localhost:9090")
    print("  Alertmanager  http://localhost:9093")
    print("  Tempo         http://localhost:3200")
    print()
    print("  nem obs-verify   prove metric -> dashboard -> alert end to end")
    print()
    return 0


@task("obs-down", "Stop the observability stack and turn trace export back off")
def _obs_down(_: list[str]) -> int:
    run([*COMPOSE, "--profile", "obs", "rm", "-sf", *OBS_SERVICES], check=False)
    # Recreate the backend services without the exporter, so a stopped collector
    # does not leave them retrying span exports on a background thread forever.
    return run([*COMPOSE, "up", "-d"], env=_obs_env(tracing=False))


@task("obs-verify", "Phase 1a gate: metric -> Prometheus -> Grafana -> alert, end to end")
def _obs_verify(_: list[str]) -> int:
    """Walk the whole chain rather than asserting any link in isolation.

    Each step below has been an independent failure in practice: a scrape that
    never resolved the service name, a rule file that would not parse (leaving
    Prometheus serving its last good config while looking healthy), a datasource
    UID that did not match what the dashboards referenced. Checking only the
    ends would pass with any one of them broken.
    """
    print("\n\033[1mPhase 1a observability gate\033[0m\n")

    # 1. Make the application emit something. Until a request is served, the
    #    counter has no time series at all — prometheus_client does not export a
    #    labelled counter before its first increment.
    for _i in range(5):
        _fetch("http://localhost:8000/health", timeout=3)
    if _fetch("http://localhost:8000/metrics", timeout=5) is None:
        print(f"  \033[31m{FAIL}\033[0m application /metrics unreachable — is the stack up?")
        return 1
    print(f"  \033[32m{OK}\033[0m application is emitting metrics")

    steps = [
        (
            "Prometheus scraped the application",
            "http://localhost:9090/api/v1/query?query=nemesis_http_requests_total",
            lambda body: len(json.loads(body).get("data", {}).get("result", [])) > 0,
        ),
        (
            "recording rules evaluated (§41 KPIs have one definition)",
            "http://localhost:9090/api/v1/rules",
            lambda body: "nemesis:kpi_classification_latency_p95_seconds" in body,
        ),
        (
            "alert rules loaded",
            "http://localhost:9090/api/v1/rules",
            lambda body: "NemesisAlertPipelineHeartbeat" in body,
        ),
        (
            "alert is firing in Prometheus",
            "http://localhost:9090/api/v1/alerts",
            lambda body: any(
                a.get("labels", {}).get("alertname") == "NemesisAlertPipelineHeartbeat"
                and a.get("state") == "firing"
                for a in json.loads(body).get("data", {}).get("alerts", [])
            ),
        ),
        (
            "alert reached Alertmanager",
            "http://localhost:9093/api/v2/alerts",
            lambda body: "NemesisAlertPipelineHeartbeat" in body,
        ),
        (
            "Grafana is healthy",
            "http://localhost:3001/api/health",
            lambda body: json.loads(body).get("database") == "ok",
        ),
        (
            "KPI dashboard is provisioned",
            "http://localhost:3001/api/dashboards/uid/nemesis-kpis",
            lambda body: "§41 Product Health KPIs" in body,
        ),
        (
            "service health dashboard is provisioned",
            "http://localhost:3001/api/dashboards/uid/nemesis-service-health",
            lambda body: "nemesis-service-health" in body,
        ),
    ]

    failures = [label for label, url, predicate in steps if not _poll(label, url, predicate)]

    # The Grafana datasource proxy is checked separately: it needs admin auth,
    # and it is the only step that proves a *panel* would render rather than
    # that Prometheus merely holds the data.
    admin = (
        os.environ.get("GRAFANA_ADMIN_USER", "admin"),
        os.environ.get("GRAFANA_ADMIN_PASSWORD", "admin"),
    )
    body = _fetch(
        "http://localhost:3001/api/datasources/uid/nemesis-prometheus/health",
        timeout=10,
        auth=admin,
    )
    if body is not None and '"OK"' in body.upper().replace("'", '"'):
        print(f"  \033[32m{OK}\033[0m Grafana can query the Prometheus datasource")
    else:
        print(f"  \033[31m{FAIL}\033[0m Grafana datasource health check", file=sys.stderr)
        failures.append("Grafana datasource health")

    print()
    if failures:
        print(f"\033[31m{FAIL} gate not met: {len(failures)} step(s) failed\033[0m", file=sys.stderr)
        print("  Start the stack with `nem obs` and retry.", file=sys.stderr)
        return 1
    print(f"\033[32m{OK} gate met — a metric emitted by the application reaches a")
    print("  provisioned dashboard and fires a configured alert, verified end to end\033[0m\n")
    return 0


@task("gate-phase3", "Phase 3 gate: submission -> outbox -> relay -> WebSocket, and a SIGKILL drill")
def _gate_phase3(_: list[str]) -> int:
    """Runs against the live stack, because the claims are about processes.

    The pytest suite proves the logic in one process. Two of the gate clauses —
    "a rolled-back transaction never emits a WebSocket event" reaching an actual
    browser, and "SIGKILL mid-pipeline loses nothing" — are about a relay in one
    container and a worker in another, and neither is reachable from a test
    process. This kills a container on purpose; it is not part of `nem check`.
    """
    return run([sys.executable, "scripts/gate_phase3.py"])


@task("flag", "Feature flags — `nem flag list` / `nem flag kill <name> --actor X --reason Y`")
def _flag(args: list[str]) -> int:
    return run([*IN_API, "python", "-m", "nemesis.flags", *(args or ["list"])])


# --------------------------------------------------------------------------
# Governance checks (Phase 1a)
# --------------------------------------------------------------------------


@task("parity", "Check the local config surface matches the deployment contract")
def _parity(_: list[str]) -> int:
    # Host-side, not in a container: `nemesis.deployment` imports nothing
    # third-party precisely so this runs on a bare interpreter, and the files it
    # compares against live at the repository root rather than in the image.
    return run([sys.executable, "scripts/check_env_parity.py"])


@task("docs-check", "Check runbook coverage and observability cross-references")
def _docs_check(_: list[str]) -> int:
    return run_all(
        [
            ("runbooks", [sys.executable, "scripts/check_runbooks.py"]),
            ("observability", [sys.executable, "scripts/check_observability.py"]),
        ]
    )


@task("changelog", "Regenerate CHANGELOG.md from conventional commits")
def _changelog(_: list[str]) -> int:
    return run([sys.executable, "scripts/generate_changelog.py"])


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


@task("migrate", "Apply migrations to head")
def _migrate(_: list[str]) -> int:
    return run([*IN_API, "alembic", "upgrade", "head"])


@task("makemigration", 'Autogenerate a migration — `nem makemigration "add x"`')
def _makemigration(args: list[str]) -> int:
    if not args:
        print("usage: nem makemigration \"message\"", file=sys.stderr)
        return 1
    return run([*IN_API, "alembic", "revision", "--autogenerate", "-m", " ".join(args)])


@task("rollback", "Revert the most recent migration")
def _rollback(_: list[str]) -> int:
    return run([*IN_API, "alembic", "downgrade", "-1"])


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


@task("doctor", "Check the local toolchain and stack prerequisites")
def _doctor(_: list[str]) -> int:
    problems: list[str] = []

    for tool in ("docker", "git"):
        if shutil.which(tool) is None:
            problems.append(f"{tool} not found on PATH")
        else:
            print(f"\033[32m{OK}\033[0m {tool}")

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        problems.append("docker daemon unreachable — start Docker Desktop")
    else:
        print(f"\033[32m{OK}\033[0m docker daemon")

    if not (ROOT / ".env").exists():
        problems.append(".env missing — copy it from .env.example")
    else:
        print(f"\033[32m{OK}\033[0m .env")

    # Ollama runs on the host with the GPU (ADR-0002), so it is checked from the
    # host rather than from inside a container.
    try:
        import urllib.request

        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3):
            print(f"\033[32m{OK}\033[0m ollama")
    except Exception:
        problems.append("ollama unreachable on localhost:11434 (needed from Phase 16)")

    print()
    for problem in problems:
        print(f"\033[31m{FAIL}\033[0m {problem}", file=sys.stderr)
    return 1 if problems else 0


@task("models", "Download and verify model weights into the offline cache")
def _models(args: list[str]) -> int:
    # Runs in worker-ml: the only image carrying torch, and the only one with
    # the /models volume mounted (ADR-0004).
    return run([*COMPOSE, "exec", "-T", "worker-ml", "python", "scripts/fetch_models.py", *args])


@task("models-verify", "Verify cached weights load with the network disabled")
def _models_verify(_: list[str]) -> int:
    return _models(["--verify"])


@task("help", "List available tasks")
def _help(_: list[str]) -> int:
    print("\n\033[1mNEMESIS task runner\033[0m\n")
    width = max(len(name) for name in _TASKS)
    for name, (description, _fn) in _TASKS.items():
        print(f"  \033[36m{name.ljust(width)}\033[0m  {description}")
    print()
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        return _help([])
    name, *args = argv
    entry = _TASKS.get(name)
    if entry is None:
        print(f"\033[31munknown task: {name}\033[0m\n", file=sys.stderr)
        return _help([]) or 1
    return entry[1](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
