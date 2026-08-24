"""The classification stage, against a real database and a deterministic encoder.

**What is proven here and what is proven elsewhere.** The published F1 number is
a claim about a model and lives in ``docs/reports/perception-f1.md``. These tests
are the claims about *the stage*: what runs in what order, what is emitted, what
degrades and what halts. Both use ``scoring.decide``; only these can assert on it
exactly, because only these control the vectors.

**Gate clause 3 — "a new tenant category is classifiable by adding prompts
alone" — is asserted twice on purpose.** Here at the service layer, where the
absence of a code change is checkable by construction (the test adds a taxonomy
node and a prompt set and nothing else), and again over HTTP against the running
stack in ``scripts/gate_phase9.py``, where the absence of a *deploy* is checkable
by comparing the API container's start time across the run. Neither subsumes the
other: this one would pass against a build that required a restart, and that one
would pass against a build that had the category hardcoded.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.control_plane import taxonomy
from nemesis.control_plane.schemas import PromptSetSpec, TaxonomyNodeSpec
from nemesis.db.models.complaint import TEXT_EMBEDDING_DIM, Complaint
from nemesis.events.store import EventStore
from nemesis.perception import harness
from nemesis.perception.encoders import (
    EncoderKind,
    Transcript,
    encoder_scope,
    reset_encoders,
)
from nemesis.perception.stage import classification_stage
from nemesis.pipeline.stages import StageAbstainedError, StageContext
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.tenancy.context import tenant_scope
from tests import perception_fixtures
from tests.conftest import postgres_required
from tests.perception_fixtures import DictTextEncoder, FixedTranscriber

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

#: The two categories every test here scores against, and the text that decides
#: between them. Kept tiny: the point is the stage's control flow, and a nine
#: category fixture would make every assertion a paragraph.
POTHOLE_PROMPT = "a complaint about a pothole in the road"
GARBAGE_PROMPT = "a complaint about uncollected garbage"


#: The stage writes its text vector into ``complaints.text_embedding``, which is
#: ``vector(384)``, so the doubles here are that wide. Read from the model rather
#: than typed, so a checkpoint change that moves the column moves the fixture with
#: it instead of failing halfway down a stack trace about pgvector.
def axis(index: int) -> tuple[float, ...]:
    return perception_fixtures.axis(index, dimensions=TEXT_EMBEDDING_DIM)


def tilted(index: int, other: int, fraction: float) -> tuple[float, ...]:
    return perception_fixtures.tilted(index, other, fraction, dimensions=TEXT_EMBEDDING_DIM)


PROMPT_VECTORS = {POTHOLE_PROMPT: axis(0), GARBAGE_PROMPT: axis(1)}


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            yield session


@pytest.fixture(autouse=True)
def _clean_encoders() -> AsyncIterator[None]:
    """No encoder leaks between tests.

    ``register_encoder`` refuses to replace an existing registration on purpose,
    so a test that leaked one would fail the *next* test with a message about
    two confidence scales — which is a true statement about a problem the second
    test does not have.
    """
    from nemesis.perception.registry import REGISTRY

    # The prompt matrix cache goes too. It is keyed on the prompt set's *content
    # hash*, which is exactly right in production — an edit is a new key by
    # construction — and exactly wrong across tests, because two tests writing
    # the same prompts get the same key and the second one is served a matrix
    # the first one embedded. The symptom is an assertion about which prefix the
    # encoder was called with, failing in whichever test happens to run second.
    reset_encoders()
    REGISTRY.clear()
    yield
    reset_encoders()
    REGISTRY.clear()


async def _taxonomy(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for key, prompt in (("pothole", POTHOLE_PROMPT), ("garbage_pile", GARBAGE_PROMPT)):
        await taxonomy.create_node(
            session,
            tenant_id=tenant_id,
            spec=TaxonomyNodeSpec(key=key, display_name=key.title()),
        )
        await taxonomy.upsert_prompt_set(
            session,
            tenant_id=tenant_id,
            spec=PromptSetSpec(
                node_key=key,
                locale="en",
                encoder="text",
                prompts=[prompt],
                prompt_set_version="test-1",
            ),
        )


async def _complaint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    description: str | None,
    audio_url: str | None = None,
) -> uuid.UUID:
    complaint_id = uuid.uuid4()
    await EventStore(session).append(
        entity_id=complaint_id,
        event_type="complaint_submitted",
        payload={
            "latitude": 18.5204,
            "longitude": 73.8567,
            "description_text": description,
            "photo_url": None,
            "audio_url": audio_url,
            "locale": "en",
            "device_fingerprint": "test-device",
            "submitted_via": "web",
        },
        tenant_id=tenant_id,
        occurred_at=BASE,
    )
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type="complaint", entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
    await session.flush()
    return complaint_id


def _context(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID, **state: object
) -> StageContext:
    return StageContext(
        session=session,
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        state={"locale": "en", "trust_score": 0.0, **state},
        correlation_id="test",
        attempt=1,
    )


def _encoder(**extra: tuple[float, ...]) -> DictTextEncoder:
    return DictTextEncoder({**PROMPT_VECTORS, **extra})


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_a_description_is_classified_and_the_event_carries_the_evidence(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``classification_scored`` has to be re-arguable years later.

    Phase 11's active learning ranks review candidates by *margin*, which cannot
    be reconstructed from the winner alone, and a calibration change has to be
    re-evaluable against old submissions without re-running the model — which
    needs the raw similarities beside the calibrated confidence.
    """
    description = "there is a big hole in the road"
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        with encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})):
            result = await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )

    (event,) = result.emitted
    assert event.event_type == "classification_scored"
    assert event.payload["category"] == "pothole"
    assert event.payload["model_id"] == "fake-text@1"
    assert event.payload["prompt_set_version"].startswith("prompts:text:en:2:")
    assert "garbage_pile" in event.payload["alternatives"]
    assert event.payload["raw_similarities"]["pothole"] == pytest.approx(1.0)
    assert 0.0 <= event.payload["confidence"] <= 1.0


