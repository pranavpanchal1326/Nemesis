"""Phase 10 gate — §14 deduplication and clustering.

Four clauses, from `docs/PHASES.md`:

1. Measured precision/recall against a labelled fixture set of true-duplicate
   and true-distinct pairs.
2. **Zero false-positive merges** on that set — an absolute, not a threshold,
   because a wrong merge suppresses a real citizen report (§14.3).
3. Stage 1 eliminates >= 90% of candidates before any embedding comparison,
   verified by query plan.
4. Decision latency within the §27.1 budget at seeded volume.

Clause 3 lives in the test suite, where `EXPLAIN (ANALYZE)` can be read against a
seeded database inside one transaction; this script runs that test rather than
re-seeding two hundred clusters to re-derive it. Clauses 1, 2 and 4 are read from
the published artefact *after reproducing it*, because "reproducible by one
command" is the property being checked and a gate that trusts a committed file
checks nothing.

Exit code 0 when every clause passes, 1 otherwise. **This gate is expected to
fail while the corpus produces false merges, and that is the point**: the honest
number ships either way, and a gate quietly tuned until it passes is a false
receipt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "reports" / "dedup-precision-recall.json"

#: §27.1: dedup match decision < 10 seconds. Duplicated from the eval script on
#: purpose, like every other gate here: this runs on a bare interpreter with no
#: `nemesis` on the path, and a gate that imported the number it checks would
#: agree with it by construction.
BUDGET_MS = 10_000.0

OK, FAIL = "[ OK ]", "[FAIL]"


def _report(passed: bool, label: str, detail: str = "") -> bool:
    """One clause result, in the shape the other gate scripts here use."""
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    suffix = f" - {detail}" if detail else ""
    stream.write(f"  {marker} {label}{suffix}\n")
    stream.flush()
    return passed


def ok(label: str, detail: str = "") -> bool:
    return _report(True, label, detail)


def fail(label: str, detail: str = "") -> bool:
    return _report(False, label, detail)


def heading(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m\n")


def section(text: str) -> None:
    print(f"\n  {text}")


def _run(command: list[str]) -> tuple[int, str]:
    """Run a child process and capture its output as text, safely on Windows.

    ``encoding`` and ``errors`` are explicit for the reason ``scripts/_console.py``
    exists: the Windows console defaults to cp1252, the harness prints the report
    path and a policy log line containing non-ASCII, and the default decode
    raises ``UnicodeDecodeError`` *inside the gate that was reporting a failure*
    — replacing the finding with a traceback about character maps. ``errors``
    rather than a strict decode because this text is only ever shown to a human.
    """
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main() -> int:
    heading("Phase 10 gate - deduplication & clustering (S14)")
    failures: list[str] = []

    section("Clause 1 & 2 - measured precision/recall, and zero false merges")
    before = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.is_file() else None
    _, output = _run([sys.executable, str(ROOT / "tasks.py"), "dedup-eval"])
    if not REPORT.is_file():
        fail("the harness produced no report", output.strip()[-400:])
        return _verdict(["the harness produced no report"])

    report: dict[str, Any] = json.loads(REPORT.read_text(encoding="utf-8"))
    ok("the report was reproduced by one command", "nem dedup-eval")

    if before is not None:
        # Reproducibility means the *numbers* repeat, not the timestamp.
        moved = [
            field
            for field in ("precision", "recall", "f1")
            if before.get(field) != report.get(field)
        ]
        if moved:
            detail = "; ".join(f"{f}: {before.get(f)} -> {report.get(f)}" for f in moved)
            failures.append(f"the published numbers are not reproducible ({detail})")
            fail("the numbers did not reproduce", detail)
        else:
            ok(
                "the numbers reproduced exactly",
                f"precision {report['precision']} recall {report['recall']}",
            )

    counts = report["counts"]
    corpus = report["corpus"]
    ok(
        "precision and recall are measured against a labelled fixture set",
        f"precision {report['precision']} recall {report['recall']} on "
        f"{corpus['id']} ({corpus['hash']}), {corpus['reports']} reports "
        f"across {corpus['incidents']} incidents",
    )

    false_merges = report["false_merges"]
    if false_merges:
        detail = "; ".join(
            f"{entry['report']} <- {', '.join(entry['merged_with'])}" for entry in false_merges
        )
        failures.append(f"{len(false_merges)} false-positive merge(s)")
        fail(f"S14.3 zero false-positive merges - {len(false_merges)} found", detail)
    else:
        ok("S14.3 zero false-positive merges", f"{counts['true_positives']} true merge(s) made")

    section("Clause 3 - Stage 1 eliminates >= 90% of candidates, by query plan")
    # Inside the container, not on the host. The host interpreter has neither
    # pytest-cov nor psycopg, so running it here fails on the arguments rather
    # than on the claim — a "gate failed" that tells you nothing about dedup.
    code, output = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "NEMESIS_TEST_ADMIN_DSN=postgresql://nemesis:nemesis@postgres:5432/postgres",
            "api",
            "pytest",
            "tests/test_dedup_queries.py",
            "-k",
            "eliminates",
            "-q",
            "--no-cov",
        ]
    )
    if code == 0:
        ok(
            "the elimination ratio and the GiST index are asserted from EXPLAIN output",
            "test_stage_one_eliminates_at_least_ninety_percent_by_index",
        )
    else:
        failures.append("the Stage 1 elimination test did not pass")
        fail("the Stage 1 elimination test did not pass", output.strip()[-400:])

    section("Clause 4 - decision latency within the S27.1 budget")
    latency = report["latency_ms"]
    p95 = latency.get("p95")
    if p95 is None:
        failures.append("no latency was measured")
        fail("no latency was measured")
    elif p95 > BUDGET_MS:
        failures.append(f"p95 {p95:.0f} ms exceeds the {BUDGET_MS:.0f} ms budget")
        fail(f"p95 {p95:.0f} ms exceeds budget", f"budget {BUDGET_MS:.0f} ms")
    else:
        ok(
            f"p95 {p95:.1f} ms over {latency['n']} decision(s)",
            f"budget {BUDGET_MS:.0f} ms (Stage 1 + Stage 2, encoder excluded)",
        )

    return _verdict(failures)


def _verdict(failures: list[str]) -> int:
    print()
    if failures:
        fail(
            f"Phase 10 gate NOT met - {len(failures)} clause(s) failed",
            "; ".join(failures),
        )
        sys.stderr.write(
            "\n      The measurement is published in docs/reports/dedup-precision-recall.md\n"
            "      together with its diagnosis: on this corpus the true-duplicate and\n"
            "      false-merge confidence distributions interleave, so no merge threshold\n"
            "      separates them and the shortfall is a property of the text modality\n"
            "      rather than of the tuning. Adjusting a threshold against that same\n"
            "      corpus until this gate passes would publish a number about the\n"
            "      adjustment, so it has not been done.\n"
        )
        return 1
    ok("Phase 10 gate met", "every clause passed against the running stack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
