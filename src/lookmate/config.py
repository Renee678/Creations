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
    catalog_source: str = "seed"  # "seed", "asos", "polyvore", "amazon", "hm", or a mix like "asos,polyvore,amazon"
    catalog_size: int = 5000
    amazon_max_items: int = 40000  # the "amazon" source's own share, on top of catalog_size
    data_dir: str = "data"

    # No-Docker local mode: run the worker as a thread inside the API process.
    inline_worker: bool = False
    # "background": the API imports a new catalog on a thread (one-process local mode).
    # "external": a separate one-off process does it (`python -m lookmate.catalog_import`, the compose
    # importer service), so a memory-hungry import can never take the API down with it.
    catalog_import: str = "background"

    max_upload_bytes: int = 8 * 1024 * 1024

    # Virtual try-on (IDM-VTON on Replicate). Empty token: the UI shows a collage preview instead.
    replicate_api_token: str = ""
    fashn_api_key: str = ""        # preferred try-on model when set: warm, seconds per garment
    fashn_model: str = "tryon-max"  # keeps the face and does shoes; "tryon-v1.6" is cheaper, clothes only
    # Per clothing step: "balanced" (about 8 s), "quality" (sharper hands, slower: three pieces took nearly a minute)
    # or "performance" (fastest). Pieces are put on one after another, so this multiplies.
    fashn_mode: str = "balanced"
    tryon_model: str = "google/nano-banana"  # or "cuuupid/idm-vton" (cheaper, slow cold starts)
    model_gen_model: str = "google/nano-banana-pro"  # draws "My model" once per user: the best at keeping a face
    shop_fetch_impersonate: bool = True  # retry a refused shop photo as Chrome (curl_cffi, when installed)
    shop_fetch_http2: bool = False  # retry a refused shop photo over HTTP/2 (looks more like a browser to a CDN)
    daily_tryon_limit: int = 30    # rendered try-ons per UTC day across all users (0 = unlimited)

    # Cost guardrails for a public deployment.
    access_code: str = ""          # if set, uploads need the X-Access-Code header
    daily_stylist_limit: int = 200  # stylist calls per UTC day (one per new lookbook page; 0 = unlimited)
    daily_look_limit: int = 200    # new image analyses per UTC day across all users (0 = unlimited)


@lru_cache
def get_settings() -> Settings:
    return Settings()
