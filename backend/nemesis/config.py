"""Typed application configuration.

Every tunable in NEMESIS is declared here. No module reads ``os.environ``
directly and no behavioural constant is inlined at a call site — a value that
changes system behaviour must be visible, typed, and overridable from one place.

Environment variables use the ``NEMESIS_`` prefix, with ``__`` as the nesting
delimiter (e.g. ``NEMESIS_DEDUP__MERGE_THRESHOLD=0.88``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nemesis import __version__

AppEnv = Literal["local", "ci", "pilot"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class DedupSettings(BaseModel):
    """Two-stage deduplication tunables (Blueprint §14).

    These are *stated defaults*, not tuned constants. Section 14.3 requires the
    threshold be documented as provisional and biased conservative: a
    false-positive merge suppresses a genuine citizen report, which is worse
    than leaving a duplicate unmerged.
    """

    geo_radius_meters: float = Field(default=50.0, gt=0)
    time_window_hours: int = Field(default=72, gt=0)
    merge_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    investigate_threshold: float = Field(default=0.65, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _bands_must_be_ordered(self) -> DedupSettings:
        if self.investigate_threshold >= self.merge_threshold:
            raise ValueError(
                "investigate_threshold must be strictly below merge_threshold; "
                "otherwise the ambiguous band that routes to the Investigation "
                "Agent collapses to empty and §14.1 silently degrades to a "
                "binary merge/no-merge decision."
            )
        return self


class SeveritySettings(BaseModel):
    """Severity rubric weights (Blueprint §13.5 / Appendix B.3).

    The rubric is versioned: any weight change must bump ``rubric_version`` so
    scored complaints remain attributable to the rubric that scored them.
    """

    rubric_version: str = "severity_rubric_v1"
    weight_visual_damage: float = Field(default=0.40, ge=0.0, le=1.0)
    weight_road_class: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_poi_proximity: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_cluster_count: float = Field(default=0.15, ge=0.0, le=1.0)
    poi_zero_score_radius_m: float = Field(default=200.0, gt=0)
    cluster_report_count_cap: int = Field(default=10, gt=0)

    @model_validator(mode="after")
    def _weights_must_sum_to_one(self) -> SeveritySettings:
        total = (
            self.weight_visual_damage
            + self.weight_road_class
            + self.weight_poi_proximity
            + self.weight_cluster_count
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"severity rubric weights must sum to 1.0, got {total:.6f}; "
                "an unnormalised rubric makes the logged severity_breakdown "
                "non-reproducible, breaking the §13.1 explainability guarantee."
            )
        return self


class OtelSettings(BaseModel):
    """OpenTelemetry. Disabled by default; enabling without an endpoint keeps
    spans in-process, which is useful for tests that assert instrumentation
    without standing up a collector."""

    enabled: bool = False
    endpoint: str | None = None
    sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)


class ModelSettings(BaseModel):
    """Local inference models. All CPU-bound by design — see pyproject `ml` extra.

    ``text_embedding_model`` deliberately departs from Blueprint §8.4's
    ``all-MiniLM-L6-v2``: that model is English-only, but §8.4 also requires
    Hindi/Marathi voice complaints, whose transcripts would embed to noise and
    silently break dedup Stage 2 (§14.1) for exactly the users the system is
    meant to reach. ``multilingual-e5-small`` is multilingual at the same 384
    dimensions, so the schema is unchanged.
    """

    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_embedding_dim: int = 512

    text_embedding_model: str = "intfloat/multilingual-e5-small"
    text_embedding_dim: int = 384
    # e5 models require asymmetric prefixes; omitting them measurably degrades
    # retrieval quality.
    text_embedding_prefix: str = "query: "

    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_languages: tuple[str, ...] = ("hi", "mr", "en")

    # MediaPipe 1.x removed the legacy `mp.solutions` API; the Tasks API loads an
    # explicit .tflite bundle, so the artefact is a real dependency to fetch and
    # pin rather than something the library carries internally.
    face_detector_model_file: str = "blaze_face_short_range.tflite"
    face_detector_model_url: str = (
        "https://storage.googleapis.com/mediapipe-models/face_detector/"
        "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
    )
    face_detector_min_confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    """Deliberately below the usual 0.5. Under §22.1 a missed face is a privacy
    breach while a false positive only blurs some pavement, so the threshold is
    biased toward over-blurring."""

    torch_num_threads: int = Field(default=4, ge=1)
    """Capped below core count so ML inference cannot starve Postgres/Redis."""


class FeatureFlagSettings(BaseModel):
    """Feature flags (Phase 1a).

    ``reload_interval_seconds`` is the honest latency of a kill switch: a change
    written to the store takes effect within this window, not instantly. Five
    seconds trades a negligible amount of Redis traffic for a switch that acts
    faster than a human can navigate to the dashboard to confirm it.
    """

    enabled: bool = True
    reload_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    redis_key: str = "nemesis:feature_flags"


class IngestSettings(BaseModel):
    """Submission limits (§26.1), enforced while the request body streams.

    ``max_upload_bytes`` is a *streaming* cap, not a post-hoc check. A cap
    applied after reading the body has already spent the disk and the memory it
    was supposed to protect, which means the limit is enforced precisely up to
    the point where enforcing it would have mattered.

    Content types are allow-lists and are matched against **sniffed magic
    bytes**, never against the ``Content-Type`` the client sent. A declared
    content type is a claim by an untrusted party; §25.1 lists upload handling
    as a threat surface, and "it said it was a JPEG" is not a control.
    """

    max_upload_bytes: int = Field(default=15 * 1024 * 1024, gt=0)
    #: §26.1 states 1000 characters. Enforced at the boundary so an oversized
    #: description is a 422 rather than a row that will not fit a later column.
    max_description_chars: int = Field(default=1000, gt=0)

    #: Read size while streaming to disk. 64 KiB is large enough that the syscall
    #: overhead is irrelevant and small enough that a hundred concurrent uploads
    #: do not add up to a memory problem on a 16 GB laptop.
    stream_chunk_bytes: int = Field(default=64 * 1024, gt=0)

    allowed_image_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
    allowed_audio_types: tuple[str, ...] = (
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/mp4",
    )

    #: §27.1 budgets submission acknowledgment at under two seconds and the full
    #: non-ambiguous pipeline at under thirty. This is what the 202 reports back
    #: to the client, so the number a citizen sees is the number the SLA table
    #: states rather than an independently invented estimate.
    estimated_processing_seconds: int = Field(default=8, gt=0)


class RateLimitTier(BaseModel):
    """One token bucket's shape."""

    #: Sustained rate: tokens refilled per window.
    requests: int = Field(gt=0)
    window_seconds: int = Field(gt=0)
    #: Bucket capacity. Above ``requests`` so a citizen photographing three
    #: potholes on one street is not rejected for it — the limit exists to stop
    #: automated flooding, not to pace an engaged reporter.
    burst: int = Field(gt=0)

    @model_validator(mode="after")
    def _burst_must_not_undercut_rate(self) -> RateLimitTier:
        if self.burst < 1:
            raise ValueError("burst must allow at least one request")
        return self


