"""Tenant-authored strings: import, resolve, and measure what is missing.

Three operations, and the third is the one that keeps this honest. Importing
strings is easy; resolving them is easy; knowing that a tenant declared Marathi
and has translated 40% of its taxonomy into it is the thing that stops a
half-localised deployment from reaching a citizen. ``coverage()`` exists so that
gap is a number somebody can be shown, rather than a discovery made by a user
looking at a screen of English on a Marathi interface.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.errors import NotFoundError, ValidationError
from nemesis.control_plane.schemas import TranslationBundle
from nemesis.db.models.i18n import KNOWN_NAMESPACES, NAMESPACE_TAXONOMY, Translation
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.db.models.tenant import Tenant


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """How much of one namespace exists in one locale."""

    namespace: str
    locale: str
    translatable: int
    translated: int
    missing_keys: tuple[str, ...]

    @property
    def ratio(self) -> float:
        """1.0 for a namespace with nothing in it — vacuously complete.

        Chosen over 0.0 deliberately: a tenant that has not defined a taxonomy
        yet is not 0% localised, and reporting it that way would put a red
        number on an onboarding dashboard for a step nobody has reached.
        """
        return 1.0 if self.translatable == 0 else self.translated / self.translatable


async def import_bundle(
    session: AsyncSession, *, tenant_id: uuid.UUID, bundle: TranslationBundle
) -> int:
    """Upsert every entry in a bundle. Returns the number of rows written.

    An upsert rather than delete-then-insert, and rather than a plain insert
    that fails on a duplicate. Re-importing a corrected bundle is the normal
    operation during onboarding — a translator sends a revised file — and both
    alternatives make that either destructive or an error.
    """
    await _assert_locale_declared(session, tenant_id=tenant_id, locale=bundle.locale)
    if bundle.namespace not in KNOWN_NAMESPACES:
        raise ValidationError(
            f"unknown translation namespace {bundle.namespace!r}; known namespaces are "
            f"{sorted(KNOWN_NAMESPACES)}. A typo here imports strings nothing will ever "
            f"look up, which reads on screen exactly like a missing translation."
        )

    rows = [
        {
            "tenant_id": tenant_id,
            "namespace": bundle.namespace,
            "message_key": key,
            "locale": bundle.locale,
            "value": value,
        }
        for key, value in bundle.entries.items()
    ]
    statement = pg_insert(Translation).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                Translation.tenant_id,
                Translation.namespace,
                Translation.message_key,
                Translation.locale,
            ],
            set_={"value": statement.excluded.value},
        )
    )
    await session.flush()
    return len(rows)


async def resolve_bundle(
    session: AsyncSession, *, tenant_id: uuid.UUID, namespace: str, locale: str
) -> dict[str, str]:
    """Every translated string in a namespace, for one locale.

    One query per rendered surface, never one per label. The caller applies its
    own fallback — see ``db.models.i18n`` on why there is no chain here.
    """
    rows = await session.execute(
        select(Translation.message_key, Translation.value).where(
            Translation.tenant_id == tenant_id,
            Translation.namespace == namespace,
            Translation.locale == locale,
        )
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def coverage(session: AsyncSession, *, tenant_id: uuid.UUID, locale: str) -> CoverageReport:
    """Taxonomy translation coverage for one locale.

    Scoped to the taxonomy namespace because that is the one whose completeness
    has a user-visible consequence: an untranslated category is the label a
    citizen reads when choosing what to report. Departments and zones appear on
    staff surfaces, where an English fallback is an inconvenience rather than a
    barrier to reporting a hazard.
    """
    translatable = await session.execute(
        select(TaxonomyNode.key).where(
            TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.is_active.is_(True)
        )
    )
    keys = {key for (key,) in translatable.all()}

    translated = await session.execute(
        select(Translation.message_key).where(
            Translation.tenant_id == tenant_id,
            Translation.namespace == NAMESPACE_TAXONOMY,
            Translation.locale == locale,
        )
    )
    have = {key for (key,) in translated.all()} & keys

    return CoverageReport(
        namespace=NAMESPACE_TAXONOMY,
        locale=locale,
        translatable=len(keys),
        translated=len(have),
        missing_keys=tuple(sorted(keys - have)),
    )


async def coverage_across_locales(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[CoverageReport]:
    """Coverage for every locale the tenant declared.

    Every *declared* locale, including ones with no translations at all. A
    report that only lists locales somebody has started work on cannot show the
    one nobody has started, which is the one worth seeing.
    """
    locales = await _declared_locales(session, tenant_id=tenant_id)
    return [await coverage(session, tenant_id=tenant_id, locale=locale) for locale in locales]


async def count_translations(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    total = await session.execute(
        select(func.count()).select_from(Translation).where(Translation.tenant_id == tenant_id)
    )
    return int(total.scalar_one())


async def _declared_locales(session: AsyncSession, *, tenant_id: uuid.UUID) -> Sequence[str]:
    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant. See tenancy.registry.
    declared = (
        await session.execute(select(Tenant.locales).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if declared is None:
        raise NotFoundError("tenant does not exist")
    return sorted(declared)


async def _assert_locale_declared(
    session: AsyncSession, *, tenant_id: uuid.UUID, locale: str
) -> None:
    declared = await _declared_locales(session, tenant_id=tenant_id)
    if locale not in declared:
        raise ValidationError(
            f"locale {locale!r} is not declared by this tenant (declared: {list(declared)}). "
            f"Importing strings for an undeclared locale writes rows nothing resolves."
        )
