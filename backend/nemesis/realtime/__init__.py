"""Realtime transport — the §26.3 WebSocket contract and what feeds it.

    events (committed)  ->  outbox_messages  ->  relay  ->  Redis  ->  hub  ->  client

Four hops rather than one, and each boundary buys a specific guarantee:

``outbox_messages``
    Publishing happens from *committed* rows, so a rolled-back transaction
    cannot put an event on a screen.
``relay``
    One process turns committed rows into a Redis publish, under a lock. Doing
    it inside the API would mean every replica publishing every event.
``Redis``
    Fan-out across API processes. A client is connected to one of them; the
    event was written by another.
``hub``
    Per-connection bounded queues. A client that stops reading is shed, never
    allowed to apply backpressure to the process serving everyone else.
"""

from __future__ import annotations
