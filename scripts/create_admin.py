from __future__ import annotations

from shared.app.config import get_settings
from shared.app.logging import configure_logging, get_logger

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Admin creation is not implemented in stage 1; no admin model exists yet")


if __name__ == "__main__":
    main()
