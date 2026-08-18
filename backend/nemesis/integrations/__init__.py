"""The integration platform: who may call, and what gets pushed to them.

``keys``
    Minting, verifying, revoking, and accounting for API keys.
``webhooks``
    Subscription management and the signature scheme.
``delivery``
    The fan-out from the outbox and the retrying dispatcher.
``errors``
    One error family, translated to HTTP in exactly one place — the same split
    ``control_plane`` uses, and for the same reason: these services are called
    from a CLI and from tests, and dragging status codes into them would make
    both awkward for the benefit of one caller.
"""

from __future__ import annotations
