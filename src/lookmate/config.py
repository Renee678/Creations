from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./lookmate.db"
    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_timeout_s: float = 60.0

    embedder: str = "hash"  # "hash" (offline) or "bge" (fastembed)
    catalog_source: str = "seed"  # "seed", "asos", "polyvore", "hm", or a mix like "asos,polyvore"
    catalog_size: int = 5000
    data_dir: str = "data"

    # No-Docker local mode: run the worker as a thread inside the API process.
    inline_worker: bool = False

    max_upload_bytes: int = 8 * 1024 * 1024

    # Virtual try-on (IDM-VTON on Replicate). Empty token: the UI shows a collage preview instead.
    replicate_api_token: str = ""
    fashn_api_key: str = ""        # preferred try-on model when set: warm, seconds per garment
    tryon_model: str = "google/nano-banana"  # or "cuuupid/idm-vton" (cheaper, slow cold starts)
    daily_tryon_limit: int = 30    # rendered try-ons per UTC day across all users (0 = unlimited)

    # Cost guardrails for a public deployment.
    access_code: str = ""          # if set, uploads need the X-Access-Code header
    daily_stylist_limit: int = 200  # stylist calls per UTC day (one per new lookbook page; 0 = unlimited)
    daily_look_limit: int = 200    # new image analyses per UTC day across all users (0 = unlimited)


@lru_cache
def get_settings() -> Settings:
    return Settings()