async def test_the_text_embedding_is_written_for_phase_ten(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The documented exception to the projection rule, exercised end to end.

    Dedup Stage 2 runs on this column, and a table of NULL vectors fails
    silently: every candidate simply does not match and the moat reports that
    everything is distinct.
    """
    description = "there is a big hole in the road"
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        with encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )
        stored = (
            await session.execute(
                # Tenant predicate included, and not as ceremony: the tenancy
                # guard refuses an unscoped domain query at execution time, so a
                # test written without it fails on the guard rather than on the
                # thing it is asserting.
                select(Complaint.text_embedding).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()

    assert stored is not None
    assert len(stored) == TEXT_EMBEDDING_DIM


async def test_the_description_is_embedded_as_a_query_and_prompts_as_passages(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """e5's asymmetry: a complaint is looked up, a category description is what
    it is looked up *in*. Getting it backwards degrades retrieval silently."""
    description = "there is a big hole in the road"
    encoder = _encoder(**{description: axis(0)})
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        with encoder_scope(EncoderKind.TEXT, encoder):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )

    assert "passage: " in encoder.prefixes
    assert "query: " in encoder.prefixes


# ---------------------------------------------------------------------------
# Abstention and degradation
# ---------------------------------------------------------------------------


async def test_a_submission_with_nothing_scoreable_abstains_rather_than_guessing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """§24.2's degraded path is real shipped behaviour, not a stub.

    ``StageAbstainedError`` rather than ``StagePermanentError``: an operator
    reading ``failure_mode`` needs "the model was not confident" and "the
    photograph would not decode" to be different strings, because only one of
    them means something is broken.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=None)
        with (
            encoder_scope(EncoderKind.TEXT, _encoder()),
            pytest.raises(StageAbstainedError, match="nothing scoreable"),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=None,
                )
            )


