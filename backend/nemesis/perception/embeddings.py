"""The only writer of ``complaints.text_embedding`` and ``complaints.image_embedding``.

**This is a deliberate, documented exception to the projection rule, and it is
the only one.** ``projections.writer`` states that nothing outside it writes the
current-state tables, because a row the log does not explain breaks §9.1. These
two columns are the exception it names in its own docstring, and the reason is
arithmetic: a 512-dimensional half-precision vector and a 384-dimensional single
is about 2.5 KB per complaint, hashed into an append-only log that must live for
years and can never be rewritten. A million complaints is 2.5 GB of a log whose
whole value is that it is small enough to replay — for data that is *regenerable
from the photograph* and that no human will ever read.

**What keeps the exception honest.** Three things, and they are checkable rather
than promised:

1. This module is the only place either column is assigned. A test walks every
   module's AST and fails on a second writer — the same guarantee, by the same
   method, that ``check_media_redaction.py`` gives the redaction path.
2. The *fact* that embeddings were computed is in the log, on
   ``classification_scored.model_ids``. So the row can be regenerated and, more
   importantly, an operator can tell an embedding that is missing because the
   stage never ran from one missing because the write failed.
3. The write is idempotent and tenant-scoped, so a redelivered stage produces the
   same row and can never touch another customer's.

**Why the vectors are written here rather than returned to the orchestrator.**
The orchestrator's contract is events (``EmittedEvent``), and giving it a second
channel for "and also write these bytes to a column" would put the exception into
the general machinery, where the next phase would reasonably use it for something
else. Keeping it in one module with one function keeps the exception the size it
should be.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import IMAGE_EMBEDDING_DIM, TEXT_EMBEDDING_DIM, Complaint
from nemesis.observability.logging import get_logger

log = get_logger(__name__)


async def store(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    text_embedding: Sequence[float] | None = None,
    image_embedding: Sequence[float] | None = None,
) -> bool:
    """Write whichever embeddings were computed. Returns whether a row changed.

    Participates in the caller's transaction and does not commit — the same rule
    every service in this codebase follows, and load-bearing here: the vectors
    and the ``classification_scored`` event that names the models which produced
    them must land together or not at all. A commit here would make a stage
    failure leave embeddings on a complaint whose log says it was never
    classified, and Phase 10 would then deduplicate against a vector no event
    explains.

    Returns ``False`` when neither embedding was supplied, rather than issuing an
    UPDATE with nothing in it. A submission with no photograph and no text is
    possible — an audio-only report whose transcription is unavailable — and it
    is not an error, it is a report a human will read.
    """
    values: dict[str, object] = {}
    if text_embedding is not None:
        values["text_embedding"] = _checked(text_embedding, TEXT_EMBEDDING_DIM, "text")
    if image_embedding is not None:
        values["image_embedding"] = _checked(image_embedding, IMAGE_EMBEDDING_DIM, "image")
    if not values:
        return False

    result = await session.execute(
        update(Complaint)
        .where(Complaint.tenant_id == tenant_id, Complaint.id == complaint_id)
        .values(**values)
    )
    # An explicit statement with its own tenant predicate, never a dirty-object
    # flush — the reason ``control_plane.taxonomy`` gives at length: assigning to
    # a loaded ORM instance emits an UPDATE with no tenant predicate, at an
    # arbitrary later autoflush, from a stack frame nowhere near the assignment.
    # ``getattr`` rather than ``.rowcount``: ``session.execute`` is typed as
    # returning ``Result``, which does not declare the attribute even though
    # every UPDATE returns a ``CursorResult`` that has it. The same accommodation
    # ``policy.service`` makes, for the same reason.
    changed = int(getattr(result, "rowcount", 0) or 0) > 0
    if not changed:
        # Not an exception. The projector writes the complaints row inside the
        # same transaction, and a stage that ran before the row existed is a
        # real ordering the orchestrator permits. Logged so a *systematic*
        # absence is visible, because Phase 10 deduplicating against a table of
        # NULL vectors is silent by construction — every candidate simply fails
        # to match, and the moat reports that everything is distinct.
        log.warning(
            "embedding_write_matched_no_row",
            complaint_id=str(complaint_id),
            columns=sorted(values),
            consequence="dedup Stage 2 has no vector for this complaint and will not match it",
        )
    return changed


def _checked(vector: Sequence[float], expected: int, label: str) -> list[float]:
    """Refuse a vector of the wrong width before it reaches the column.

    pgvector raises on a dimension mismatch, so this is not the only guard — it
    is the *legible* one. The database's error names a column and a number; this
    one names the encoder whose output changed, which is the fact somebody
    debugging a model upgrade actually needs.
    """
    if len(vector) != expected:
        raise ValueError(
            f"the {label} encoder produced {len(vector)} dimensions but the column and "
            f"its HNSW index are built for {expected}. This is a checkpoint change, not "
            f"a data problem: storing it would corrupt dedup Stage 2 for every "
            f"complaint written after it."
        )
    return [float(component) for component in vector]


__all__ = ["store"]
