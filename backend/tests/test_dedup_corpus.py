"""The labelled corpus: does it load, and does it say what it claims to say.

A corpus is data, and data that nothing validates is data that drifts. These are
cheap tests protecting an expensive claim — every precision and recall number
Phase 10 publishes is only as good as the ground truth underneath it, and a
duplicate pair silently mislabelled would make the whole report wrong in a way
no other test could detect.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nemesis.dedup import corpus as corpus_module


@pytest.fixture(scope="module")
def loaded() -> corpus_module.Corpus:
    return corpus_module.load()


def test_the_default_corpus_loads() -> None:
    assert corpus_module.DEFAULT_CORPUS in corpus_module.available()


def test_an_unknown_corpus_names_the_ones_that_exist() -> None:
    """A missing corpus is a typo nine times out of ten, and a message that
    lists the alternatives resolves it without a directory listing."""
    with pytest.raises(FileNotFoundError, match="available:"):
        corpus_module.load("no-such-corpus")


def test_reports_come_back_in_submission_order(loaded: corpus_module.Corpus) -> None:
    """Dedup is order-dependent: the second report of an incident can only merge
    into a cluster the first one created. A corpus that yielded reports grouped
    by incident would measure a system nobody runs."""
    timestamps = [report.reported_at for report in loaded.reports]

    assert timestamps == sorted(timestamps)


def test_every_report_carries_its_incident_and_a_timezone(
    loaded: corpus_module.Corpus,
) -> None:
    for report in loaded.reports:
        assert report.incident_id
        assert report.text.strip()
        # Naive timestamps would be interpreted as the worker's locale, and the
        # dedup time window is compared across tenants in different zones.
        assert report.reported_at.tzinfo is not None


def test_the_truth_map_covers_every_report_exactly_once(
    loaded: corpus_module.Corpus,
) -> None:
    truth = loaded.truth

    assert len(truth) == len(loaded.reports)
    assert set(truth) == {report.id for report in loaded.reports}


def test_report_ids_are_unique(loaded: corpus_module.Corpus) -> None:
    """Two reports sharing an id would silently collapse in the truth map, and
    the harness would score one of them against the other's incident."""
    ids = [report.id for report in loaded.reports]

    assert len(ids) == len(set(ids))


def test_a_report_inherits_its_incidents_category(loaded: corpus_module.Corpus) -> None:
    for incident in loaded.incidents:
        for report in incident.reports:
            assert report.category == incident.category


def test_offsets_move_a_report_off_its_incident_centre(
    loaded: corpus_module.Corpus,
) -> None:
    """The jitter is what makes the corpus realistic — two citizens do not stand
    in the same spot — and a loader that dropped it would test a degenerate
    case where every duplicate is at distance zero."""
    moved = [
        report
        for incident in loaded.incidents
        for report in incident.reports
        if report.latitude != incident.latitude
    ]

    assert moved


def test_pairs_are_labelled_by_incident_identity(loaded: corpus_module.Corpus) -> None:
    pairs = list(loaded.pairs())
    duplicates = [pair for pair in pairs if pair[2]]

    assert pairs, "a corpus with no pairs measures nothing"
    assert duplicates, "a corpus with no true duplicates cannot measure recall"
    for left, right, is_duplicate in pairs:
        assert is_duplicate == (left.incident_id == right.incident_id)


def test_the_corpus_contains_a_hard_negative(loaded: corpus_module.Corpus) -> None:
    """Two distinct incidents of the same category within 50 m of each other.

    This is the case the gate is actually about. A corpus where every incident
    is far from every other measures the geospatial filter and reports the
    result as dedup accuracy, so its absence would be a silent downgrade of the
    whole measurement.
    """
    metres_per_degree = 111_320.0
    close_pairs = 0
    for index, first in enumerate(loaded.incidents):
        for second in loaded.incidents[index + 1 :]:
            if first.category != second.category:
                continue
            north = abs(first.latitude - second.latitude) * metres_per_degree
            east = abs(first.longitude - second.longitude) * metres_per_degree * 0.948
            if (north**2 + east**2) ** 0.5 <= 50.0:
                close_pairs += 1

    assert close_pairs, "the corpus has no same-category incidents inside one dedup radius"


def test_the_content_hash_is_stable_and_short() -> None:
    """Stamped onto every published number, so a reader can tell whether their
    copy of the corpus is the one that produced the report."""
    first = corpus_module.content_hash()

    assert first == corpus_module.content_hash()
    assert len(first) == 12


def test_a_supplied_base_time_shifts_every_report(loaded: corpus_module.Corpus) -> None:
    base = datetime(2030, 1, 1, tzinfo=UTC)

    shifted = corpus_module.load(base=base)

    assert min(report.reported_at for report in shifted.reports) == base
    assert len(shifted.reports) == len(loaded.reports)
