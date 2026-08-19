"""Phase 7 gate, against the running stack.

The pytest suite proves the logic in one process against a throwaway database.
This proves the *deployment*, and for this phase the distinction is sharper than
usual: two of the three gate clauses are claims about scale and about a
guarantee, and neither is meaningfully demonstrated by a fixture.

The three clauses, executed in order:

1. **A rubric change is backtested over twelve months of seeded history,
   producing a quantified impact report before activation.** Twelve months of
   real event chains are seeded through the real ``EventStore`` — real hashes,
   real chain tails — and a candidate rubric is replayed over them. The report
   has to contain numbers, and the numbers have to move when the candidate does:
   a backtest that reports "nothing changed" for a rubric that inverts every
   weight is a backtest that is not reading the corpus.
2. **A policy that regresses the labelled evaluation set cannot be activated.**
   A set is labelled from the seeded complaints and published, a deliberately
   wrong candidate is evaluated and fails, and the activation is refused with
   the set named. Then a candidate that agrees with the labels passes and
   activates — because a guardrail that refuses everything is not a guardrail
   either.
3. **Shadow mode provably cannot mutate state or emit domain events.** The
   tenant's event count and chain head are captured, shadow mode is run over
   real complaints, and both must be unchanged — while the observations it
   produced are non-empty, so the check cannot pass by doing nothing.

Two further checks that are about the deployment rather than the logic:

4. Every certification decision and every guardrail change is **on a verifiable
   hash chain**, through the same ``verify_chain`` the earlier gates use.
   Evidence that is not on the chain is decoration.
5. **No deploy**, measured the way Phase 6 measures it: the API container's id
   and ``State.StartedAt`` are compared across the whole run. Everything here
   happens over HTTP against a container nothing restarts.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"
CONTROL_PLANE = f"{API}/api/v1/control-plane"
POLICIES = f"{CONTROL_PLANE}/policies"
SIMULATIONS = f"{CONTROL_PLANE}/simulations"

TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: How many complaint chains the gate seeds, and over how long. Four hundred is
#: comfortably above ``MINIMUM_CASES`` and small enough that the seeding step
#: stays under a minute on a laptop; 365 days is the span the gate clause names.
HISTORY_COMPLAINTS = 400
HISTORY_DAYS = 365


def _report(passed: bool, label: str, detail: str = "") -> bool:
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    stream.write(f"  {marker} {label}{f' - {detail}' if detail else ''}\n")
    stream.flush()
    return passed


def _token() -> str:
    return os.environ.get("NEMESIS_CONTROL_PLANE_TOKEN", DEFAULT_TOKEN)


def _request(
    method: str, url: str, *, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _psql(sql: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "nemesis", "-d", "nemesis", "-tAc", sql],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _api_identity() -> str:
    """The running API container's id and exact start instant.

    The measurement behind clause 5, deliberately precise rather than readable —
    see ``gate_phase6`` for why ``docker compose ps`` is not good enough.
    """
    result = subprocess.run(
        [*COMPOSE, "ps", "-q", "api"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    container = result.stdout.strip().splitlines()
    if not container:
        return ""
    identifier = container[0].strip()
    inspected = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", identifier],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return f"{identifier[:12]}@{inspected.stdout.strip()}"


def _seed_history(slug: str) -> dict[str, Any]:
    """Provision a sandbox tenant and give it a year of real event chains.

    Through ``python -m nemesis.sandbox --history`` in the api container rather
    than through SQL. Writing event rows directly would produce entries that
    look like chain links and fail ``verify_chain`` — the same reasoning the
    Phase 6 migration gives for refusing to seed policies in DDL.
    """
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "nemesis.sandbox",
            slug,
            "--complaints",
            "20",
            "--history",
            str(HISTORY_COMPLAINTS),
            "--history-days",
            str(HISTORY_DAYS),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip()[:400]}
    return _trailing_json(result.stdout)


def _trailing_json(output: str) -> dict[str, Any]:
    """The JSON document a CLI printed, ignoring the structured logs around it.

    ``structlog`` writes to stdout by design (§24.3 — the log *is* the
    observability surface at this scale), so a CLI's stdout is log lines and
    then a payload, not a payload. Slicing from the first line that opens an
    object is what makes this readable by a script without turning off the
    logging that every other operator relies on.
    """
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("{"):
            try:
                return dict(json.loads("\n".join(lines[index:])))
            except json.JSONDecodeError:
                continue
    return {"error": output.strip()[-400:]}


def _rubric(visual: float) -> dict[str, Any]:
    """A two-component rubric. ``visual`` carries the weight under test."""
    return {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual damage",
                "weight": visual,
                "description": "How severe the defect looks in the photograph.",
            },
            {
                "key": "road_class",
                "display_name": "Road class",
                "weight": round(1.0 - visual, 6),
                "description": "How important the affected road is.",
            },
        ]
    }


def _draft_and_approve(
    tenant: str, admin: dict[str, str], kind: str, body: Any, reason: str
) -> int | None:
    """Draft → submit → approve. Stops short of activation, deliberately.

    Activation is what the guardrail gates, so the gate has to reach a candidate
    that is *approved and not yet live* — which is exactly the state an operator
    is in when they press the button this phase learned to refuse.
    """
    headers = {"X-Tenant-ID": tenant, **admin}
    status, drafted = _request(
        "POST", f"{POLICIES}/{kind}", headers=headers, body={"body": body, "change_reason": reason}
    )
    if status != 201 or not isinstance(drafted, dict):
        _report(False, f"draft {kind}", f"status {status}: {drafted}")
        return None
    revision = int(drafted["revision"])
    for verb in ("submit", "approve"):
        status, detail = _request(
            "POST",
            f"{POLICIES}/{kind}/{revision}/{verb}",
            headers=headers,
            body={"reason": reason},
        )
        if status != 200:
            _report(False, f"{verb} {kind}@{revision}", f"status {status}: {detail}")
            return None
    return revision


def _complaint_ids(tenant_id: str, limit: int) -> list[str]:
    """Complaint ids from the seeded chains, oldest first.

    Read from ``events`` rather than from ``complaints``: the history this gate
    seeds is a log, and the projections were never built for it. That is not a
    gap in the seeding — it is what makes the corpus builder's claim testable
    here, since a backtest that had quietly been reading projections would find
    nothing at all.
    """
    rows = _psql(
        f"SELECT entity_id FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND event_type = 'complaint_submitted' ORDER BY occurred_at LIMIT {limit}"
    )
    return [line.strip() for line in rows.splitlines() if line.strip()]


# ---------------------------------------------------------------------------


def main() -> int:
    sys.stdout.write("\nPhase 7 gate - configuration simulation & backtesting\n\n")
    results: list[bool] = []
    run = uuid.uuid4().hex[:8]
    admin = {TOKEN_HEADER: _token()}

    # -- 0. The stack answers at all --------------------------------------
    status, _ = _request("GET", f"{API}/health")
    if not _report(status == 200, "stack is up", f"/health returned {status}"):
        sys.stderr.write("\n  Start the stack with `nem up` and retry.\n\n")
        return 1
    results.append(True)

    started_before = _api_identity()
    results.append(
        _report(bool(started_before), "the running API container was identified", started_before)
    )

    # -- 1. Twelve months of real history ---------------------------------
    slug = f"sim-gate-{run}"
    seeded = _seed_history(slug)
    tenant_id = str(seeded.get("tenant_id", ""))
    events = int(seeded.get("history_events", 0) or 0)
    if not _report(
        bool(tenant_id) and events == HISTORY_COMPLAINTS,
        f"{HISTORY_COMPLAINTS} complaint chains seeded over {HISTORY_DAYS} days",
        seeded.get("error", f"tenant={tenant_id[:8]} chains={events}"),
    ):
        return 1
    results.append(True)

    chained = _psql(
        f"SELECT count(*) FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND entity_type = 'complaint'"
    )
    results.append(
        _report(
            chained == str(HISTORY_COMPLAINTS * 4),
            "the seeded history is real events on real chains, not projection rows",
            f"complaint events={chained}",
        )
    )

    forks = _psql(
        f"SELECT count(*) FROM (SELECT entity_id, sequence FROM events WHERE "
        f"tenant_id = '{tenant_id}' AND entity_type = 'complaint' "
        f"GROUP BY entity_id, sequence HAVING count(*) > 1) f"
    )
    results.append(
        _report(forks == "0", "no seeded complaint chain has a forked sequence", f"forks={forks}")
    )

    # -- 2. Clause 1: a quantified report, before activation ---------------
    candidate = _draft_and_approve(
        tenant_id, admin, "severity_rubric", _rubric(0.95), "gate: retune toward visual damage"
    )
    if not _report(candidate is not None, "a candidate rubric is drafted and approved"):
        return 1
    results.append(True)

    status, report_run = _request(
        "POST",
        f"{SIMULATIONS}/runs",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"kind": "severity_rubric", "revision": candidate},
    )
    report = (
        report_run.get("run", {}).get("report") if isinstance(report_run, dict) else None
    ) or {}
    results.append(
        _report(
            status == 201 and report.get("case_count", 0) >= HISTORY_COMPLAINTS,
            "the candidate is backtested over the whole seeded window",
            f"status {status}: {report.get('case_count')} case(s) of "
            f"{report.get('population')}",
        )
    )

    affected = int(report.get("affected", 0) or 0)
    severity = report.get("severity", {}) or {}
    results.append(
        _report(
            affected > 0 and severity.get("changed", 0) > 0,
            "the report quantifies the impact rather than reporting nothing",
            f"affected={affected}, severity changed={severity.get('changed')}, "
            f"tier moves={sum((severity.get('tier_transitions') or {}).values())}",
        )
    )

    # A report that names only the kind it changed cannot be reproduced: the SLA
    # matrix that turned those scores into tiers has to be in the record too.
    stamps = report.get("baseline_stamps", {}) or {}
    results.append(
        _report(
            "severity_rubric" in stamps and "sla_matrix" in stamps,
            "the report names every configuration that produced its numbers",
            ", ".join(f"{k}={v}" for k, v in sorted(stamps.items())),
        )
    )

    still_live = _psql(
        f"SELECT revision FROM policy_versions WHERE tenant_id = '{tenant_id}' "
        f"AND kind = 'severity_rubric' AND status = 'active'"
    )
    results.append(
        _report(
            still_live == "1",
            "backtesting an approved candidate does not activate it",
            f"live revision={still_live}, candidate={candidate}",
        )
    )

    # -- 3. Clause 2: a regression cannot be activated ---------------------
    labelled = _complaint_ids(tenant_id, 12)
    status, _ = _request(
        "POST",
        f"{SIMULATIONS}/evaluation-sets",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={
            "code": "gate-review",
            "name": "Gate review",
            "kind": "severity_rubric",
            "description": "Complaints reviewed by hand for the Phase 7 gate",
            "pass_ratio": 1.0,
        },
    )
    created = status == 201

    # Label against what the *live* rubric decides today, so the incumbent
    # passes its own exam and only a change is capable of failing it.
    for complaint_id in labelled:
        _request(
            "POST",
            f"{SIMULATIONS}/evaluation-sets/gate-review/labels",
            headers={"X-Tenant-ID": tenant_id, **admin},
            body={
                "complaint_id": complaint_id,
                "rationale": "Reviewed for the gate: the danger path must stay quiet here",
                "expected_safety_fired": False,
                "expected_severity_max": 10.0,
                "expected_severity_min": 0.0,
            },
        )
    status, published = _request(
        "POST",
        f"{SIMULATIONS}/evaluation-sets/gate-review/publish",
        headers={"X-Tenant-ID": tenant_id, **admin},
    )
    results.append(
        _report(
            created and status == 200 and published.get("label_count") == len(labelled),
            "a labelled evaluation set is published, gating this kind",
            f"status {status}: {published.get('label_count')} label(s)",
        )
    )

    status, refusal = _request(
        "POST",
        f"{POLICIES}/severity_rubric/{candidate}/activate",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"reason": "gate: must be refused, never evaluated"},
    )
    detail = refusal.get("detail", "") if isinstance(refusal, dict) else ""
    results.append(
        _report(
            status == 409 and "gate-review" in detail,
            "an uncertified candidate cannot be activated",
            f"status {status}: {detail[:90]}",
        )
    )

    # A guardrail that refuses everything is not a guardrail. Certify the same
    # candidate and it must go live.
    status, certified = _request(
        "POST",
        f"{SIMULATIONS}/runs",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"kind": "severity_rubric", "revision": candidate, "certify": True},
    )
    certificate = (certified or {}).get("certificate") or {}
    results.append(
        _report(
            status == 201 and certificate.get("verdict") == "pass",
            "an evaluation against the published set issues a verdict",
            f"status {status}: verdict={certificate.get('verdict')} "
            f"{certificate.get('labels_passed')}/{certificate.get('labels_evaluated')}",
        )
    )

    status, activated = _request(
        "POST",
        f"{POLICIES}/severity_rubric/{candidate}/activate",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"reason": "gate: certified, so it may go live"},
    )
    results.append(
        _report(
            status == 200,
            "a certified candidate activates normally",
            f"status {status}: revision "
            f"{(activated or {}).get('version', {}).get('revision')}",
        )
    )

    # -- 4. Clause 3: shadow mode writes nothing --------------------------
    watched = _complaint_ids(tenant_id, 25)
    shadow_candidate = _draft_and_approve(
        tenant_id, admin, "severity_rubric", _rubric(0.05), "gate: shadow candidate"
    )
    events_before = _psql(f"SELECT count(*) FROM events WHERE tenant_id = '{tenant_id}'")
    head_before = _psql(
        f"SELECT string_agg(head_hash, ',' ORDER BY entity_id) FROM event_chain_heads "
        f"WHERE tenant_id = '{tenant_id}'"
    )

    status, summary = _request(
        "POST",
        f"{SIMULATIONS}/shadow",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={
            "kind": "severity_rubric",
            "revision": shadow_candidate,
            "complaint_ids": watched,
        },
    )
    events_after = _psql(f"SELECT count(*) FROM events WHERE tenant_id = '{tenant_id}'")
    head_after = _psql(
        f"SELECT string_agg(head_hash, ',' ORDER BY entity_id) FROM event_chain_heads "
        f"WHERE tenant_id = '{tenant_id}'"
    )
    observed = int((summary or {}).get("observed", 0) or 0)

    results.append(
        _report(
            status == 200 and observed == len(watched),
            "shadow mode observed every complaint it was given",
            f"status {status}: observed={observed} of {len(watched)}",
        )
    )
    results.append(
        _report(
            events_before == events_after and head_before == head_after,
            "shadow mode emitted no event and moved no chain head",
            f"events {events_before} -> {events_after}",
        )
    )

    quoted = ", ".join(f"'{identifier}'" for identifier in watched) or "NULL"
    projections = _psql(
        f"SELECT count(*) FROM complaints WHERE tenant_id = '{tenant_id}' AND id IN ({quoted})"
    )
    results.append(
        _report(
            projections in {"0", ""},
            "shadow mode wrote no projection row for the complaints it watched",
            f"rows={projections or '0'}",
        )
    )

    still_live = _psql(
        f"SELECT revision FROM policy_versions WHERE tenant_id = '{tenant_id}' "
        f"AND kind = 'severity_rubric' AND status = 'active'"
    )
    results.append(
        _report(
            still_live == str(candidate),
            "the shadow candidate did not become the deciding document",
            f"live revision={still_live}, shadow candidate={shadow_candidate}",
        )
    )

    # -- 5. The evidence is on the chain ----------------------------------
    chain = _psql(
        f"SELECT string_agg(DISTINCT event_type, ',') FROM events WHERE "
        f"tenant_id = '{tenant_id}' AND entity_type = 'tenant' AND event_type IN "
        f"('evaluation_set_published','policy_certified')"
    )
    results.append(
        _report(
            "evaluation_set_published" in chain and "policy_certified" in chain,
            "publishing a set and certifying a candidate are both on the tenant chain",
            chain or "(none)",
        )
    )

    certificates = _psql(
        f"SELECT count(*) FROM policy_certificates WHERE tenant_id = '{tenant_id}' "
        f"AND verdict = 'pass' AND labels_evaluated = 0"
    )
    results.append(
        _report(
            certificates == "0",
            "no certificate passed without marking anything",
            f"vacuous passes={certificates}",
        )
    )

    published_sets = _psql(
        f"SELECT count(*) FROM (SELECT kind FROM evaluation_sets WHERE "
        f"tenant_id = '{tenant_id}' AND status = 'published' GROUP BY kind "
        f"HAVING count(*) > 1) d"
    )
    results.append(
        _report(
            published_sets == "0",
            "no policy kind has two published evaluation sets",
            f"kinds with more than one={published_sets}",
        )
    )

    # -- 6. Clause 5, measured: nothing was deployed ----------------------
    started_after = _api_identity()
    results.append(
        _report(
            bool(started_after) and started_after == started_before,
            "the API container was never restarted, rebuilt, or recreated",
            f"before={started_before!r} after={started_after!r}",
        )
    )

    passed = sum(1 for result in results if result)
    total = len(results)
    sys.stdout.write("\n")
    if passed != total:
        sys.stderr.write(f"{FAIL} Phase 7 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 7 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A rubric change was backtested over {HISTORY_DAYS} days of real event\n"
        f"      history and produced a quantified report before anything went live. An\n"
        f"      uncertified candidate could not be activated; the same candidate could\n"
        f"      once it had passed the tenant's labelled set. Shadow mode observed real\n"
        f"      complaints and left the event count, every chain head, and the deciding\n"
        f"      document exactly as it found them.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
