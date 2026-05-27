from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./data/weather.db"
    poll_interval_seconds: int = 3600
    open_meteo_base_url: str = "https://api.open-meteo.com"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
