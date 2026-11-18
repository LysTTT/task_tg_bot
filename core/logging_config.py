import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "taskbot.log"
LOG_ZONE = ZoneInfo("Europe/Moscow")


class MonthlyFileHandler(logging.FileHandler):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, encoding="utf-8", delay=True)
        self.path = path
        self.month = None

    def clear_previous_month(self, now: datetime) -> None:
        month = (now.year, now.month)
        with self.lock:
            if self.month == month:
                return
            try:
                modified_at = self.path.stat().st_mtime
            except FileNotFoundError:
                pass
            else:
                modified = datetime.fromtimestamp(modified_at, LOG_ZONE)
                if (modified.year, modified.month) < month:
                    if self.stream is not None:
                        self.stream.close()
                        self.stream = None
                    self.path.write_bytes(b"")
            self.month = month

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.clear_previous_month(datetime.now(LOG_ZONE))
            super().emit(record)
        except OSError:
            self.handleError(record)


async def clear_logs_monthly(handler: MonthlyFileHandler) -> None:
    while True:
        now = datetime.now(LOG_ZONE)
        try:
            handler.clear_previous_month(now)
        except OSError:
            logging.getLogger(__name__).warning("Не удалось очистить журнал. Повторим через минуту.")
            await asyncio.sleep(60)
            continue
        next_month = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=LOG_ZONE)
        await asyncio.sleep((next_month - now).total_seconds())


def setup_logging() -> MonthlyFileHandler:
    log_file = MonthlyFileHandler(LOG_PATH)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    formatter.converter = lambda timestamp: datetime.fromtimestamp(timestamp, LOG_ZONE).timetuple()
    handlers = (logging.StreamHandler(), log_file)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )
    return log_file
