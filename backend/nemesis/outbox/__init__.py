"""Transactional outbox: side effects that cannot outlive a rollback.

``writer`` enqueues, inside the caller's transaction. ``relay`` drains, in a
process of its own. The split is the whole design — see
``nemesis/db/models/outbox.py`` for why publishing from the request handler is
the bug this replaces, and ``docs/adr/0015-transactional-outbox-for-realtime.md``
for why a dedicated relay beat the two alternatives.
"""

from __future__ import annotations
