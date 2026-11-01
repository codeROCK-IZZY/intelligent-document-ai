"""Application configuration from ``DOCAI_*`` environment variables (or a git-ignored ``.env``).

Nothing secret has a default: the app refuses to start without ``DOCAI_JWT_SECRET``.
The Anthropic key is read by the SDK from ``ANTHROPIC_API_KEY``; when it is absent the
platform runs in offline *extractive* mode.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCAI_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://docai:docai@localhost:5432/docai"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- storage & upload limits ---
    file_dir: Path = Path("./var/files")
    s3_bucket: str | None = None
    max_upload_mb: int = 25
    max_pages: int = 300
    max_docx_uncompressed_mb: int = 200

    # --- AI providers ---
    llm_provider: Literal["auto", "claude", "extractive"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_fallbacks: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    ocr_languages: str = "eng+fra+spa"

    # --- retrieval ---
    retrieval_top_k: int = 8
    min_relevance: float = 0.35  # below this hybrid score, answer "not found" without calling the LLM

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("DOCAI_JWT_SECRET must be at least 32 characters")
        return v

    @property
    def llm_mode(self) -> Literal["claude", "extractive"]:
        """Resolve ``auto``: Claude when an API key is configured, otherwise extractive."""
        if self.llm_provider == "auto":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "extractive"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