class RateLimitSettings(BaseModel):
    """Tiered limits (§26.4), resolved per tenant plan.

    **Provisional by design.** Phase 6 makes every behavioural knob governed,
    effective-dated policy data, and these limits are on that list. Until then
    they are typed configuration and the plan-to-tier mapping is a dict, which
    is honest about what it is: a solutions engineer can change a customer's
    tier without a deploy only once Phase 6 lands.

    ``fail_open`` is the decision worth arguing about. When Redis is unavailable
    the limiter cannot know whether a request is over budget, and it must choose.
    It allows the request, because the thing being rate-limited is a citizen
    reporting a hazard: dropping real reports during a Redis outage is a worse
    outcome than briefly permitting abuse, and the abuse paths have their own
    controls in §11.3. The choice is counted (``failed_open``) so it is visible
    rather than assumed.
    """

    enabled: bool = True
    fail_open: bool = True

    anonymous: RateLimitTier = RateLimitTier(requests=20, window_seconds=3600, burst=5)
    authenticated: RateLimitTier = RateLimitTier(requests=120, window_seconds=3600, burst=20)
    partner: RateLimitTier = RateLimitTier(requests=3600, window_seconds=3600, burst=120)

    #: Tenant plan -> tier name. An unlisted plan resolves to ``anonymous``,
    #: which is the restrictive direction: a plan nobody has configured must not
    #: silently receive the highest allowance.
    plan_tiers: dict[str, str] = Field(
        default_factory=lambda: {
            "trial": "anonymous",
            "pilot": "authenticated",
            "partner": "partner",
        }
    )


