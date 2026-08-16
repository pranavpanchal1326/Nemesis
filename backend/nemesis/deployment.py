"""The deployment contract: what a *deployed* environment must supply.

Phase 1b will stand up dev, staging, and production against a provider nobody
has chosen yet. The expensive way to discover what those environments need is
to build one and find out. The cheap way is to write the list down now, while
the constraints are fresh, and have CI hold both the local stack and the
documentation to it.

This module is the single source of that list. It is checked from two directions
so neither can drift:

* ``backend/tests/test_environment_parity.py`` asserts every entry names a real
  ``Settings`` field, that no entry's local default survives ``app_env=pilot``,
  and that nothing security-relevant is missing from the list.
* ``scripts/check_env_parity.py`` asserts ``.env.example`` documents every
  entry and ``docker-compose.yml`` supplies each one as an override-able value
  rather than a baked-in literal.

**STDLIB ONLY, DELIBERATELY.** The root-level parity script imports this module
with nothing installed but Python. Adding a pydantic import here would mean the
check could only run where the full dependency set is present, which is exactly
the environment-specific coupling it exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Criticality(StrEnum):
    SECRET = "secret"
    """Must be generated per environment and never appear in the repo, an image,
    or a log. Rotation procedure in docs/SECRETS.md."""

    ENDPOINT = "endpoint"
    """Points at infrastructure. Differs per environment by definition; a wrong
    value is loud and immediate."""

    POLICY = "policy"
    """A safety or behaviour decision whose local default is deliberately
    permissive. A wrong value here is *quiet*, which is what makes it dangerous."""


@dataclass(frozen=True, slots=True)
class RequiredSetting:
    env_var: str
    setting_path: str | None
    """Dotted path on ``Settings``, or ``None`` for a variable consumed by
    compose or a sidecar rather than by the application."""

    criticality: Criticality
    local_default: str
    """What the local stack uses. Recorded so the check can assert this exact
    value cannot follow the stack into a deployed environment."""

    why: str


# --------------------------------------------------------------------------
# The contract.
# --------------------------------------------------------------------------
DEPLOYMENT_REQUIRED: tuple[RequiredSetting, ...] = (
    RequiredSetting(
        env_var="NEMESIS_APP_ENV",
        setting_path="app_env",
        criticality=Criticality.POLICY,
        local_default="local",
        why=(
            "Gates the production safety validator. Left at 'local', a deployed "
            "stack will happily boot with the development signing key — the "
            "guard exists but never runs."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_JWT_SECRET",
        setting_path="jwt_secret",
        criticality=Criticality.SECRET,
        local_default="dev-only-insecure-secret-change-me",
        why=(
            "Signs every session token. The local value is published in this "
            "repository, so a deployment that kept it has no authentication at "
            "all — anyone can mint a valid token."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_CORS_ALLOW_ORIGINS",
        setting_path="cors_allow_origins",
        criticality=Criticality.POLICY,
        local_default="http://localhost:3000",
        why=(
            "A wildcard or a stale localhost entry in front of citizen data is "
            "the quiet kind of misconfiguration: everything works, and the "
            "browser stops being a boundary."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_DATABASE_URL",
        setting_path="database_url",
        criticality=Criticality.ENDPOINT,
        local_default="postgresql+asyncpg://nemesis:nemesis@localhost:5432/nemesis",
        why="Carries the database credential as well as the host.",
    ),
    RequiredSetting(
        env_var="NEMESIS_REDIS_URL",
        setting_path="redis_url",
        criticality=Criticality.ENDPOINT,
        local_default="redis://localhost:6379/0",
        why=(
            "Celery broker, result backend, and the feature flag override store. "
            "A deployed Redis needs auth and TLS, which live in this URL."
        ),
    ),
    RequiredSetting(
        env_var="POSTGRES_PASSWORD",
        setting_path=None,
        criticality=Criticality.SECRET,
        local_default="nemesis",
        why=(
            "Consumed by the postgres container, not by the application. Held "
            "here anyway: a secret that only compose reads is still a secret, "
            "and the one most likely to be forgotten precisely because no "
            "application code mentions it."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_OLLAMA__BASE_URL",
        setting_path="ollama.base_url",
        criticality=Criticality.ENDPOINT,
        local_default="http://host.docker.internal:11434",
        why=(
            "host.docker.internal is a Docker Desktop convenience that does not "
            "exist in a cloud runtime. This is the value most likely to be "
            "carried forward by accident and fail only when the Investigation "
            "Agent is first invoked (§12.4)."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_OTEL__ENABLED",
        setting_path="otel.enabled",
        criticality=Criticality.POLICY,
        local_default="false",
        why=(
            "Off locally so no service depends on a running collector. A "
            "deployed environment with this off has no traces and nobody "
            "notices until the first incident needs one."
        ),
    ),
    RequiredSetting(
        env_var="NEMESIS_OTEL__ENDPOINT",
        setting_path="otel.endpoint",
        criticality=Criticality.ENDPOINT,
        local_default="",
        why="Collector address. Empty locally; per-environment once deployed.",
    ),
    RequiredSetting(
        env_var="GRAFANA_ADMIN_PASSWORD",
        setting_path=None,
        criticality=Criticality.SECRET,
        local_default="admin",
        why=(
            "The observability stack is not exempt from secret handling just "
            "because it is 'only monitoring'. Dashboards disclose traffic "
            "patterns, error rates, and tenant activity."
        ),
    ),
)

REQUIRED_BY_ENV_VAR: dict[str, RequiredSetting] = {r.env_var: r for r in DEPLOYMENT_REQUIRED}


def secrets() -> tuple[RequiredSetting, ...]:
    """Entries with a rotation procedure in docs/SECRETS.md."""
    return tuple(r for r in DEPLOYMENT_REQUIRED if r.criticality is Criticality.SECRET)


def application_settings() -> tuple[RequiredSetting, ...]:
    """Entries backed by a field on ``Settings``."""
    return tuple(r for r in DEPLOYMENT_REQUIRED if r.setting_path is not None)
