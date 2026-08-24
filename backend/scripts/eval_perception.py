"""Measure per-category precision, recall and F1, and publish the artefact.

This is the "one command" Phase 9's gate names:

    nem f1

which runs this inside ``worker-ml`` — the only image carrying the ml extra —
and writes ``docs/reports/perception-f1.json`` and ``docs/reports/perception-f1.md``.

**What it does, in order.**

1. Loads the labelled corpus and splits it, stratified by (category, locale),
   into a calibration third and a held-out two thirds. The split is computed
   from the corpus file alone, so re-running this on another machine produces
   the same two sets.
2. Embeds the tenant template's prompt sets with the real encoders.
3. Fits a per-category temperature, bias and abstain floor on the **calibration**
   split — the "per-tenant, per-category confidence calibration derived from
   measured curves" the phase ships — and writes the fitted document out in the
   shape the policy API accepts, so an operator proposes it rather than this
   script deploying it.
4. Scores the **held-out** split twice: once with the tenant template's document
   defaults (the baseline a new tenant actually gets) and once with the fitted
   curves. Both numbers are published; a fit that made things worse is a fact
   about the fit.
5. Measures distant-face recall against the registered MediaPipe detector, which
   is Phase 0's carried-forward §22.1 question and is explicitly *not* discharged
   by Phase 8's "a face was blurred".
6. Writes the JSON and the Markdown, prints the table, and exits non-zero only
   if it could not measure — never because the number was disappointing. Whether
   a disappointing number blocks the phase is ``scripts/gate_phase9.py``'s
   decision, and it belongs there: this program's job is to be honest, not to be
   the judge of its own output.

**Why the encoders are real and there is no ``--fake`` flag.** A deterministic
fake is the right instrument for asserting the *scoring rule*, and the test
suite uses one for exactly that. A published F1 measured against a fake would be
a number about the fake. The report records ``model_ids`` for every run so a
reader can check which is which, and the gate refuses a report whose model ids
do not name the checkpoints ``docs/MODELS.md`` declares.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nemesis.config import get_settings
from nemesis.perception import corpus as corpus_module
from nemesis.perception import harness
from nemesis.perception.encoders import (
    EncoderKind,
    active_image_encoder,
    active_text_encoder,
    active_transcriber,
)
from nemesis.perception.scoring import Calibration

ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS = ROOT / "docs" / "reports"
JSON_REPORT = REPORTS / "perception-f1.json"
MARKDOWN_REPORT = REPORTS / "perception-f1.md"
PROPOSED_CALIBRATION = REPORTS / "perception-calibration-proposed.json"

#: What the report says about itself. Written by hand, never generated: a caveat
#: a program composed is a caveat that will be regenerated into meaninglessness
#: the first time somebody changes the corpus.
CAVEATS: tuple[str, ...] = (
    "The corpus is **authored**, not field data. The sentences are written in citizen "
    "voice from the defect vocabulary in Blueprint §8 and deliberately not paraphrased "
    "from the prompt sets they are scored against — but they were still written by "
    "people who know what the categories are. A real intake queue carries misspellings, "
    "code-switching mid-sentence, dictation artefacts and reports that name two defects "
    "at once, and none of that is here. Treat this number as an upper bound on the text "
    "modality, and re-measure the day there are labelled field submissions.",
    "**The image modality is not measured at all.** There is no licence-clean corpus of "
    "photographed civic defects in this repository, and rendering synthetic street "
    "scenes to score CLIP against would measure the renderer. So the published F1 is "
    "the text modality's, the CLIP prompt sets ship unmeasured, and Phase 10 — which "
    "needs image embeddings for dedup Stage 2 anyway — is where that corpus has to "
    "arrive. This is the same shape of carried-forward gap the HNSW report records, "
    "stated in the same place rather than discovered later.",
    "The fitted calibration is **scale normalisation, not a calibrated posterior**. It "
    "maps each category's measured in-class/out-of-class similarity gap onto a common "
    "logit gap so the softmax can compare categories at all. A confidence of 0.7 from "
    "this system does not mean seven in ten, and the document's `provenance` field says "
    "so on every entry an approver will read.",
    "**Marathi is measurably worse than English and Hindi here, and the corpus cannot "
    "say why.** The per-locale table is the finding; the explanation is not in it. It "
    "could be the encoder (multilingual-e5's Marathi coverage is thinner than its "
    "Hindi), the prompts (written in Marathi by the same hand that wrote the corpus, so "
    "a systematic vocabulary mismatch would be invisible), or the corpus (four examples "
    "per category per locale is a small number to conclude anything from). Separating "
    "those three needs a native-speaker review of the prompts and more Marathi "
    "examples, and until that happens the honest statement is the gap and not a cause.",
    "**Nine held-out examples per category is a small sample and the per-category "
    "numbers move accordingly.** One example is 0.125 of a category's recall, so a "
    "category's F1 can shift by more than a tenth on a single sentence. Read the "
    "per-category column as an indication and the macro figure as the number, and treat "
    "a category moving between two runs of a prompt pass as noise unless the "
    "calibration-split work list moved with it.",
    "**The per-model latency table meets the budget and the transcriber only meets it "
    "for short clips.** Whisper runs at roughly 1.15x real time on this CPU — a "
    "two-second clip costs ~2.3 s, against 116 ms for a CLIP image encode and 36 ms for "
    "a text encode. The §27.1 budget is per *stage*, so a voice note longer than about "
    "eight seconds breaches it, and `NEMESIS_PERCEPTION__MAX_AUDIO_SECONDS` currently "
    "permits 300. Nothing breaks — the stage degrades to `pending_classification` and a "
    "human plays the clip — but the practical ceiling is set by the recording length "
    "rather than by the budget, and the number to watch is "
    '`nemesis_perception_inference_seconds{operation="transcribe"}` against the audio '
    "duration recorded on `media_transcribed`. This is a measurement, not a regression: "
    "it is what the model costs, and it was previously unmeasured.",
    "The distant-face recall curve uses a **controlled synthetic stimulus**, not "
    "photographs. That is the right instrument for the question — recall as a function "
    "of face size in pixels is geometric — and it is the wrong instrument for absolute "
    "recall on real bystanders, who have hats, hair, angles and motion blur. What the "
    "curve establishes is the size at which this detector stops finding a face it can "
    "otherwise find; what it does not establish is the fraction of real bystanders "
    "protected in a real photograph.",
)


#: The §43.2 prompt passes that have been done, newest last. Hand-written and
#: append-only, for the reason the caveats are: this is the record the gate looks
#: for when a category is below the floor, and a record a program generated would
#: say only that a program ran.
#:
#: **Each entry says what the calibration-split work list showed and what was
#: changed because of it.** Never what the held-out set showed — acting on that
#: turns the held-out set into a development set, and the re-measured number then
#: reports how well the prompts were tuned to the examples they were tuned on.
PROMPT_PASS: tuple[str, ...] = (
    "**Pass 1 (2026-08-23, template `municipality` 1.1.0 → 1.2.0).** The "
    "calibration-split work list showed two patterns, neither of them about the "
    "categories being hard. (a) `dead_animal` absorbed everything — four other "
    'categories leaked into it — because its prompts said "lying in a public '
    'place" and "on the roadside", which describes every street complaint in '
    "the taxonomy; it was rewritten around the carcass itself with the leaking "
    "categories as explicit negatives. (b) The three road-surface categories "
    "(`pothole`, `footpath_damage`, `open_manhole`) had no contrast against each "
    "other at all, so a sentence about a hole in the ground competed on generic "
    "road vocabulary; each now names the other two as negatives. Held-out macro "
    "F1 moved 0.574 → 0.595 and `pothole` moved 0.000 → 0.667.",
    "**Not done in pass 1, deliberately: `waterlogging`.** It is the worst "
    "held-out category (F1 0.000, confused with `water_leak`) and it does not "
    "appear in the calibration-split work list at all — it scores fine there. "
    "Editing it would be tuning against the measurement. It is carried to the "
    "next pass, which needs calibration examples that actually exercise the "
    "`waterlogging`/`water_leak` boundary rather than more prompt text.",
)


def _calibration_defaults() -> tuple[dict[str, Calibration], Calibration, float]:
    """The tenant-template defaults, read from the policy document's own defaults.

    Read off ``PerceptionCalibration`` rather than retyped, so the baseline this
    report publishes is the baseline a tenant with no approved document actually
    gets. A constant here would drift from the document the first time somebody
    changed a default, and the report would keep claiming the old one.
    """
    from nemesis.policy.documents import PerceptionCalibration

    document = PerceptionCalibration()
    default = Calibration(
        temperature=document.default_temperature,
        bias=0.0,
        abstain_below=document.default_abstain_below,
        min_margin=document.default_min_margin,
    )
    return {}, default, document.image_weight


def _install_encoders() -> None:
    from nemesis.perception.backends import install_perception_encoders
    from nemesis.perception.encoders import encoder_is_registered

    if encoder_is_registered(EncoderKind.TEXT):
        return
    if not install_perception_encoders():
        raise SystemExit(
            "this image does not carry the `ml` extra, so there is no text encoder to "
            "measure with. Run `nem f1`, which executes this inside worker-ml — the "
            "only image that has one. A report produced anywhere else would be a "
            "report about nothing."
        )


def run(
    *,
    corpus_id: str,
    template: str,
    locales: Sequence[str] | None,
    face_repeats: int,
    skip_faces: bool,
) -> harness.Report:
    labelled = corpus_module.load(corpus_id)
    calibration_split, holdout = labelled.split()

    # Installed here rather than assumed: this script runs as a bare process
    # inside ``worker-ml``, not inside a Celery child, so nothing has run the
    # worker startup path that normally registers the encoders. Failing with
    # ``EncoderUnavailableError`` instead would be technically accurate and
    # would send the reader looking at queue routing.
    _install_encoders()
    text_encoder = active_text_encoder()
    print(f"text encoder: {text_encoder.model_id}", flush=True)

    chosen_locales = tuple(locales) if locales else labelled.locales
    specs = _merge_specs(template, chosen_locales)
    if not specs:
        raise SystemExit(
            f"the {template!r} template declares no active `text` prompt sets for "
            f"{', '.join(chosen_locales)}. There is nothing to score against, and a "
            f"report produced from an empty prompt bundle would abstain on every "
            f"example and publish an F1 of zero that says nothing about the model."
        )
    print(
        f"prompts: {len(specs)} categor(y/ies) over locales {', '.join(chosen_locales)}",
        flush=True,
    )

    categories = harness.embed_specs(specs, encoder=text_encoder)
    empty_calibration, default, image_weight = _calibration_defaults()

    print(f"fitting calibration on {len(calibration_split)} example(s)...", flush=True)
    fitted = harness.fit(
        calibration_split,
        categories=categories,
        encoder=text_encoder,
        provenance=(
            f"perception harness, corpus {labelled.corpus_id} "
            f"({len(calibration_split)} calibration examples), "
            f"{text_encoder.model_id}, {datetime.now(UTC).date().isoformat()}"
        ),
    )

    print(f"scoring {len(holdout)} held-out example(s) at document defaults...", flush=True)
    baseline = harness.evaluate(
        holdout,
        corpus=labelled,
        split="holdout",
        text_categories=categories,
        text_encoder=text_encoder,
        calibration=empty_calibration,
        default=default,
        image_weight=image_weight,
    )

    print("scoring the same examples with the fitted curves...", flush=True)
    measured = harness.evaluate(
        holdout,
        corpus=labelled,
        split="holdout",
        text_categories=categories,
        text_encoder=text_encoder,
        calibration=harness.calibration_from(fitted),
        default=default,
        image_weight=image_weight,
    )

    # The §43.2 work list, on the split the curves were already fitted on. Never
    # the held-out one: see ``Report.worklist``.
    worklist = harness.evaluate(
        calibration_split,
        corpus=labelled,
        split="calibration",
        text_categories=categories,
        text_encoder=text_encoder,
        calibration=harness.calibration_from(fitted),
        default=default,
        image_weight=image_weight,
    )

    # §27.1 for all three models, not just the one the corpus happens to
    # exercise. The corpus is text, so everything above times `e5` — the
    # cheapest of the three — while the gate clause it satisfies says
    # "inference latency within the §27.1 budget". CLIP encode and Whisper
    # transcribe are the dominant production costs, and a budget checked
    # against the model that comfortably meets it is a budget nobody is
    # checking.
    per_model_latency = _per_model_latency(text_encoder)

    face_recall = None if skip_faces else _face_recall(face_repeats)

    return harness.Report(
        generated=datetime.now(UTC).isoformat(timespec="seconds"),
        corpus_id=labelled.corpus_id,
        corpus_fingerprint=labelled.fingerprint(),
        corpus_description=labelled.description,
        template=template,
        calibration_source=(
            f"fitted on the {len(calibration_split)}-example calibration split of "
            f"{labelled.corpus_id}"
        ),
        latency_budget_seconds=get_settings().perception.latency_budget_seconds,
        holdout=measured,
        baseline=baseline,
        worklist=worklist,
        per_model_latency=per_model_latency,
        calibration_split_size=len(calibration_split),
        fitted=fitted,
        face_recall=face_recall,
        caveats=CAVEATS,
        prompt_pass=PROMPT_PASS,
    )


def _merge_specs(template: str, locales: Sequence[str]) -> tuple[harness.PromptSpec, ...]:
    """One prompt spec per category, pooling every requested locale's prompts.

    **Pooled rather than one run per locale, and the choice is load-bearing.**
    A tenant's scoring path resolves *one* locale per submission, so a per-locale
    run would be the faithful simulation of a single report. But the number the
    gate asks for is per *category*, and nine categories times three locales is
    twenty-seven numbers with four held-out examples behind each — noise wearing
    a decimal point. Pooling gives one number per category with the whole
    held-out set behind it, and the per-locale macro F1 in the report is what
    answers "does this work in Marathi" separately.
    """
    pooled: dict[str, tuple[list[str], list[str]]] = {}
    for locale in locales:
        for spec in harness.prompt_specs_from_template(template, locale=locale, encoder="text"):
            positives, negatives = pooled.setdefault(spec.category, ([], []))
            positives.extend(spec.prompts)
            negatives.extend(spec.negative_prompts)
    return tuple(
        harness.PromptSpec(
            category=category,
            prompts=tuple(positives),
            negative_prompts=tuple(negatives),
        )
        for category, (positives, negatives) in sorted(pooled.items())
    )


def _probe_image() -> bytes:
    """A small PNG, encoded with Pillow — a base dependency in every image.

    Synthetic on purpose and it does not matter here: this measures how long
    CLIP's image tower takes on a 64 by 64 RGB input, which is a property of the
    tower and the CPU rather than of what the picture is of. What a synthetic
    input cannot measure is *accuracy*, which is why the F1 table above says
    `text` in its modality column and the caveats say the image modality is
    unmeasured.
    """
    import io

    from PIL import Image

    image = Image.new("RGB", (64, 64))
    image.putdata(
        [((x * 3) % 256, (y * 5) % 256, (x + y) % 256) for y in range(64) for x in range(64)]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _probe_audio(seconds: float = 2.0, rate: int = 16000) -> bytes:
    """Two seconds of 16-bit PCM WAV, built from bytes. Standard library only.

    A swept tone rather than silence: Whisper's VAD filter discards pure silence
    before the model sees it, so a silent clip would time the VAD and nothing
    else. Not speech — see the caveat about transcription quality.
    """
    import math
    import struct

    frames = int(rate * seconds)
    samples = bytearray()
    for index in range(frames):
        phase = 2.0 * math.pi * (200.0 + 400.0 * index / frames) * index / rate
        samples += struct.pack("<h", int(12000 * math.sin(phase)))
    data = bytes(samples)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def _per_model_latency(text_encoder: Any) -> tuple[harness.LatencySummary, ...]:
    """Time each registered model once per pass. Absent models are skipped.

    Failures are caught and dropped rather than raised: a worker with no
    transcriber is a real and supported deployment (§8.4 is optional intake),
    and a latency measurement that took the report down with it would be an
    observability feature causing an outage.
    """
    from nemesis.perception.encoders import EncoderKind, encoder_is_registered

    image_encoder = active_image_encoder() if encoder_is_registered(EncoderKind.IMAGE) else None
    transcriber = active_transcriber() if encoder_is_registered(EncoderKind.TRANSCRIBE) else None

    try:
        return harness.measure_inference_latency(
            image_encoder=image_encoder,
            text_encoder=text_encoder,
            transcriber=transcriber,
            image=_probe_image() if image_encoder is not None else None,
            audio=_probe_audio() if transcriber is not None else None,
        )
    except Exception as exc:  # pragma: no cover - a broken model, not a data error
        print(f"per-model latency could not be measured: {exc}", file=sys.stderr)
        return ()


def _face_recall(repeats: int) -> harness.FaceRecallResult | None:
    from nemesis.trust.detectors import active_detector, detector_is_registered
    from nemesis.trust.providers import install_trust_workers

    if not detector_is_registered():
        install_trust_workers()
    if not detector_is_registered():
        print(
            "no face detector registered in this process; skipping the §22.1 recall curve",
            file=sys.stderr,
        )
        return None
    detector = active_detector()
    print(f"measuring distant-face recall against {detector.detector_id}...", flush=True)
    return harness.measure_face_recall(detector, repeats=repeats)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _table(result: harness.EvaluationResult) -> str:
    header = (
        "| Category | Support | Precision | Recall | **F1** | Coverage | Abstained | Forced F1 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    rows = []
    for entry in result.per_category:
        if entry.support == 0:
            continue
        mark = "" if entry.meets_floor else " ⚠"
        rows.append(
            f"| `{entry.category}` | {entry.support} | {entry.precision:.3f} | "
            f"{entry.recall:.3f} | **{entry.f1:.3f}**{mark} | {entry.coverage:.2f} | "
            f"{entry.abstentions} | {entry.forced_f1:.3f} |"
        )
    return header + "\n".join(rows) + "\n"


def _markdown(report: harness.Report) -> str:
    result = report.holdout
    below = report.holdout.below_floor
    lines: list[str] = []
    add = lines.append

    add("# Perception layer — per-category precision, recall and F1")
    add("")
    add(
        f"**Generated:** {report.generated} · **Phase:** 9 · **Owner:** DATA  \n"
        f"**Reproduce:** `nem f1`  \n"
        f"**Raw data:** [`perception-f1.json`](perception-f1.json) · "
        f"**Proposed calibration:** "
        f"[`perception-calibration-proposed.json`](perception-calibration-proposed.json)"
    )
    add("")
    add(
        "Phase 9's gate is *a published per-category F1 number in the repo, reproducible "
        "by one command*. This is that number. It is measured by "
        "`nemesis.perception.harness`, which calls `scoring.decide` — the same function "
        "the pipeline stage calls — so the table below describes shipped behaviour "
        "rather than a re-implementation of it."
    )
    add("")
    add("---")
    add("")
    add("## What was measured")
    add("")
    add("| | |\n|---|---|")
    add(f"| Corpus | `{report.corpus_id}` ({report.corpus_fingerprint[:12]}) |")
    add(f"| Held-out examples | {len(result.predictions)} |")
    add(f"| Calibration examples | {report.calibration_split_size} |")
    add(f"| Modality | `{result.modality}` |")
    add(f"| Models | {', '.join(f'`{model}`' for model in result.model_ids) or '—'} |")
    add(f"| Calibration | {report.calibration_source} |")
    add("")
    add(report.corpus_description)
    add("")
    add("## Result")
    add("")
    add(_table(result))
    add(
        f"**Macro F1 {result.macro_f1:.3f}** · micro F1 {result.micro_f1:.3f} · "
        f"coverage {result.coverage:.2f} · forced macro F1 {result.forced_macro_f1:.3f}"
    )
    add("")
    if report.baseline is not None:
        add(
            f"At the tenant template's **document defaults**, the same held-out examples "
            f"score macro F1 {report.baseline.macro_f1:.3f} at coverage "
            f"{report.baseline.coverage:.2f}. The fitted curves are what the difference "
            f"buys, and they are a proposal for an approver rather than a deployment."
        )
        add("")
    add("### Reading the columns")
    add("")
    add(
        "**Coverage** is the share of a category's held-out examples that got any answer "
        "at all. It is beside F1 in every row because an abstention is counted as a false "
        "negative and never as a false positive — which is correct, since §24.2 sends the "
        "report to a human rather than to the wrong department, and which is also "
        "gameable: raise every abstain floor and precision goes to 1.0 while the system "
        "classifies nothing. **Forced F1** is the same model judged with abstention "
        "disabled. A row where F1 is high, coverage is low, and forced F1 is much lower "
        "is a category the system is declining to answer rather than answering well."
    )
    add("")
    add("### Per locale")
    add("")
    add("| Locale | Macro F1 |\n|---|---:|")
    for locale, value in report.holdout.per_locale.items():
        add(f"| `{locale}` | {value:.3f} |")
    add("")
    add(
        "Reported separately because §8.4's promise is that a complaint in the citizen's "
        "own language works, and a single number over a mixed-language corpus hides a "
        "language that does not. Hindi and Marathi are the rows ADR-0003 chose "
        "multilingual-e5 for, and the ones nobody would notice were broken."
    )
    add("")
    add("## Where it goes wrong")
    add("")
    if result.confusions:
        add("| Truth | Called | Count |\n|---|---|---:|")
        for truth, predicted, count in result.confusions[:12]:
            add(f"| `{truth}` | `{predicted}` | {count} |")
    else:
        add("No held-out example was given a wrong category.")
    add("")
    add(
        "Held-out confusions, published for the reader. They are **not** the prompt-pass "
        "work list: rewriting prompts against these would turn the held-out set into a "
        "development set, and the next number would report how well the prompts were "
        "tuned to the examples they were tuned on."
    )
    add("")
    if report.worklist is not None:
        add("### The §43.2 work list (calibration split)")
        add("")
        if report.worklist.confusions:
            add("| Truth | Called | Count |")
            add("|---|---|---:|")
            for truth, predicted, count in report.worklist.confusions[:12]:
                add(f"| `{truth}` | `{predicted}` | {count} |")
        else:
            add("No calibration example was given a wrong category.")
        add("")
        add(
            "This is what a prompt author works from. *Category X scored 0.4* says "
            "something is wrong; *X was called Y five times* says which two prompts to "
            "contrast. It is measured on the split the calibration curves were already "
            "fitted on — already spent — so acting on it leaves the held-out number "
            "measuring examples nothing has been tuned against."
        )
        add("")
    add(f"## The {harness.F1_FLOOR:.0%} floor")
    add("")
    if below:
        add(
            "The following categories are below the gate's floor. Each one triggers the "
            "§43.2 prompt pass and a re-measure; the honest number ships either way, and "
            "this section is where the work done on them is recorded."
        )
        add("")
        add("| Category | F1 | Coverage | Confused with |\n|---|---:|---:|---|")
        for entry in below:
            confused = ", ".join(
                f"`{predicted}` x{count}"
                for truth, predicted, count in result.confusions
                if truth == entry.category
            )
            add(
                f"| `{entry.category}` | {entry.f1:.3f} | {entry.coverage:.2f} | "
                f"{confused or '—'} |"
            )
        add("")
        if report.prompt_pass:
            add("**Prompt pass:**")
            add("")
            for note in report.prompt_pass:
                add(f"- {note}")
            add("")
    else:
        add(
            f"Every measured category is at or above {harness.F1_FLOOR:.0%}. No §43.2 "
            f"prompt pass was triggered by this run."
        )
    add("")
    add("## Latency (§27.1)")
    add("")
    add(
        f"| Operation | n | p50 | p95 | max | Budget |\n|---|---:|---:|---:|---:|---:|\n"
        f"| `{result.latency.operation}` | {result.latency.count} | "
        f"{result.latency.p50 * 1000:.1f} ms | {result.latency.p95 * 1000:.1f} ms | "
        f"{result.latency.max * 1000:.1f} ms | "
        f"{report.latency_budget_seconds * 1000:.0f} ms |"
    )
    add("")
    add(
        "One example end to end — encode, score every category, fuse, decide — on this "
        "hardware, measured rather than estimated. Model *load* is excluded and reported "
        "by `nemesis_perception_model_load_seconds`: a cold start is a deployment "
        "property and no complaint after the first pays it."
    )
    add("")
    if report.per_model_latency:
        add("### Per model")
        add("")
        add("| Model pass | n | p50 | p95 | max | In budget |")
        add("|---|---:|---:|---:|---:|---|")
        for entry in report.per_model_latency:
            mark = "yes" if entry.p95 <= report.latency_budget_seconds else "**no**"
            add(
                f"| `{entry.operation}` | {entry.count} | {entry.p50 * 1000:.0f} ms | "
                f"{entry.p95 * 1000:.0f} ms | {entry.max * 1000:.0f} ms | {mark} |"
            )
        add("")
        add(
            "**Reported separately because the table above times the text encoder and "
            "nothing else.** The corpus is text, so a budget checked against it alone is "
            "a budget checked against the cheapest of the three models, while CLIP encode "
            "and Whisper transcribe are what a photographed or spoken report actually "
            "costs. These are single forward passes over one fixed input, with the first "
            "call discarded so the model load is not counted."
        )
        add("")
    if report.face_recall is not None:
        add("## Distant-face recall (§22.1)")
        add("")
        add(
            f"Detector `{report.face_recall.detector_id}`, IoU ≥ "
            f"{report.face_recall.iou_threshold}."
        )
        add("")
        add("| Face width | Faces present | Found | Recall | Mean confidence |")
        add("|---:|---:|---:|---:|---:|")
        for bucket in report.face_recall.buckets:
            add(
                f"| {bucket.face_pixels} px | {bucket.faces_present} | {bucket.faces_found} | "
                f"**{bucket.recall:.2f}** | {bucket.mean_confidence:.3f} |"
            )
        add("")
        smallest = report.face_recall.smallest_reliable
        add(
            f"**Smallest face size at full recall: "
            f"{f'{smallest} px' if smallest is not None else 'none — see the caveats'}.**"
        )
        add("")
        add(
            "This is Phase 0's carried-forward question and it is *not* discharged by "
            'Phase 8\'s "a face was blurred". `blaze_face_short_range` is a two-metre '
            "model; street photography is full of small bystanders, and small bystanders "
            "are exactly the population §22.1 protects. A shortfall here means a second "
            "detector or a tiled pass, not a footnote — and the number ships either way."
        )
        add("")
    add("## What this does not establish")
    add("")
    for caveat in report.caveats:
        add(f"- {caveat}")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="municipality-v1")
    parser.add_argument("--template", default="municipality")
    parser.add_argument("--locale", action="append", dest="locales")
    parser.add_argument("--face-repeats", type=int, default=4)
    parser.add_argument(
        "--skip-faces",
        action="store_true",
        help="omit the §22.1 recall curve (it needs the MediaPipe detector)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPORTS,
        help="directory the report artefacts are written to",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="measure and print, write nothing",
    )
    args = parser.parse_args()

    report = run(
        corpus_id=args.corpus,
        template=args.template,
        locales=args.locales,
        face_repeats=args.face_repeats,
        skip_faces=args.skip_faces,
    )

    print()
    print(_table(report.holdout))
    print(
        f"macro F1 {report.holdout.macro_f1:.3f} · micro F1 {report.holdout.micro_f1:.3f} · "
        f"coverage {report.holdout.coverage:.2f} · "
        f"p95 {report.holdout.latency.p95 * 1000:.1f} ms "
        f"(budget {report.latency_budget_seconds * 1000:.0f} ms)"
    )
    below = report.holdout.below_floor
    if below:
        print(
            f"below the {harness.F1_FLOOR:.0%} floor: "
            f"{', '.join(entry.category for entry in below)}"
        )
    if report.worklist is not None and report.worklist.confusions:
        # The prompt-pass work list, from the calibration split. Printed rather
        # than only written, because the person who just ran this is the person
        # about to do the prompt pass.
        print()
        print("§43.2 work list (calibration split):")
        for truth, predicted, count in report.worklist.confusions[:12]:
            print(f"  {truth} -> {predicted} x{count}")

    if args.print_only:
        return 0

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / JSON_REPORT.name).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / MARKDOWN_REPORT.name).write_text(_markdown(report), encoding="utf-8")
    (out / PROPOSED_CALIBRATION.name).write_text(
        json.dumps(
            harness.calibration_document(report.fitted),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out / JSON_REPORT.name}")
    print(f"wrote {out / MARKDOWN_REPORT.name}")
    print(f"wrote {out / PROPOSED_CALIBRATION.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
