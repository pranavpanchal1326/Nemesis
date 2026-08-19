"""Perception failures, as a small closed set the stage can act on.

A fifth hierarchy beside ``control_plane.errors``, ``policy.errors``,
``simulation.errors`` and ``trust.errors``, importing from none of them. The
question these answer is narrower than any of those: **"can this build say
anything true about what is in this submission?"**

**The split that decides everything downstream is unavailable-vs-failed.** An
encoder that is not installed in this image will still not be installed in
thirty seconds, so the stage maps that onto ``StageUnavailableError`` and takes
the §24.2 fallback immediately — ``pending_classification``, parked for a human,
with the report intact. A model that *is* loaded and threw is a different fact:
the next attempt may succeed, so it is retryable and spends the budget.

**Nothing here is a reason to guess.** The classification stage never invents a
category to keep the pipeline moving; §24.2's degraded path exists precisely so
"we do not know" is a shippable outcome. A perception layer whose failure mode
is a plausible-looking wrong category is worse than one that stops, because a
wrong category routes a citizen's report to the wrong department with a
confidence number attached to it.
"""

from __future__ import annotations


class PerceptionError(Exception):
    """Base for every refusal this package makes."""


class EncoderUnavailableError(PerceptionError):
    """No encoder of the requested kind is registered in this process.

    The expected state in four of the six images: the API, ``worker-io``,
    ``relay`` and ``webhooks`` have never imported torch and never will. It is
    an error rather than a null encoder for the reason ``detectors`` gives about
    face detection — a stand-in that returns a zero vector produces a
    classification for every submission, at a confidence that looks credible,
    and the wrongness is invisible from the event log outward.
    """


class TranscriptionUnavailableError(PerceptionError):
    """No transcriber is registered, and this submission is audio-only.

    Separate from ``EncoderUnavailableError`` because the consequence differs: a
    missing image encoder degrades a photo report to ``pending_classification``,
    while a missing transcriber on a *voice* report also means §11.2's safety
    re-check has no text at all. Both park the report; only one of them is also
    a safety gap, and an operator reading the log should not have to infer which.
    """


class ModelLoadError(PerceptionError):
    """The weights are absent, truncated, or refused to initialise.

    Distinct from "not installed": the library is here and the load was
    attempted and failed, which is the state ``scripts/fetch_models.py`` exists
    to prevent and the one an air-gapped deployment reaches when the cache
    volume was never populated. Carries the model id, because "a model failed to
    load" without naming which is a message that costs an operator the first ten
    minutes of an incident.
    """


class ModelCapacityError(PerceptionError):
    """A model cannot be resident without exceeding the registry's ceiling.

    Raised rather than swallowed by evicting something in use. The ceiling is a
    real constraint — ``worker-ml`` has the tightest memory cap in the
    deployment and the OOM killer does not produce a log line anybody can act on
    — so a request that cannot be satisfied within it is refused with a number,
    which is a debuggable event, instead of being satisfied by an eviction that
    makes the next request reload 600 MB of weights.
    """


class PromptSetUnavailableError(PerceptionError):
    """The tenant has no active prompt set for this encoder and locale.

    Not a bug and not a crash: it is Phase 5's gate seen from the other side. A
    tenant whose taxonomy carries no prompts cannot be classified zero-shot, and
    the correct outcome is the §24.2 fallback plus a message that names the
    missing (locale, encoder) pair — which is a control-plane fix an operator
    can make in a minute, if they are told what it is.
    """


class CalibrationError(PerceptionError):
    """A calibration document cannot be applied to these scores.

    Reachable only through a policy document that validated but disagrees with
    the taxonomy — a temperature for a category that no longer exists, say.
    Raised rather than ignored: silently dropping a calibration entry means a
    category is scored on the raw curve while its approved document says
    otherwise, and the two are indistinguishable in the output.
    """


__all__ = [
    "CalibrationError",
    "EncoderUnavailableError",
    "ModelCapacityError",
    "ModelLoadError",
    "PerceptionError",
    "PromptSetUnavailableError",
    "TranscriptionUnavailableError",
]
