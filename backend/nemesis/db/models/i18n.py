"""Translations as tenant data, so a new language is an import not a release.

The program plan's requirement is stated as a test: *a new language is a data
import, not a release*. That rules out the ordinary approach — message catalogues
compiled into the artefact — for anything a tenant authors, because a tenant
authors its own taxonomy and nobody at NEMESIS can ship a translation for a
category that did not exist at build time.

**Two catalogues, and only one of them lives here.** Product copy — button
labels, error prose, the §22.1 consent text — is authored by NEMESIS, versioned
with the code, and reviewed like code; Phase 18 owns it. What lives in this
table is *tenant-authored* text: the display name of a taxonomy node, the name
of a ward, the label on a business calendar. The distinction is who can be
correct about the string. Mixing the two would mean a tenant could overwrite the
wording of a legal notice, which is not a localisation feature.

**Resolution is deliberately not a fallback chain.** A missing translation
resolves to the row's own ``display_name`` (or equivalent), not to the tenant's
primary locale and then to English. A chain produces a screen where three
languages are interleaved and the reader cannot tell which strings are
authoritative; a single documented fallback produces a screen in one language
with some untranslated entries, which is honest about what has been translated.
``TranslationService.coverage()`` exists so that gap is measurable rather than
discovered by a user.
"""

from __future__ import annotations

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

#: Namespaces that exist today. A *convention*, checked by the service, not a
#: CHECK constraint — a tenant importing a namespace nobody anticipated should
#: get a validation error it can read, not a driver-level constraint violation,
#: and adding one must not be a migration.
NAMESPACE_TAXONOMY = "taxonomy"
NAMESPACE_ORGANISATION = "organisation"
NAMESPACE_ZONE = "zone"
NAMESPACE_CALENDAR = "calendar"

KNOWN_NAMESPACES = frozenset(
    {NAMESPACE_TAXONOMY, NAMESPACE_ORGANISATION, NAMESPACE_ZONE, NAMESPACE_CALENDAR}
)


class Translation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One string, in one locale, for one tenant-owned entity."""

    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "namespace",
            "message_key",
            "locale",
            name="uq_translations_tenant_id_namespace_message_key_locale",
        ),
        # The read is always "every string in this namespace for this locale" —
        # one query per rendered page, never one per label. Without this index
        # that read is a scan of every translation the tenant owns.
        Index(
            "ix_translations_tenant_id_namespace_locale",
            "tenant_id",
            "namespace",
            "locale",
        ),
    )

    namespace: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The stable key of the thing being translated — a taxonomy node's ``key``,
    #: a zone's ``code``. Not the entity's UUID: an import is authored against
    #: keys a human recognises, and keying by UUID would make a translation
    #: bundle unreviewable and impossible to prepare before the rows exist.
    message_key: Mapped[str] = mapped_column(String(128), nullable=False)

    locale: Mapped[str] = mapped_column(String(35), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
