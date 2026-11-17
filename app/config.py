from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://assistant:assistant@localhost:5432/assistant"
    redis_url: str = "redis://localhost:6379/0"

    telegram_bot_token: str = ""
    telegram_proxy_url: str = ""
    telegram_allowed_ids: str = ""
    owner_telegram_id: int = 0

    llm_base_url: str = "https://opencode.ai/zen/v1"
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5"
    llm_vision_model: str = "claude-sonnet-5"

    s3_endpoint: str = "https://storage.yandexcloud.net"
    s3_region: str = "ru-central1"
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    web_token: str = ""

    @field_validator("owner_telegram_id", mode="before")
    @classmethod
    def _empty_to_zero(cls, value: object) -> object:
        return 0 if value in ("", None) else value

    @property
    def allowed_ids(self) -> set[int]:
        return {int(part) for part in self.telegram_allowed_ids.split(",") if part.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
