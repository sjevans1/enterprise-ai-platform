"""Application configuration. All settings are loaded from environment variables.

Secrets never live in source control. In development, use a .env file.
In production, use environment variables or a secrets manager.

This config aligns with the existing codebase (security.py, factory.py, providers).
Attribute names use snake_case to match the existing code.
"""
import secrets
import warnings
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────
    app_name: str = "Enterprise AI Platform"
    app_env: Literal["development", "production", "test"] = "development"
    debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # ── Database (application's own account — separate from customer DBs) ──
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    postgres_db: str = "enterprise_ai"
    postgres_user: str = "app_admin"
    postgres_password: SecretStr = SecretStr("changeme_dev")
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 40

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}"
            f":{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Security / Encryption ────────────────────────────────────────
    # Master key for Fernet encryption of credentials at rest.
    # Must be provided in production. In dev, a random ephemeral key is generated.
    master_key: SecretStr = SecretStr("dev-master-key-change-in-production-32bytes!")

    # JWT settings
    jwt_secret_key: SecretStr = SecretStr("dev-jwt-secret-key-change-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    jwt_refresh_expire_minutes: int = 43200  # 30 days

    @property
    def effective_jwt_secret(self) -> str:
        """Return JWT secret key, generating one if not set (dev only)."""
        val = self.jwt_secret_key.get_secret_value()
        if not val:
            if self.app_env == "production":
                raise ValueError("JWT_SECRET_KEY must be set in production")
            val = secrets.token_urlsafe(32)
        return val

    @property
    def effective_master_key(self) -> str:
        """Return master key, generating one if not set (dev only)."""
        val = self.master_key.get_secret_value()
        if not val:
            if self.app_env == "production":
                raise ValueError("MASTER_KEY must be set in production")
            val = secrets.token_urlsafe(32)
        return val

    # Bootstrap
    bootstrap_token: SecretStr = SecretStr("")

    def generate_bootstrap_token(self) -> str:
        """Generate and cache a one-time bootstrap token."""
        token = secrets.token_urlsafe(32)
        self.bootstrap_token = SecretStr(token)
        return token

    def verify_bootstrap_token(self, token: str) -> bool:
        expected = self.bootstrap_token.get_secret_value()
        if not expected:
            return False
        return secrets.compare_digest(token, expected)

    # ── Session Security ────────────────────────────────────────────
    session_cookie_name: str = "session_id"
    session_cookie_secure: bool = False
    session_cookie_httponly: bool = True
    session_cookie_samesite: str = "lax"

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    # ── Password Policy ─────────────────────────────────────────────
    min_password_length: int = 12

    # ── Model Gateway / Providers ──────────────────────────────────
    default_provider: str = "hermes"
    default_model: str = "hermes-agent"
    request_timeout_seconds: int = 120
    default_temperature: float = 0.7
    default_max_tokens: int | None = 2048

    # REMOTE_MODEL_ALLOWED=false by default. When false, only LOCAL inference is used.
    remote_model_allowed: bool = False

    # Hermes API server (development reference: http://127.0.0.1:8642/v1)
    hermes_api_base: str = "http://127.0.0.1:8642/v1"
    hermes_api_key: SecretStr = SecretStr("")

    # Ollama API
    ollama_api_base: str = "http://127.0.0.1:11434/v1"
    ollama_api_key: SecretStr = SecretStr("")
    ollama_model: str = "llama3.2:3b"

    # Generic OpenAI-compatible provider
    openai_compatible_base: str = ""
    openai_compatible_key: SecretStr = SecretStr("")
    openai_compatible_model: str = ""

    # ── Embedding ───────────────────────────────────────────────────
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    embedding_cache_dir: str = ".cache/embeddings"
    embedding_batch_size: int = 32

    # ── Files / Documents ───────────────────────────────────────────
    upload_dir: str = ".data/uploads"
    max_upload_size_mb: int = 50
    allowed_upload_extensions: list[str] = [
        ".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".html",
    ]

    # ── Structured Data Query ────────────────────────────────────────
    # Customer database connection (separate from application database).
    # In production, this uses a read-only database account.
    structured_data_db_url: str = ""
    structured_data_schema: str = "public"
    structured_data_max_rows: int = 1000
    structured_data_query_timeout: int = 30

    # ── Job Queue ────────────────────────────────────────────────────
    job_max_retries: int = 3
    job_timeout_seconds: int = 600

    # ── Audit ────────────────────────────────────────────────────────
    audit_retention_days: int = 365
    full_prompt_logging_enabled: bool = False

    model_config_validation: bool = True


settings = Settings()

# Warn in development if JWT secret is not set
if not settings.jwt_secret_key.get_secret_value() and settings.app_env == "development":
    warnings.warn(
        "JWT_SECRET_KEY not set — ephemeral key will be used. "
        "Set JWT_SECRET_KEY=env:JWT_SECRET_KEY for persistent authentication.",
        stacklevel=2,
    )
