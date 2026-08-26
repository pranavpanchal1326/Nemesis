"""Tenant-authored public names, in the reader's language — C7, ADR-0052.

Two catalogues answer a public page, and they are different kinds of thing:

* the **§22.2 notices**, which NEMESIS authors and a tenant must not be able to
  edit — `public.notices`;
* the **names of the tenant's own places and categories**, which only the tenant
  can be correct about — the `translations` table, through
  `control_plane.translations.resolve_bundle`.

This module is the second half. `NAMESPACE_ZONE` and `NAMESPACE_TAXONOMY` have
existed since Phase 5 and the public endpoints never consulted them, which is
C7's complaint in one sentence: *"`zones.name` is an English constant on every
response; `NAMESPACE_ZONE` exists and the endpoints never consult it."*

**One query per namespace per response, never one per label.** A ward index
renders fifty places and a hundred category rows; resolving each one on its own
would turn a cached public page into a hundred and fifty round trips. The
bundle read is exactly what `resolve_bundle` was built for.

**The fallback is the row's own name, not a chain.** `db.models.i18n` argues
this and it is not restated here — except to note the consequence a reader sees:
a Marathi page with three untranslated ward names is a page in Marathi with
three gaps, which is honest, rather than a page in two languages, which is not
legible in either.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.translations import resolve_bundle
from nemesis.db.models.i18n import NAMESPACE_TAXONOMY, NAMESPACE_ZONE
from nemesis.db.models.taxonomy import TaxonomyNode


@dataclass(frozen=True, slots=True)
class PublicStrings:
    """Whatever the tenant has translated into one locale, for one response."""

    locale: str
    zones: dict[str, str]
    categories: dict[str, str]

    def zone(self, code: str, fallback: str) -> str:
        return self.zones.get(code, fallback)

    def category(self, key: str) -> str:
        """The display name for a taxonomy key.

        Three steps, and the middle one is the reason this class reads the
        taxonomy table as well as the translations table: the node's own
        `display_name` is the tenant's label in its primary language, and
        falling straight from "no translation" to "the key" would have made an
        English page read `roads.pothole` where it used to read `Pothole`.

        The last fallback is the key itself. A key is not a label and looks like
        one only by accident — but a public API returning an empty string for an
        untranslated category renders a blank cell beside a count, which is the
        shape §E18 spends its whole argument forbidding.
        """
        return self.categories.get(key, key)


async def public_strings(
    session: AsyncSession, *, tenant_id: uuid.UUID, locale: str
) -> PublicStrings:
    """Everything a public body needs to name things. Three queries, whatever
    the page renders — never one per label."""
    zones = await resolve_bundle(
        session, tenant_id=tenant_id, namespace=NAMESPACE_ZONE, locale=locale
    )
    # The node's own label first, then the translation on top. A zone's fallback
    # arrives with the aggregate (`zones.name`); a category's does not, because
    # the breakdown carries keys rather than rows.
    rows = await session.execute(
        select(TaxonomyNode.key, TaxonomyNode.display_name).where(
            TaxonomyNode.tenant_id == tenant_id
        )
    )
    categories: dict[str, str] = dict(rows.all())  # type: ignore[arg-type]
    categories.update(
        await resolve_bundle(
            session, tenant_id=tenant_id, namespace=NAMESPACE_TAXONOMY, locale=locale
        )
    )
    return PublicStrings(locale=locale, zones=zones, categories=categories)
