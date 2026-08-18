"""The §16.3 / §26.4 public surface: what leaves the system, and what cannot.

Three modules, split along the line that matters for testing:

``policy``
    What a public field is allowed to be. Pure functions and declared field
    sets, with no database import at all — which is what makes "no public field
    carries citizen data" an assertion about code rather than an integration
    test that only covers the rows the fixture happened to create.
``aggregates``
    The queries, with suppression applied before anything is serialised.
``export``
    Bulk CSV/NDJSON for RTI applicants and researchers, streamed rather than
    assembled.
"""

from __future__ import annotations