async def test_two_categories_within_the_margin_park_the_report_for_a_human(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A report exactly between two categories is a question, not a coin flip."""
    description = "something is wrong outside"
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        # Exactly between the two prompt vectors, so the margin is zero and the
        # document's default margin rule refuses to pick.
        with (
            encoder_scope(EncoderKind.TEXT, _encoder(**{description: tilted(0, 1, 0.5)})),
            pytest.raises(StageAbstainedError, match="margin"),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )


async def test_a_tenant_with_no_prompts_at_all_abstains_rather_than_crashing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A control-plane gap an operator fixes in a minute, not a model failure.

    The text side is optional in a way the image side is not: a tenant may
    legitimately have authored CLIP prompts and no text prompts.
    """
    description = "there is a big hole in the road"
    async with scoped(migrated_engine, tenant_id) as session:
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        with (
            encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})),
            pytest.raises(StageAbstainedError),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )


# ---------------------------------------------------------------------------
# §8.4 — transcription
# ---------------------------------------------------------------------------


async def test_a_transcript_is_emitted_and_then_scored(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transcription runs first because everything after it reads the transcript.

    Asserted through the emitted order rather than by reading the source: the
    ``media_transcribed`` event has to precede ``classification_scored`` on the
    chain, and the classification has to have been decided on text that only
    existed after transcription.
    """
    spoken = "the street light is out"
    transcript = Transcript(
        text=spoken,
        language="en",
        language_confidence=0.95,
        duration_seconds=4.2,
        model_id="fake-whisper@1",
    )

    async def _always_on(*args: object, **kwargs: object) -> bool:
        return True

    from nemesis.flags import get_flags

    monkeypatch.setattr(type(get_flags()), "is_enabled", _always_on, raising=False)

    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(
            session, tenant_id=tenant_id, description=None, audio_url="quarantine://x.ogg"
        )
        monkeypatch.setattr("nemesis.perception.media.audio_bytes", lambda state: b"audio-bytes")
        with (
            encoder_scope(EncoderKind.TEXT, _encoder(**{spoken: axis(1)})),
            encoder_scope(EncoderKind.TRANSCRIBE, FixedTranscriber(transcript)),
        ):
            result = await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=None,
                    audio_url="quarantine://x.ogg",
                )
            )

    types = [event.event_type for event in result.emitted]
    assert types == ["media_transcribed", "classification_scored"]
    assert result.emitted[0].payload["transcript"] == spoken
    assert result.emitted[0].payload["language"] == "en"
    assert result.emitted[0].payload["language_uncertain"] is False
    assert result.emitted[1].payload["category"] == "garbage_pile"


async def test_the_transcription_kill_switch_leaves_the_clip_unread(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most expensive operation on the ml worker, sheddable without a deploy.

    With the switch off and no description, there is nothing to score — which is
    the honest consequence and the reason the flag is a kill switch rather than
    a tuning knob.
    """

    async def _always_off(*args: object, **kwargs: object) -> bool:
        return False

    from nemesis.flags import get_flags

    monkeypatch.setattr(type(get_flags()), "is_enabled", _always_off, raising=False)

    transcriber = FixedTranscriber(
        Transcript(
            text="the street light is out",
            language="en",
            language_confidence=0.95,
            duration_seconds=4.2,
            model_id="fake-whisper@1",
        )
    )
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        complaint_id = await _complaint(
            session, tenant_id=tenant_id, description=None, audio_url="quarantine://x.ogg"
        )
        monkeypatch.setattr("nemesis.perception.media.audio_bytes", lambda state: b"audio-bytes")
        with (
            encoder_scope(EncoderKind.TEXT, _encoder()),
            encoder_scope(EncoderKind.TRANSCRIBE, transcriber),
            pytest.raises(StageAbstainedError),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=None,
                    audio_url="quarantine://x.ogg",
                )
            )

    assert transcriber.locales_seen == [], "the kill switch did not stop the transcriber"


# ---------------------------------------------------------------------------
# Gate clause 3 — a new category, by adding prompts alone
# ---------------------------------------------------------------------------


