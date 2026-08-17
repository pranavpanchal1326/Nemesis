"""The taxonomy service — where "a new customer is a code change" stops being true.

Every function here takes a session and participates in the caller's
transaction. None of them commits: a provisioning request that creates forty
nodes and then fails on the forty-first must leave no partial taxonomy behind,
and that is only true if the unit of atomicity is the caller's, not each write's.

**Mutations do not publish; the caller publishes.** ``create_node`` and its
siblings change rows and return; ``publish()`` bumps the tenant's revision and
appends one ``taxonomy_published`` event. That split is deliberate — an import
of four hundred nodes is one operator action and should be one event, not four
hundred. The event carries the changed keys so the granularity that was lost is
still recoverable from the record.

**Updates are explicit statements, never dirty-object flushes.** Setting an
attribute on a loaded ORM instance makes SQLAlchemy emit
``UPDATE ... WHERE id = ?`` with no tenant predicate, which the runtime guard
correctly refuses — and would refuse at an arbitrary later autoflush, from a
stack frame nowhere near the assignment. Every write below states its own
``tenant_id`` predicate.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane import hierarchy
from nemesis.control_plane.errors import (
    ConflictError,
    HierarchyError,
    NotFoundError,
    ValidationError,
)
from nemesis.control_plane.schemas import (
    PromptSetSpec,
    TaxonomyNodeSpec,
    TaxonomyNodeUpdate,
)
from nemesis.db.models.i18n import NAMESPACE_TAXONOMY, Translation
from nemesis.db.models.taxonomy import MAX_TAXONOMY_DEPTH, TaxonomyNode, TaxonomyPromptSet
from nemesis.db.models.tenant import Tenant
from nemesis.domain.lifecycle import EntityType
from nemesis.events.canonical import JSONValue, canonicalise
from nemesis.events.store import EventStore

TENANT_ENTITY: Final = EntityType.TENANT.value

#: Change kinds recorded in ``taxonomy_published.change_kind``. Constants rather
#: than inline strings so the API, the provisioner, and the tests cannot disagree
#: about the spelling of a value that ends up in an immutable log.
CHANGE_CREATED: Final = "created"
CHANGE_UPDATED: Final = "updated"
CHANGE_IMPORTED: Final = "imported"
CHANGE_PROMPTS: Final = "prompts_updated"

#: Fields a partial update copies straight across. Listed rather than derived
#: from the model so adding a field to ``TaxonomyNodeUpdate`` is a deliberate
#: decision about whether it is safely settable, not an automatic one — ``path``
#: and ``depth`` are on the same model in spirit and must never be.
_SCALAR_UPDATE_FIELDS: Final = (
    "display_name",
    "description",
    "icon",
    "sort_order",
    "is_selectable",
    "is_active",
)


@dataclass(frozen=True, slots=True)
class TaxonomyDigest:
    """A tenant's whole taxonomy, reduced to something comparable.

    ``content_hash`` is over the canonical JSON of every node's *semantic*
    fields — not over ``updated_at``, not over ids. Two databases seeded from
    the same template must produce the same hash, or the value cannot be used to
    answer "was this complaint classified under the taxonomy I am looking at".
    """

    content_hash: str
    node_count: int


def _node_query(tenant_id: uuid.UUID, *, include_inactive: bool) -> Select[Any]:
    statement = select(TaxonomyNode).where(TaxonomyNode.tenant_id == tenant_id)
    if not include_inactive:
        statement = statement.where(TaxonomyNode.is_active.is_(True))
    return statement.order_by(TaxonomyNode.path)


async def list_nodes(
    session: AsyncSession, *, tenant_id: uuid.UUID, include_inactive: bool = False
) -> list[TaxonomyNode]:
    """Every node for the tenant, ordered by path so the result reads as a tree."""
    rows = await session.execute(_node_query(tenant_id, include_inactive=include_inactive))
    return list(rows.scalars().all())


async def get_node(session: AsyncSession, *, tenant_id: uuid.UUID, key: str) -> TaxonomyNode | None:
    row = await session.execute(
        select(TaxonomyNode).where(TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.key == key)
    )
    return row.scalar_one_or_none()


async def require_node(session: AsyncSession, *, tenant_id: uuid.UUID, key: str) -> TaxonomyNode:
    node = await get_node(session, tenant_id=tenant_id, key=key)
    if node is None:
        raise NotFoundError(f"no taxonomy node {key!r} for this tenant")
    return node


async def subtree(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str, include_inactive: bool = False
) -> list[TaxonomyNode]:
    """A node and everything under it.

    One index-backed prefix scan, which is the reason ``path`` exists. The
    ancestor itself is matched by equality rather than by the pattern, because
    the pattern requires a trailing separator and a node's own path has none.
    """
    node = await require_node(session, tenant_id=tenant_id, key=key)
    statement = _node_query(tenant_id, include_inactive=include_inactive).where(
        (TaxonomyNode.path == node.path)
        | TaxonomyNode.path.like(hierarchy.subtree_pattern(node.path), escape="\\")
    )
    rows = await session.execute(statement)
    return list(rows.scalars().all())


async def create_node(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: TaxonomyNodeSpec
) -> TaxonomyNode:
    """Add one node, resolving its parent by key within the same tenant."""
    if await get_node(session, tenant_id=tenant_id, key=spec.key) is not None:
        raise ConflictError(f"taxonomy key {spec.key!r} already exists for this tenant")

    path, depth = await _resolve_placement(
        session, tenant_id=tenant_id, key=spec.key, parent_key=spec.parent_key
    )

    node = TaxonomyNode(
        tenant_id=tenant_id,
        key=spec.key,
        parent_id=await _parent_id(session, tenant_id=tenant_id, parent_key=spec.parent_key),
        path=path,
        depth=depth,
        display_name=spec.display_name,
        description=spec.description,
        icon=spec.icon,
        sort_order=spec.sort_order,
        is_active=spec.is_active,
        is_selectable=spec.is_selectable,
        severity_semantics=spec.severity_semantics.model_dump(mode="json"),
        routing_hints=spec.routing_hints.model_dump(mode="json", exclude_none=True),
        attributes=spec.attributes,
    )
    session.add(node)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - the pre-check above wins the race
        raise ConflictError(f"taxonomy key {spec.key!r} already exists for this tenant") from exc

    await _write_translations(
        session, tenant_id=tenant_id, message_key=spec.key, translations=spec.translations
    )
    return node


async def update_node(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str, changes: TaxonomyNodeUpdate
) -> TaxonomyNode:
    """Apply a partial update, rewriting the subtree if the parent moved."""
    node = await require_node(session, tenant_id=tenant_id, key=key)

    # Captured *before* the UPDATE, and this is not defensive style — it is the
    # fix for a real defect the reparenting test caught. `session.execute(update(...))`
    # synchronises the session by default, so reading `node.path` afterwards
    # returns the *new* path. The subtree rewrite then searched for descendants
    # of a prefix nothing was under, matched zero rows, and left every
    # descendant pointing at a path that no longer existed — while the node
    # itself moved correctly, so the tree read as intact at the level anyone
    # would have looked.
    node_id = node.id
    old_path = node.path

    values: dict[str, Any] = {}
    for field in _SCALAR_UPDATE_FIELDS:
        candidate = getattr(changes, field)
        if candidate is not None:
            values[field] = candidate
    if changes.severity_semantics is not None:
        values["severity_semantics"] = changes.severity_semantics.model_dump(mode="json")
    if changes.routing_hints is not None:
        values["routing_hints"] = changes.routing_hints.model_dump(mode="json", exclude_none=True)
    if changes.attributes is not None:
        values["attributes"] = changes.attributes

    reparenting = changes.detach_to_root or changes.parent_key is not None
    if reparenting:
        new_parent_key = None if changes.detach_to_root else changes.parent_key
        new_path, new_depth = await _resolve_placement(
            session, tenant_id=tenant_id, key=key, parent_key=new_parent_key, moving=node
        )
        values["parent_id"] = await _parent_id(
            session, tenant_id=tenant_id, parent_key=new_parent_key
        )
        values["path"] = new_path
        values["depth"] = new_depth

    if not values:
        return node

    # `version` is the optimistic counter every mutable aggregate carries. It is
    # bumped here rather than left to the caller because a control-plane row is
    # edited by humans through two UIs and a CLI, and a lost update between two
    # operators editing the same category is exactly what the column is for.
    values["version"] = TaxonomyNode.version + 1
    await session.execute(
        update(TaxonomyNode)
        .where(TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.id == node_id)
        .values(**values)
    )

    if reparenting:
        await _rewrite_subtree(
            session,
            tenant_id=tenant_id,
            old_path=old_path,
            new_path=str(values["path"]),
        )

    await session.flush()
    session.expire(node)
    return await require_node(session, tenant_id=tenant_id, key=key)


async def import_nodes(
    session: AsyncSession, *, tenant_id: uuid.UUID, specs: Sequence[TaxonomyNodeSpec]
) -> list[TaxonomyNode]:
    """Create a whole taxonomy, parents before children.

    The caller supplies specs in whatever order is natural to write — a template
    file is authored as a list, not as a traversal — so the order is derived
    here. A spec naming a parent that is neither already in the database nor
    anywhere in the batch is a validation failure that names the key, rather
    than a foreign-key violation that names a UUID.
    """
    ordered = _topologically_ordered(specs)
    return [await create_node(session, tenant_id=tenant_id, spec=spec) for spec in ordered]


async def upsert_prompt_set(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: PromptSetSpec
) -> TaxonomyPromptSet:
    """Attach or replace the prompts for one (node, locale, encoder).

    Upsert rather than insert: Phase 9's iteration loop is "measure F1, edit the
    prompts, measure again", and making that a delete-then-create would lose the
    row's identity between measurements for no reason.
    """
    node = await require_node(session, tenant_id=tenant_id, key=spec.node_key)
    await _assert_locale_declared(session, tenant_id=tenant_id, locale=spec.locale)

    existing = (
        await session.execute(
            select(TaxonomyPromptSet).where(
                TaxonomyPromptSet.tenant_id == tenant_id,
                TaxonomyPromptSet.node_id == node.id,
                TaxonomyPromptSet.locale == spec.locale,
                TaxonomyPromptSet.encoder == spec.encoder,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        prompt_set = TaxonomyPromptSet(
            tenant_id=tenant_id,
            node_id=node.id,
            locale=spec.locale,
            encoder=spec.encoder,
            prompts=list(spec.prompts),
            negative_prompts=list(spec.negative_prompts),
            prompt_set_version=spec.prompt_set_version,
            is_active=spec.is_active,
        )
        session.add(prompt_set)
        await session.flush()
        return prompt_set

    await session.execute(
        update(TaxonomyPromptSet)
        .where(
            TaxonomyPromptSet.tenant_id == tenant_id,
            TaxonomyPromptSet.id == existing.id,
        )
        .values(
            prompts=list(spec.prompts),
            negative_prompts=list(spec.negative_prompts),
            prompt_set_version=spec.prompt_set_version,
            is_active=spec.is_active,
            version=TaxonomyPromptSet.version + 1,
        )
    )
    await session.flush()
    session.expire(existing)
    return existing


async def prompt_sets_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, locale: str, encoder: str
) -> list[tuple[str, TaxonomyPromptSet]]:
    """Active prompt sets for a locale and encoder, paired with their node key.

    Returned as pairs because the caller — Phase 9's classifier — needs the key
    to write into ``classification_scored.category`` and would otherwise issue a
    second query per prompt set to get it.
    """
    rows = await session.execute(
        select(TaxonomyNode.key, TaxonomyPromptSet)
        .join(TaxonomyPromptSet, TaxonomyPromptSet.node_id == TaxonomyNode.id)
        .where(
            TaxonomyNode.tenant_id == tenant_id,
            TaxonomyPromptSet.tenant_id == tenant_id,
            TaxonomyPromptSet.locale == locale,
            TaxonomyPromptSet.encoder == encoder,
            TaxonomyPromptSet.is_active.is_(True),
            TaxonomyNode.is_active.is_(True),
            TaxonomyNode.is_selectable.is_(True),
        )
        .order_by(TaxonomyNode.path)
    )
    return list(rows.all())


async def digest(session: AsyncSession, *, tenant_id: uuid.UUID) -> TaxonomyDigest:
    """Hash the tenant's taxonomy as it stands.

    Deliberately includes inactive nodes. A category that was deactivated is
    still part of what the taxonomy *was* for complaints already classified into
    it, and excluding it would make two materially different taxonomies hash
    identically.
    """
    nodes = await list_nodes(session, tenant_id=tenant_id, include_inactive=True)
    payload: JSONValue = [
        {
            "key": node.key,
            "path": node.path,
            "display_name": node.display_name,
            "is_active": node.is_active,
            "is_selectable": node.is_selectable,
            "sort_order": node.sort_order,
            # The models annotate JSONB as ``dict[str, object]``, which is
            # looser than what the driver can actually return: psycopg decodes
            # JSONB into exactly the JSON value space. The cast records that,
            # rather than widening every JSONB column's annotation and losing
            # the type error the day one of them holds something else.
            "severity_semantics": cast("dict[str, JSONValue]", node.severity_semantics),
            "routing_hints": cast("dict[str, JSONValue]", node.routing_hints),
            "attributes": cast("dict[str, JSONValue]", node.attributes),
        }
        for node in sorted(nodes, key=lambda candidate: candidate.key)
    ]
    return TaxonomyDigest(
        content_hash=hashlib.sha256(canonicalise(payload)).hexdigest(),
        node_count=len(nodes),
    )


async def publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    change_kind: str,
    changed_keys: Iterable[str] = (),
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> int:
    """Bump the tenant's taxonomy revision and record it on the tenant chain.

    The revision increment and the event append are in the caller's transaction,
    so a revision can never advance without the event that explains it — the
    same §9.1 rule the complaint write path follows, applied to configuration.
    """
    snapshot = await digest(session, tenant_id=tenant_id)

    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant, so there is nothing to scope it by. See tenancy.registry.
    revision_row = await session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(taxonomy_revision=Tenant.taxonomy_revision + 1)
        .returning(Tenant.taxonomy_revision)
    )
    revision = revision_row.scalar_one()

    await EventStore(session).append(
        entity_id=tenant_id,
        event_type="taxonomy_published",
        payload={
            "revision": revision,
            "node_count": snapshot.node_count,
            "content_hash": snapshot.content_hash,
            "changed_keys": sorted(set(changed_keys)),
            "change_kind": change_kind,
        },
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        occurred_at=datetime.now(tz=UTC),
    )
    return int(revision)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _parent_id(
    session: AsyncSession, *, tenant_id: uuid.UUID, parent_key: str | None
) -> uuid.UUID | None:
    if parent_key is None:
        return None
    return (await require_node(session, tenant_id=tenant_id, key=parent_key)).id


async def _resolve_placement(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key: str,
    parent_key: str | None,
    moving: TaxonomyNode | None = None,
) -> tuple[str, int]:
    """The path and depth a node takes under ``parent_key``.

    ``moving`` is supplied when reparenting an existing node, and is what makes
    the cycle check possible: a node may not move under its own descendant, and
    the descendant test is a prefix comparison on the path it currently has.
    """
    if parent_key is None:
        return key, 0

    parent = await require_node(session, tenant_id=tenant_id, key=parent_key)

    if moving is not None and hierarchy.is_descendant(parent.path, moving.path):
        raise HierarchyError(
            f"moving {key!r} under {parent_key!r} would make it its own ancestor — "
            f"{parent_key!r} is currently at {parent.path!r}, inside the subtree being moved"
        )

    path = hierarchy.build_path(hierarchy.path_keys(parent.path), key)
    hierarchy.assert_within_depth(path, maximum=MAX_TAXONOMY_DEPTH, label="taxonomy node")
    return path, hierarchy.depth_of(path)


async def _rewrite_subtree(
    session: AsyncSession, *, tenant_id: uuid.UUID, old_path: str, new_path: str
) -> None:
    """Move every strict descendant to sit under the node's new path.

    One statement rather than a row-by-row rewrite. A subtree move is rare, but
    when it happens it touches every descendant, and doing that in Python would
    mean a window during which half the tree points at a path that no longer
    exists — visible to any concurrent reader, because the transaction is not the
    only thing reading.

    The depth is recomputed from the new path rather than adjusted by a delta,
    so a move that changes depth by two is not a second kind of arithmetic.
    """
    descendants = await session.execute(
        select(TaxonomyNode.id, TaxonomyNode.path).where(
            TaxonomyNode.tenant_id == tenant_id,
            TaxonomyNode.path.like(hierarchy.subtree_pattern(old_path), escape="\\"),
        )
    )
    for node_id, path in descendants.all():
        moved = hierarchy.reparented_path(path, old_path, new_path)
        hierarchy.assert_within_depth(moved, maximum=MAX_TAXONOMY_DEPTH, label="taxonomy node")
        await session.execute(
            update(TaxonomyNode)
            .where(TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.id == node_id)
            .values(path=moved, depth=hierarchy.depth_of(moved), version=TaxonomyNode.version + 1)
        )


async def _assert_locale_declared(
    session: AsyncSession, *, tenant_id: uuid.UUID, locale: str
) -> None:
    """Refuse prompts in a language the tenant has not declared.

    Not a foreign key, because ``tenants.locales`` is an array and Postgres
    cannot reference into one. Enforced anyway: a prompt set in an undeclared
    locale is never selected by the classifier — nothing asks for that locale —
    so it fails silently, which is the failure mode that costs a day to find.
    """
    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant. See tenancy.registry.
    declared = (
        await session.execute(select(Tenant.locales).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if declared is None:
        raise NotFoundError("tenant does not exist")
    if locale not in declared:
        raise ValidationError(
            f"locale {locale!r} is not declared by this tenant (declared: {sorted(declared)}). "
            f"Add it to the tenant's locale set first, or the classifier will never "
            f"ask for these prompts."
        )


async def _write_translations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_key: str,
    translations: Mapping[str, str],
) -> None:
    """Replace this key's taxonomy translations with the supplied set.

    Replace rather than merge: a spec is a complete description of the node, and
    a merge would make removing a translation impossible through the same path
    that added it.
    """
    if not translations:
        return
    await session.execute(
        delete(Translation).where(
            Translation.tenant_id == tenant_id,
            Translation.namespace == NAMESPACE_TAXONOMY,
            Translation.message_key == message_key,
        )
    )
    for locale, value in translations.items():
        session.add(
            Translation(
                tenant_id=tenant_id,
                namespace=NAMESPACE_TAXONOMY,
                message_key=message_key,
                locale=locale,
                value=value,
            )
        )
    await session.flush()


def _topologically_ordered(specs: Sequence[TaxonomyNodeSpec]) -> list[TaxonomyNodeSpec]:
    """Parents before children, with cycles and dangling parents named.

    Kahn's algorithm over the parent edges. A spec whose parent is not in the
    batch is treated as a root *for ordering purposes only* — it may legitimately
    hang off a node already in the database — and ``create_node`` is what
    ultimately refuses an unknown parent, with the key in the message.
    """
    counts = Counter(spec.key for spec in specs)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise ValidationError(
            f"duplicate taxonomy keys in the batch: {duplicates}. The database would "
            f"reject the second one, but only after the first had already been written."
        )
    by_key = {spec.key: spec for spec in specs}

    ordered: list[TaxonomyNodeSpec] = []
    placed: set[str] = set()
    remaining = list(specs)

    while remaining:
        ready = [
            spec
            for spec in remaining
            if spec.parent_key is None or spec.parent_key not in by_key or spec.parent_key in placed
        ]
        if not ready:
            stuck = sorted(spec.key for spec in remaining)
            raise HierarchyError(
                f"taxonomy batch contains a parent cycle among {stuck}; no node in "
                f"this set can be created before the others"
            )
        for spec in ready:
            ordered.append(spec)
            placed.add(spec.key)
        remaining = [spec for spec in remaining if spec.key not in placed]

    return ordered


async def count_nodes(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    total = await session.execute(
        select(func.count()).select_from(TaxonomyNode).where(TaxonomyNode.tenant_id == tenant_id)
    )
    return int(total.scalar_one())
