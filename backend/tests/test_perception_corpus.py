"""The corpus and its split — the instrument the published F1 is measured with.

**Why the split gets this much attention.** Every number in
``docs/reports/perception-f1.md`` is a statement about the held-out set, and a
held-out set that is not actually held out, or that is missing a category, or
that changes when an unrelated example is edited, makes every one of those
numbers wrong in a way no reader can detect. The split is the part of this phase
where a silent bug is most expensive and least visible, so it is the part with
the most assertions.

The committed corpus is loaded and checked too. It is a data file, and data files
rot: a category renamed in the taxonomy, an example added with a duplicate id, a
stratum that quietly drops to one example. Each of those turns the report into a
number about a different thing while it keeps printing the same headings.
"""

from __future__ import annotations

import json

import pytest

from nemesis.perception import corpus as corpus_module
from nemesis.perception.corpus import (
    FACE_PIXEL_SIZES,
    MIN_STRATUM_SIZE,
    Corpus,
    CorpusError,
    Example,
    Provenance,
    face_stimulus,
    loads,
)

CORPUS_ID = "municipality-v1"


def _example(identifier: str, category: str, locale: str = "en") -> Example:
    return Example(
        id=identifier,
        category=category,
        locale=locale,
        provenance=Provenance.AUTHORED,
        text=f"text for {identifier}",
    )


def _corpus(*examples: Example) -> Corpus:
    from pathlib import Path

    return Corpus(
        corpus_id="test-v1",
        template="municipality",
        description="a test corpus",
        authored="2026-08-23",
        root=Path("."),
        examples=examples,
    )


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def test_every_stratum_appears_on_both_sides_of_the_split() -> None:
    """Stratified by (category, locale), not just by category.

    A held-out set that happened to put every Marathi example on one side would
    produce a per-category F1 that is really a per-language F1 wearing a
    category's name, and the failure is invisible from the number.
    """
    examples = [
        _example(f"{category}-{locale}-{index}", category, locale)
        for category in ("pothole", "garbage_pile")
        for locale in ("en", "hi", "mr")
        for index in range(4)
    ]
    calibration, holdout = _corpus(*examples).split()

    for side in (calibration, holdout):
        assert {item.stratum for item in side} == {
            (category, locale)
            for category in ("pothole", "garbage_pile")
            for locale in ("en", "hi", "mr")
        }


def test_the_two_sides_are_disjoint_and_cover_everything() -> None:
    examples = [_example(f"e{index}", "pothole") for index in range(9)]
    calibration, holdout = _corpus(*examples).split()

    assert not {item.id for item in calibration} & {item.id for item in holdout}
    assert len(calibration) + len(holdout) == 9


def test_the_split_does_not_depend_on_the_order_of_the_file() -> None:
    """Because the author wrote the easy ones first. Everybody does."""
    examples = [_example(f"e{index}", "pothole") for index in range(9)]
    forward = _corpus(*examples).split()
    backward = _corpus(*reversed(examples)).split()

    assert {item.id for item in forward[1]} == {item.id for item in backward[1]}


def test_adding_an_unrelated_example_does_not_reshuffle_another_stratum() -> None:
    """Stability under edit, which is what makes two reports comparable.

    A split keyed on a shuffle seed or on the example count would move every
    category's held-out set whenever anybody added a sentence anywhere, and two
    consecutive F1 numbers would differ for a reason nothing recorded.
    """
    base = [_example(f"p{index}", "pothole") for index in range(6)]
    other = [_example(f"g{index}", "garbage_pile") for index in range(6)]

    before = {item.id for item in _corpus(*base, *other).split()[1] if item.category == "pothole"}
    after = {
        item.id
        for item in _corpus(*base, *other, _example("g99", "garbage_pile")).split()[1]
        if item.category == "pothole"
    }
    assert before == after


def test_a_stratum_of_two_contributes_one_example_to_each_side() -> None:
    """``ceil``, not ``round`` — otherwise calibration gets nothing from it."""
    calibration, holdout = _corpus(_example("a", "x"), _example("b", "x")).split()
    assert len(calibration) == 1
    assert len(holdout) == 1


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_a_degenerate_calibration_fraction_is_refused(fraction: float) -> None:
    """One of the two halves would be empty, and the report would be measuring
    the set it was fitted on."""
    with pytest.raises(CorpusError, match="strictly between"):
        _corpus(_example("a", "x"), _example("b", "x")).split(calibration_fraction=fraction)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_a_duplicate_example_id_is_refused() -> None:
    document = json.dumps(
        {
            "corpus_id": "t",
            "template": "municipality",
            "description": "d",
            "authored": "2026-08-23",
            "examples": [
                {"id": "same", "category": "x", "locale": "en", "text": "one"},
                {"id": "same", "category": "x", "locale": "en", "text": "two"},
            ],
        }
    )
    with pytest.raises(CorpusError, match="duplicate example id"):
        loads(document, root=corpus_module.CORPUS_DIR)


def test_a_stratum_too_small_to_split_is_refused() -> None:
    """A category with no held-out examples reports an F1 of zero that says
    nothing about the model, while the report's category list claims to cover it."""
    document = json.dumps(
        {
            "corpus_id": "t",
            "template": "municipality",
            "description": "d",
            "authored": "2026-08-23",
            "examples": [
                {"id": "a", "category": "x", "locale": "en", "text": "one"},
                {"id": "b", "category": "y", "locale": "en", "text": "two"},
                {"id": "c", "category": "y", "locale": "en", "text": "three"},
            ],
        }
    )
    with pytest.raises(CorpusError, match="stratum too small"):
        loads(document, root=corpus_module.CORPUS_DIR)


