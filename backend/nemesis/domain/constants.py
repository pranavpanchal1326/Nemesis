"""Facts that both the schema and the event store need, owned by neither.

``HASH_HEX_LENGTH`` lives here rather than in ``events.hashing`` because
``db.models.event`` needs it to size its columns, and ``events.store`` needs
``db.models.event`` to write rows. Importing the constant from the events
package made that a cycle — models → events → store → models — which Python
reports as a confusing partial-initialisation error at the *third* module in the
chain, far from the import that caused it.

A leaf module with no imports of its own cannot participate in a cycle. The
dependency direction is therefore one-way and stays that way: models depend on
domain, events depend on both, nothing depends on events.
"""

from __future__ import annotations

from typing import Final

#: Hex digits in a SHA-256 digest. A property of the algorithm, not a choice —
#: which is exactly why neither layer should own it.
HASH_HEX_LENGTH: Final = 64
