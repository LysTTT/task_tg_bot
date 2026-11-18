import logging
import os
from datetime import datetime, timedelta

from core.logging_config import LOG_ZONE, MonthlyFileHandler


def test_log_clears_previous_month_and_appends_after_restart(tmp_path):
    path = tmp_path / "logs" / "taskbot.log"
    now = datetime.now(LOG_ZONE)
    previous_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    first = MonthlyFileHandler(path)
    second = MonthlyFileHandler(path)
    try:
        first.emit(logging.LogRecord("taskbot", logging.INFO, "", 0, "Старый лог", (), None))
        os.utime(path, (previous_month.timestamp(), previous_month.timestamp()))
        second.clear_previous_month(now)
        assert path.read_text(encoding="utf-8") == ""

        second.emit(logging.LogRecord("taskbot", logging.INFO, "", 0, "Первая запись", (), None))
        first.emit(logging.LogRecord("taskbot", logging.INFO, "", 0, "Вторая запись", (), None))
        assert path.read_text(encoding="utf-8").splitlines() == ["Первая запись", "Вторая запись"]
    finally:
        first.close()
        second.close()
