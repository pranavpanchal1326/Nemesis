"""Tenant prompt sets, resolved and embedded — the pair half of the registry.

Phase 5 put the prompts in ``taxonomy_prompt_sets``, keyed by node, locale and
encoder, so "a new tenant category is classifiable by adding prompts alone" —
Phase 9's gate — has somewhere to be true. This module is where it becomes true:
it reads those rows, embeds them once, and hands the classifier a bundle it can
score against.

**Why the bundle is cached under a content hash and not under a tenant id.** A
tenant id says *whose* prompts these are; it does not say *which* prompts, and
the whole reason a prompt set is versioned is that somebody edits it and
measures again. Keying the cache on the tenant would serve yesterday's matrix
after today's publish, and the symptom would be an F1 number that refuses to
move no matter what anyone writes in the control plane. Keying on the content
hash means an edit is a new key by construction, and the old matrix ages out of
the registry on its own.

**The hash covers what the vectors depend on and nothing else.** Node keys,
prompt text, negative prompt text, locale, encoder, and the embedding model's
id. Not ``updated_at``, not row ids, not the tenant — two tenants who have
written character-identical prompts get the same matrix, which is correct and
saves a load. The model id is in there because the *same* prompts embedded by a
different checkpoint are different vectors, and serving the cached ones would
compare an image against text from another model's space.

**Locale resolution is explicit, and falls back rather than failing.** A tenant
declares locales; a submission carries one, or a transcriber detected one, or
neither. Prompts exist per locale because the text encoder scores a Marathi
description against a Marathi prompt. When the exact locale has no prompt set,
this falls back to the tenant's first declared locale rather than returning
nothing — a report in a locale nobody wrote prompts for should be classified
slightly worse, not parked forever.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane import taxonomy
from nemesis.db.models.tenant import Tenant
from nemesis.observability.logging import get_logger
from nemesis.perception.encoders import ImageEncoder, TextEncoder
from nemesis.perception.errors import PromptSetUnavailableError
from nemesis.perception.registry import REGISTRY, ModelRegistry
from nemesis.perception.scoring import CategoryVectors

log = get_logger(__name__)

#: ``taxonomy_prompt_sets.encoder`` values this build knows how to score. The
#: column is deliberately free text (Phase 5) so a third family needs no
#: migration; these two are what exists today, and a row naming anything else is
#: skipped with a log line rather than crashing a citizen's submission.
ENCODER_IMAGE: Final = "clip"
ENCODER_TEXT: Final = "text"


@dataclass(frozen=True, slots=True)
class PromptSet:
    """One category's prompts, as authored. No vectors yet."""

    category: str
    prompts: tuple[str, ...]
    negative_prompts: tuple[str, ...]
    version: str


@dataclass(frozen=True, slots=True)
class PromptBundle:
    """Every category a submission can be classified into, for one (locale, encoder).

    ``version`` is the composite written into
    ``classification_scored.prompt_set_version``. Composite rather than one row's
    version because a classification is scored against *all* of them at once: if
    one category's prompts changed, the decision could have changed, and a stamp
    naming only the winner's version would say nothing about that.
    """

    locale: str
    encoder: str
    sets: tuple[PromptSet, ...]
    content_hash: str
    version: str

    def __bool__(self) -> bool:
        return bool(self.sets)


@dataclass(frozen=True, slots=True)
class EmbeddedBundle:
    """A bundle with its vectors, ready to score against."""

    bundle: PromptBundle
    model_id: str
    categories: tuple[CategoryVectors, ...]

    @property
    def cache_key(self) -> str:
        return _cache_key(self.bundle, model_id=self.model_id)


