from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./stylebuddy.db"
    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_s: float = 60.0

    embedder: str = "hash"  # "hash" (offline) or "bge" (fastembed)
    catalog_source: str = "seed"  # "seed" or "hm"
    catalog_size: int = 5000
    data_dir: str = "data"

    max_upload_bytes: int = 8 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
