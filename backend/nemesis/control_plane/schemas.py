"""Validated shapes for every control-plane write.

One module for all of them, and they are the *same* models the HTTP layer binds
and the template loader parses. That is deliberate: a template that validates
against a different model from the API is a template that can describe a tenant
the API would reject, and the failure surfaces during onboarding rather than in
review.

The engineering standards require Pydantic v2 on every boundary. These are that
boundary — everything below them (the services, the ORM) may assume the values
have already been checked, which is why the services raise ``ValidationError``
only for things a single model cannot see: cycles, cross-entity references, and
collisions with rows that already exist.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nemesis.db.models.calendar import ISO_MONDAY, ISO_SUNDAY, MAX_SLA_MULTIPLIER
from nemesis.db.models.taxonomy import KEY_PATTERN

#: A machine key: the string that reaches the event log, a URL segment, and a
#: metric label. Constrained identically here and in the database, because the
#: service is not the only writer — a migration or a psql session is — and the
#: pattern is only a guarantee if both layers state it.
TaxonomyKey = Annotated[str, Field(pattern=KEY_PATTERN, max_length=64)]

#: BCP-47 in the shape this system actually uses: a language subtag, optionally
#: a script and a region. Not full BCP-47 — extensions and private-use subtags
#: are legal in the standard and meaningless here, and accepting them would put
#: unbounded caller text into a column that keys a translation lookup.
LocaleTag = Annotated[
    str, Field(pattern=r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$", max_length=35)
]

#: An organisation or zone code. Looser than a taxonomy key — it is displayed
#: and typed by humans and often mirrors an existing municipal code — but it
#: still may not contain the path separator.
OrgCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$", max_length=64)]

#: IANA zone name. Validated for shape here and for *existence* by the service,
#: which resolves it through ``zoneinfo`` — a pattern match cannot tell
#: ``Asia/Kolkata`` from ``Asia/Kolkatta``, and the second silently produces
#: deadlines in the wrong timezone.
TimezoneName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9+_\-]*(/[A-Za-z0-9+_\-]+)*$")]


class ControlPlaneModel(BaseModel):
    """``extra="forbid"`` on every control-plane input.

    A misspelled field in a tenant template must fail the import, not be
    silently dropped — the failure mode of ignoring it is a tenant that
    onboards successfully and behaves subtly differently from the one that was
    described, which is the hardest class of support ticket there is.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class SeveritySemantics(ControlPlaneModel):
    """§13.5 hints attached to a taxonomy node.

    A *floor and a multiplier*, not a score. The rubric computes severity from
    evidence; these say what the category itself implies regardless of the
    photograph — an exposed live cable is severe even when the picture is bad.
    Phase 6 replaces this whole object with a versioned rubric document.
    """

    #: Severity can never come out below this for a complaint in this category.
    floor: float = Field(default=0.0, ge=0.0, le=10.0)
    #: Applied to the rubric's output before the floor. Bounded above 0 so a
    #: multiplier of zero cannot silently disable scoring for a category — that
    #: is what ``is_active`` is for, and it is visible.
    multiplier: float = Field(default=1.0, gt=0.0, le=5.0)
    #: §11.2. A category that always routes straight to human review without
    #: waiting for the classifier. The deterministic ruleset in Phase 6 is the
    #: real control; this is the category-level shortcut a tenant can express
    #: on day one.
    bypasses_scoring: bool = False


class RoutingHints(ControlPlaneModel):
    """Where work in a category tends to go, before Phase 6's rules exist."""

    #: A ``departments.code``. Resolved by the service against the tenant's own
    #: departments; an unknown code is a validation failure rather than a
    #: silently ignored hint, because a hint that points nowhere reads on screen
    #: exactly like one that works.
    department_code: OrgCode | None = None
    #: §27.2 tier name, or a tenant-defined one. Free text: the tier table is
    #: policy data in Phase 6 and a closed set here would pre-empt it.
    default_sla_tier: str | None = Field(default=None, max_length=64)


