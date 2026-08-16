"""Schema registry, versioning and upcasting.

The versioning mechanism is exercised with **test-only event types**, not with
the real catalog. Registering a fabricated v2 of ``complaint_submitted`` to prove
upcasting works would put a fictional schema in the permanent lock file and
imply a payload change that never happened — the log's own history would then
record an evolution the product did not go through.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import Field, ValidationError

from nemesis.events import registry as registry_module
from nemesis.events.catalog import DEFERRED_EVENT_TYPES, RENAMED_EVENT_TYPES, EventPayload
from nemesis.events.registry import (
    EventRegistryError,
    UnknownEventTypeError,
    entity_type_of,
    get_registered,
    latest_version,
    read_payload,
    register_event,
    registered_events,
    upcast,
    validate_payload,
)
from nemesis.events.schema_fingerprint import compare, current_entries, load_lock


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    """Register test types without leaving them in the process-wide registry.

    Restoring by snapshot rather than by deleting known keys: a test that fails
    partway through would otherwise leak a half-registered type into every test
    that runs after it, and the failure would be attributed to the wrong one.
    """
    registry, latest = registry_module._registry, registry_module._latest
    saved_registry, saved_latest = dict(registry), dict(latest)
    try:
        yield
    finally:
        registry.clear()
        registry.update(saved_registry)
        latest.clear()
        latest.update(saved_latest)


# ---------------------------------------------------------------------------
# The real catalog
# ---------------------------------------------------------------------------


def test_schema_lock_matches_the_registered_catalog() -> None:
    """The Phase 2 gate: a payload change without a version bump fails CI.

    ``compare`` returns the problems as remediation sentences, so a failure here
    tells the next person what to do rather than only that something moved.
    """
    problems = compare(current_entries(), load_lock())
    assert not problems, "\n".join(problems)


def test_every_registered_event_has_a_complete_upcaster_chain() -> None:
    for event in registered_events():
        for version in range(2, event.version + 1):
            assert get_registered(event.event_type, version).upcaster_from_previous is not None


def test_deferred_and_renamed_types_are_not_also_registered() -> None:
    """A stale deferral reads as a gap that is not one."""
    registered = {event.event_type for event in registered_events()}
    assert not registered & set(DEFERRED_EVENT_TYPES)
    assert not registered & set(RENAMED_EVENT_TYPES)
    assert set(RENAMED_EVENT_TYPES.values()) <= registered


def test_entity_type_is_fixed_per_event_type() -> None:
    assert entity_type_of("complaint_submitted") == "complaint"
    assert entity_type_of("work_order_assigned") == "work_order"


def test_payload_validation_returns_the_model_dump_not_the_input() -> None:
    """Defaults must be present in what gets hashed.

    Two writers emitting the same logical event — one setting an optional field
    explicitly, one relying on the default — must produce identical payloads.
    Passing the caller's dict through would make the hash depend on how much of
    the schema the caller happened to fill in.
    """
    sparse = validate_payload("complaint_submitted", 1, {"latitude": 19.0, "longitude": 72.8})
    assert isinstance(sparse, dict)
    assert sparse["submitted_via"] == "web"
    assert sparse["photo_url"] is None


def test_unknown_type_and_version_are_distinguishable() -> None:
    with pytest.raises(UnknownEventTypeError, match="not a registered event type"):
        latest_version("no_such_event")
    with pytest.raises(UnknownEventTypeError, match="reader is older than the writer"):
        get_registered("complaint_submitted", 99)


# ---------------------------------------------------------------------------
# Payload boundary rules
# ---------------------------------------------------------------------------


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            "complaint_submitted",
            1,
            {"latitude": 0.0, "longitude": 0.0, "undeclared": "x"},
        )


def test_non_canonicalisable_values_are_rejected_at_the_boundary() -> None:
    """A NaN severity must fail here, not inside ``EventStore.append``.

    By the time the store runs, the caller believes the event was accepted and
    may have done other work in the same transaction.
    """
    with pytest.raises(ValidationError, match="cannot be canonicalised"):
        validate_payload(
            "severity_scored",
            1,
            {
                "score": 5.0,
                "components": {"visual": float("nan")},
                "weights": {"visual": 1.0},
                "policy_version": "v1",
            },
        )


def test_integers_beyond_safe_double_precision_are_rejected() -> None:
    class BigIntPayload(EventPayload):
        count: int = Field(default=0)

    with pytest.raises(ValidationError, match="safe double precision"):
        BigIntPayload(count=2**60)


# ---------------------------------------------------------------------------
# Versioning and upcasting, with test-only types
# ---------------------------------------------------------------------------


def _define_versions() -> None:
    @register_event("test_thing_happened", version=1, entity_type="complaint")
    class _V1(EventPayload):
        name: str

    def v1_to_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
        updated = dict(payload)
        # A rename: the field is carried forward, never dropped.
        updated["display_name"] = updated.pop("name")
        updated.setdefault("priority", 0)
        return updated

    @register_event(
        "test_thing_happened",
        version=2,
        entity_type="complaint",
        upcaster_from_previous=v1_to_v2,
    )
    class _V2(EventPayload):
        display_name: str
        priority: int = 0

    def v2_to_v3(payload: Mapping[str, Any]) -> dict[str, Any]:
        updated = dict(payload)
        updated.setdefault("source", "legacy")
        return updated

    @register_event(
        "test_thing_happened",
        version=3,
        entity_type="complaint",
        upcaster_from_previous=v2_to_v3,
    )
    class _V3(EventPayload):
        display_name: str
        priority: int = 0
        source: str = "legacy"


def test_upcasting_composes_across_multiple_versions(isolated_registry: None) -> None:
    """A v1 payload must reach v3 by composition, not by a bespoke v1→v3 path.

    Requiring an upcaster from every prior version to the newest would be N²
    functions and N² chances to get one wrong.
    """
    _define_versions()
    assert latest_version("test_thing_happened") == 3

    upcasted = upcast("test_thing_happened", 1, {"name": "pothole"})
    assert upcasted == {"display_name": "pothole", "priority": 0, "source": "legacy"}

    model = read_payload("test_thing_happened", 1, {"name": "pothole"})
    assert model.model_dump()["display_name"] == "pothole"


def test_a_new_version_without_an_upcaster_is_refused(isolated_registry: None) -> None:
    """The registration that would break replay, refused at import time."""

    @register_event("test_no_upcaster", version=1, entity_type="complaint")
    class _V1(EventPayload):
        name: str

    with pytest.raises(EventRegistryError, match="must declare upcaster_from_previous"):

        @register_event("test_no_upcaster", version=2, entity_type="complaint")
        class _V2(EventPayload):
            renamed: str


def test_versions_must_be_registered_in_order(isolated_registry: None) -> None:
    with pytest.raises(EventRegistryError, match="must be registered in order"):

        @register_event(
            "test_out_of_order",
            version=2,
            entity_type="complaint",
            upcaster_from_previous=dict,
        )
        class _V2(EventPayload):
            name: str


def test_v1_cannot_declare_an_upcaster(isolated_registry: None) -> None:
    with pytest.raises(EventRegistryError, match="there is no v0"):

        @register_event(
            "test_v1_upcaster",
            version=1,
            entity_type="complaint",
            upcaster_from_previous=dict,
        )
        class _V1(EventPayload):
            name: str


def test_one_event_type_cannot_span_two_entity_chains(isolated_registry: None) -> None:
    """Replay order would be ambiguous, which is the one thing a chain removes."""

    @register_event("test_two_chains", version=1, entity_type="complaint")
    class _V1(EventPayload):
        name: str

    with pytest.raises(EventRegistryError, match="belongs to one chain"):

        @register_event(
            "test_two_chains",
            version=2,
            entity_type="work_order",
            upcaster_from_previous=dict,
        )
        class _V2(EventPayload):
            name: str


def test_reading_an_event_newer_than_this_build_fails_clearly(
    isolated_registry: None,
) -> None:
    """A rolling deploy puts an old reader in front of a new writer.

    The error must say so, because "cannot upcast" reads like corruption and
    sends the on-call looking for a tamper that did not happen.
    """
    _define_versions()
    with pytest.raises(UnknownEventTypeError, match="newer than this build"):
        upcast("test_thing_happened", 9, {"display_name": "x"})
