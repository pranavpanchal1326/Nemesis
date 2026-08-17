"""One event loop per worker process, for the life of the process.

**The defect this exists to prevent.** A Celery task body is synchronous, so the
obvious way to call async code from it is ``asyncio.run(coro)``. That creates a
*new* event loop, runs the coroutine, and closes it — per task.

``nemesis.db.session`` caches a module-global engine, and asyncpg connections
bind to the loop that created them. So the first task in a worker child creates
the engine on loop A and succeeds; the second task runs on loop B and is handed a
pooled connection owned by loop A, which fails with::

    RuntimeError: got Future <Future pending> attached to a different loop

The shape of that failure is what makes it dangerous. It is not "the worker is
broken" — it is **exactly one task succeeds per child process, and everything
after it fails until ``worker_max_tasks_per_child`` recycles the child**. On a
quiet system, where tasks arrive one at a time with restarts in between, it looks
like it works. Under load it degrades every complaint behind the first one into
the dead-letter queue, correctly and uselessly, with a stack trace that names
asyncio rather than anything in this codebase.

``tests/conftest.py`` already documents this hazard for the test engine, where
pytest-asyncio gives each test a fresh loop. The same reasoning applies to the
worker and had not been applied to it.

**The fix is a persistent loop, not a disposable engine.** Disposing the engine
after every task would also work and would throw away connection pooling — a
fresh TCP connection, TLS handshake, and asyncpg prepared-statement cache per
pipeline stage, on the hot path of the thing this system exists to do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from nemesis.observability.logging import get_logger

log = get_logger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    """This process's loop, created on first use.

    Also set as the thread's current loop, so any library that reaches for
    ``asyncio.get_event_loop()`` finds the same one rather than creating a second.
    """
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` on this process's loop and return its result.

    The one sanctioned way to call async code from a Celery task. Using
    ``asyncio.run`` instead is not a style difference — see the module docstring.
    """
    return get_loop().run_until_complete(coro)


def close_loop() -> None:
    """Dispose the engine and close the loop, in that order.

    Order matters: disposing the engine is itself async and has to run on the
    loop that owns the connections. Closing first would leave the pool's sockets
    to be finalised by the garbage collector, which is where ``ResourceWarning``
    noise at interpreter shutdown comes from.
    """
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = None
        return
    try:
        from nemesis.db.session import dispose_engine

        _loop.run_until_complete(dispose_engine())
    except Exception as exc:  # pragma: no cover — shutdown path
        log.warning("worker_loop_dispose_failed", error_type=type(exc).__name__)
    finally:
        _loop.close()
        _loop = None
