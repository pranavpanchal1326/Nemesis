"""Declared feature flags.

Flags are declared **in code** and overridden **in data**. That split is the
whole design:

* Declaring in code means a flag has a name, an owner, a reason, and a removal
  date that go through review like anything else. A flag created by typing a
  key into Redis has none of those, and nobody can tell six months later whether
  it is load-bearing or debris.
* Overriding in data means flipping one takes effect within a reload interval
  with no deploy, which is what makes a kill switch worth having. A kill switch
  that requires a release is a post-mortem action item, not a control.

**Every flag carries a ``remove_by`` date, and CI fails once it passes.** This
is the only mechanism that has ever been observed to actually remove flags. The
alternative — "we'll clean them up later" — produces a codebase where every
branch is conditional on a value nobody dares change, which is strictly worse
than the un-flagged code it replaced.

Removing a flag is not a chore to apologise for. It is the last step of shipping
the thing the flag was protecting.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Owning function codes, mirroring docs/PHASES.md. A flag with no owning
# function is a flag nobody will remove.
Owner = str


class FlagSpec(BaseModel):
    """A declared flag. Immutable; the mutable part lives in the store."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """Snake case, and validated. The name becomes a Prometheus label value and
    a Redis key suffix; letting it be free-form invites a space or a colon that
    breaks one of the two in a way that only shows up in production."""

    description: str = Field(min_length=20)
    """What turning this off actually does, in operator language. `min_length`
    is a blunt instrument against "temp flag" as a description, which is the
    single most common entry in every flag system that does not forbid it."""

    owner: Owner
    """Owning function (PLT / DATA / PROD / SEC / SRE / BIZ)."""

    default: bool
    """Value when no override exists. For a kill switch this is ``True`` — the
    capability is on, and the flag exists to turn it off."""

    created: date
    remove_by: date
    """The date this flag becomes a CI failure. Extending it is a deliberate,
    reviewable act; letting it lapse silently is not possible."""

    kill_switch: bool = False
    """Marks a flag whose purpose is emergency shutdown of a shipped capability,
    rather than staged rollout of a new one. Surfaced separately in the ops
    listing so that under pressure the relevant handles are not buried among
    rollout toggles."""

    blueprint: str | None = None
    """Section this protects, where one applies."""

    @model_validator(mode="after")
    def _removal_must_follow_creation(self) -> FlagSpec:
        if self.remove_by <= self.created:
            raise ValueError(f"flag {self.name!r}: remove_by must be after created")
        return self


def _spec(**kwargs: object) -> FlagSpec:
    return FlagSpec.model_validate(kwargs)


# --------------------------------------------------------------------------
# The registry.
#
# Phase 1a declares the flags that Phase 1a itself needs plus the kill switches
# for capabilities the plan already commits to shipping behind one. Flags for
# unbuilt features are NOT pre-declared: a flag guarding nothing is exactly the
# debris this module exists to prevent, and it would start its removal clock
# against work that has not begun.
# --------------------------------------------------------------------------
_FLAGS: tuple[FlagSpec, ...] = (
    _spec(
        name="observability_trace_export",
        description=(
            "Export OpenTelemetry spans to the collector. Turning this off stops "
            "trace export without restarting any service — the escape hatch for "
            "when the collector is the thing that is broken."
        ),
        owner="SRE",
        default=True,
        created=date(2026, 8, 16),
        remove_by=date(2027, 8, 16),
        kill_switch=True,
        blueprint="24.3",
    ),
    _spec(
        name="pipeline_agent_investigation",
        description=(
            "Route ambiguous dedup decisions to the Investigation Agent. Killing "
            "this sends every ambiguous case straight to the human review queue, "
            "which is the same fallback §27.3 specifies for Ollama being down — "
            "so the degraded path is one already exercised, not a new one."
        ),
        owner="DATA",
        default=True,
        created=date(2026, 8, 16),
        remove_by=date(2027, 8, 16),
        kill_switch=True,
        blueprint="12.4",
    ),
    _spec(
        name="realtime_websocket_hub",
        description=(
            "Serve pipeline events over WebSocket. Killing this forces clients to "
            "the §27.3 polling fallback deliberately, rather than waiting for the "
            "hub to fail on its own under load."
        ),
        owner="PLT",
        default=True,
        created=date(2026, 8, 16),
        remove_by=date(2027, 8, 16),
        kill_switch=True,
        blueprint="27.3",
    ),
    _spec(
        name="simulation_shadow_mode",
        description=(
            "Evaluate candidate policies alongside the live ones and record what "
            "they would have decided. Killing this stops the observation only — no "
            "decision changes, because shadow mode never made one. It exists "
            "because the work is unbounded by design (every complaint, no "
            "sampling), and the handle to stop it has to be reachable without a "
            "deploy on the day somebody points it at a year of traffic."
        ),
        owner="DATA",
        default=True,
        created=date(2026, 8, 19),
        remove_by=date(2027, 8, 19),
        kill_switch=True,
        blueprint="13.3",
    ),
    _spec(
        name="trust_abuse_detection",
        description=(
            "Run the §11.3 coordinated-abuse detectors — device velocity and "
            "geographic clustering. Killing this stops both from firing; nothing "
            "else changes, because §11.3 flags and never blocks. It exists "
            "because these two are the noisiest heuristics in the phase: a "
            "genuine street-level incident produces exactly the pattern the "
            "cluster detector looks for, and the handle to stop a review queue "
            "filling with one flood has to be reachable without a deploy."
        ),
        owner="DATA",
        default=True,
        created=date(2026, 8, 19),
        remove_by=date(2027, 8, 19),
        kill_switch=True,
        blueprint="11.3",
    ),
    _spec(
        name="perception_audio_transcription",
        description=(
            "Transcribe voice complaints with faster-whisper (§8.4). Killing this "
            "stops the transcription only: a report carrying audio is still "
            "classified on its description and its photograph, and a voice-only "
            "report parks as pending_classification for a human who can play the "
            "clip. It exists because transcription is by far the most expensive "
            "operation on the ml worker — seconds per report against milliseconds "
            "for an embedding — so it is the first thing to shed when the queue is "
            "backing up, and that handle has to be reachable without a deploy."
        ),
        owner="DATA",
        default=True,
        created=date(2026, 8, 19),
        remove_by=date(2027, 8, 19),
        kill_switch=True,
        blueprint="8.4",
    ),
    # **There is deliberately no kill switch for classification itself**, and the
    # absence follows the same rule as the two below. A switch that turned the
    # classifier off would park every complaint in the review queue, which is not
    # a degraded mode anybody would choose deliberately — it is an outage with a
    # friendly name. The stage already degrades honestly when its models are
    # absent, and that path is exercised by every image in the deployment that
    # does not carry the ml extra.
    #
    # **There is deliberately no kill switch for face blur or for the safety
    # fail-safe**, and the absence is a decision rather than an oversight.
    #
    # §22.1 is a legal obligation and §11.2 is the control whose whole design
    # argument is that false negatives on danger signals are unacceptable. A
    # documented, one-command way to turn either off is a documented way to
    # cause a privacy breach or miss a gas leak — and a switch that exists gets
    # pulled on the afternoon the queue is backed up and somebody is under
    # pressure. Both fail *closed* instead: no face detector means the trust
    # stage halts the complaint for human review (never serves an unblurred
    # image), and a safety ruleset with every rule inactive is refused by
    # ``SafetyRuleset`` at validation time.
    _spec(
        name="experience_webgl_scenes",
        description=(
            "Render the Three.js severity map. Killing this drops every client to "
            "the documented Leaflet fallback (§20.3). A crashed WebGL context in "
            "front of a buyer is worse than no 3D, and this is the handle that "
            "prevents finding that out live."
        ),
        owner="PROD",
        default=True,
        created=date(2026, 8, 16),
        remove_by=date(2027, 8, 16),
        kill_switch=True,
        blueprint="20.3",
    ),
)

REGISTRY: dict[str, FlagSpec] = {spec.name: spec for spec in _FLAGS}


class UnknownFlagError(KeyError):
    """Raised when code asks for a flag that was never declared.

    Deliberately fatal rather than defaulting to ``False``. A typo in a flag
    name that silently resolves to "off" disables a feature nobody meant to
    disable, and the only evidence is the feature not working.
    """


def get_spec(name: str) -> FlagSpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UnknownFlagError(
            f"{name!r} is not a declared flag. Declare it in "
            f"nemesis/flags/registry.py — flags are not created by writing a key "
            f"into the store."
        ) from None


def kill_switches() -> tuple[FlagSpec, ...]:
    """The emergency handles, for the ops listing and the incident runbook."""
    return tuple(spec for spec in REGISTRY.values() if spec.kill_switch)


def expired_flags(today: date) -> tuple[FlagSpec, ...]:
    """Flags past their removal date. A non-empty result fails CI.

    Returned sorted by how far overdue they are, so the worst offender — the
    one most likely to have become permanent by accident — is first.
    """
    overdue = [spec for spec in REGISTRY.values() if spec.remove_by < today]
    return tuple(sorted(overdue, key=lambda s: s.remove_by))
