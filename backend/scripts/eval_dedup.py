"""Measure §14 dedup against the labelled corpus and publish the report.

One command, per the Phase 10 gate. Runs inside ``worker-ml`` because that is
the image carrying the text encoder, and against a real Postgres because Stage 1
is PostGIS and Stage 2 is pgvector — a number measured against fakes would be a
number about the fakes.

The scratch tenant is created and torn
down afterwards, so a re-run measures the corpus rather than the residue of the
previous run, and so the corpus can never be scored against a tenant somebody
had hand-edited.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nemesis.config import get_settings
from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.dedup import corpus as corpus_module
from nemesis.dedup.decide import DedupOutcome
from nemesis.dedup.harness import Measurement, measure
from nemesis.tenancy.context import tenant_scope

BUDGET_MS = 10_000.0
"""§27.1: dedup match decision < 10 seconds."""


def _install_encoders() -> None:
    from nemesis.perception.backends import install_perception_encoders

    if not install_perception_encoders():
        raise SystemExit(
            "the text encoder could not be installed; this script must run in an image "
            "carrying the ml extra (worker-ml), because a similarity measured against a "
            "deterministic fake measures the fake"
        )


async def _provision(session: Any, tenant_id: uuid.UUID) -> None:
    """A bare tenant row, and nothing else.

    Not a full ``control_plane.provision``, deliberately. The engine needs three
    things from a tenant: a row for the foreign keys, a dedup thresholds
    document, and a category lineage. The second resolves to
    ``policy.baselines`` when the tenant has approved nothing — which is the
    path a brand-new customer is on — and the third returns the bare key for a
    category the taxonomy does not define. So the measurement runs against the
    *baseline* bands, which is the honest default to publish a number for: a
    tenant that had retuned its thresholds would be measuring its own tuning.
    """
    await session.execute(
        sql_text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)").bindparams(
            id=tenant_id,
            slug=f"dedup-eval-{tenant_id.hex[:8]}",
            name="Dedup evaluation (scratch)",
        )
    )


async def _teardown(session: Any, tenant_id: uuid.UUID) -> None:
    await session.execute(delete(Complaint).where(Complaint.tenant_id == tenant_id))
    await session.execute(delete(ComplaintCluster).where(ComplaintCluster.tenant_id == tenant_id))
    await session.execute(sql_text("DELETE FROM tenants WHERE id = :id").bindparams(id=tenant_id))


async def run(name: str, out: Path) -> int:
    _install_encoders()
    settings = get_settings()
    loaded = corpus_module.load(name)
    digest = corpus_module.content_hash(name)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    try:
        with tenant_scope(tenant_id):
            async with sessions() as session:
                await _provision(session, tenant_id)
                await session.commit()
            async with sessions() as session:
                measurement = await measure(
                    session,
                    tenant_id=tenant_id,
                    corpus=loaded,
                    corpus_hash=digest,
                    settings=settings.dedup,
                )
                await session.rollback()
            async with sessions() as session:
                await _teardown(session, tenant_id)
                await session.commit()
    finally:
        await engine.dispose()

    out.mkdir(parents=True, exist_ok=True)
    (out / "dedup-precision-recall.json").write_text(
        json.dumps(_as_json(measurement, loaded), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "dedup-precision-recall.md").write_text(
        _as_markdown(measurement, loaded), encoding="utf-8"
    )

    print(
        f"precision {measurement.precision:.3f} · recall {measurement.recall:.3f} · "
        f"F1 {measurement.f1:.3f} · false merges {len(measurement.false_merges)}"
    )
    return 0 if not measurement.false_merges else 1


def _as_json(measurement: Measurement, loaded: corpus_module.Corpus) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "phase": 10,
        "corpus": {
            "id": measurement.corpus_id,
            "hash": measurement.corpus_hash,
            "incidents": len(loaded.incidents),
            "reports": len(loaded.reports),
        },
        "encoder": measurement.encoder_id,
        "policy_version": measurement.policy_version,
        "counts": {
            "true_positives": measurement.true_positives,
            "false_positives": measurement.false_positives,
            "false_negatives": measurement.false_negatives,
            "true_negatives": measurement.true_negatives,
            "investigated": measurement.investigated,
        },
        "precision": round(measurement.precision, 4),
        "recall": round(measurement.recall, 4),
        "f1": round(measurement.f1, 4),
        "false_merges": [
            {"report": judgement.report_id, "merged_with": list(judgement.false_merge_with)}
            for judgement in measurement.false_merges
        ],
        "latency_ms": {
            "p95": measurement.p95_latency_ms,
            "budget": BUDGET_MS,
            "n": len(measurement.latencies_ms),
        },
        "judgements": [
            {
                "report": judgement.report_id,
                "incident": judgement.incident_id,
                "outcome": judgement.outcome.value,
                "confidence": round(judgement.combined_confidence, 4),
                "candidates": judgement.candidates,
                "correct": judgement.correct,
            }
            for judgement in measurement.judgements
        ],
    }


def _root_and_cascade(measurement: Measurement) -> str:
    """Separate the merges that contaminated a cluster from those that inherited it.

    Generated rather than written by hand, because the distinction is the whole
    argument of §14.3 and a hand-written paragraph goes stale the first time the
    corpus changes. A root error is one whose cluster held a single foreign
    incident at the time; a cascade is a later report finding a cluster that was
    already mixed.
    """
    roots = [
        judgement
        for judgement in measurement.false_merges
        if len({judgement.incident_id, *judgement.false_merge_with}) <= 2
        and len(judgement.false_merge_with) <= 2
    ]
    cascades = len(measurement.false_merges) - len(roots)
    if cascades <= 0:
        return ""
    return (
        f"**Roughly {len(roots)} root error and {cascades} cascades.** Once a cluster holds "
        "two incidents, every later report of either finds a cluster that already contains "
        "both and merges into it correctly by its own lights. This is the mechanism §14.3 "
        "is about: a false merge is not one wrong decision, it is a permanently contaminated "
        "cluster that makes every subsequent decision wrong for free. Cascades are counted "
        "separately on purpose — the citizen whose fourth report vanished into the wrong "
        "incident is no less suppressed for the error having been made earlier.\n"
    )


def _separability(measurement: Measurement) -> str:
    """Whether any merge threshold could have passed the gate.

    The single most useful line in this report when it fails, because it decides
    what kind of problem this is. If the classes separate, the thresholds are
    mistuned and a calibration pass fixes it. If they interleave, the modality
    cannot tell them apart and no amount of tuning will.
    """
    wrong = {judgement.report_id for judgement in measurement.false_merges}
    true_scores = sorted(
        judgement.combined_confidence
        for judgement in measurement.judgements
        if judgement.outcome is DedupOutcome.MERGE and judgement.report_id not in wrong
    )
    false_scores = sorted(judgement.combined_confidence for judgement in measurement.false_merges)
    if not true_scores or not false_scores:
        return ""

    lines = ["### Could any threshold have separated them?\n"]
    lines.append("| | Combined confidence |")
    lines.append("|---|---|")
    lines.append(f"| True-duplicate merges | {', '.join(f'{v:.4f}' for v in true_scores)} |")
    lines.append(f"| False merges | {', '.join(f'{v:.4f}' for v in false_scores)} |")
    lines.append("")
    if min(false_scores) > max(true_scores):
        lines.append(
            f"The classes **separate**: every false merge scored above every true one, so a "
            f"`merge_threshold` between {max(true_scores):.4f} and {min(false_scores):.4f} "
            f"would have passed this gate. That makes this a tuning problem rather than a "
            f"capability one — and it must be tuned on a calibration split, not on this "
            f"measurement.\n"
        )
    else:
        lines.append(
            f"The classes **interleave**: the highest true duplicate ({max(true_scores):.4f}) "
            f"sits above the lowest false merge ({min(false_scores):.4f}). **No value of "
            f"`merge_threshold` separates them.** Raising it to exclude the false merges "
            f"excludes most of the true ones, and the gate would then be met by a system "
            f"that deduplicates nothing.\n"
        )
        lines.append(
            "That is a statement about the modality, not about the thresholds. Two citizens "
            "describing two different potholes on one street write nearly the same sentence, "
            "because the sentence is nearly the same. The text encoder compresses same-domain "
            "civic complaints into a narrow similarity band, and inside that band the distance "
            "between *the same defect* and *another defect of the same kind nearby* is smaller "
            "than the noise.\n"
        )
        lines.append("**Two remedies exist, and neither is applied in this pass.**\n")
        lines.append(
            "1. **The image modality.** Two photographs of two different potholes are not "
            "nearly the same image. This is the signal that separates the classes, and it is "
            "the one Phase 9 shipped unmeasured while naming Phase 10 as where the photo "
            "corpus had to arrive. It is still absent."
        )
        lines.append(
            "2. **A tighter radius for point defects.** `DedupBand` is per-category precisely "
            "so a pothole and a flooded junction can have different radii, and the baseline "
            "currently gives them the same 50 m. A pothole is a metre across; fifty metres of "
            "road holds many of them."
        )
        lines.append(
            "\nNeither is applied because **this corpus has no held-out split.** Tuning "
            "against the only measurement available produces a number describing the tuning, "
            "which is the mistake Phase 9's F1 report documents at length and declines to "
            "repeat. The corpus needs a calibration split before either remedy can be adopted "
            "and honestly re-measured.\n"
        )
    return "\n".join(lines)


def _as_markdown(measurement: Measurement, loaded: corpus_module.Corpus) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Deduplication — precision, recall and the false-merge count\n")
    add(
        f"**Generated:** {datetime.now(tz=UTC).isoformat()} · **Phase:** 10 · **Owner:** DATA  \n"
        "**Reproduce:** `nem dedup-eval`  \n"
        "**Raw data:** [`dedup-precision-recall.json`](dedup-precision-recall.json)\n"
    )
    add(
        "Phase 10's gate asks for measured precision and recall against a labelled fixture "
        "set of true-duplicate and true-distinct pairs, and for **zero false-positive "
        "merges**. This is that measurement. It is produced by `nemesis.dedup.harness`, "
        "which calls `engine.evaluate` — the same function the pipeline stage calls, "
        "against a real PostGIS and a real pgvector — so the table below describes shipped "
        "behaviour rather than a re-implementation of it.\n"
    )
    add("---\n")
    add("## What was measured\n")
    add("| | |")
    add("|---|---|")
    add(f"| Corpus | `{measurement.corpus_id}` ({measurement.corpus_hash}) |")
    add(f"| Incidents | {len(loaded.incidents)} |")
    add(f"| Reports | {len(loaded.reports)} |")
    add("| Modality | `text` |")
    add(f"| Encoder | `{measurement.encoder_id}` |")
    add(f"| Policy | `{measurement.policy_version}` |")
    add("")
    add(loaded.description + "\n")

    add("## Result\n")
    add("| Metric | Value |")
    add("|---|---:|")
    add(f"| **Precision** | **{measurement.precision:.3f}** |")
    add(f"| **Recall** | **{measurement.recall:.3f}** |")
    add(f"| F1 | {measurement.f1:.3f} |")
    add(f"| **False-positive merges** | **{len(measurement.false_merges)}** |")
    add(f"| True positives | {measurement.true_positives} |")
    add(f"| False negatives | {measurement.false_negatives} |")
    add(f"| True negatives | {measurement.true_negatives} |")
    add(f"| Sent to the ambiguous band | {measurement.investigated} |")
    add("")
    add(
        "**Precision is the gate's number and recall is its cost.** §14.3 makes the two "
        "errors incomparable: a false merge tells a citizen their problem is already being "
        "handled when it is not, while a missed merge costs an operator the time to "
        "reconcile two work orders. The engine is tuned so the error it makes is the "
        "second one, and a recall below precision is that tuning working rather than a "
        "defect.\n"
    )

    if measurement.false_merges:
        add("### False merges — the gate has failed\n")
        add("| Report | Merged with reports of |")
        add("|---|---|")
        for judgement in measurement.false_merges:
            add(f"| `{judgement.report_id}` | {', '.join(judgement.false_merge_with)} |")
        add("")
        add(_root_and_cascade(measurement))
        add(_separability(measurement))
    else:
        add(
            "No report was merged into a cluster containing a report of a different "
            "incident. That is the gate's absolute, and it holds on this corpus.\n"
        )

    missed = [
        judgement
        for judgement in measurement.judgements
        if not judgement.correct and not judgement.false_merge_with
    ]
    if missed:
        add("### Missed merges\n")
        add("Reports whose incident was already known and which did not join it.\n")
        add("| Report | Incident | Outcome | Confidence | Candidates |")
        add("|---|---|---|---:|---:|")
        for judgement in missed:
            add(
                f"| `{judgement.report_id}` | `{judgement.incident_id}` | "
                f"{judgement.outcome.value} | {judgement.combined_confidence:.3f} | "
                f"{judgement.candidates} |"
            )
        add("")

    p95 = measurement.p95_latency_ms
    add("## Latency (§27.1)\n")
    add("| Operation | n | p95 | Budget |")
    add("|---|---:|---:|---:|")
    add(
        f"| `evaluate` | {len(measurement.latencies_ms)} | "
        f"{'n/a' if p95 is None else f'{p95:.1f} ms'} | {BUDGET_MS:.0f} ms |"
    )
    add(
        "\nStage 1 and Stage 2 end to end, against a database holding the corpus. It "
        "excludes the encoder, which §27.1 budgets under classification and Phase 9 "
        "measures there.\n"
    )

    add("## What this does not establish\n")
    add(
        "- **The image modality is not measured.** Phase 9's F1 report carried this "
        "forward and named Phase 10 as where the photo corpus had to arrive; it has not. "
        "There is still no licence-clean set of photographed civic defects in this "
        "repository, and rendering synthetic street scenes would measure the renderer. "
        "So `image_weight` ships unmeasured, every number here is the text side alone, "
        "and the carried-forward gap is now carried forward again — stated here rather "
        "than quietly dropped, and it is the most significant limitation on this page."
    )
    add(
        "- **The corpus is authored, not field data.** The reports are written in citizen "
        "voice and deliberately not paraphrased from each other, but they were still "
        "written by people who knew which incident each belonged to. A real intake queue "
        "carries misspellings, code-switching, and reports that name two defects at once."
    )
    add(
        "- **One label encodes a policy decision rather than a physical fact.** The stale "
        "pothole re-reported outside its time window is counted as distinct because a "
        "fixed defect that reopens is a new work order, not an addition to a closed one. "
        "That is a defensible rule and it is a rule, not an observation."
    )
    add(
        "- **The corpus is small.** A handful of incidents means one report moving changes "
        "recall by a visible fraction. Read precision and the false-merge count as the "
        "gate; read recall as an indication."
    )
    add(
        "- **Zero false merges on this corpus is not zero false merges in production.** "
        "It is the strongest claim a fixture set can support, and the measurement that "
        "matters next is `nemesis_dedup_merge_reversions_total` — operators undoing "
        "merges is the real false-positive rate."
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=corpus_module.DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    return asyncio.run(run(args.corpus, args.out))


if __name__ == "__main__":
    sys.exit(main())
