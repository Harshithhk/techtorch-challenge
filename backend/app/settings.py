"""Application settings, loaded from environment and the repo-root .env file.

Business logic does not live here. Paths default relative to the repo root,
so the backend can be started from any working directory.
"""
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/settings.py -> parents[2] is the repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed settings. Every field can be overridden by an env var of the same name (upper-case)."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # LLM provider (Anthropic). Empty key = agent cannot run; tests use a fake model.
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-5-5"
    guard_model: str = "claude-haiku-5-5"

    # Postgres for users, thread mapping and the LangGraph checkpointer.
    database_url: str = "postgresql://rla:rla@localhost:5433/rla"

    # Investigation "as of" date. None means today. Can be changed per conversation in chat.
    as_of_date: date | None = None

    # Contract term is not in the data, so it is assumed (see plan section 2).
    default_term_months: int = 12

    # Source data is read-only. Sandbox is the only writable location.
    data_dir: Path = PROJECT_ROOT / "data" / "technical-case" / "data"
    sandbox_dir: Path = PROJECT_ROOT / "data" / "sandbox"

    # Langfuse is a no-op when keys are empty (offline dev and tests).
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached). Input: none. Output: Settings."""
    return Settings()
