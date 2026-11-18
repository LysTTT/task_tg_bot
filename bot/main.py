import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.handlers.drafts import router as drafts_router
from bot.handlers.start import router
from bot.handlers.tasks import router as tasks_router
from bot.reports import run_reports
from bot.terms_middleware import TermsMiddleware
from core.config import BotSettings
from core.database import create_db_engine
from core.logging_config import setup_logging


logger = logging.getLogger(__name__) #!!!!!!!!!


async def main() -> None:
    setup_logging()  #!!!!!!
    settings = BotSettings()

    engine = create_db_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = Dispatcher(
        session_factory=session_factory,
        settings=settings,
        events_isolation=SimpleEventIsolation(),
    )
    terms_middleware = TermsMiddleware()
    dispatcher.message.outer_middleware(terms_middleware)
    dispatcher.callback_query.outer_middleware(terms_middleware)
    dispatcher.include_router(router)
    dispatcher.include_router(drafts_router)
    dispatcher.include_router(tasks_router)

    logger.info("Запускаем Telegram-бота в режиме polling")
    reports = asyncio.create_task(run_reports(bot, session_factory))
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=["message", "callback_query"], handle_signals=False,
            close_bot_session=False,
        )      #!!!!!!!!!!!!!!!!!1
    finally:
        reports.cancel()
        with suppress(asyncio.CancelledError):
            await reports
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
