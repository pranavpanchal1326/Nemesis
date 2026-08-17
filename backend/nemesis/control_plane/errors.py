"""Control-plane failures, as a small closed set the API layer can translate.

Deliberately *not* ``ProblemDetailError``. The services in this package are
called from HTTP handlers, from the provisioning CLI, from the template loader,
and from tests — a service that raised an HTTP error would drag FastAPI's status
codes into a Celery worker and into a migration script, and would make "what
should this return outside HTTP?" a question with no answer.

The API layer owns the mapping, in one place, so a new error kind here surfaces
consistently rather than as whatever the nearest handler improvised.
"""

from __future__ import annotations


class ControlPlaneError(Exception):
    """Base for every rejection this package makes."""


class NotFoundError(ControlPlaneError):
    """A referenced entity does not exist *for this tenant*.

    The tenant qualifier is the whole point. "Exists but belongs to someone
    else" and "does not exist" must be indistinguishable to a caller, or the
    control plane becomes an oracle for enumerating another customer's taxonomy
    one key at a time — the same reasoning ``api.deps`` applies to tenant
    lookup.
    """


class ConflictError(ControlPlaneError):
    """The change collides with something that already exists.

    A duplicate key, a second default calendar, a code already taken. Separate
    from ``ValidationError`` because the caller's remedy is different: a
    conflict is fixed by choosing another identifier, a validation failure by
    correcting the request.
    """


class ValidationError(ControlPlaneError):
    """The request is internally inconsistent or violates a stated invariant."""


class HierarchyError(ValidationError):
    """A tree operation would produce a cycle, an orphan, or excessive depth.

    Its own type because the three ways to break a tree share a remedy — look at
    the parent you named — and because a cycle is the failure most likely to be
    introduced by a bulk import, where the offending row is not the one the
    caller was thinking about.
    """
