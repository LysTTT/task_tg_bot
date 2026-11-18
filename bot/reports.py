import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from bot.handlers.tasks import ACTIVE, card, keyboard
from core.models import Delivery, Task, TaskEvent, TaskStatus, TermsVersion, User
from core.services.terms import has_current_consent

logger = logging.getLogger(__name__)
OFFSETS = (1440, 60, 30, 0)


async def deliver(bot: Bot, sessions, user_id: int, key: str, now: datetime, message: str, markup=None):
    async with sessions.begin() as session:
        if not await has_current_consent(session, user_id):
            return
        claimed = await session.scalar(
            insert(Delivery)
            .values(user_id=user_id, key=key, expires_at=now + timedelta(days=2))
            .on_conflict_do_nothing()
            .returning(Delivery.key)
        )
        if claimed is not None:
            await bot.send_message(user_id, message, reply_markup=markup)


async def tick(bot: Bot, sessions) -> None:
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        overdue = (await session.scalars(
            select(Task).where(
                Task.status.in_((TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.POSTPONED)),
                Task.due_at <= now,
            ).limit(500).with_for_update(skip_locked=True)
        )).all()
        for task in overdue:
            session.add(TaskEvent(task_id=task.id, old_status=task.status, new_status=TaskStatus.OVERDUE))
            task.status = TaskStatus.OVERDUE
        await session.execute(delete(Delivery).where(Delivery.expires_at < now))
        users = (await session.scalars(
            select(User).join(TermsVersion, User.terms_version == TermsVersion.version)
            .where(TermsVersion.is_current.is_(True), User.terms_accepted_at.is_not(None), ~User.is_blocked)
        )).all()
        reminders = (await session.scalars(
            select(Task).where(
                Task.user_id.in_([user.telegram_id for user in users]),
                Task.status.in_(ACTIVE),
                Task.due_at > now - timedelta(minutes=30),
                Task.due_at <= now + timedelta(days=1),
            )
        )).all()

    zones = {user.telegram_id: ZoneInfo(user.timezone) for user in users}
    for task in reminders:
        for minutes in OFFSETS:
            scheduled = task.due_at - timedelta(minutes=minutes)
            if scheduled <= now < scheduled + timedelta(minutes=30):
                try:
                    await deliver(
                        bot, sessions, task.user_id,
                        f"reminder:{task.id}:{scheduled.isoformat()}:{minutes}", now,
                        "Напоминание\n\n" + card(task, zones[task.user_id]), keyboard(task),
                    )
                except TelegramAPIError as error:
                    logger.warning("Напоминание не отправлено: %s", type(error).__name__)

    for user in users:
        local = now.astimezone(zones[user.telegram_id])
        for label, report_time in (("Утренний", user.morning_report_time), ("Вечерний", user.evening_report_time)):
            if report_time is None:
                continue
            scheduled = datetime.combine(local.date(), report_time, local.tzinfo).astimezone(UTC)
            if not scheduled <= now < scheduled + timedelta(minutes=30):
                continue
            start = datetime.combine(local.date(), time.min, local.tzinfo)
            end = datetime.combine(local.date() + timedelta(days=1), time.min, local.tzinfo)
            async with sessions() as session:
                tasks = (await session.scalars(select(Task).where(
                    Task.user_id == user.telegram_id,
                    Task.due_at >= start,
                    Task.due_at < end,
                ).order_by(Task.due_at).limit(15))).all()
            lines = [f"{label} отчёт", *(
                f"/task {task.id} — {task.title[:140]}" for task in tasks
            )] if tasks else [f"{label} отчёт", "На сегодня нет задач со сроком."]
            try:
                await deliver(bot, sessions, user.telegram_id, f"report:{label}:{local.date()}", now, "\n".join(lines))
            except TelegramAPIError as error:
                logger.warning("Отчёт не отправлен: %s", type(error).__name__)


async def run_reports(bot: Bot, sessions) -> None:
    # ponytail: один цикл polling на установку. При росте нагрузки вынести доставку в воркер.
    while True:
        try:
            await tick(bot, sessions)
        except Exception:
            logger.exception("Не удалось обработать расписание")
        await asyncio.sleep(30)
