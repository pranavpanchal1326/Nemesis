"""Deterministic encoders the Phase 9 tests share.

**Why a fake here when the F1 report insists on real models.** The two answer
different questions and the split is the whole reason both exist. The report
asks *how well does multilingual-e5 classify a citizen's sentence*, which only a
real checkpoint can answer. These tests ask *does the scoring rule do what the
module docstring says*, and that question has exact answers — this category wins,
that one abstains, the margin is this number — which a real model can only ever
make probable. A suite built on a real encoder tests the encoder, is slow, and
goes red when somebody upgrades a checkpoint for a reason unconnected to any bug.

**Why the fakes are hand-written rather than mocks.** ``ImageEncoder`` and
``TextEncoder`` are protocols precisely so an implementation can be six lines
with no base class and no patching. A test that reaches for ``unittest.mock``
here would be asserting that the code called the function it was told to call,
which is true by construction and interesting to nobody.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from nemesis.perception.encoders import Transcript, l2_normalise

#: Width of the fake vectors. Not 512 or 384: a test that used the real widths
#: would invite a reader to believe the numbers came from somewhere, and every
#: assertion here is about arithmetic that does not care how wide the vector is.
DIMENSIONS = 8


def unit(*components: float, dimensions: int = DIMENSIONS) -> tuple[float, ...]:
    """A normalised vector padded out to ``dimensions``.

    ``dimensions`` is a parameter because two kinds of test need different
    widths for opposite reasons. The scoring and harness tests want the narrowest
    vector that makes the arithmetic checkable by eye; the stage tests write
    their vectors into ``complaints.text_embedding``, which is ``vector(384)``,
    and a narrower one is refused by the width guard before it reaches pgvector.

    Normalised because ``encoders`` promises every vector it returns is, and
    ``cosine`` is only a cosine if that promise holds. A test that fed
    un-normalised vectors into ``score_against`` would be measuring magnitudes.
    """
    padded = list(components) + [0.0] * (dimensions - len(components))
    return l2_normalise(padded)


def axis(index: int, *, dimensions: int = DIMENSIONS) -> tuple[float, ...]:
    """The ``index``-th basis vector: cosine 1.0 with itself, 0.0 with the rest.

    The sharpest available input. A category whose prompt is ``axis(0)`` and a
    query of ``axis(0)`` produce a similarity of exactly 1.0, so an assertion
    about the winner is about the pooling rule rather than about float noise.
    """
    return unit(
        *[1.0 if position == index else 0.0 for position in range(dimensions)],
        dimensions=dimensions,
    )


def tilted(
    index: int, other: int, fraction: float, *, dimensions: int = DIMENSIONS
) -> tuple[float, ...]:
    """A vector ``fraction`` of the way from ``axis(index)`` toward ``axis(other)``.

    For the near-tie cases the margin rule exists for. The resulting cosines are
    computable by hand, which is what makes "abstained because the margin was
    0.04" an assertion rather than an observation.
    """
    components = [0.0] * dimensions
    components[index] = math.cos(fraction * math.pi / 2)
    components[other] = math.sin(fraction * math.pi / 2)
    return unit(*components, dimensions=dimensions)


class DictTextEncoder:
    """Maps exact strings to vectors from a table. Refuses anything else.

    **Refuses rather than returning a default**, which is the entire value of the
    double: a prompt the test did not plan for is a test that has drifted from
    what it claims to assert, and a zero vector for it would score identically
    against everything and look like a model that had an opinion.
    """

    dimensions = DIMENSIONS
    footprint_bytes = 1024

    def __init__(self, table: dict[str, tuple[float, ...]], *, model_id: str = "fake-text@1"):
        self._table = dict(table)
        self._model_id = model_id
        #: Every prefix this encoder was called with, in order. The e5 asymmetry
        #: is a correctness property with no visible symptom — getting it
        #: backwards degrades retrieval and raises nothing — so the tests assert
        #: on it directly.
        self.prefixes: list[str] = []
        self.calls = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        self.prefixes.append(prefix)
        missing = [text for text in texts if text not in self._table]
        if missing:
            raise KeyError(
                f"the fake text encoder was asked for {missing!r}, which is not in its "
                f"table. Add it deliberately — a default vector here would make an "
                f"unplanned prompt score against every category and the test would still "
                f"pass."
            )
        return tuple(self._table[text] for text in texts)


class DictImageEncoder:
    """CLIP's two towers, both from tables. Same refusal rule as the text fake."""

    dimensions = DIMENSIONS
    footprint_bytes = 2048

    def __init__(
        self,
        images: dict[bytes, tuple[float, ...]],
        prompts: dict[str, tuple[float, ...]],
        *,
        model_id: str = "fake-clip@1",
    ) -> None:
        self._images = dict(images)
        self._prompts = dict(prompts)
        self._model_id = model_id
        self.prompt_calls = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        if data not in self._images:
            raise KeyError("the fake image encoder was handed bytes it has no vector for")
        return self._images[data]

    def encode_prompts(self, prompts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.prompt_calls += 1
        missing = [prompt for prompt in prompts if prompt not in self._prompts]
        if missing:
            raise KeyError(f"the fake image encoder has no vector for {missing!r}")
        return tuple(self._prompts[prompt] for prompt in prompts)


class FixedTranscriber:
    """Returns one transcript. For the §8.4 paths that do not depend on audio."""

    footprint_bytes = 4096

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript
        self.locales_seen: list[list[str]] = []

    @property
    def model_id(self) -> str:
        return self._transcript.model_id

    def transcribe(self, data: bytes, *, locales: Sequence[str]) -> Transcript:
        self.locales_seen.append(list(locales))
        return self._transcript


class ExplodingEncoder:
    """Raises on every call. For the degraded paths that must not halt a stage."""

    model_id = "exploding@1"
    dimensions = DIMENSIONS
    footprint_bytes = 1024

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        raise RuntimeError("this image will not decode")

    def encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("the text tower is broken")

    def encode_prompts(self, prompts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("the prompt tower is broken")


__all__ = [
    "DIMENSIONS",
    "DictImageEncoder",
    "DictTextEncoder",
    "ExplodingEncoder",
    "FixedTranscriber",
    "axis",
    "tilted",
    "unit",
]
