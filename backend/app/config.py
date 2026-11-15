from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = ""

    # Admin Auth
    admin_username: str = "admin"
    admin_password: str = "admin"
    jwt_secret_key: str = "change-me-super-secret-jwt-key-for-bot-talker-2"
    jwt_expire_hours: int = 168  # 7 days

    # Database & Redis
    database_url: str = "postgresql+asyncpg://bot_talker:bot_talker_secret@postgres:5432/bot_talker"
    redis_url: str = "redis://redis:6379/0"

    # Default LLM Config (seeded into SystemConfig on first launch)
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_fast_model: str = "google/gemini-2.5-flash"
    llm_smart_model: str = "anthropic/claude-3.7-sonnet"

    embedding_base_url: str = "https://openrouter.ai/api/v1"
    embedding_api_key: str = ""
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536


@lru_cache
def get_settings() -> Settings:
    return Settings()