class TaxonomyNodeSpec(ControlPlaneModel):
    key: TaxonomyKey
    display_name: str = Field(min_length=1, max_length=200)
    parent_key: TaxonomyKey | None = None
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int = Field(default=0, ge=0, le=10_000)
    is_selectable: bool = True
    is_active: bool = True
    severity_semantics: SeveritySemantics = SeveritySemantics()
    routing_hints: RoutingHints = RoutingHints()
    attributes: dict[str, Any] = Field(default_factory=dict)
    #: Translations of ``display_name``, keyed by locale. Carried on the node
    #: rather than imported separately so a template describes a category once —
    #: the service splits them into the ``translations`` table on write.
    translations: dict[LocaleTag, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parent_is_not_self(self) -> TaxonomyNodeSpec:
        if self.parent_key == self.key:
            raise ValueError(f"node {self.key!r} cannot be its own parent")
        return self


class TaxonomyNodeUpdate(ControlPlaneModel):
    """A partial update. ``None`` means "leave alone" for every field.

    ``parent_key`` therefore cannot express "make this a root", which is a real
    operation — so it is a separate, explicit flag. Overloading ``None`` to mean
    both "unchanged" and "cleared" is how a partial-update endpoint quietly
    detaches a subtree.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    is_selectable: bool | None = None
    is_active: bool | None = None
    parent_key: TaxonomyKey | None = None
    detach_to_root: bool = False
    severity_semantics: SeveritySemantics | None = None
    routing_hints: RoutingHints | None = None
    attributes: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _reparenting_is_unambiguous(self) -> TaxonomyNodeUpdate:
        if self.detach_to_root and self.parent_key is not None:
            raise ValueError("detach_to_root and parent_key are mutually exclusive")
        return self


class PromptSetSpec(ControlPlaneModel):
    """Phase 9's gate: a new category is classifiable by adding prompts alone."""

    node_key: TaxonomyKey
    locale: LocaleTag
    #: 'clip' scores an image, 'text' scores a transcript. Free text — Phase 9
    #: may add a third encoder, and that must not be a migration.
    encoder: str = Field(min_length=1, max_length=32)
    prompts: list[str] = Field(min_length=1, max_length=64)
    negative_prompts: list[str] = Field(default_factory=list, max_length=64)
    prompt_set_version: str = Field(min_length=1, max_length=64)
    is_active: bool = True

    @field_validator("prompts", "negative_prompts")
    @classmethod
    def _prompts_are_non_empty(cls, value: list[str]) -> list[str]:
        if any(not prompt.strip() for prompt in value):
            raise ValueError("a blank prompt scores every image equally and biases the softmax")
        return value


# ---------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------


class DepartmentSpec(ControlPlaneModel):
    code: OrgCode
    name: str = Field(min_length=1, max_length=200)
    #: The tenant's own word for what this unit is. No enum — see
    #: ``db.models.organisation``.
    kind: str = Field(default="department", min_length=1, max_length=64)
    parent_code: OrgCode | None = None
    is_assignable: bool = True
    is_active: bool = True
    calendar_code: OrgCode | None = None
    ward: str | None = Field(default=None, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    translations: dict[LocaleTag, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parent_is_not_self(self) -> DepartmentSpec:
        if self.parent_code == self.code:
            raise ValueError(f"department {self.code!r} cannot be its own parent")
        return self


class ZoneSpec(ControlPlaneModel):
    code: OrgCode
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="zone", min_length=1, max_length=64)
    parent_code: OrgCode | None = None
    is_active: bool = True
    #: GeoJSON ``MultiPolygon`` coordinates, WGS84. Accepted as raw coordinates
    #: rather than a GeoJSON document because the type and CRS are fixed by the
    #: column, and a caller-supplied ``"crs"`` member would be either ignored or
    #: obeyed — and obeying it means reprojection this phase does not do.
    boundary: list[list[list[tuple[float, float]]]] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    translations: dict[LocaleTag, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parent_is_not_self(self) -> ZoneSpec:
        if self.parent_code == self.code:
            raise ValueError(f"zone {self.code!r} cannot be its own parent")
        return self

    @field_validator("boundary")
    @classmethod
    def _rings_are_closed(
        cls, value: list[list[list[tuple[float, float]]]] | None
    ) -> list[list[list[tuple[float, float]]]] | None:
        """A polygon ring must close, and PostGIS will refuse it if it does not.

        Checked here so the caller is told which ring is open, instead of
        receiving a driver error naming a WKT string it never wrote.
        """
        if value is None:
            return value
        for polygon_index, polygon in enumerate(value):
            for ring_index, ring in enumerate(polygon):
                if len(ring) < 4:
                    raise ValueError(
                        f"polygon {polygon_index} ring {ring_index} has {len(ring)} points; "
                        f"a closed ring needs at least four"
                    )
                if ring[0] != ring[-1]:
                    raise ValueError(
                        f"polygon {polygon_index} ring {ring_index} is not closed — "
                        f"the last point must repeat the first"
                    )
                for longitude, latitude in ring:
                    if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
                        raise ValueError(
                            "coordinates are (longitude, latitude) in WGS84; "
                            f"({longitude}, {latitude}) is outside the planet"
                        )
        return value


class ShiftSpec(ControlPlaneModel):
    department_code: OrgCode
    code: OrgCode
    name: str = Field(min_length=1, max_length=200)
    weekdays: list[Annotated[int, Field(ge=ISO_MONDAY, le=ISO_SUNDAY)]] = Field(
        min_length=1, max_length=7
    )
    starts_at: time
    ends_at: time
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool = True

    @field_validator("weekdays")
    @classmethod
    def _weekdays_are_unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("a weekday listed twice is a shift counted twice")
        return sorted(value)

    @model_validator(mode="after")
    def _shift_has_duration(self) -> ShiftSpec:
        # Equal times are rejected; `ends_at < starts_at` is not, because that is
        # how a night shift crossing midnight is written.
        if self.starts_at == self.ends_at:
            raise ValueError("a shift starting and ending at the same time has no duration")
        if (
            self.effective_to is not None
            and self.effective_from is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to precedes effective_from")
        return self


class ContractorSpec(ControlPlaneModel):
    registration_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    registered_address: str | None = Field(default=None, max_length=1000)
    phone: str | None = Field(default=None, max_length=32)
    director_names: list[str] = Field(default_factory=list, max_length=64)
    active_since: date | None = None


class CertificationSpec(ControlPlaneModel):
    contractor_registration_id: str = Field(min_length=1, max_length=128)
    taxonomy_key: TaxonomyKey
    certificate_ref: str | None = Field(default=None, max_length=128)
    valid_from: date | None = None
    valid_until: date | None = None

    @model_validator(mode="after")
    def _validity_is_ordered(self) -> CertificationSpec:
        if (
            self.valid_until is not None
            and self.valid_from is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until precedes valid_from")
        return self


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


class WorkingWindow(ControlPlaneModel):
    """One contiguous span of working time within a day."""

    start: time
    end: time

    @model_validator(mode="after")
    def _window_is_ordered(self) -> WorkingWindow:
        if self.end <= self.start:
            raise ValueError(
                "a working window must end after it starts; a shift crossing "
                "midnight is expressed as two windows on two days, because the "
                "SLA clock is accounted per calendar day"
            )
        return self


class CalendarExceptionSpec(ControlPlaneModel):
    starts_on: date
    ends_on: date
    label: str = Field(min_length=1, max_length=200)
    #: ``False`` stops the SLA clock; ``True`` keeps it running and stretches
    #: the budget by ``sla_multiplier`` (§13.4).
    is_working: bool = False
    sla_multiplier: Decimal | None = Field(default=None, gt=0, le=MAX_SLA_MULTIPLIER)
    source: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _multiplier_belongs_to_working_spans(self) -> CalendarExceptionSpec:
        if self.is_working and self.sla_multiplier is None:
            raise ValueError(
                "a working exception with no multiplier changes nothing; omit it "
                "or state the adjustment"
            )
        if not self.is_working and self.sla_multiplier is not None:
            raise ValueError(
                "a non-working span stops the clock, so a multiplier on it would "
                "never be applied — the combination is silently inert"
            )
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on precedes starts_on")
        return self


class CalendarSpec(ControlPlaneModel):
    code: OrgCode
    name: str = Field(min_length=1, max_length=200)
    timezone: TimezoneName | None = None
    #: Keyed by ISO weekday as a string, because JSON object keys are strings and
    #: round-tripping an integer key through JSONB would change its type between
    #: write and read.
    working_hours: dict[str, list[WorkingWindow]] = Field(default_factory=dict)
    is_continuous: bool = False
    is_default: bool = False
    exceptions: list[CalendarExceptionSpec] = Field(default_factory=list)

    @field_validator("working_hours")
    @classmethod
    def _days_are_iso_weekdays(
        cls, value: dict[str, list[WorkingWindow]]
    ) -> dict[str, list[WorkingWindow]]:
        for day, windows in value.items():
            if day not in {str(d) for d in range(ISO_MONDAY, ISO_SUNDAY + 1)}:
                raise ValueError(f"{day!r} is not an ISO weekday; Monday is '1' and Sunday is '7'")
            ordered = sorted(windows, key=lambda window: window.start)
            for earlier, later in pairwise(ordered):
                if later.start < earlier.end:
                    raise ValueError(
                        f"working windows on day {day} overlap; overlapping windows "
                        f"would count the same minute of SLA budget twice"
                    )
        return value

    @model_validator(mode="after")
    def _continuous_calendars_declare_no_week(self) -> CalendarSpec:
        if self.is_continuous and self.working_hours:
            raise ValueError(
                "a continuous calendar runs the clock at all times, so declared "
                "working hours would be silently ignored"
            )
        if not self.is_continuous and not self.working_hours:
            raise ValueError(
                "a calendar with neither continuous time nor any working window "
                "has no working time at all, and every deadline computed against "
                "it would be unreachable"
            )
        return self


# ---------------------------------------------------------------------------
# Translations and provisioning
# ---------------------------------------------------------------------------


class TranslationBundle(ControlPlaneModel):
    """A namespace's strings for one locale — the unit of import."""

    namespace: str = Field(min_length=1, max_length=32)
    locale: LocaleTag
    entries: dict[str, str] = Field(min_length=1)


class TenantSpec(ControlPlaneModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", max_length=64)
    name: str = Field(min_length=1, max_length=200)
    plan: str = Field(default="pilot", min_length=1, max_length=64)
    primary_locale: LocaleTag = "en"
    locales: list[LocaleTag] = Field(default_factory=lambda: ["en"], min_length=1, max_length=32)
    timezone: TimezoneName = "Asia/Kolkata"
    data_residency: str = Field(default="in", min_length=2, max_length=32)
    branding: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _primary_locale_is_declared(self) -> TenantSpec:
        if self.primary_locale not in self.locales:
            raise ValueError(
                f"primary_locale {self.primary_locale!r} is not in locales "
                f"{self.locales!r}; notification fallback and SLA reporting both "
                f"resolve through it, so a primary nobody declared makes them "
                f"fall back to a language the tenant does not speak"
            )
        return self


class ProvisioningRequest(ControlPlaneModel):
    """Everything needed to bring a tenant into existence, in one transaction."""

    tenant: TenantSpec
    #: A name from the seeded library. ``None`` provisions a bare tenant, which
    #: is a supported case: a customer migrating an existing taxonomy does not
    #: want a municipal one seeded underneath it.
    template: str | None = Field(default=None, max_length=64)
    #: Applied *after* the template, so a template can be adopted and adjusted in
    #: one call rather than requiring a second round trip that leaves the tenant
    #: briefly wrong.
    taxonomy: list[TaxonomyNodeSpec] = Field(default_factory=list)
    departments: list[DepartmentSpec] = Field(default_factory=list)
    zones: list[ZoneSpec] = Field(default_factory=list)
    calendars: list[CalendarSpec] = Field(default_factory=list)
    shifts: list[ShiftSpec] = Field(default_factory=list)
    prompt_sets: list[PromptSetSpec] = Field(default_factory=list)
    translations: list[TranslationBundle] = Field(default_factory=list)


class ProvisioningResult(ControlPlaneModel):
    tenant_id: uuid.UUID
    slug: str
    template: str | None
    template_version: str | None
    taxonomy_revision: int
    counts: dict[str, int]
