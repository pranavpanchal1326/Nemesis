"""One event loop per worker process.

**What this reproduces.** With ``asyncio.run`` per task, the first task in a
worker child creates the engine on loop A and succeeds; the second runs on loop B
and is handed a pooled asyncpg connection owned by loop A, which fails with
``got Future attached to a different loop``.

The shape is what makes it dangerous: exactly one task succeeds per child
process, and everything after it fails until ``worker_max_tasks_per_child``
recycles the child. On a quiet system with restarts in between it looks like it
works. It was found by the live gate, not by this suite, because pytest-asyncio
gives every test a fresh loop and therefore reproduces the *working* case by
construction. So this test does what a worker does — two synchronous calls
through the same process-level loop — rather than what a test normally does.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from nemesis.db.session import session_scope
from nemesis.worker.loop import close_loop, get_loop, run_async
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]


def test_two_sequential_tasks_reuse_the_same_loop_and_pool(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """The regression. Synchronous on purpose — a Celery task body is synchronous.

    ``asyncio.run`` here instead of ``run_async`` fails on the second call, which
    is exactly the production failure this replaces.
    """

    async def touch_database() -> int:
        async with session_scope() as session:
            return int((await session.execute(text("SELECT 1"))).scalar_one())

    try:
        first = run_async(touch_database())
        second = run_async(touch_database())
        third = run_async(touch_database())
    finally:
        close_loop()

    assert (first, second, third) == (1, 1, 1)


def test_the_loop_is_reused_rather_than_recreated() -> None:
    try:
        assert get_loop() is get_loop()
    finally:
        close_loop()


def test_closing_is_idempotent_and_a_new_loop_is_created_after() -> None:
    """A recycled child closes its loop; a fresh one must not inherit a dead loop."""
    first = get_loop()
    close_loop()
    close_loop()
    second = get_loop()
    try:
        assert first.is_closed()
        assert second is not first
        assert not second.is_closed()
    finally:
        close_loop()


def test_no_celery_task_module_calls_asyncio_run() -> None:
    """A grep, deliberately, and it is the check that would have caught this.

    The defect was not subtle once seen — it was one call in one module — and no
    behavioural test in this suite could reach it, because a test process never
    runs two tasks through one persistent loop the way a worker child does.
    """
    from pathlib import Path

    from nemesis.worker.celery_app import TASK_MODULES

    offenders: list[str] = []
    for module in TASK_MODULES:
        path = Path(__file__).resolve().parent.parent / Path(*module.split("."))
        source = path.with_suffix(".py").read_text(encoding="utf-8")
        if "asyncio.run(" in source:
            offenders.append(module)

    assert offenders == [], (
        f"{offenders} call asyncio.run; use nemesis.worker.loop.run_async — a new "
        f"loop per task hands the next task a connection owned by a closed one"
    )


async def test_run_async_is_not_for_use_inside_a_running_loop() -> None:
    """Guard against the other misuse: calling it from async code.

    ``run_until_complete`` on the loop that is already running raises. Asserted
    so the failure is a documented one rather than a surprise the first time
    somebody reaches for it from a FastAPI handler.
    """

    async def noop() -> None:
        return None

    coro = noop()
    try:
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop().run_until_complete(coro)
    finally:
        # Closed explicitly. A coroutine that is created and never awaited is
        # finalised by the garbage collector, and `filterwarnings = ["error"]`
        # turns the resulting warning into a failure attributed to whichever
        # test happens to be running then — the same intermittent
        # blame-the-wrong-test flake Phase 1a spent a day on.
        coro.close()
