#!/usr/bin/env python3
"""Cross-reference checks over the observability configuration.

Three drifts this catches, all of which are invisible at runtime:

  1. **A dashboard or alert querying a stage the code never emits.** Renders an
     empty panel that is indistinguishable from a healthy system with no
     traffic. The single worst failure mode an operational signal can have.
  2. **An alert whose runbook_url 404s.** Costs the responder time at the moment
     they have least of it, and teaches them to distrust the next alert.
  3. **A panel querying a raw histogram instead of a recorded KPI rule.** Breaks
     the one-definition-per-KPI property that §41 and Phase 23's metrics layer
     depend on — this is how two surfaces start disagreeing about the same
     number.

Deliberately regex over text rather than a YAML parse: this script must run with
nothing installed but Python, and PyYAML is not a dependency of this project.
Semantic validity of the rules themselves is checked separately by `promtool
check rules`, which is the right tool for that job and already ships in the
Prometheus image. The two checks are complementary, not redundant.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _console import init, safe

OK, FAIL = init()

ROOT = Path(__file__).resolve().parent.parent
OBS = ROOT / "infra" / "observability"
RUNBOOKS = ROOT / "docs" / "runbooks"
METRICS_MODULE = ROOT / "backend" / "nemesis" / "observability" / "metrics.py"

RUNBOOK_URL_RE = re.compile(r"runbook_url:\s*\S*?/docs/runbooks/([A-Za-z0-9._-]+\.md)")
ALERT_RE = re.compile(r"^\s*-\s*alert:\s*(\S+)", re.MULTILINE)
STAGE_RE = re.compile(r'stage\s*=~?\s*"([^"]+)"')
DEPENDENCY_RE = re.compile(r'dependency\s*=~?\s*"([^"]+)"')
ENUM_MEMBER_RE = re.compile(r'^\s+[A-Z_]+\s*=\s*"([a-z_]+)"', re.MULTILINE)


def _enum_members(source: str, class_name: str) -> set[str]:
    """Pull the string values out of one StrEnum in metrics.py.

    Read as text rather than imported: this script runs on a bare interpreter,
    and importing nemesis.observability.metrics pulls in prometheus_client.
    """
    marker = f"class {class_name}("
    start = source.index(marker)
    rest = source[start + len(marker) :]
    # The class body ends at the next top-level `class ` or `def `.
    end = len(rest)
    for terminator in ("\nclass ", "\ndef ", "\n# ---"):
        found = rest.find(terminator)
        if found != -1:
            end = min(end, found)
    return set(ENUM_MEMBER_RE.findall(rest[:end]))


def _without_comments(text: str, path: Path) -> str:
    """Strip comment-only lines from YAML before scanning for selectors.

    Found by this check catching itself: the header of `alerts.yml` documents
    the rule by quoting `stage="..."`, and the scanner dutifully reported that
    the literal string `...` is not a declared stage. Documentation about a
    selector is not a selector.
    """
    if path.suffix not in {".yml", ".yaml"}:
        return text
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _config_files() -> list[Path]:
    return sorted(
        [*OBS.rglob("*.yml"), *OBS.rglob("*.yaml"), *OBS.rglob("*.json")],
        key=lambda p: p.as_posix(),
    )


def main() -> int:
    problems: list[str] = []

    if not METRICS_MODULE.exists():
        print(f"error: {METRICS_MODULE} not found")
        return 1

    source = METRICS_MODULE.read_text(encoding="utf-8")
    known_stages = _enum_members(source, "PipelineStage")
    known_dependencies = _enum_members(source, "Dependency")

    if not known_stages:
        problems.append("could not parse PipelineStage from metrics.py — the check is not running")

    # -- 1. stage and dependency selectors must name declared members --------
    for path in _config_files():
        text = _without_comments(path.read_text(encoding="utf-8"), path)
        rel = path.relative_to(ROOT).as_posix()

        for raw in STAGE_RE.findall(text):
            for stage in raw.split("|"):
                if stage and stage not in known_stages:
                    problems.append(
                        f"{rel}: queries stage={stage!r}, which is not a member of "
                        f"PipelineStage. This panel or alert will silently never "
                        f"match. Declared: {sorted(known_stages)}"
                    )

        for raw in DEPENDENCY_RE.findall(text):
            for dependency in raw.split("|"):
                # `!~` exclusion lists are written against the same vocabulary,
                # so they are validated identically.
                if dependency and dependency not in known_dependencies:
                    problems.append(
                        f"{rel}: queries dependency={dependency!r}, which is not a "
                        f"member of Dependency. Declared: {sorted(known_dependencies)}"
                    )

    # -- 2. every alert has a runbook, and every runbook exists -------------
    alert_files = sorted((OBS / "prometheus" / "rules").glob("*.yml"))
    if not alert_files:
        problems.append("no Prometheus rule files found — the check is not running")

    for path in alert_files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()

        alerts = ALERT_RE.findall(text)
        runbooks = RUNBOOK_URL_RE.findall(text)

        if len(alerts) != len(runbooks):
            problems.append(
                f"{rel}: {len(alerts)} alerts but {len(runbooks)} runbook_url "
                f"annotations. Every alert needs one — an alert with no runbook "
                f"is a 2am research project."
            )

        for page in runbooks:
            if not (RUNBOOKS / page).exists():
                problems.append(
                    f"{rel}: runbook_url points at docs/runbooks/{page}, which "
                    f"does not exist."
                )

    # -- 3. dashboards read recorded KPI rules, not raw histograms ----------
    for path in sorted((OBS / "grafana" / "dashboards").glob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            dashboard = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{rel}: invalid JSON — {exc}")
            continue

        if not dashboard.get("uid"):
            problems.append(f"{rel}: no stable `uid`; provisioning would reassign it on redeploy")

        for expr in _expressions(dashboard):
            if "histogram_quantile" in expr:
                problems.append(
                    f"{rel}: computes histogram_quantile inline in a panel. "
                    f"Quantile definitions belong in recording-kpis.yml so a "
                    f"panel and an alert cannot disagree about the same KPI. "
                    f"Offending expr: {expr[:80]}"
                )

    if problems:
        print(f"\nobservability check: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  {FAIL} {safe(problem)}")
        print()
        return 1

    print(
        f"observability check: ok — {len(known_stages)} stages, "
        f"{len(known_dependencies)} dependencies, all runbook links resolve"
    )
    return 0


def _expressions(node: object) -> list[str]:
    """Every `expr` string anywhere in a dashboard, including collapsed rows."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "expr" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_expressions(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_expressions(item))
    return found


if __name__ == "__main__":
    raise SystemExit(main())
