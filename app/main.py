import logging

from core.config import Settings
from core.logging_config import setup_logging


logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("Настройки загружены")
    logger.info(
        "Адрес PostgreSQL: %s:%s",
        settings.postgres_host,
        settings.postgres_port,
    )


if __name__ == "__main__":
    main()
