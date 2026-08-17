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

import uuid
from typing import Final

#: Hex digits in a SHA-256 digest. A property of the algorithm, not a choice —
#: which is exactly why neither layer should own it.
HASH_HEX_LENGTH: Final = 64

#: The tenant a deployment-level fact is recorded against.
#:
#: Integrity findings and dependency degradations belong to the deployment, not
#: to whichever customer happened to be submitting when Redis fell over. Giving
#: them a fixed reserved tenant keeps an operational failure out of a customer's
#: audit trail.
#:
#: **This is a real row in ``tenants``**, seeded by migration, and it has to be:
#: ``events.tenant_id`` carries a foreign key, so a reserved id that exists only
#: as a Python constant makes every degradation record fail with a foreign key
#: violation — raised inside the error handler, which means the second failure
#: masks the first one somebody was trying to record.
SYSTEM_TENANT_ID: Final = uuid.UUID("00000000-0000-0000-0000-000000000001")

#: Chosen to be unmistakable in operator tooling and impossible to collide with
#: a customer handle — no real tenant slug starts and ends with underscores.
SYSTEM_TENANT_SLUG: Final = "__system__"