async def test_a_category_this_repository_has_never_heard_of_classifies(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Phase 9's third gate clause, at the service layer.

    The category key is deliberately absurd and appears in no module — a
    ``git grep`` for it in the live gate is what proves that half. What is
    proven here is the mechanism: a taxonomy node and a prompt set are written
    through the ordinary control-plane API, nothing else changes, and the very
    next classification can return it.
    """
    invented = "abandoned_palanquin"
    description = "someone has left a palanquin blocking the lane"

    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        await taxonomy.create_node(
            session,
            tenant_id=tenant_id,
            spec=TaxonomyNodeSpec(key=invented, display_name="Abandoned palanquin"),
        )
        await taxonomy.upsert_prompt_set(
            session,
            tenant_id=tenant_id,
            spec=PromptSetSpec(
                node_key=invented,
                locale="en",
                encoder="text",
                prompts=["a complaint about an abandoned palanquin blocking a lane"],
                prompt_set_version="invented-1",
            ),
        )
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        encoder = _encoder(
            **{
                "a complaint about an abandoned palanquin blocking a lane": axis(4),
                description: axis(4),
            }
        )
        with encoder_scope(EncoderKind.TEXT, encoder):
            result = await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )

    (event,) = result.emitted
    assert event.payload["category"] == invented
    # And it needed no calibration entry: an unlisted category gets the tenant's
    # declared defaults, which is what makes "by adding prompts alone" true
    # rather than "by adding prompts and then a calibration document".
    assert event.payload["calibration_version"]


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


async def test_one_tenants_prompts_never_classify_another_tenants_report(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Prompt bundles are cached under a content hash, not a tenant id.

    That is the right key — an edit is a new key by construction — and it makes
    a cross-tenant leak expressible in a way a tenant-keyed cache would not, so
    it is asserted rather than assumed.
    """
    description = "there is a big hole in the road"
    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        await session.commit()

    async with scoped(migrated_engine, other_tenant_id) as session:
        complaint_id = await _complaint(session, tenant_id=other_tenant_id, description=description)
        with (
            encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})),
            pytest.raises(StageAbstainedError),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=other_tenant_id,
                    complaint_id=complaint_id,
                    description_text=description,
                )
            )