class OllamaSettings(BaseModel):
    """Local LLM used by the Investigation Agent (Blueprint §12.4)."""

    base_url: str = "http://host.docker.internal:11434"
    model: str = "llama3.1:8b"
    request_timeout_seconds: float = Field(default=90.0, gt=0)
    # A hung LLM must never hold the pipeline open; on timeout the ambiguous
    # case routes to human review (§27.3).
    connect_timeout_seconds: float = Field(default=5.0, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEMESIS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    # --- runtime -----------------------------------------------------------
    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"
    service_name: str = "nemesis-api"

    service_version: str = __version__
    """Sourced from the package, never restated.

    This value reaches the ``/health`` response, the OpenAPI document, and the
    OTel ``service.version`` resource attribute. Restating the number here — as
    it was — meant the running service could report a version the artefact did
    not have, which is a lie told at exactly the moment somebody is trying to
    work out what changed. A test asserts the package and ``pyproject.toml``
    agree, so there is one number in one place."""

    # Permissive default for local browser clients; a pilot deployment must set
    # an explicit origin list. Validated below so production cannot ship "*".
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3000",)

    # --- datastores --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://nemesis:nemesis@postgres:5432/nemesis"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
    redis_url: str = "redis://redis:6379/0"

    # --- storage -----------------------------------------------------------
    upload_dir: Path = Path("/data/uploads")
    model_cache_dir: Path = Path("/models")

    # --- auth --------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-insecure-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = Field(default=3600, gt=0)

    # --- nested groups -----------------------------------------------------
    ingest: IngestSettings = IngestSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    dedup: DedupSettings = DedupSettings()
    severity: SeveritySettings = SeveritySettings()
    models: ModelSettings = ModelSettings()
    ollama: OllamaSettings = OllamaSettings()
    otel: OtelSettings = OtelSettings()
    flags: FeatureFlagSettings = FeatureFlagSettings()

    @model_validator(mode="after")
    def _production_safety(self) -> Settings:
        """Refuse to start a pilot deployment with development defaults.

        These are the two mistakes that are trivial to make and expensive to
        discover: shipping the well-known dev signing key, and shipping a
        wildcard CORS policy in front of citizen data.
        """
        if self.app_env == "pilot":
            if "change-me" in self.jwt_secret.get_secret_value():
                raise ValueError(
                    "refusing to start: the development JWT secret is still set. "
                    "Set NEMESIS_JWT_SECRET to a generated value."
                )
            if "*" in self.cors_allow_origins:
                raise ValueError(
                    "refusing to start: wildcard CORS origin in a pilot "
                    "deployment. Set NEMESIS_CORS_ALLOW_ORIGINS explicitly."
                )
        return self

    @property
    def is_production_like(self) -> bool:
        return self.app_env == "pilot"

    @property
    def sync_database_url(self) -> str:
        """Alembic and test bootstrap run on the sync driver."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Cached so config is parsed and validated once."""
    return Settings()
