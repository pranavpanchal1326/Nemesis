"""Environment parity, from the application's side.

`scripts/check_env_parity.py` checks the deployment contract against the files a
new environment is built from (`.env.example`, `docker-compose.yml`). This
module checks it against the thing those files configure — so neither half can
drift without something failing.

The property being defended: when Phase 1b stands up a real environment, the
list of what it needs is already written down and already true. The expensive
alternative is discovering that list one outage at a time.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from nemesis import __version__
from nemesis.config import Settings
from nemesis.deployment import DEPLOYMENT_REQUIRED, Criticality, application_settings
from nemesis.flags.registry import REGISTRY, expired_flags


def _resolve(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _field_annotation(dotted: str) -> Any:
    """Walk the pydantic model tree to the annotation of a nested field."""
    model: Any = Settings
    parts = dotted.split(".")
    for part in parts[:-1]:
        model = model.model_fields[part].annotation
    return model.model_fields[parts[-1]].annotation


class TestDeploymentContract:
    @pytest.mark.parametrize("required", application_settings(), ids=lambda r: r.env_var)
    def test_setting_path_resolves(self, required: Any) -> None:
        """A contract entry naming a field that does not exist is a contract
        nobody is actually holding the code to."""
        settings = Settings()
        assert _resolve(settings, required.setting_path) is not None or required.local_default == ""

    @pytest.mark.parametrize("required", DEPLOYMENT_REQUIRED, ids=lambda r: r.env_var)
    def test_env_var_naming_matches_settings_convention(self, required: Any) -> None:
        """NEMESIS_-prefixed variables must map to a real setting, and
        unprefixed ones must not claim to."""
        if required.env_var.startswith("NEMESIS_"):
            assert required.setting_path is not None, (
                f"{required.env_var} is prefixed as an application setting but "
                f"names no field on Settings"
            )
            expected = "NEMESIS_" + required.setting_path.replace(".", "__").upper()
            assert required.env_var == expected, (
                f"{required.env_var} would not reach {required.setting_path}; "
                f"pydantic-settings expects {expected}"
            )
        else:
            assert required.setting_path is None

    @pytest.mark.parametrize(
        "required",
        [r for r in DEPLOYMENT_REQUIRED if r.criticality is Criticality.SECRET],
        ids=lambda r: r.env_var,
    )
    def test_secrets_are_typed_as_secrets(self, required: Any) -> None:
        """Every application-backed secret is a SecretStr.

        Not a style preference. `SecretStr.__repr__` redacts, which is the only
        thing standing between a settings object reaching a log line and the
        signing key reaching a log line.
        """
        if required.setting_path is None:
            pytest.skip("consumed by compose or a sidecar, not by Settings")
        assert _field_annotation(required.setting_path) is SecretStr


class TestProductionSafetyGuards:
    """The local defaults must be *unable* to survive into a pilot deployment."""

    def test_pilot_refuses_the_development_signing_key(self) -> None:
        with pytest.raises(ValueError, match="development JWT secret"):
            Settings(app_env="pilot", jwt_secret=SecretStr("dev-only-insecure-secret-change-me"))

    def test_pilot_refuses_wildcard_cors(self) -> None:
        with pytest.raises(ValueError, match="wildcard CORS"):
            Settings(
                app_env="pilot",
                jwt_secret=SecretStr("a-real-generated-value"),
                cors_allow_origins=("*",),
            )

    def test_pilot_boots_with_real_values(self) -> None:
        settings = Settings(
            app_env="pilot",
            jwt_secret=SecretStr("a-real-generated-value"),
            cors_allow_origins=("https://nemesis.example.gov.in",),
        )
        assert settings.is_production_like

    def test_secret_does_not_leak_through_repr(self) -> None:
        """The failure this prevents: an exception handler logging the settings
        object and publishing the signing key to stdout."""
        settings = Settings(jwt_secret=SecretStr("super-secret-value"))
        assert "super-secret-value" not in repr(settings)
        assert "super-secret-value" not in str(settings.jwt_secret)
        assert settings.jwt_secret.get_secret_value() == "super-secret-value"


class TestVersionConsistency:
    """One version number, in one place.

    Restating it meant the running service could report a version the artefact
    did not have — a lie told at exactly the moment somebody is trying to work
    out what changed.
    """

    def test_package_and_pyproject_agree(self) -> None:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        assert (
            declared == __version__
        ), f"pyproject.toml says {declared}, nemesis.__version__ says {__version__}"

    def test_settings_reports_the_package_version(self) -> None:
        assert Settings().service_version == __version__


class TestFlagHygiene:
    def test_no_flag_is_past_its_removal_date(self) -> None:
        """The only mechanism observed to actually remove feature flags.

        When this fails, the fix is to remove the flag and the branch it guards.
        Extending `remove_by` is a deliberate, reviewable act — but it is a
        decision, not a way to make the build green.
        """
        overdue = expired_flags(datetime.now(UTC).date())
        assert not overdue, "flags past their removal date: " + ", ".join(
            f"{spec.name} (due {spec.remove_by}, owner {spec.owner})" for spec in overdue
        )

    def test_every_flag_has_an_owning_function(self) -> None:
        valid = {"PLT", "DATA", "PROD", "SEC", "SRE", "BIZ"}
        for spec in REGISTRY.values():
            assert spec.owner in valid, f"{spec.name}: unknown owning function {spec.owner!r}"
