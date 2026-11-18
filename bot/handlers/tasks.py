from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.models import Task, TaskStatus
from core.services.tasks import change_status, get_task
from core.services.users import get_user

router = Router()
router.message.filter(F.chat.type == "private", F.from_user)
router.callback_query.filter(F.message.chat.type == "private")

STATUSES = {
    TaskStatus.NEW: "Новая",
    TaskStatus.IN_PROGRESS: "В работе",
    TaskStatus.COMPLETED: "Выполнена",
    TaskStatus.OVERDUE: "Просрочена",
    TaskStatus.POSTPONED: "Отложена",
    TaskStatus.CANCELLED: "Отменена",
}
USER_STATUSES = (
    TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED,
    TaskStatus.POSTPONED, TaskStatus.CANCELLED,
)
ACTIVE = (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.POSTPONED, TaskStatus.OVERDUE)
LISTS = ("today", "tomorrow", "week", "overdue", "completed", "all", "search")
PAGE_SIZE = 8


class TaskAction(CallbackData, prefix="task"):
    task_id: int
    status: TaskStatus


def card(task: Task, zone: ZoneInfo) -> str:
    deadline = task.due_at.astimezone(zone).strftime("%d.%m.%Y %H:%M") if task.due_at else "без срока"
    return f"Задача №{task.id}\n{task.title}\nСтатус: {STATUSES[task.status]}\nСрок: {deadline}"


def keyboard(task: Task) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=STATUSES[status],
            callback_data=TaskAction(task_id=task.id, status=status).pack(),
        )]
        for status in USER_STATUSES if status != task.status
    ])


@router.message(Command("task"))
async def show_task(message: Message, command: CommandObject, session_factory: async_sessionmaker[AsyncSession]):
    try:
        task_id = int(command.args or "")
        if not 0 < task_id < 2**63:
            raise ValueError
    except ValueError:
        await message.answer("Укажи номер задачи. Пример: /task 12")
        return
    async with session_factory() as session:
        user = await get_user(session, message.from_user.id)
        task = await get_task(session, user_id=message.from_user.id, task_id=task_id)
    if task is None or user is None:
        await message.answer("Задача не найдена.")
        return
    await message.answer(card(task, ZoneInfo(user.timezone)), reply_markup=keyboard(task))


@router.message(Command("deadline"))
async def set_deadline(message: Message, command: CommandObject, session_factory: async_sessionmaker[AsyncSession]):
    try:
        raw_id, raw_time = (command.args or "").split(maxsplit=1)
        task_id = int(raw_id)
        if not 0 < task_id < 2**63:
            raise ValueError
        local_time = None if raw_time == "off" else datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Пример: /deadline 12 2026-12-01 18:00\nУбрать срок: /deadline 12 off")
        return
    async with session_factory.begin() as session:
        user = await get_user(session, message.from_user.id)
        task = await get_task(session, user_id=message.from_user.id, task_id=task_id, lock=True)
        if task is not None and user is not None:
            zone = ZoneInfo(user.timezone)
            task.due_at = local_time.replace(tzinfo=zone).astimezone(UTC) if local_time else None
            if task.status == TaskStatus.OVERDUE:
                await change_status(session, user_id=user.telegram_id, task_id=task.id, status=TaskStatus.NEW)
    if task is None or user is None:
        await message.answer("Задача не найдена.")
        return
    await message.answer(card(task, zone), reply_markup=keyboard(task))


@router.message(Command(*LISTS))
async def list_tasks(message: Message, command: CommandObject, session_factory: async_sessionmaker[AsyncSession]):
    mode = command.command
    args = (command.args or "").strip()
    try:
        page = 1 if mode == "search" else int(args or "1")
        if not 1 <= page <= 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer(f"Укажи номер страницы. Пример: /{mode} 2")
        return
    if mode == "search" and not args:
        await message.answer("Пример: /search Купить хлеб")
        return

    async with session_factory() as session:
        user = await get_user(session, message.from_user.id)
        if user is None:
            return
        zone = ZoneInfo(user.timezone)
        now = datetime.now(UTC)
        query = select(Task).where(Task.user_id == user.telegram_id)
        if mode in ("today", "tomorrow", "week"):
            day = now.astimezone(zone).date() + timedelta(days=int(mode == "tomorrow"))
            start = datetime.combine(day, time.min, zone)
            end = datetime.combine(day + timedelta(days=7 if mode == "week" else 1), time.min, zone)
            query = query.where(Task.due_at >= start, Task.due_at < end)
        elif mode == "overdue":
            query = query.where(Task.due_at < now, Task.status.in_(ACTIVE))
        elif mode == "completed":
            query = query.where(Task.status == TaskStatus.COMPLETED)
        elif mode == "search":
            query = query.where(Task.title.icontains(args, autoescape=True))
        tasks = (await session.scalars(query.order_by(Task.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1))).all()
    if not tasks:
        await message.answer("Задач не найдено.")
        return
    lines = [
        f"/task {task.id} — {' '.join(task.title.split())[:160]} ({STATUSES[task.status]})"
        for task in tasks[:PAGE_SIZE]
    ]
    if len(tasks) > PAGE_SIZE and mode != "search":
        lines.append(f"Следующая страница: /{mode} {page + 1}")
    elif len(tasks) > PAGE_SIZE:
        lines.append("Уточни запрос, чтобы увидеть меньше результатов.")
    await message.answer("\n".join(lines))


@router.callback_query(TaskAction.filter())
async def update_task(callback: CallbackQuery, callback_data: TaskAction, session_factory: async_sessionmaker[AsyncSession]):
    if callback_data.status not in USER_STATUSES or not 0 < callback_data.task_id < 2**63:
        await callback.answer("Это действие недоступно.", show_alert=True)
        return
    async with session_factory.begin() as session:
        task = await change_status(
            session, user_id=callback.from_user.id,
            task_id=callback_data.task_id, status=callback_data.status,
        )
        user = await get_user(session, callback.from_user.id)
    if task is None or user is None:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    await callback.answer("Статус сохранён.")
    if isinstance(callback.message, Message):
        new_text = card(task, ZoneInfo(user.timezone))
        if callback.message.text != new_text:
            await callback.message.edit_text(new_text, reply_markup=keyboard(task))


@router.message(F.document | F.photo | F.video | F.animation | F.audio | F.video_note)
async def unsupported_attachment(message: Message):
    await message.answer("Опиши задачу текстом или отправь голосовое сообщение.")
