"""Provisioning, the template library, and the organisation trees.

The Phase 5 gate's first clause is here in its service-level form: a brand-new
tenant with a completely different taxonomy is onboarded end to end with no code
change. The HTTP version is in ``test_control_plane_api.py`` and the live-stack
version is ``scripts/gate_phase5.py``; all three exist because they fail
differently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.control_plane import organisation, provisioning, taxonomy, templates, translations
from nemesis.control_plane.errors import ConflictError, NotFoundError, ValidationError
from nemesis.control_plane.schemas import (
    CertificationSpec,
    ContractorSpec,
    DepartmentSpec,
    ProvisioningRequest,
    TaxonomyNodeSpec,
    TenantSpec,
    ZoneSpec,
)
from nemesis.db.models.event import Event
from nemesis.db.models.organisation import Zone
from nemesis.db.models.tenant import Tenant
from nemesis.domain.constants import SYSTEM_TENANT_SLUG
from nemesis.events.verify import verify_chain
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]


@asynccontextmanager
async def session_for(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session


def request_for(slug: str, **overrides: object) -> ProvisioningRequest:
    payload: dict[str, object] = {
        "tenant": TenantSpec(slug=slug, name=slug.title(), locales=["en"], primary_locale="en")
    }
    return ProvisioningRequest.model_validate(payload | overrides)


# ---------------------------------------------------------------------------
# The template library
# ---------------------------------------------------------------------------


def test_every_seeded_template_parses_and_declares_itself() -> None:
    """A broken template must fail CI, not the first onboarding that reaches it.

    ``all_templates()`` is what makes ``templates.load``'s validation a build
    gate rather than a runtime surprise — the library is data, and data that is
    never loaded is data that is never checked.
    """
    loaded = templates.all_templates()
    assert {t.name for t in loaded} == {"campus", "industrial_park", "municipality"}
    for template in loaded:
        assert template.version
        assert template.description
        assert template.taxonomy, f"{template.name} defines no categories"
        assert template.departments, f"{template.name} defines no departments"


def test_the_campus_and_municipality_taxonomies_share_no_category() -> None:
    """The gate says "zero categories in common with the civic set".

    Asserted on the library itself rather than only through a provisioning run,
    because the property is about the templates: if somebody adds ``pothole`` to
    the campus template, the gate's premise quietly stops holding and the
    end-to-end test would still pass.
    """
    campus = {node.key for node in templates.load("campus").taxonomy}
    municipality = {node.key for node in templates.load("municipality").taxonomy}
    assert campus & municipality == set()


def test_every_template_routing_hint_names_a_department_it_creates() -> None:
    """A hint pointing nowhere looks identical on screen to one that works."""
    for template in templates.all_templates():
        codes = {department.code for department in template.departments}
        for node in template.taxonomy:
            hinted = node.routing_hints.department_code
            if hinted is not None:
                assert hinted in codes, f"{template.name}: {node.key} -> {hinted}"


def test_every_template_prompt_set_names_a_category_it_creates() -> None:
    for template in templates.all_templates():
        keys = {node.key for node in template.taxonomy}
        for prompt_set in template.prompt_sets:
            assert prompt_set.node_key in keys, f"{template.name}: {prompt_set.node_key}"
            assert prompt_set.locale in template.locales


def test_an_unknown_template_name_cannot_traverse_the_filesystem() -> None:
    """The name reaches ``TEMPLATE_DIR / f"{name}.json"`` from a request body."""
    with pytest.raises(NotFoundError):
        templates.load("../../../etc/passwd")


# ---------------------------------------------------------------------------
# Provisioning — gate clause 1
# ---------------------------------------------------------------------------


async def test_a_campus_is_provisioned_end_to_end_from_a_template(
    migrated_engine: AsyncEngine,
) -> None:
    """One call produces a working tenant with a non-civic taxonomy.

    The assertions walk the whole result rather than checking a count, because
    "provisioning succeeded" and "provisioning produced a usable tenant" are
    different claims and only the second one matters.
    """
    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session, request=request_for("north-campus", template="campus")
        )
        await session.commit()

        with tenant_scope(result.tenant_id):
            nodes = await taxonomy.list_nodes(session, tenant_id=result.tenant_id)
            departments = await organisation.list_departments(session, tenant_id=result.tenant_id)
            zones = await organisation.list_zones(session, tenant_id=result.tenant_id)
            shifts = await organisation.list_shifts(session, tenant_id=result.tenant_id)
            prompts = await taxonomy.prompt_sets_for(
                session, tenant_id=result.tenant_id, locale="en", encoder="clip"
            )

    keys = {node.key for node in nodes}
    assert "elevator_fault" in keys
    assert "lab_spill" in keys
    assert "pothole" not in keys
    assert result.template == "campus"
    assert result.template_version == "1.0.0"
    assert result.taxonomy_revision == 1
    assert {d.code for d in departments} >= {"EST", "EST-MECH", "IT", "EHS"}
    assert {z.code for z in zones} >= {"CAMPUS", "BLD-CHEM"}
    assert [s.code for s in shifts] == ["night-watch"]
    assert {key for key, _ in prompts} >= {"elevator_fault", "lab_spill"}


async def test_the_department_tree_is_built_with_derived_paths(
    migrated_engine: AsyncEngine,
) -> None:
    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session, request=request_for("estate", template="industrial_park")
        )
        await session.commit()
        with tenant_scope(result.tenant_id):
            departments = {
                d.code: d
                for d in await organisation.list_departments(session, tenant_id=result.tenant_id)
            }

    assert departments["EM"].path == "EM"
    assert departments["HSE"].path == "EM/HSE"
    assert departments["HSE"].parent_id == departments["EM"].id
    assert departments["EM"].is_assignable is False


async def test_two_tenants_from_different_templates_coexist(
    migrated_engine: AsyncEngine,
) -> None:
    """Gate clause 3, at the provisioning level.

    Two customers, two vocabularies, one database, one process — and each read
    sees only its own. A leak here is a customer's defect taxonomy visible to a
    competitor, which is materially worse than a bug.
    """
    async with session_for(migrated_engine) as session:
        campus = await provisioning.provision(
            session, request=request_for("campus-a", template="campus")
        )
        city = await provisioning.provision(
            session,
            request=request_for(
                "city-b",
                template="municipality",
                tenant=TenantSpec(
                    slug="city-b", name="City B", locales=["en", "hi", "mr"], primary_locale="en"
                ),
            ),
        )
        await session.commit()

        with tenant_scope(campus.tenant_id):
            campus_keys = {
                n.key for n in await taxonomy.list_nodes(session, tenant_id=campus.tenant_id)
            }
        with tenant_scope(city.tenant_id):
            city_keys = {
                n.key for n in await taxonomy.list_nodes(session, tenant_id=city.tenant_id)
            }

    assert campus_keys & city_keys == set()
    assert "elevator_fault" in campus_keys
    assert "pothole" in city_keys


async def test_provisioning_appends_a_verifiable_tenant_chain(
    migrated_engine: AsyncEngine,
) -> None:
    """Configuration history is hash-chained like everything else (§9.1).

    The organisation half is two events, not one per department: a template that
    creates eleven units is a single operator action, and eleven events would
    bury it. Phase 6's policy seeding is deliberately *not* batched the same way
    — each seeded document walks the full draft → review → approve → activate
    lifecycle, because there must be exactly one way a policy becomes live and a
    shortcut for seeds would be a second one.

    The chain is verified rather than merely counted, because an event whose
    hash does not recompute is not evidence of anything.
    """
    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session, request=request_for("chain-check", template="campus")
        )
        await session.commit()

        events = list(
            (
                await session.execute(
                    select(Event)
                    .where(Event.tenant_id == result.tenant_id)
                    .order_by(Event.sequence)
                )
            )
            .scalars()
            .all()
        )
        report = await verify_chain(
            session,
            tenant_id=result.tenant_id,
            entity_type="tenant",
            entity_id=result.tenant_id,
        )

    kinds = [event.event_type for event in events]
    assert kinds[:2] == ["tenant_provisioned", "taxonomy_published"]
    assert events[0].payload["template"] == "campus"
    assert events[0].payload["template_version"] == "1.0.0"

    # Phase 6 seeds four baseline documents, each drafted and then moved through
    # three transitions. Asserted as a shape rather than a count so adding a
    # fifth governed structure is a one-line change here, not a puzzle.
    seeded = {
        payload["kind"]
        for event_type, payload in ((event.event_type, event.payload) for event in events)
        if event_type == "policy_drafted"
    }
    assert seeded == {"safety_ruleset", "severity_rubric", "dedup_thresholds", "sla_matrix"}
    assert kinds.count("policy_transitioned") == 3 * len(seeded)

    assert report.is_intact, report.first_break


async def test_a_failed_provisioning_leaves_no_partial_tenant(
    migrated_engine: AsyncEngine,
) -> None:
    """The whole reason the services never commit.

    A tenant with a taxonomy and no departments accepts complaints and misroutes
    them, which is worse than no tenant. The failure is induced late — a
    duplicate taxonomy key inside the request — so the rollback has real work to
    undo rather than being vacuously true.
    """
    async with session_for(migrated_engine) as session:
        request = request_for(
            "doomed",
            departments=[DepartmentSpec(code="OPS", name="Ops")],
            taxonomy=[
                TaxonomyNodeSpec(key="a", display_name="A"),
                TaxonomyNodeSpec(key="a", display_name="A again"),
            ],
        )
        with pytest.raises(ValidationError):
            await provisioning.provision(session, request=request)
        await session.rollback()

        # tenant-scope-exempt: asserting the tenant registry itself is empty.
        surviving = (
            await session.execute(select(Tenant.id).where(Tenant.slug == "doomed"))
        ).scalar_one_or_none()

    assert surviving is None


async def test_a_reserved_slug_cannot_be_claimed(migrated_engine: AsyncEngine) -> None:
    """``__system__`` owns degradations and integrity findings.

    Letting a customer claim it would interleave citizen data into the
    deployment's own operational audit trail — and the slug is legal under the
    tenant slug pattern, so nothing else refuses it.
    """
    async with session_for(migrated_engine) as session:
        request = ProvisioningRequest(
            tenant=TenantSpec(slug=SYSTEM_TENANT_SLUG.strip("_"), name="Impostor")
        )
        # The stripped form is a legal customer slug; the reserved one is not.
        await provisioning.provision(session, request=request)
        await session.rollback()

    assert SYSTEM_TENANT_SLUG in provisioning.RESERVED_SLUGS


async def test_a_duplicate_slug_is_a_conflict(migrated_engine: AsyncEngine) -> None:
    async with session_for(migrated_engine) as session:
        await provisioning.provision(session, request=request_for("taken"))
        await session.commit()
        with pytest.raises(ConflictError, match="already taken"):
            await provisioning.provision(session, request=request_for("taken"))


async def test_a_request_colliding_with_its_template_is_refused(
    migrated_engine: AsyncEngine,
) -> None:
    """Provisioning is additive; a silent last-writer-wins would be worse.

    The caller either misunderstood what the template contains or is trying to
    override it, and both deserve an error naming the key.
    """
    async with session_for(migrated_engine) as session:
        request = request_for(
            "clash",
            template="campus",
            taxonomy=[TaxonomyNodeSpec(key="lab_spill", display_name="Mine")],
        )
        with pytest.raises(ValidationError, match="lab_spill"):
            await provisioning.provision(session, request=request)


async def test_a_routing_hint_naming_no_department_is_refused(
    migrated_engine: AsyncEngine,
) -> None:
    async with session_for(migrated_engine) as session:
        request = request_for(
            "dangling",
            taxonomy=[
                TaxonomyNodeSpec(
                    key="thing", display_name="Thing", routing_hints={"department_code": "NOPE"}
                )
            ],
        )
        with pytest.raises(ValidationError, match="NOPE"):
            await provisioning.provision(session, request=request)


async def test_a_template_speaking_an_undeclared_language_is_refused(
    migrated_engine: AsyncEngine,
) -> None:
    """The municipality template carries Hindi and Marathi strings.

    A tenant that declares only English would get rows nothing resolves, which
    read on screen exactly like a missing translation. Refused before the first
    write so the message names the whole set at once.
    """
    async with session_for(migrated_engine) as session:
        request = request_for("english-only", template="municipality")
        with pytest.raises(ValidationError, match="declared"):
            await provisioning.provision(session, request=request)


async def test_a_bare_tenant_is_a_supported_outcome(migrated_engine: AsyncEngine) -> None:
    """A customer migrating an existing taxonomy does not want one seeded."""
    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(session, request=request_for("bare"))
        await session.commit()
        with tenant_scope(result.tenant_id):
            nodes = await taxonomy.list_nodes(session, tenant_id=result.tenant_id)

    assert nodes == []
    assert result.template is None
    assert result.taxonomy_revision == 1


# ---------------------------------------------------------------------------
# Zones, contractors, and certification scopes
# ---------------------------------------------------------------------------


async def test_a_zone_boundary_is_stored_and_its_centroid_computed(
    migrated_engine: AsyncEngine,
) -> None:
    """PostGIS computes the centroid, so it can never disagree with the polygon.

    A square from (0,0) to (2,2) has its centroid at (1,1); anything else means
    the ring order or the coordinate order was misread, and §23.2's equity
    rollup would then group complaints by the wrong ward.

    The tolerance is 1e-3 rather than 1e-6 because the column is ``geography``,
    so PostGIS computes the centroid on the spheroid rather than on a plane —
    it comes out at latitude 1.00005, which is correct and is exactly what the
    containment queries that will use it also assume. Tightening the tolerance
    would be asserting planar geometry against a geodetic column.
    """
    from geoalchemy2 import Geometry
    from sqlalchemy import cast, func

    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(session, request=request_for("mapped"))
        with tenant_scope(result.tenant_id):
            await organisation.create_zone(
                session,
                tenant_id=result.tenant_id,
                spec=ZoneSpec(
                    code="W-01",
                    name="Ward 1",
                    kind="ward",
                    boundary=[[[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]]],
                ),
            )
            await session.commit()

            point = (
                await session.execute(
                    select(
                        func.ST_X(cast(Zone.centroid, Geometry)),
                        func.ST_Y(cast(Zone.centroid, Geometry)),
                    ).where(Zone.tenant_id == result.tenant_id, Zone.code == "W-01")
                )
            ).one()

    assert point[0] == pytest.approx(1.0, abs=1e-3)
    assert point[1] == pytest.approx(1.0, abs=1e-3)


def test_an_unclosed_polygon_ring_is_refused_with_the_ring_named() -> None:
    """PostGIS would reject it too, naming a WKT string the caller never wrote."""
    with pytest.raises(ValueError, match="not closed"):
        ZoneSpec(
            code="W-01",
            name="Ward 1",
            boundary=[[[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]]],
        )


async def test_certification_validity_is_answered_as_of_a_date(
    migrated_engine: AsyncEngine,
) -> None:
    """§17 asks retrospectively: was this contractor certified when assigned?

    A function that could only answer for today could not audit an assignment
    made last March, which is the only question the audit actually has.
    """
    from datetime import UTC, date, datetime

    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session, request=request_for("certifying", template="campus")
        )
        with tenant_scope(result.tenant_id):
            await organisation.register_contractor(
                session,
                tenant_id=result.tenant_id,
                spec=ContractorSpec(registration_id="LIFT-CO-1", name="Lift Co"),
            )
            await organisation.certify(
                session,
                tenant_id=result.tenant_id,
                spec=CertificationSpec(
                    contractor_registration_id="LIFT-CO-1",
                    taxonomy_key="elevator_fault",
                    valid_from=date(2026, 1, 1),
                    valid_until=date(2026, 6, 30),
                ),
            )
            await session.commit()

            during = await organisation.certified_contractors(
                session,
                tenant_id=result.tenant_id,
                taxonomy_key="elevator_fault",
                on=datetime(2026, 3, 1, tzinfo=UTC),
            )
            after = await organisation.certified_contractors(
                session,
                tenant_id=result.tenant_id,
                taxonomy_key="elevator_fault",
                on=datetime(2026, 9, 1, tzinfo=UTC),
            )

    assert [c.registration_id for c in during] == ["LIFT-CO-1"]
    assert after == []


async def test_a_contractor_cannot_be_certified_across_a_tenant_boundary(
    migrated_engine: AsyncEngine,
) -> None:
    """Both sides resolve by tenant-scoped lookup, so the grant cannot cross.

    The failure has to be "not found", not "forbidden" — a distinguishable
    rejection would confirm that another tenant's contractor exists.
    """
    async with session_for(migrated_engine) as session:
        owner = await provisioning.provision(
            session, request=request_for("owner", template="campus")
        )
        other = await provisioning.provision(
            session, request=request_for("other", template="campus")
        )
        with tenant_scope(owner.tenant_id):
            await organisation.register_contractor(
                session,
                tenant_id=owner.tenant_id,
                spec=ContractorSpec(registration_id="ONLY-MINE", name="Mine"),
            )
        await session.commit()

        with tenant_scope(other.tenant_id), pytest.raises(NotFoundError):
            await organisation.certify(
                session,
                tenant_id=other.tenant_id,
                spec=CertificationSpec(
                    contractor_registration_id="ONLY-MINE", taxonomy_key="elevator_fault"
                ),
            )


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


async def test_coverage_reports_every_declared_locale_including_untouched_ones(
    migrated_engine: AsyncEngine,
) -> None:
    """The locale nobody has started is the one worth seeing.

    A report listing only locales with some translations cannot show that
    Marathi has none, which is precisely the gap that reaches a citizen.
    """
    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session,
            request=request_for(
                "multi",
                template="municipality",
                tenant=TenantSpec(
                    slug="multi", name="Multi", locales=["en", "hi", "mr"], primary_locale="en"
                ),
            ),
        )
        await session.commit()
        with tenant_scope(result.tenant_id):
            reports = {
                report.locale: report
                for report in await translations.coverage_across_locales(
                    session, tenant_id=result.tenant_id
                )
            }

    assert set(reports) == {"en", "hi", "mr"}
    # The template translates every category into hi and mr, and none into en —
    # English is the fallback carried on the node itself.
    assert reports["mr"].ratio == pytest.approx(1.0)
    assert reports["en"].translated == 0
    assert reports["en"].missing_keys


async def test_importing_a_bundle_twice_updates_rather_than_duplicating(
    migrated_engine: AsyncEngine,
) -> None:
    """Re-importing a corrected file is the normal onboarding operation."""
    from nemesis.control_plane.schemas import TranslationBundle

    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(
            session,
            request=request_for(
                "reimport",
                tenant=TenantSpec(
                    slug="reimport", name="Reimport", locales=["en", "hi"], primary_locale="en"
                ),
                taxonomy=[TaxonomyNodeSpec(key="thing", display_name="Thing")],
            ),
        )
        with tenant_scope(result.tenant_id):
            for value in ("पहला", "दूसरा"):
                await translations.import_bundle(
                    session,
                    tenant_id=result.tenant_id,
                    bundle=TranslationBundle(
                        namespace="taxonomy", locale="hi", entries={"thing": value}
                    ),
                )
            await session.commit()
            bundle = await translations.resolve_bundle(
                session, tenant_id=result.tenant_id, namespace="taxonomy", locale="hi"
            )
            total = await translations.count_translations(session, tenant_id=result.tenant_id)

    assert bundle == {"thing": "दूसरा"}
    assert total == 1


async def test_an_unknown_namespace_is_refused(migrated_engine: AsyncEngine) -> None:
    """A typo imports strings nothing will look up — invisible on screen."""
    from nemesis.control_plane.schemas import TranslationBundle

    async with session_for(migrated_engine) as session:
        result = await provisioning.provision(session, request=request_for("namespaces"))
        with tenant_scope(result.tenant_id), pytest.raises(ValidationError, match="namespace"):
            await translations.import_bundle(
                session,
                tenant_id=result.tenant_id,
                bundle=TranslationBundle(namespace="taxnomy", locale="en", entries={"a": "b"}),
            )