async def load_bundle(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    locale: str | None,
    encoder: str,
    fallback_locales: Sequence[str] = (),
) -> PromptBundle:
    """The tenant's prompts for one encoder, in the best available locale.

    Raises rather than returning an empty bundle when nothing matches in any
    locale. The two states are genuinely different — "this tenant has authored no
    prompts" is a control-plane gap an operator fixes in a minute, while an empty
    bundle scored against would abstain on every submission and look like a model
    problem — so they get different code paths and different messages.
    """
    tried: list[str] = []
    for candidate in _locale_order(locale, fallback_locales):
        if candidate in tried:
            continue
        tried.append(candidate)
        rows = await taxonomy.prompt_sets_for(
            session, tenant_id=tenant_id, locale=candidate, encoder=encoder
        )
        if rows:
            return _bundle_from(rows, locale=candidate, encoder=encoder)

    raise PromptSetUnavailableError(
        f"no active {encoder!r} prompt sets for this tenant in any of "
        f"{tried or ['(no locale)']}. A category with no prompts cannot be scored "
        f"zero-shot; add them through the taxonomy API and the next submission will "
        f"classify. Nothing is lost in the meantime — the report is parked as "
        f"pending_classification for a human."
    )


async def tenant_locales(session: AsyncSession, *, tenant_id: uuid.UUID) -> tuple[str, ...]:
    """The tenant's declared locales, in declaration order.

    Order matters: the first is the fallback the prompt loader reaches for, and a
    tenant lists its primary language first. Read here rather than passed in
    because the caller that needs it is a pipeline stage holding a tenant id and
    nothing else.
    """
    row = await session.execute(select(Tenant.locales).where(Tenant.id == tenant_id))
    locales = row.scalar_one_or_none()
    return tuple(locales or ())


def embed(
    bundle: PromptBundle,
    *,
    encoder: ImageEncoder | TextEncoder,
    registry: ModelRegistry | None = None,
    footprint_bytes: int | None = None,
) -> EmbeddedBundle:
    """Embed a bundle's prompts, once per (prompts, model) pair.

    Goes through the model registry rather than an ``lru_cache`` for the reason
    the registry exists: a prompt matrix for a four-hundred-category taxonomy is
    not free, it competes for the same bounded memory as the weights, and an
    unbounded cache keyed on a tenant-controlled value is a memory leak a
    customer can trigger by publishing taxonomies in a loop.
    """
    from nemesis.config import get_settings

    store = registry if registry is not None else REGISTRY
    budget = (
        footprint_bytes
        if footprint_bytes is not None
        else get_settings().perception.prompt_matrix_footprint_mb * 1024 * 1024
    )
    key = _cache_key(bundle, model_id=encoder.model_id)

    def _load() -> tuple[CategoryVectors, ...]:
        return _embed_now(bundle, encoder=encoder)

    categories = store.get(key, footprint_bytes=budget, load=_load, kind="prompts")
    return EmbeddedBundle(bundle=bundle, model_id=encoder.model_id, categories=categories)


def _embed_now(
    bundle: PromptBundle, *, encoder: ImageEncoder | TextEncoder
) -> tuple[CategoryVectors, ...]:
    """One pass over every prompt in the bundle, positives and negatives together.

    **One pass, not one per category.** A text tower call has a fixed cost per
    invocation that dwarfs the marginal cost of another row in the batch, so
    forty categories embedded separately is forty times the overhead for the same
    arithmetic. Batching them means the split back into per-category tuples has
    to be exact, which is what the offset bookkeeping below is doing — and why it
    asserts rather than trusting itself: a misaligned split would silently give
    one category another's prompt vectors, and every downstream number would look
    entirely reasonable.
    """
    from nemesis.config import get_settings

    limit = get_settings().perception.max_prompts_per_pass
    flat: list[str] = []
    spans: list[tuple[int, int, int]] = []
    for prompt_set in bundle.sets:
        start = len(flat)
        flat.extend(prompt_set.prompts)
        middle = len(flat)
        flat.extend(prompt_set.negative_prompts)
        spans.append((start, middle, len(flat)))

    if len(flat) > limit:
        raise PromptSetUnavailableError(
            f"this tenant's {bundle.encoder} prompt set for {bundle.locale} has "
            f"{len(flat)} prompts across {len(bundle.sets)} categories, above the "
            f"{limit} a single pass embeds. Raise NEMESIS_PERCEPTION__MAX_PROMPTS_PER_PASS "
            f"if this host can afford the allocation, or trim the prompt lists — a "
            f"category rarely needs more than a handful of distinct descriptions."
        )

    vectors = _encode(encoder, flat)
    if len(vectors) != len(flat):  # pragma: no cover - an encoder contract breach
        raise PromptSetUnavailableError(
            f"the encoder returned {len(vectors)} vectors for {len(flat)} prompts; "
            f"splitting them per category would misattribute prompts to categories "
            f"and every score after that would be quietly wrong"
        )

    return tuple(
        CategoryVectors(
            category=prompt_set.category,
            positives=tuple(vectors[start:middle]),
            negatives=tuple(vectors[middle:end]),
        )
        for prompt_set, (start, middle, end) in zip(bundle.sets, spans, strict=True)
    )


