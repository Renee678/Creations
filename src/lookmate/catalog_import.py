"""Import the configured catalog in its own process: `python -m lookmate.catalog_import`.

Compose runs this as the one-off `importer` service, under a memory limit, so a large first import
(ASOS + Polyvore + Amazon, embedded with bge) can't take the API down on a small server. The API keeps
serving its current catalog and swaps in the new one when this signals it through Redis. Progress is
resumable: downloads, the filtered Amazon rows and every embedded chunk are cached under data/cache,
so a crashed run picks up where it stopped. Exits 0 straight away when the stored catalog is current.
"""

import logging
import time
from pathlib import Path

from .catalog.embedder import make_embedder
from .catalog.importer import catalog_is_current, ensure_catalog
from .config import get_settings
from .db import Base, SessionLocal, engine
from .runtime import CATALOG_VERSION, make_redis

log = logging.getLogger("lookmate.catalog_import")


def run(settings, redis_client=None) -> bool:
    """True if a new catalog was stored (and announced), False if the stored one was already current."""
    Base.metadata.create_all(engine)
    embedder = make_embedder(settings.embedder)
    with SessionLocal() as session:
        if catalog_is_current(session, embedder, settings.catalog_source):
            log.info("catalog %s/%s is current; nothing to import", settings.catalog_source, embedder.name)
            return False
        started = time.monotonic()
        n = ensure_catalog(session, embedder, settings.catalog_source, Path(settings.data_dir), settings.catalog_size,
                           settings.amazon_max_items)
    (redis_client or make_redis(settings.redis_url)).set(CATALOG_VERSION, str(time.time()))
    log.info("stored %d products in %.0f s; the API and worker reload them", n, time.monotonic() - started)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(get_settings())


if __name__ == "__main__":
    main()
