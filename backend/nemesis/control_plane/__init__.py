"""The control plane — Track B, and the answer to critique-log defects #1 and #2.

Everything a customer could plausibly want different is data here: defect
categories, the organisation chart, the geography, working hours, holidays,
translations, and the classifier prompts that make a new category classifiable.
None of it is a constant, an enum, or an environment variable, and the test the
program plan sets is a practical one — *could a solutions engineer onboard a new
campus without opening an editor?*

**What this package is not.** It is not the policy engine. Severity rubrics,
dedup thresholds, safety rulesets, SLA matrices, and routing rules become
versioned, effective-dated, draft→approve→activate documents in **Phase 6**. What
Phase 5 ships is the *structure* those policies attach to, plus the day-one
defaults (``severity_semantics``, ``routing_hints``) that let a tenant be useful
before the policy phase lands. Where a default is a placeholder for a Phase 6
document, the code says so at the point a reader will look.

**Layering.** Every service takes an ``AsyncSession`` and never commits.
Transaction boundaries belong to the caller — an HTTP handler, the provisioner,
a test — because provisioning is atomic across a dozen services and that is only
expressible if none of them commits on its own.
"""

from __future__ import annotations

from nemesis.control_plane.errors import (
    ConflictError,
    ControlPlaneError,
    HierarchyError,
    NotFoundError,
    ValidationError,
)

__all__ = [
    "ConflictError",
    "ControlPlaneError",
    "HierarchyError",
    "NotFoundError",
    "ValidationError",
]
