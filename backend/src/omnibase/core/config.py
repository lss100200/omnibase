"""Application configuration.

Strongly-typed settings loaded from environment variables via Pydantic Settings.
Validates required fields at startup; missing JWT_SECRET / POSTGRES_PASSWORD etc.
will produce a clear error before the app boots.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from omnibase.rag.index_metadata import IndexVersion


class Environment(str, Enum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_prod(self) -> bool:
        return self == Environment.PRODUCTION

    @property
    def is_dev(self) -> bool:
        return self == Environment.DEVELOPMENT


class LogLevel(str, Enum):
    """Standard log levels (structlog-compatible)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """OmniBase application settings.

    Loaded from environment variables (or .env file) with strong validation.
    All fields are documented for the benefit of new contributors.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
        # Validate on assignment so mutation (e.g. tests) keeps invariants
        validate_assignment=True,
    )

    # ---------------------------------------------------------
    # Application
    # ---------------------------------------------------------
    env: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    app_name: str = "OmniBase"
    app_version: str = "0.1.0"

    # CORS - JSON array of origins, e.g. ["http://localhost:3000","http://localhost:3001"]
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        description='JSON array of allowed CORS origins, e.g. ["http://localhost:3000"]',
    )
    cors_allow_methods: tuple[str, ...] = Field(
        default=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
        description="Explicit CORS method allowlist.",
    )
    cors_allow_headers: tuple[str, ...] = Field(
        default=(
            "Accept",
            "Authorization",
            "Content-Type",
            "X-Platform-Admin-Token",
            "X-Request-Id",
        ),
        description="Explicit CORS request-header allowlist.",
    )

    # ---------------------------------------------------------
    # Database (PostgreSQL + pgvector)
    # ---------------------------------------------------------
    database_url: str = Field(
        ...,
        description="PostgreSQL connection URL (psycopg3 driver).",
        examples=["postgresql+psycopg://omnibase:secret@postgres:5432/omnibase"],
    )

    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout_seconds: int = Field(default=30, ge=5, le=300)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)

    # ---------------------------------------------------------
    # MinIO (object storage)
    # ---------------------------------------------------------
    minio_endpoint: str = Field(
        ..., description="MinIO endpoint, e.g. 'minio:9000' or 'localhost:9000'."
    )
    minio_access_key: str = Field(..., description="MinIO access key (root user).")
    minio_secret_key: str = Field(..., description="MinIO secret key (root password).")
    minio_bucket: str = Field(default="omnibase-files")
    minio_secure: bool = Field(default=False, description="Use HTTPS to connect to MinIO.")

    # ---------------------------------------------------------
    # Redis
    # ---------------------------------------------------------
    redis_url: str = Field(
        ...,
        description="Redis URL for refresh tokens and cache.",
        examples=["redis://redis:6379/0"],
    )
    redis_timeout_seconds: float = Field(default=1.0, gt=0, le=10)

    # ---------------------------------------------------------
    # Request rate limits (fixed window, Redis-backed)
    # ---------------------------------------------------------
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_fail_closed: bool = Field(
        default=True,
        description=(
            "Return 503 when Redis is unavailable instead of allowing protected requests. "
            "Set false only as an explicit local-development availability trade-off."
        ),
    )
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    auth_rate_limit_per_window: int = Field(default=5, ge=1, le=10_000)
    rag_rate_limit_per_window: int = Field(default=10, ge=1, le=10_000)
    upload_rate_limit_per_window: int = Field(default=10, ge=1, le=10_000)
    provider_test_rate_limit_per_window: int = Field(default=3, ge=1, le=100)

    # ---------------------------------------------------------
    # Local AI model boundary (no implicit network by default)
    # ---------------------------------------------------------
    model_cache_dir: str = Field(default="/app/models")
    model_download_enabled: bool = Field(
        default=False,
        description="Allow model libraries to contact remote registries when cache is missing.",
    )
    reranker_model_path: str = Field(default="/app/models/bge-reranker-v2-m3")

    # ---------------------------------------------------------
    # JWT / Authentication
    # ---------------------------------------------------------
    jwt_secret: str = Field(
        ...,
        min_length=32,
        description=(
            "Secret used to sign JWT tokens. Must be at least 32 characters. "
            "Generate with: python -c 'import secrets; print(secrets.token_hex(32))'"
        ),
    )
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)

    # ---------------------------------------------------------
    # Privileged API exposure (fail closed)
    # ---------------------------------------------------------
    tenant_management_api_enabled: bool = Field(
        default=False,
        description="Enable cross-tenant management routes. Requires PLATFORM_ADMIN_TOKEN.",
    )
    platform_admin_token: str | None = Field(
        default=None,
        min_length=32,
        description="Shared bootstrap token for platform-admin-only management routes.",
    )

    # ---------------------------------------------------------
    # File uploads
    # ---------------------------------------------------------
    max_upload_size_mb: int = Field(default=50, ge=1, le=500)
    request_body_overhead_kb: int = Field(
        default=1024,
        ge=64,
        le=8192,
        description=(
            "Extra request-envelope allowance above MAX_UPLOAD_SIZE_MB for multipart headers."
        ),
    )
    # MIME-type allowlist for document uploads
    allowed_mime_types: tuple[str, ...] = Field(
        default=(
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/markdown",
        )
    )

    # ---------------------------------------------------------
    # LLM (Phase 1: RAG Q&A via OpenAI-compatible API)
    # ---------------------------------------------------------
    llm_api_key: str = Field(
        default="", description="API key for LLM provider (DeepSeek/Zhipu/OpenAI)"
    )
    llm_api_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="Base URL for OpenAI-compatible LLM API",
    )
    llm_model: str = Field(default="deepseek-chat", description="LLM model name")
    llm_provider: str = Field(
        default="openai_compatible",
        description="Logical provider identity recorded by the internal Model Gateway",
    )
    provider_credential_encryption_key: str = Field(
        default="",
        description=(
            "Base64url-encoded 32-byte AES-GCM key for user model-provider credentials. "
            "Production and staging require an independent key."
        ),
    )
    provider_endpoint_allowlist: tuple[str, ...] = Field(
        default=(
            "api.deepseek.com",
            "open.bigmodel.cn",
            "dashscope.aliyuncs.com",
            "api.openai.com",
        ),
        description="Exact HTTPS host allowlist for user-configured model providers.",
    )

    # ---------------------------------------------------------
    # Embedding index migration (Phase 1.6)
    # ---------------------------------------------------------
    embedding_index_version: IndexVersion = Field(
        default=IndexVersion.V1,
        description="Embedding index contract used by the primary write lane.",
    )
    embedding_shadow_index_version: IndexVersion | None = Field(
        default=None,
        description=(
            "Optional second embedding index write lane. Disabled by default; "
            "must differ from EMBEDDING_INDEX_VERSION when enabled."
        ),
    )

    @model_validator(mode="after")
    def validate_embedding_write_lanes(self) -> Settings:
        """Reject invalid fail-closed feature configuration."""
        if self.embedding_index_version is not IndexVersion.V1:
            raise ValueError(
                "EMBEDDING_INDEX_VERSION must remain v1 until the explicit cutover gate is implemented"
            )
        if self.embedding_shadow_index_version == self.embedding_index_version:
            raise ValueError(
                "EMBEDDING_SHADOW_INDEX_VERSION must differ from EMBEDDING_INDEX_VERSION"
            )
        if self.tenant_management_api_enabled and not self.platform_admin_token:
            raise ValueError(
                "PLATFORM_ADMIN_TOKEN is required when TENANT_MANAGEMENT_API_ENABLED is true"
            )
        return self

    # ---------------------------------------------------------
    # Derived helpers
    # ---------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_size_bytes(self) -> int:
        """Max upload size in bytes (derived from MB)."""
        return self.max_upload_size_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_request_body_size_bytes(self) -> int:
        """Global HTTP body ceiling including multipart envelope overhead."""
        return self.max_upload_size_bytes + self.request_body_overhead_kb * 1024

    @property
    def cors_origin_strings(self) -> list[str]:
        """CORS origins as plain strings (for FastAPI middleware)."""
        return [o.rstrip("/") for o in self.cors_origins]

    @property
    def is_production(self) -> bool:
        return self.env.is_prod

    @property
    def is_development(self) -> bool:
        return self.env.is_dev


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance.

    Cached for performance; call `get_settings.cache_clear()` in tests
    to pick up environment changes.
    """
    return Settings()  # type: ignore[call-arg]


# Convenience module-level alias (lazy on first access)
def settings() -> Settings:
    """Convenience accessor. Use `from omnibase.core.config import settings`."""
    return get_settings()
