import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import Settings
from core.database import create_db_engine
from core.logging_config import setup_logging


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("Настройки загружены")
    logger.info(
        "Адрес PostgreSQL: %s:%s",
        settings.postgres_host,
        settings.postgres_port,
    )

    engine = create_db_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT current_database(), current_user")
            )
            database_name, database_user = result.one()
            logger.info(
                "Подключение к БД %s успешно, пользователь: %s",
                database_name,
                database_user,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
