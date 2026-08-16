"""Event schema registry with versioning and upcasting.

Critique-log defect #11: an append-only log lives forever, so the first payload
field change breaks replay permanently and event sourcing becomes a liability
rather than an asset. The fix is not "be careful" — it is a registry that makes
the careless version impossible to merge.

Three rules, each enforced by machine rather than by review:

1. **Every event type declares a versioned payload model.** An unregistered
   type cannot be appended, so a payload shape can never enter the log without a
   schema describing it.
2. **A new version declares an upcaster from the version before it.** Reading
   any historical event therefore has a path to the current shape, by
   composition. Registering v3 without a v2 → v3 upcaster raises at import.
3. **Changing a released schema in place fails CI.** ``schema_lock.json``
   fingerprints every registered ``(type, version)``; ``check_event_schemas.py``
   compares it to what the code declares. Editing a shipped model is the exact
   mistake that silently invalidates every hash already written, because the
   payload validates differently than it did when it was signed.

Upcasting is applied **on read, never on write.** The stored payload is the
bytes the hash was computed over, and rewriting it to a newer shape would break
the chain for every event it touched. The log is what happened; the current
shape is an interpretation of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from pydantic import BaseModel

from nemesis.events.canonical import JSONValue

#: An upcaster receives the payload as stored at version N and returns it shaped
#: for version N+1. Deliberately dict-to-dict rather than model-to-model: the
#: old model may no longer exist in the codebase, and requiring it to be kept
#: alive forever is how a registry becomes a museum nobody dares delete from.
Upcaster = Callable[[Mapping[str, Any]], dict[str, Any]]

PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class EventRegistryError(RuntimeError):
    """A registration is inconsistent, and would corrupt replay if allowed."""


class UnknownEventTypeError(EventRegistryError):
    """An event type or version that nothing in this build can interpret."""


@dataclass(frozen=True, slots=True)
class RegisteredEvent:
    event_type: str
    version: int
    model: type[BaseModel]
    #: Upcaster from ``version - 1`` to ``version``; ``None`` only for v1.
    upcaster_from_previous: Upcaster | None
    #: Which §9.2 entity's chain this event belongs to. Fixed per event type:
    #: the same event type appearing on two different chains would make replay
    #: order ambiguous, which is the one thing a chain exists to remove.
    entity_type: str


_registry: dict[tuple[str, int], RegisteredEvent] = {}
_latest: dict[str, int] = {}


def register_event(
    event_type: str,
    *,
    version: int,
    entity_type: str,
    upcaster_from_previous: Upcaster | None = None,
) -> Callable[[type[PayloadModel]], type[PayloadModel]]:
    """Register a payload model as ``(event_type, version)``.

    Raises at import time rather than at append time. A registry error found
    when the first event of that type is written is a production incident; the
    same error found when the module is imported is a failed test run.
    """

    def decorator(model: type[PayloadModel]) -> type[PayloadModel]:
        key = (event_type, version)
        if key in _registry:
            raise EventRegistryError(
                f"{event_type} v{version} is already registered by {_registry[key].model.__name__}"
            )
        if version < 1:
            raise EventRegistryError(f"{event_type} version must be >= 1, got {version}")

        if version == 1:
            if upcaster_from_previous is not None:
                raise EventRegistryError(
                    f"{event_type} v1 cannot have an upcaster — there is no v0 to upcast from"
                )
        else:
            if upcaster_from_previous is None:
                raise EventRegistryError(
                    f"{event_type} v{version} must declare upcaster_from_previous; without it, "
                    f"every v{version - 1} event already in the log becomes unreadable"
                )
            if (event_type, version - 1) not in _registry:
                raise EventRegistryError(
                    f"{event_type} v{version} was registered before v{version - 1}; "
                    f"versions must be registered in order so the upcaster chain is complete"
                )

        registered_entity = _entity_type_for(event_type)
        if registered_entity is not None and registered_entity != entity_type:
            raise EventRegistryError(
                f"{event_type} is registered against entity '{registered_entity}' at an earlier "
                f"version and '{entity_type}' at v{version}; an event type belongs to one chain"
            )

        _registry[key] = RegisteredEvent(
            event_type=event_type,
            version=version,
            model=model,
            upcaster_from_previous=upcaster_from_previous,
            entity_type=entity_type,
        )
        _latest[event_type] = max(_latest.get(event_type, 0), version)
        return model

    return decorator


def _entity_type_for(event_type: str) -> str | None:
    for (registered_type, _), entry in _registry.items():
        if registered_type == event_type:
            return entry.entity_type
    return None


def get_registered(event_type: str, version: int) -> RegisteredEvent:
    try:
        return _registry[(event_type, version)]
    except KeyError:
        known = sorted(v for t, v in _registry if t == event_type)
        if not known:
            raise UnknownEventTypeError(
                f"'{event_type}' is not a registered event type; register it in "
                f"nemesis.events.catalog before appending it"
            ) from None
        raise UnknownEventTypeError(
            f"'{event_type}' has no v{version}; this build knows versions {known}. "
            f"A newer version in the log means the reader is older than the writer"
        ) from None


def latest_version(event_type: str) -> int:
    try:
        return _latest[event_type]
    except KeyError:
        raise UnknownEventTypeError(f"'{event_type}' is not a registered event type") from None


def entity_type_of(event_type: str) -> str:
    return get_registered(event_type, latest_version(event_type)).entity_type


def registered_events() -> tuple[RegisteredEvent, ...]:
    """Every registration, ordered — the input to the CI fingerprint check."""
    return tuple(_registry[key] for key in sorted(_registry))


def validate_payload(event_type: str, version: int, payload: Mapping[str, Any]) -> JSONValue:
    """Validate against the registered model and return canonicalisable JSON.

    Returns the model's own dump rather than the input: the model is the
    contract, so a field the model does not declare must not reach the log, and
    a default the model supplies must be present in what gets hashed. Passing
    the raw input through would let two writers produce different payloads for
    the same logical event depending on which optional fields they bothered to
    set.
    """
    registered = get_registered(event_type, version)
    model = registered.model.model_validate(dict(payload))
    dumped: JSONValue = model.model_dump(mode="json")
    return dumped


def upcast(event_type: str, from_version: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Bring a stored payload up to the latest registered version.

    Applied on read only. The row itself is never rewritten — its hash was
    computed over the bytes as stored, and "migrating" them would invalidate
    every link from that event onward.
    """
    target = latest_version(event_type)
    if from_version > target:
        raise UnknownEventTypeError(
            f"'{event_type}' v{from_version} is newer than this build's v{target}; "
            f"a reader cannot invent an upcaster for a shape it has never seen"
        )

    current = dict(payload)
    for version in range(from_version + 1, target + 1):
        registered = get_registered(event_type, version)
        upcaster = registered.upcaster_from_previous
        if upcaster is None:  # pragma: no cover — registration forbids this
            raise EventRegistryError(f"{event_type} v{version} lost its upcaster")
        current = upcaster(current)
    return current


def read_payload(event_type: str, stored_version: int, payload: Mapping[str, Any]) -> BaseModel:
    """Upcast a stored payload and validate it against the current model."""
    upcasted = upcast(event_type, stored_version, payload)
    return get_registered(event_type, latest_version(event_type)).model.model_validate(upcasted)


#: Exported for the CI check and for tests that need a clean registry. Nothing
#: in application code may clear the registry — an empty registry means every
#: append fails, and doing it at runtime would be indistinguishable from a
#: deployment that shipped no catalog.
_REGISTRY_INTERNALS: Final = (_registry, _latest)
