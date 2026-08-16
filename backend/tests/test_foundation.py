"""Datastore contract and observability foundation tests.

Configuration invariants live in `test_config_invariants.py`, probe contracts in
`test_probes.py`, and the error contract in `test_error_contract.py`.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.observability.logging import (
    configure_logging,
    get_correlation_id,
    get_logger,
    new_correlation_id,
    set_correlation_id,
)
from tests.conftest import postgres_required


class TestCorrelationId:
    def test_generated_when_absent(self) -> None:
        configure_logging(level="WARNING", service_name="test")
        cid = set_correlation_id(None)
        assert cid and get_correlation_id() == cid

    def test_inbound_value_is_preserved(self) -> None:
        assert set_correlation_id("abc123") == "abc123"

    def test_generated_ids_are_unique(self) -> None:
        assert len({new_correlation_id() for _ in range(1000)}) == 1000

    def test_correlation_id_appears_in_log_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        # The whole point of the ContextVar: a log line must be joinable to the
        # request that produced it without the caller passing the ID down.
        configure_logging(level="INFO", service_name="test")
        set_correlation_id("trace-in-log")
        get_logger("test").info("something_happened", detail="x")

        line = capsys.readouterr().out.strip().splitlines()[-1]
        assert json.loads(line)["correlation_id"] == "trace-in-log"

    def test_log_output_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        # §24.3 makes stdout the observability substrate; a non-JSON line breaks
        # every downstream collector.
        configure_logging(level="INFO", service_name="test")
        get_logger("test").info("structured", count=3, nested={"a": 1})

        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["event"] == "structured"
        assert payload["count"] == 3
        assert payload["level"] == "info"
        assert "timestamp" in payload


@postgres_required
class TestDatastoreContract:
    async def test_required_extensions_present(self, engine: AsyncEngine) -> None:
        # The dedup engine (§14) is built directly on these. Absence must fail at
        # boot, not at the first similarity query.
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT extname FROM pg_extension"))
            found = {r[0] for r in rows}
        assert {"postgis", "vector", "pgcrypto", "pg_trgm"} <= found

    async def test_postgis_radius_filter_matches_stage_one_semantics(
        self, engine: AsyncEngine
    ) -> None:
        # Stage 1 in miniature. 0.001° of longitude at the equator is ~111 m, so
        # it must fall outside the 50 m default window and inside a 200 m one.
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT "
                    "  ST_DWithin(a, b, 50) AS within_50, "
                    "  ST_DWithin(a, b, 200) AS within_200 "
                    "FROM (SELECT "
                    "  ST_MakePoint(0, 0)::geography AS a, "
                    "  ST_MakePoint(0.001, 0)::geography AS b) p"
                )
            )
            within_50, within_200 = result.one()
        assert within_50 is False
        assert within_200 is True

    async def test_pgvector_cosine_distance_semantics(self, engine: AsyncEngine) -> None:
        # Stage 2 in miniature: identical vectors distance 0, orthogonal 1.
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT "
                    "  '[1,0,0]'::vector <=> '[1,0,0]'::vector AS same, "
                    "  '[1,0,0]'::vector <=> '[0,1,0]'::vector AS orthogonal"
                )
            )
            same, orthogonal = result.one()
        assert same == pytest.approx(0.0)
        assert orthogonal == pytest.approx(1.0)

    async def test_halfvec_available_for_clip_embeddings(self, engine: AsyncEngine) -> None:
        # pgvector 0.8 halfvec halves index memory for the 512-dim CLIP column,
        # which is why the image pins 0.8+ rather than any pgvector.
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT '[1,0,0]'::halfvec <=> '[0,1,0]'::halfvec"))
        assert result.scalar_one() == pytest.approx(1.0)

    async def test_uuid_generation_available(self, engine: AsyncEngine) -> None:
        # Entity ids are UUIDs so public URLs cannot be enumerated (§22).
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT gen_random_uuid()"))
        assert result.scalar_one() is not None

    async def test_database_is_utc(self, engine: AsyncEngine) -> None:
        # SLA deadlines and the 72-hour dedup window are time-based; a
        # server-local timezone would corrupt both in a way that is very hard to
        # see in test data.
        async with engine.connect() as conn:
            result = await conn.execute(text("SHOW timezone"))
        assert result.scalar_one().upper() in {"UTC", "ETC/UTC"}