def _encode(
    encoder: ImageEncoder | TextEncoder, prompts: Sequence[str]
) -> tuple[tuple[float, ...], ...]:
    """Dispatch to whichever tower this encoder exposes for text.

    CLIP's prompt tower and the sentence encoder have different signatures
    because they mean different things — one embeds a caption into image space,
    the other embeds a passage into text space — and unifying them behind a
    single method name would hide the one distinction that matters when reading
    a similarity number.
    """
    encode_prompts = getattr(encoder, "encode_prompts", None)
    if encode_prompts is not None:
        return tuple(encode_prompts(prompts))
    from nemesis.perception.encoders import PASSAGE_PREFIX

    return tuple(encoder.encode(prompts, prefix=PASSAGE_PREFIX))  # type: ignore[union-attr]


def _bundle_from(rows: Sequence[tuple[str, object]], *, locale: str, encoder: str) -> PromptBundle:
    sets = tuple(
        PromptSet(
            category=key,
            prompts=tuple(row.prompts),  # type: ignore[attr-defined]
            negative_prompts=tuple(row.negative_prompts),  # type: ignore[attr-defined]
            version=str(row.prompt_set_version),  # type: ignore[attr-defined]
        )
        # Sorted by category so the content hash does not depend on the order the
        # database happened to return rows in. The query orders by path today;
        # relying on that would make the cache key an implicit contract with a
        # query somebody may reasonably reorder.
        for key, row in sorted(rows, key=lambda item: item[0])
    )
    digest = _content_hash(sets, locale=locale, encoder=encoder)
    return PromptBundle(
        locale=locale,
        encoder=encoder,
        sets=sets,
        content_hash=digest,
        # The stamp a decision is attributed by: how many categories, and the
        # first twelve hex characters of the hash over all of them. Short enough
        # to read in a log line, long enough that a collision is not the
        # explanation anybody should reach for.
        version=f"prompts:{encoder}:{locale}:{len(sets)}:{digest[:12]}",
    )


def _content_hash(sets: Sequence[PromptSet], *, locale: str, encoder: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"{locale}\x00{encoder}\x00".encode())
    for prompt_set in sets:
        digest.update(prompt_set.category.encode())
        digest.update(b"\x01")
        for prompt in prompt_set.prompts:
            digest.update(prompt.encode())
            digest.update(b"\x02")
        digest.update(b"\x03")
        for prompt in prompt_set.negative_prompts:
            digest.update(prompt.encode())
            digest.update(b"\x02")
        digest.update(b"\x04")
    return digest.hexdigest()


def _cache_key(bundle: PromptBundle, *, model_id: str) -> str:
    return f"prompts:{model_id}:{bundle.content_hash}"


def _locale_order(locale: str | None, fallbacks: Sequence[str]) -> tuple[str, ...]:
    order: list[str] = []
    if locale:
        order.append(locale)
        # The bare language subtag, so a submission tagged ``mr-IN`` still finds
        # prompts authored for ``mr``. One step only: ``mr`` and ``mr-IN`` are the
        # same language, while walking further would eventually match anything.
        if "-" in locale:
            order.append(locale.split("-", 1)[0])
    order.extend(fallbacks)
    return tuple(order)


__all__ = [
    "ENCODER_IMAGE",
    "ENCODER_TEXT",
    "EmbeddedBundle",
    "PromptBundle",
    "PromptSet",
    "embed",
    "load_bundle",
    "tenant_locales",
]
