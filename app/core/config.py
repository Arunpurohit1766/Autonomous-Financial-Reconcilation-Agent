"""Central application configuration using Pydantic Settings."""

from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core App Settings
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    APP_NAME: str = "AFRA"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # Database & Storage
    DATABASE_URL: str = "sqlite:///./afra.db"
    STORAGE_DIR: str = "./.local_storage"

    # Ingestion Limits
    MAX_UPLOAD_MB: int = Field(default=10, description="Max upload size in MB")
    MAX_ROWS_PER_FILE: int = Field(default=50000, description="Max rows allowed per CSV")

    # Timeouts & Deadlines
    TOOL_TIMEOUT_SECONDS: int = Field(default=10, description="Per-tool timeout in seconds")
    JOB_TIMEOUT_SECONDS: int = Field(default=300, description="Overall job timeout in seconds")

    # AI Agent Circuit Breakers
    MAX_AGENT_STEPS: int = Field(default=5, description="Max LangGraph steps per investigation")
    MAX_LLM_CALLS_PER_EXCEPTION: int = Field(default=3, description="Max LLM invocations per exception")

    # LLM Provider Configuration
    LLM_PROVIDER: Literal["mock", "groq", "openai", "gemini"] = "mock"
    GROQ_API_KEY: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "openai/gpt-oss-120b"


# Global singleton instance
settings = Settings()
