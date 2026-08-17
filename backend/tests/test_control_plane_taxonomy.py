"""Taxonomy as tenant data — the hierarchy, the digest, and the isolation.

The Phase 5 gate has three clauses and two of them are about this module: a
brand-new tenant with a completely different taxonomy works with no code change,
and two tenants with conflicting taxonomies operate simultaneously without
leakage. The end-to-end version of the first runs over HTTP in
``test_control_plane_api.py`` and live against the stack in
``scripts/gate_phase5.py``; what is proven here is the layer underneath.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.control_plane import hierarchy, taxonomy
from nemesis.control_plane.errors import (
    ConflictError,
    HierarchyError,
    NotFoundError,
    ValidationError,
)
from nemesis.control_plane.schemas import PromptSetSpec, TaxonomyNodeSpec, TaxonomyNodeUpdate
from nemesis.db.models.event import Event
from nemesis.db.models.taxonomy import MAX_TAXONOMY_DEPTH, TaxonomyNode
from nemesis.db.models.tenant import Tenant
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session with the tenant bound, as one context manager.

    ``tenant_scope`` is *synchronous*, so it cannot appear in an ``async with``
    list alongside the session — that fails at runtime with a message about the
    asynchronous context manager protocol, several lines away from the cause.
    Wrapping both here keeps every test one line instead of two nested blocks,
    and keeps the mistake unrepeatable.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            yield session


def node(key: str, *, parent: str | None = None, **overrides: object) -> TaxonomyNodeSpec:
    return TaxonomyNodeSpec.model_validate(
        {"key": key, "display_name": key.replace("_", " ").title(), "parent_key": parent}
        | overrides
    )


# ---------------------------------------------------------------------------
# The path arithmetic, without a database
# ---------------------------------------------------------------------------


def test_subtree_prefix_does_not_match_a_sibling_that_shares_a_prefix() -> None:
    """``roads`` is not an ancestor of ``roadside_waste``.

    The separator in the prefix is the whole reason ``is_descendant`` is not a
    bare ``startswith``. Without it, deactivating a category would silently
    deactivate an unrelated one whose key happened to begin with the same
    letters — and both would look correct in every listing that sorts by path.
    """
    assert not hierarchy.is_descendant("roadside_waste", "roads")
    assert hierarchy.is_descendant("roads/pothole", "roads")
    assert hierarchy.is_descendant("roads", "roads")


def test_like_pattern_escapes_wildcards_in_a_key() -> None:
    """``_`` is legal in a key and is a single-character wildcard in LIKE.

    ``water_leak``'s subtree pattern must not match ``waterXleak``. Not
    hypothetical: underscore is the recommended word separator, so almost every
    real key contains one.
    """
    assert hierarchy.subtree_pattern("water_leak") == "water\\_leak/%"


def test_reparenting_a_path_that_is_not_under_the_prefix_raises() -> None:
    with pytest.raises(HierarchyError):
        hierarchy.reparented_path("waste/garbage", "roads", "civic/roads")


# ---------------------------------------------------------------------------
# Creation, hierarchy, and reparenting
# ---------------------------------------------------------------------------


async def test_a_tree_is_created_with_paths_and_depths_derived_from_parents(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(
            session,
            tenant_id=tenant_id,
            # Deliberately out of order: a template is authored as a list, not
            # as a traversal, and the service is what derives the order.
            specs=[
                node("pothole", parent="roads"),
                node("roads"),
                node("deep_pothole", parent="pothole"),
            ],
        )
        await session.commit()
        nodes = {n.key: n for n in await taxonomy.list_nodes(session, tenant_id=tenant_id)}

    assert nodes["roads"].path == "roads"
    assert nodes["roads"].depth == 0
    assert nodes["pothole"].path == "roads/pothole"
    assert nodes["pothole"].depth == 1
    assert nodes["deep_pothole"].path == "roads/pothole/deep_pothole"
    assert nodes["deep_pothole"].depth == 2
    assert nodes["pothole"].parent_id == nodes["roads"].id


async def test_reparenting_rewrites_every_descendant_path(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A subtree move is the operation most likely to leave the tree torn.

    Asserted on the *grandchild*, because a rewrite that only handles direct
    children leaves the deeper rows pointing at a path that no longer exists —
    and every listing still looks plausible, because it sorts by that path.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(
            session,
            tenant_id=tenant_id,
            specs=[
                node("civic"),
                node("roads"),
                node("pothole", parent="roads"),
                node("deep_pothole", parent="pothole"),
            ],
        )
        await session.commit()

        await taxonomy.update_node(
            session,
            tenant_id=tenant_id,
            key="roads",
            changes=TaxonomyNodeUpdate(parent_key="civic"),
        )
        await session.commit()
        nodes = {n.key: n for n in await taxonomy.list_nodes(session, tenant_id=tenant_id)}

    assert nodes["roads"].path == "civic/roads"
    assert nodes["pothole"].path == "civic/roads/pothole"
    assert nodes["deep_pothole"].path == "civic/roads/pothole/deep_pothole"
    assert nodes["deep_pothole"].depth == 3


async def test_a_node_cannot_be_moved_under_its_own_descendant(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The cycle a foreign key cannot prevent.

    ``parent_id`` has a self-referencing FK and a CHECK against direct
    self-parenting, and neither catches ``a -> b -> a``. Left unchecked, the
    ancestor walk Phase 6 runs per scored complaint never terminates.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(
            session, tenant_id=tenant_id, specs=[node("roads"), node("pothole", parent="roads")]
        )
        await session.commit()

        with pytest.raises(HierarchyError, match="its own ancestor"):
            await taxonomy.update_node(
                session,
                tenant_id=tenant_id,
                key="roads",
                changes=TaxonomyNodeUpdate(parent_key="pothole"),
            )


async def test_a_batch_containing_a_parent_cycle_is_refused_before_any_write(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(HierarchyError, match="cycle"):
            await taxonomy.import_nodes(
                session, tenant_id=tenant_id, specs=[node("a", parent="b"), node("b", parent="a")]
            )
        assert await taxonomy.count_nodes(session, tenant_id=tenant_id) == 0


async def test_depth_beyond_the_bound_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        specs = [node("n0")]
        specs += [node(f"n{i}", parent=f"n{i - 1}") for i in range(1, MAX_TAXONOMY_DEPTH + 1)]
        with pytest.raises(HierarchyError, match="depth"):
            await taxonomy.import_nodes(session, tenant_id=tenant_id, specs=specs)


async def test_a_duplicate_key_is_a_conflict_not_a_second_row(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.create_node(session, tenant_id=tenant_id, spec=node("pothole"))
        await session.commit()
        with pytest.raises(ConflictError):
            await taxonomy.create_node(session, tenant_id=tenant_id, spec=node("pothole"))


async def test_an_unknown_parent_names_the_key_rather_than_a_uuid(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(NotFoundError, match="roads"):
            await taxonomy.create_node(
                session, tenant_id=tenant_id, spec=node("pothole", parent="roads")
            )


# ---------------------------------------------------------------------------
# Tenant isolation — gate clause 3
# ---------------------------------------------------------------------------


async def test_two_tenants_hold_conflicting_taxonomies_without_leakage(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The same key, meaning different things, under different parents.

    The gate's third clause in its sharpest form: not merely two disjoint
    taxonomies, but the *same key* used by both with a different hierarchy and
    different severity semantics. If any read is unscoped, one of these
    assertions sees the other tenant's row.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(
            session,
            tenant_id=tenant_id,
            specs=[
                node("roads"),
                node("blockage", parent="roads", severity_semantics={"floor": 2.0}),
            ],
        )
        await session.commit()

    async with scoped(migrated_engine, other_tenant_id) as session:
        await taxonomy.import_nodes(
            session,
            tenant_id=other_tenant_id,
            specs=[
                node("plumbing"),
                node("blockage", parent="plumbing", severity_semantics={"floor": 7.0}),
            ],
        )
        await session.commit()

    async with scoped(migrated_engine, tenant_id) as session:
        first = await taxonomy.list_nodes(session, tenant_id=tenant_id)
        first_blockage = await taxonomy.require_node(session, tenant_id=tenant_id, key="blockage")

    async with scoped(migrated_engine, other_tenant_id) as session:
        second = await taxonomy.list_nodes(session, tenant_id=other_tenant_id)
        second_blockage = await taxonomy.require_node(
            session, tenant_id=other_tenant_id, key="blockage"
        )

    assert {n.key for n in first} == {"roads", "blockage"}
    assert {n.key for n in second} == {"plumbing", "blockage"}
    assert first_blockage.path == "roads/blockage"
    assert second_blockage.path == "plumbing/blockage"
    assert first_blockage.severity_semantics["floor"] == 2.0
    assert second_blockage.severity_semantics["floor"] == 7.0


async def test_a_subtree_read_cannot_reach_another_tenants_matching_paths(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Both tenants have ``roads/pothole``; one subtree read must return one tree.

    The prefix scan is the read most likely to leak, because the *path* is
    identical across tenants by design — only the ``tenant_id`` predicate
    separates them.
    """
    for owner in (tenant_id, other_tenant_id):
        async with scoped(migrated_engine, owner) as session:
            await taxonomy.import_nodes(
                session, tenant_id=owner, specs=[node("roads"), node("pothole", parent="roads")]
            )
            await session.commit()

    async with scoped(migrated_engine, tenant_id) as session:
        found = await taxonomy.subtree(session, tenant_id=tenant_id, key="roads")

    assert [n.key for n in found] == ["roads", "pothole"]