def test_an_example_with_neither_text_nor_an_image_is_refused() -> None:
    """There is nothing to classify, and it would count as a miss for its label."""
    with pytest.raises(CorpusError, match="neither text nor an image"):
        Example(id="a", category="x", locale="en", provenance=Provenance.AUTHORED)


def test_an_unknown_provenance_is_refused_rather_than_defaulted() -> None:
    """Provenance is how a reader tells an authored sentence from a citizen's.

    Defaulting an unrecognised value would let a corpus claim a provenance the
    report then groups by, and the grouping would be wrong in the direction that
    flatters it.
    """
    document = json.dumps(
        {
            "corpus_id": "t",
            "template": "municipality",
            "description": "d",
            "authored": "2026-08-23",
            "examples": [
                {"id": "a", "category": "x", "locale": "en", "text": "1", "provenance": "vibes"},
                {"id": "b", "category": "x", "locale": "en", "text": "2"},
            ],
        }
    )
    with pytest.raises(CorpusError, match="provenance"):
        loads(document, root=corpus_module.CORPUS_DIR)


# ---------------------------------------------------------------------------
# The committed corpus
# ---------------------------------------------------------------------------


def test_the_committed_corpus_loads_and_splits() -> None:
    labelled = corpus_module.load(CORPUS_ID)
    calibration, holdout = labelled.split()

    assert labelled.examples
    assert calibration and holdout
    for count in labelled.counts().values():
        assert count >= MIN_STRATUM_SIZE


def test_every_corpus_category_exists_in_the_template_it_names() -> None:
    """The rot this catches: a taxonomy key renamed, and every example for it
    silently becoming a category the prompts can never win."""
    from nemesis.control_plane import templates

    labelled = corpus_module.load(CORPUS_ID)
    template = templates.load(labelled.template)
    selectable = {node.key for node in template.taxonomy if getattr(node, "is_selectable", True)}

    assert set(labelled.categories) <= selectable, (
        f"corpus categories missing from the {labelled.template} template: "
        f"{sorted(set(labelled.categories) - selectable)}"
    )


def test_every_corpus_locale_is_one_the_template_declares() -> None:
    from nemesis.control_plane import templates

    labelled = corpus_module.load(CORPUS_ID)
    template = templates.load(labelled.template)

    assert set(labelled.locales) <= set(template.locales)


def test_every_corpus_category_has_text_prompts_in_every_corpus_locale() -> None:
    """Otherwise the harness scores a category against nothing and reports a
    zero that looks like a model failure and is a configuration gap."""
    from nemesis.perception.harness import prompt_specs_from_template

    labelled = corpus_module.load(CORPUS_ID)
    for locale in labelled.locales:
        specs = prompt_specs_from_template(labelled.template, locale=locale, encoder="text")
        covered = {spec.category for spec in specs}
        missing = sorted(set(labelled.categories) - covered)
        assert not missing, f"no {locale!r} text prompts for {missing}"


def test_the_fingerprint_ignores_prose_and_tracks_labels() -> None:
    """A typo fixed in the description does not invalidate a measurement; a label
    changed does. A fingerprint that says otherwise is one people learn to ignore."""
    first = _corpus(_example("a", "x"), _example("b", "x"))
    reworded = Corpus(
        corpus_id=first.corpus_id,
        template=first.template,
        description="completely different prose",
        authored="2027-01-01",
        root=first.root,
        examples=first.examples,
    )
    relabelled = _corpus(_example("a", "y"), _example("b", "x"))

    assert first.fingerprint() == reworded.fingerprint()
    assert first.fingerprint() != relabelled.fingerprint()


# ---------------------------------------------------------------------------
# The face-scale stimulus
# ---------------------------------------------------------------------------


def test_the_stimulus_reports_the_geometry_it_actually_drew() -> None:
    """Ground truth computed by the generator, which is the point of using one.

    A photograph's face size is whatever the photographer stood at; here it is a
    parameter, which is what makes recall-as-a-function-of-size measurable at
    all.
    """
    stimulus = face_stimulus(48, faces=3)

    assert stimulus.face_count == 3
    assert len(stimulus.rgb) == stimulus.width * stimulus.height * 3
    for _, _, width, _ in stimulus.boxes:
        assert width == 48


def test_faces_are_drawn_clear_of_the_frame_edges() -> None:
    """A clipped face is a different measurement — partial occlusion — wearing
    this one's name."""
    stimulus = face_stimulus(max(FACE_PIXEL_SIZES), faces=2)

    for x, y, width, height in stimulus.boxes:
        assert x >= 0 and y >= 0
        assert x + width <= stimulus.width
        assert y + height <= stimulus.height


def test_the_stimulus_is_deterministic_for_a_given_seed() -> None:
    assert face_stimulus(32, seed=3).rgb == face_stimulus(32, seed=3).rgb
    assert face_stimulus(32, seed=3).rgb != face_stimulus(32, seed=4).rgb


def test_a_face_below_four_pixels_is_refused() -> None:
    with pytest.raises(CorpusError, match="not a stimulus"):
        face_stimulus(2)
