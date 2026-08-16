"""Property-based tests for configuration invariants.

Three examples prove a function works on three inputs. These invariants guard
arithmetic and ordering that the rest of the system assumes without re-checking,
so they are proven over generated inputs instead.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from nemesis.config import (
    DedupSettings,
    ModelSettings,
    Settings,
    SeveritySettings,
    get_settings,
)

_weight = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


class TestSeverityRubricInvariants:
    def test_default_weights_sum_to_one(self) -> None:
        s = SeveritySettings()
        total = (
            s.weight_visual_damage
            + s.weight_road_class
            + s.weight_poi_proximity
            + s.weight_cluster_count
        )
        assert total == pytest.approx(1.0)

    @given(a=_weight, b=_weight, c=_weight, d=_weight)
    def test_only_normalised_weight_sets_are_accepted(
        self, a: float, b: float, c: float, d: float
    ) -> None:
        """§13.1 requires a scored complaint to be reproducible from its logged
        breakdown. An unnormalised rubric makes that arithmetic lie, so the
        constructor must accept a weight set if and only if it sums to 1."""
        normalised = abs((a + b + c + d) - 1.0) <= 1e-6
        try:
            SeveritySettings(
                weight_visual_damage=a,
                weight_road_class=b,
                weight_poi_proximity=c,
                weight_cluster_count=d,
            )
            accepted = True
        except ValidationError:
            accepted = False
        assert accepted == normalised

    def test_rubric_is_versioned(self) -> None:
        # §13.3: a scored complaint must stay attributable to the rubric that
        # scored it, so the version is not optional.
        assert SeveritySettings().rubric_version.startswith("severity_rubric_v")


class TestDedupBandInvariants:
    def test_default_bands_are_ordered(self) -> None:
        d = DedupSettings()
        assert d.investigate_threshold < d.merge_threshold

    @given(merge=_unit, investigate=_unit)
    def test_ambiguity_band_can_never_collapse(self, merge: float, investigate: float) -> None:
        """If the bands meet or invert, the 0.65-0.85 'maybe' band that routes to
        the Investigation Agent (§12.4) vanishes and dedup silently degrades to a
        binary merge/no-merge decision — losing the human-review safety valve."""
        try:
            cfg = DedupSettings(merge_threshold=merge, investigate_threshold=investigate)
            assert cfg.investigate_threshold < cfg.merge_threshold
        except ValidationError:
            assert investigate >= merge

    def test_default_threshold_is_conservative(self) -> None:
        # §14.3: a false-positive merge suppresses a real citizen report, which
        # is strictly worse than leaving a duplicate unmerged.
        assert DedupSettings().merge_threshold >= 0.85

    @given(radius=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False))
    def test_non_positive_geo_radius_is_rejected(self, radius: float) -> None:
        with pytest.raises(ValidationError):
            DedupSettings(geo_radius_meters=radius)


class TestModelSelection:
    def test_text_embedding_model_is_multilingual(self) -> None:
        # ADR-0003. all-MiniLM-L6-v2 is English-only; §8.4 also requires
        # Hindi/Marathi. Devanagari would embed to noise and silently break dedup
        # Stage 2 for exactly the users the system targets. This test is the
        # guardrail against a future "optimisation" back to MiniLM.
        assert "multilingual" in ModelSettings().text_embedding_model

    def test_embedding_dims_match_the_schema_contract(self) -> None:
        m = ModelSettings()
        assert m.text_embedding_dim == 384
        assert m.clip_embedding_dim == 512

    def test_e5_prefix_is_configured(self) -> None:
        # e5 models are asymmetric; omitting the prefix measurably degrades
        # retrieval, so it is configuration rather than an implementation detail.
        assert ModelSettings().text_embedding_prefix.strip()

    def test_torch_threads_capped_below_core_count(self) -> None:
        # ML inference must not starve Postgres and Redis on a shared machine.
        assert 1 <= ModelSettings().torch_num_threads <= 6


class TestSettingsHygiene:
    def test_unknown_env_keys_are_rejected(self) -> None:
        # `extra="forbid"`: a typo'd variable must fail loudly at boot rather
        # than silently leaving the default in place.
        with pytest.raises(ValidationError):
            Settings(not_a_real_setting="x")  # type: ignore[call-arg]

    def test_secret_is_not_leaked_by_repr(self) -> None:
        s = Settings(jwt_secret="super-secret-value")  # type: ignore[arg-type]
        assert "super-secret-value" not in repr(s)
        assert "super-secret-value" not in str(s)

    def test_settings_are_frozen(self) -> None:
        # Mutating configuration at runtime makes behaviour depend on request
        # ordering; the control plane (Phase 6) is the sanctioned path instead.
        with pytest.raises(ValidationError):
            Settings().app_env = "pilot"  # type: ignore[misc]

    def test_settings_are_cached_process_wide(self) -> None:
        assert get_settings() is get_settings()

    def test_sync_url_derives_from_async_url(self) -> None:
        s = Settings(database_url="postgresql+asyncpg://u:p@h:5432/db")
        assert s.sync_database_url == "postgresql+psycopg://u:p@h:5432/db"