# ---------------------------------------------------------------------------
# Digest, revision, and the published event
# ---------------------------------------------------------------------------


async def test_the_digest_ignores_row_identity_but_tracks_semantics(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Two tenants seeded identically must hash identically.

    That is the property the hash is *for*: answering "was this complaint
    classified under the taxonomy I am looking at" by comparison rather than by
    replay. If ids or timestamps leaked into it, the answer would always be no.
    """
    specs = [node("roads"), node("pothole", parent="roads")]
    for owner in (tenant_id, other_tenant_id):
        async with scoped(migrated_engine, owner) as session:
            await taxonomy.import_nodes(session, tenant_id=owner, specs=specs)
            await session.commit()

    async with scoped(migrated_engine, tenant_id) as session:
        first = await taxonomy.digest(session, tenant_id=tenant_id)
    async with scoped(migrated_engine, other_tenant_id) as session:
        second = await taxonomy.digest(session, tenant_id=other_tenant_id)
    assert first.content_hash == second.content_hash

    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.update_node(
            session,
            tenant_id=tenant_id,
            key="pothole",
            changes=TaxonomyNodeUpdate(display_name="Road crater"),
        )
        await session.commit()
        changed = await taxonomy.digest(session, tenant_id=tenant_id)

    assert changed.content_hash != first.content_hash


async def test_a_deactivated_node_still_counts_toward_the_digest(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Deactivation is a taxonomy change, not a taxonomy deletion.

    A complaint classified into a since-retired category is still classified
    into it, so a digest that dropped inactive nodes would report two materially
    different taxonomies as identical.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(session, tenant_id=tenant_id, specs=[node("pothole")])
        await session.commit()
        before = await taxonomy.digest(session, tenant_id=tenant_id)

        await taxonomy.update_node(
            session, tenant_id=tenant_id, key="pothole", changes=TaxonomyNodeUpdate(is_active=False)
        )
        await session.commit()
        after = await taxonomy.digest(session, tenant_id=tenant_id)

    assert after.node_count == before.node_count == 1
    assert after.content_hash != before.content_hash


async def test_publish_bumps_the_revision_and_records_it_on_the_tenant_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """§9.1 applied to configuration: the counter cannot move without the event."""
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.import_nodes(session, tenant_id=tenant_id, specs=[node("pothole")])
        revision = await taxonomy.publish(
            session,
            tenant_id=tenant_id,
            change_kind=taxonomy.CHANGE_IMPORTED,
            changed_keys=["pothole"],
        )
        await session.commit()

        stored = list(
            (
                await session.execute(
                    select(Event).where(
                        Event.tenant_id == tenant_id, Event.event_type == "taxonomy_published"
                    )
                )
            )
            .scalars()
            .all()
        )
        digest = await taxonomy.digest(session, tenant_id=tenant_id)
        current = (
            await session.execute(select(Tenant.taxonomy_revision).where(Tenant.id == tenant_id))
        ).scalar_one()

    assert revision == 1
    assert current == 1
    assert len(stored) == 1
    assert stored[0].payload["revision"] == 1
    assert stored[0].payload["content_hash"] == digest.content_hash
    assert stored[0].payload["changed_keys"] == ["pothole"]
    assert stored[0].entity_id == tenant_id


# ---------------------------------------------------------------------------
# Prompt sets — Phase 9's dependency
# ---------------------------------------------------------------------------


async def test_prompts_in_an_undeclared_locale_are_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A prompt set nothing will ever select is worse than a missing one.

    The classifier asks for the tenant's declared locales. Prompts in any other
    locale are simply never read, so the failure is silent — the category
    appears configured and never classifies anything.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.create_node(session, tenant_id=tenant_id, spec=node("pothole"))
        await session.commit()
        with pytest.raises(ValidationError, match="not declared"):
            await taxonomy.upsert_prompt_set(
                session,
                tenant_id=tenant_id,
                spec=PromptSetSpec(
                    node_key="pothole",
                    locale="mr",
                    encoder="clip",
                    prompts=["a pothole"],
                    prompt_set_version="v1",
                ),
            )


async def test_upserting_a_prompt_set_replaces_rather_than_accumulates(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.create_node(session, tenant_id=tenant_id, spec=node("pothole"))
        for version, prompts in (("v1", ["a pothole"]), ("v2", ["a deep pothole", "a crater"])):
            await taxonomy.upsert_prompt_set(
                session,
                tenant_id=tenant_id,
                spec=PromptSetSpec(
                    node_key="pothole",
                    locale="en",
                    encoder="clip",
                    prompts=prompts,
                    prompt_set_version=version,
                ),
            )
        await session.commit()
        pairs = await taxonomy.prompt_sets_for(
            session, tenant_id=tenant_id, locale="en", encoder="clip"
        )

    assert len(pairs) == 1
    key, prompt_set = pairs[0]
    assert key == "pothole"
    assert prompt_set.prompt_set_version == "v2"
    assert prompt_set.prompts == ["a deep pothole", "a crater"]


async def test_prompt_sets_are_not_offered_for_an_unselectable_node(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An interior grouping is not a classification target.

    Letting the classifier land on "Roads" produces a complaint nobody can
    route, which is indistinguishable downstream from a confident answer.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await taxonomy.create_node(
            session, tenant_id=tenant_id, spec=node("roads", is_selectable=False)
        )
        await taxonomy.upsert_prompt_set(
            session,
            tenant_id=tenant_id,
            spec=PromptSetSpec(
                node_key="roads",
                locale="en",
                encoder="clip",
                prompts=["a road"],
                prompt_set_version="v1",
            ),
        )
        await session.commit()
        pairs = await taxonomy.prompt_sets_for(
            session, tenant_id=tenant_id, locale="en", encoder="clip"
        )

    assert pairs == []


async def test_the_database_refuses_a_key_the_service_would_have_rejected(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The CHECK constraint is not decoration.

    The service validates keys through Pydantic, but a migration and a psql
    session are also writers. A key containing ``/`` would silently merge one
    node's subtree into another's, because the subtree query is a path prefix.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            TaxonomyNode(
                tenant_id=tenant_id,
                key="roads/pothole",
                path="roads/pothole",
                depth=0,
                display_name="Smuggled",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
