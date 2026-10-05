"""Force a trend refresh now: `python -m lookmate.trends_cli`."""

import logging

from .config import get_settings
from .db import SessionLocal
from .runtime import build_runtime
from .services.trends import refresh


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rt = build_runtime(get_settings(), import_catalog=False)
    with SessionLocal() as session:
        print("stored trend batch", refresh(session, rt.trend_researcher, rt.data_dir))


if __name__ == "__main__":
    main()
