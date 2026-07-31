"""Application settings.

All configuration is environment driven (12-factor).  Settings are validated at
import time so a misconfigured deployment fails fast at boot rather than at the
first request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.dburl import async_url, redact, sync_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Vutto Auctions"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- Security -----------------------------------------------------------
    # Development-only default. `_secret_strength` below refuses to boot in
    # production if this value survives, so it cannot ship by accident.
    secret_key: str = "dev-only-secret-change-me-0123456789abcdef0123456789abcdef"  # noqa: S105
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000
    # `NoDecode` is load-bearing. pydantic-settings JSON-decodes list-typed
    # fields straight out of the environment, *before* any validator runs, so
    # the natural `CORS_ORIGINS=https://a.com,https://b.com` blows up at import
    # with a JSONDecodeError — a boot crash in every deployment that sets it.
    # NoDecode hands us the raw string and lets `_split_origins` below do the
    # parsing.  A JSON array still works, for anyone who prefers it.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- Datastores ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://vutto:vutto@localhost:5432/vutto"
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # --- Auction engine -----------------------------------------------------
    anti_snipe_window_seconds: int = 120
    anti_snipe_extension_seconds: int = 120
    anti_snipe_max_extensions: int = 20
    auction_tick_seconds: float = 1.0
    outbox_relay_batch: int = 200

    # --- Rate limiting ------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_bid_per_minute: int = 30
    rate_limit_auth_per_minute: int = 10
    rate_limit_default_per_minute: int = 300

    # --- Observability ------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "vutto-api"
    metrics_enabled: bool = True

    # --- Seeds --------------------------------------------------------------
    seed_admin_email: str = "admin@vutto.example.com"
    seed_admin_password: str = "Admin@12345"  # noqa: S105 - demo seed only
    seed_demo_password: str = "Demo@12345"  # noqa: S105 - demo seed only

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept `a,b,c`, a JSON array, or an already-parsed list.

        Origins are normalised by stripping trailing slashes: `https://x.dev/`
        and `https://x.dev` are the same origin to a browser, but a raw string
        comparison in the CORS middleware would reject the mismatch, producing a
        failure that looks like a server bug and is really a stray character.
        """
        if isinstance(v, str):
            raw = v.strip()
            if raw.startswith("["):
                import json

                try:
                    v = json.loads(raw)
                except ValueError:
                    v = raw
            else:
                v = raw.split(",")
        if isinstance(v, list | tuple):
            return [str(o).strip().rstrip("/") for o in v if str(o).strip()]
        return v

    @field_validator("secret_key")
    @classmethod
    def _secret_strength(cls, v: str, info) -> str:
        env = (info.data or {}).get("app_env")
        if env == "production" and ("dev-only" in v or len(v) < 32):
            raise ValueError("SECRET_KEY must be a strong unique value in production")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def async_database_url(self) -> str:
        """The URL the application connects with (asyncpg).

        Normalised, so a connection string pasted straight from Neon, Supabase,
        Render or Railway works without hand-editing its SSL parameters.
        """
        return async_url(self.database_url)

    @property
    def sync_database_url(self) -> str:
        """The URL Alembic migrates with (psycopg2, no event loop required)."""
        return sync_url(self.database_url)

    @property
    def safe_database_url(self) -> str:
        """Credential-free form, for logs and error messages."""
        return redact(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
