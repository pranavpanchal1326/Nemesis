"""The labelled fixture set the §14 gate is measured against.

**What "labelled" means here, precisely.** The corpus is a list of *incidents*,
each with one or more citizen reports. Two reports of the same incident are a
true duplicate; two reports of different incidents are truly distinct, however
close together they sit and however alike they sound. Ground truth is therefore
a partition, not a list of pairs — which is the honest shape, because "is this
the same pothole" is a fact about the world and pairwise labels of a partition
can contradict each other in ways a partition cannot.

**Why the hard cases are deliberate.** Two potholes thirty metres apart on the
same street in the same week; a burst water main and a flooded junction at the
same corner. A corpus where every incident sits a kilometre from every other
measures the geospatial filter and reports it as dedup accuracy. These pairs are
the ones the gate is actually about, and the corpus says so in each incident's
``note`` so a reader can see what was and was not attempted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

CORPORA = Path(__file__).resolve().parent / "corpora"
DEFAULT_CORPUS = "municipality-dedup-v1"

#: Metres per degree of latitude. The corpus expresses within-incident jitter in
#: metres because "the second reporter stood six metres further along" is a
#: sentence somebody can check, and a decimal degree offset is not.
_METRES_PER_DEGREE = 111_320.0


@dataclass(frozen=True, slots=True)
class Report:
    """One citizen's submission, and where its incident sits."""

    id: str
    incident_id: str
    category: str
    locale: str
    text: str
    latitude: float
    longitude: float
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class Incident:
    """One real-world problem. Its reports are each other's true duplicates."""

    id: str
    category: str
    latitude: float
    longitude: float
    note: str
    reports: tuple[Report, ...]


@dataclass(frozen=True, slots=True)
class Corpus:
    corpus_id: str
    template: str
    description: str
    authored: str
    incidents: tuple[Incident, ...]

    @property
    def reports(self) -> tuple[Report, ...]:
        """Every report, in submission order.

        Chronological rather than grouped by incident, because that is the order
        the engine will see them in production and dedup is order-dependent by
        construction: the second report of an incident can only merge into a
        cluster the first one created.
        """
        everything = [report for incident in self.incidents for report in incident.reports]
        return tuple(sorted(everything, key=lambda report: (report.reported_at, report.id)))

    @property
    def truth(self) -> dict[str, str]:
        """Report id → incident id. The partition, as a lookup."""
        return {report.id: report.incident_id for report in self.reports}

    def pairs(self) -> Iterator[tuple[Report, Report, bool]]:
        """Every unordered pair and whether it is a true duplicate.

        Reported alongside the sequential measurement because the two answer
        different questions: the pair count says how discriminable the corpus
        is in principle, and the sequential run says what the engine did with it
        in the order it actually arrives.
        """
        everything = self.reports
        for index, left in enumerate(everything):
            for right in everything[index + 1 :]:
                yield left, right, left.incident_id == right.incident_id


def _offset(latitude: float, longitude: float, *, metres: float) -> tuple[float, float]:
    return latitude + metres / _METRES_PER_DEGREE, longitude


def load(name: str = DEFAULT_CORPUS, *, base: datetime | None = None) -> Corpus:
    """Read a corpus file and resolve its relative offsets into absolutes."""
    path = CORPORA / f"{name}.json"
    if not path.is_file():
        available = ", ".join(sorted(item.stem for item in CORPORA.glob("*.json"))) or "none"
        raise FileNotFoundError(f"no dedup corpus named {name!r}; available: {available}")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    origin = base or datetime.fromisoformat(f"{raw['authored']}T06:00:00+00:00")

    incidents: list[Incident] = []
    for entry in raw["incidents"]:
        reports: list[Report] = []
        for item in entry["reports"]:
            latitude, longitude = _offset(
                float(entry["latitude"]),
                float(entry["longitude"]),
                metres=float(item.get("offset_meters", 0.0)),
            )
            reports.append(
                Report(
                    id=item["id"],
                    incident_id=entry["id"],
                    category=entry["category"],
                    locale=item.get("locale", "en"),
                    text=item["text"],
                    latitude=latitude,
                    longitude=longitude,
                    reported_at=origin + timedelta(hours=float(item.get("offset_hours", 0.0))),
                )
            )
        incidents.append(
            Incident(
                id=entry["id"],
                category=entry["category"],
                latitude=float(entry["latitude"]),
                longitude=float(entry["longitude"]),
                note=entry.get("note", ""),
                reports=tuple(reports),
            )
        )

    return Corpus(
        corpus_id=raw["corpus_id"],
        template=raw["template"],
        description=raw["description"],
        authored=raw["authored"],
        incidents=tuple(incidents),
    )


@lru_cache(maxsize=4)
def content_hash(name: str = DEFAULT_CORPUS) -> str:
    """A stable digest of the corpus file, stamped onto every published number.

    Without it a report says "precision 1.0 on municipality-dedup-v1" and the
    next reader has no way to know whether their copy of the corpus is the one
    that produced it.
    """
    import hashlib

    return hashlib.sha256((CORPORA / f"{name}.json").read_bytes()).hexdigest()[:12]


def available() -> Sequence[str]:
    return sorted(item.stem for item in CORPORA.glob("*.json"))


__all__ = [
    "CORPORA",
    "DEFAULT_CORPUS",
    "Corpus",
    "Incident",
    "Report",
    "available",
    "content_hash",
    "load",
]