async def test_the_embedding_write_cannot_touch_another_tenants_row(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The write is an explicit statement with its own tenant predicate, never a
    dirty-object flush — the reason ``control_plane.taxonomy`` gives at length."""
    from nemesis.perception import embeddings

    description = "there is a big hole in the road"
    async with scoped(migrated_engine, tenant_id) as session:
        complaint_id = await _complaint(session, tenant_id=tenant_id, description=description)
        await session.commit()

    async with scoped(migrated_engine, other_tenant_id) as session:
        changed = await embeddings.store(
            session,
            tenant_id=other_tenant_id,
            complaint_id=complaint_id,
            text_embedding=[0.1] * TEXT_EMBEDDING_DIM,
        )
        assert changed is False

    async with scoped(migrated_engine, tenant_id) as session:
        stored = (
            await session.execute(
                text("SELECT text_embedding FROM complaints WHERE id = :id").bindparams(
                    id=complaint_id
                )
            )
        ).scalar_one()
    assert stored is None


async def test_an_embedding_of_the_wrong_width_is_refused_before_the_column(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """pgvector would raise too; this one names the encoder whose output changed,
    which is the fact somebody debugging a model upgrade actually needs."""
    from nemesis.perception import embeddings

    async with scoped(migrated_engine, tenant_id) as session:
        complaint_id = await _complaint(session, tenant_id=tenant_id, description="x")
        with pytest.raises(ValueError, match="checkpoint change"):
            await embeddings.store(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                text_embedding=[0.1] * (TEXT_EMBEDDING_DIM * 2),
            )


# ---------------------------------------------------------------------------
# The calibration loop: harness proposes, an approver decides, the stage obeys
# ---------------------------------------------------------------------------


async def _activate_calibration(
    session: AsyncSession, *, tenant_id: uuid.UUID, body: dict[str, object]
) -> None:
    """Draft -> review -> approve -> activate. The only path a document goes live by.

    Walked in full rather than shortcut, because the claim under test is that
    the *harness's output* reaches the *stage* through the governed path an
    operator actually has. A test that reached into `baselines` would prove the
    resolver works and say nothing about whether a measured calibration can ever
    be deployed.
    """
    from nemesis.policy import service as policy_service
    from nemesis.policy.documents import PolicyKind
    from nemesis.policy.resolver import RESOLVER

    kind = PolicyKind.PERCEPTION_CALIBRATION
    version = await policy_service.draft(
        session,
        tenant_id=tenant_id,
        kind=kind,
        body=body,
        change_reason="Fitted by the Phase 9 validation harness",
    )
    for step, reason in (
        (policy_service.submit_for_review, "measured on the calibration split"),
        (policy_service.approve, "approved by the data owner"),
        (policy_service.activate, "go live"),
    ):
        await step(
            session,
            tenant_id=tenant_id,
            kind=kind,
            revision=version.revision,
            reason=reason,
        )
    await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)


async def test_a_harness_fitted_calibration_can_be_approved_and_changes_the_decision(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The loop the phase actually ships, end to end in one test.

    `nem f1` writes `docs/reports/perception-calibration-proposed.json` and the
    module docstring claims it is "shaped so it can be POSTed to the policy API
    unchanged". That claim was untested: nothing anywhere activated a
    `perception_calibration` document, so "the harness proposes and an approver
    decides" was an architecture diagram rather than a path.

    The assertion is deliberately about a *changed outcome* rather than about a
    stored row. A document that validates, activates, and is then ignored by the
    stage would satisfy every weaker check — so the same submission is scored
    twice, once under the tenant defaults and once under a fitted document whose
    only material difference is an abstain floor above the achievable
    confidence, and the second one must abstain.
    """
    description = "there is a big hole in the road"

    async with scoped(migrated_engine, tenant_id) as session:
        await _taxonomy(session, tenant_id)
        first = await _complaint(session, tenant_id=tenant_id, description=description)
        with encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})):
            result = await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=first,
                    description_text=description,
                )
            )
        (event,) = result.emitted
        assert event.payload["category"] == "pothole"
        baseline_stamp = event.payload["calibration_version"]
        await session.commit()

    # What the harness produces, in the shape it produces it. Built through
    # `calibration_document` rather than hand-written, so a change to the
    # harness's output shape fails here rather than in an operator's console.
    proposed = harness.calibration_document(
        (
            harness.FittedCategory(
                category="pothole",
                # **Both numbers matter and the temperature is the load-bearing
                # one.** The fake encoder's prompt vectors are orthogonal, so at
                # the document default of 0.05 the softmax saturates to a
                # confidence of exactly 1.000 and *no* floor below 1.0 could ever
                # fire — a version of this test with only the floor changed
                # passed the activation and then classified anyway, proving
                # nothing. A temperature of 10.0 flattens the same two
                # similarities to ~0.52, which the floor below then refuses.
                temperature=10.0,
                bias=0.0,
                abstain_below=0.9,
                min_margin=0.0,
                sample_size=54,
                positives=6,
                mean_positive_similarity=0.84,
                mean_negative_similarity=0.81,
                provenance="fitted by the validation harness on the calibration split",
            ),
        )
    )

    async with scoped(migrated_engine, tenant_id) as session:
        await _activate_calibration(session, tenant_id=tenant_id, body=proposed)

    async with scoped(migrated_engine, tenant_id) as session:
        second = await _complaint(session, tenant_id=tenant_id, description=description)
        with (
            encoder_scope(EncoderKind.TEXT, _encoder(**{description: axis(0)})),
            pytest.raises(StageAbstainedError, match=r"0\.900"),
        ):
            await classification_stage(
                _context(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=second,
                    description_text=description,
                )
            )

    # And the stamp moved, so a decision made before the approval is still
    # attributable to the document that made it.
    assert baseline_stamp != "perception_calibration@1"
