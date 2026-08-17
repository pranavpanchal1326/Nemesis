"""The seeded template library — a campus, an industrial park, a municipality.

**Why these are data files and not Python.** The program plan's test for the
whole control plane is "could a solutions engineer onboard a new campus without
opening an editor?", and a template written as a module fails that test in the
most embarrassing way: the library that exists to prove nothing is hardcoded
would itself be hardcoded. A JSON file can be copied, edited, diffed by a
non-engineer, and shipped by a customer.

**Why they are validated at import rather than at use.** ``load()`` parses a
template through the same Pydantic models the API binds, so a malformed template
fails when it is loaded — and ``all_templates()`` in the test suite loads every
one of them, which means a broken template fails CI rather than the first
onboarding that reaches for it.

**Versioning.** Each template declares its own ``version``, recorded on the
tenant and in ``tenant_provisioned``. The library will drift — that is what a
library is for — and without the version a support engineer cannot tell why a
campus onboarded in March behaves differently from one onboarded in September.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Final

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from nemesis.control_plane.errors import NotFoundError, ValidationError
from nemesis.control_plane.schemas import (
    CalendarSpec,
    ControlPlaneModel,
    DepartmentSpec,
    PromptSetSpec,
    ShiftSpec,
    TaxonomyNodeSpec,
    TranslationBundle,
    ZoneSpec,
)

TEMPLATE_DIR: Final = Path(__file__).resolve().parent / "templates"
TEMPLATE_SUFFIX: Final = ".json"


class TenantTemplate(ControlPlaneModel):
    """A ready-made deployment shape, applied at provisioning time."""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)

    #: Locales the template's own strings are written in. Checked against the
    #: tenant's declared set at provisioning time: applying a template whose
    #: translations are in a language the tenant did not declare would write
    #: rows nothing ever resolves.
    locales: list[str] = Field(default_factory=lambda: ["en"], min_length=1)

    taxonomy: list[TaxonomyNodeSpec] = Field(default_factory=list)
    departments: list[DepartmentSpec] = Field(default_factory=list)
    zones: list[ZoneSpec] = Field(default_factory=list)
    calendars: list[CalendarSpec] = Field(default_factory=list)
    shifts: list[ShiftSpec] = Field(default_factory=list)
    prompt_sets: list[PromptSetSpec] = Field(default_factory=list)
    translations: list[TranslationBundle] = Field(default_factory=list)


def available() -> list[str]:
    """Template names on disk, sorted. Empty is a packaging failure, not a state."""
    return sorted(path.stem for path in TEMPLATE_DIR.glob(f"*{TEMPLATE_SUFFIX}"))


@cache
def load(name: str) -> TenantTemplate:
    """Parse and validate one template.

    Cached because provisioning reads the same three files repeatedly and they
    do not change within a process. The cache is keyed by name and the return
    value is a frozen-by-convention Pydantic model — callers must not mutate it,
    and nothing in this package does.

    The path is built from a validated name rather than from caller input:
    ``TEMPLATE_DIR / f"{name}.json"`` with an unvalidated name is a traversal,
    and this function is reachable from an HTTP request body.
    """
    if name not in available():
        raise NotFoundError(f"no template {name!r}; available templates are {available()}")

    path = TEMPLATE_DIR / f"{name}{TEMPLATE_SUFFIX}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"template {name!r} is not valid JSON: {exc}") from exc

    try:
        template = TenantTemplate.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(f"template {name!r} failed validation: {exc}") from exc

    if template.name != name:
        raise ValidationError(
            f"template file {name!r} declares name {template.name!r}. The two must "
            f"agree — the filename is what a caller asks for and the declared name "
            f"is what gets recorded on the tenant."
        )
    return template


def all_templates() -> list[TenantTemplate]:
    """Every template, loaded. Used by the test that proves the library parses."""
    return [load(name) for name in available()]
