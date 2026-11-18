from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiohttp import ClientError
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.handlers.tasks import card, keyboard
from core.config import BotSettings
from core.services.gemini import TaskDraft, parse_task
from core.services.tasks import create_task
from core.services.users import get_user

router = Router()
router.message.filter(F.chat.type == "private", F.from_user)
router.callback_query.filter(F.message.chat.type == "private")
VOICE_LIMIT = 5_000_000
DRAFT_LIFETIME = 900


def buttons(action: str, nonce: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"{action}:{nonce}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"discard_ai:{nonce}"),
    ]])


async def pending(callback: CallbackQuery, state: FSMContext) -> dict | None:
    data = (await state.get_data()).get("ai_task", {})
    if (
        callback.data.rpartition(":")[2] != data.get("nonce")
        or monotonic() >= data.get("expires", 0)
    ):
        await callback.answer("Открой новый черновик.", show_alert=True)
        return None
    return data


async def prepare_draft(
    message: Message, settings: BotSettings, state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession], *,
    user_id: int, text: str = "", voice: bytes | None = None,
) -> None:
    async with session_factory() as session:
        user = await get_user(session, user_id)
    if user is None:
        await message.answer("Отправь /start, чтобы создать профиль.")
        return
    zone = ZoneInfo(user.timezone)
    try:
        draft = await parse_task(settings, datetime.now(zone), user.timezone, text=text, voice=voice)
    except (ClientError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        await message.answer("Не удалось разобрать сообщение. Попробуй отправить его ещё раз чуть позже.")
        return
    if draft.kind == "not_task":
        await message.answer("Не нашёл задачи. Напиши, что нужно сделать. Например, «Купить хлеб».")
        return
    if draft.kind == "unclear_deadline":
        await message.answer(
            "Не удалось определить срок. Отправь задачу ещё раз с датой и временем. "
            "Например, «Купить хлеб 18 декабря в 6 вечера»."
        )
        return
    nonce = uuid4().hex
    await state.update_data(ai_task={
        "nonce": nonce, "expires": monotonic() + DRAFT_LIFETIME,
        "draft": draft.model_dump_json(),
    })
    deadline = draft.due_at.astimezone(zone).strftime("%d.%m.%Y %H:%M") if draft.due_at else "без срока"
    await message.answer(
        f"Черновик\n{draft.title}\nСрок: {deadline}\nПроверь данные и сохрани задачу.",
        reply_markup=buttons("save_ai", nonce, "Сохранить"),
    )


@router.message(Command("ai"))
@router.message(F.text, ~F.text.startswith("/"))
async def draft_text(
    message: Message, settings: BotSettings,
    state: FSMContext, session_factory: async_sessionmaker[AsyncSession],
    command: CommandObject | None = None,
) -> None:
    text = (command.args or "") if command is not None else message.text
    text = (text or "").strip()
    if not settings.gemini_api_key.get_secret_value():
        await message.answer("Распознавание задач пока недоступно. Для него нужен ключ Gemini в настройках бота.")
    elif not text:
        await message.answer("Напиши задачу одним сообщением. Например, «Купить хлеб 18 декабря в 6 вечера».")
    else:
        await prepare_draft(
            message, settings, state, session_factory, user_id=message.from_user.id, text=text,
        )


@router.message(F.voice)
async def offer_voice(message: Message, settings: BotSettings, state: FSMContext) -> None:
    voice = message.voice
    if not settings.gemini_api_key.get_secret_value():
        await message.answer("Распознавание задач пока недоступно. Для него нужен ключ Gemini в настройках бота.")
    elif voice.duration > 60 or not 0 < (voice.file_size or 0) <= VOICE_LIMIT:
        await message.answer("Отправь голосовое сообщение до минуты и до 5 МБ.")
    else:
        nonce = uuid4().hex
        await state.update_data(ai_task={
            "nonce": nonce, "expires": monotonic() + DRAFT_LIFETIME, "voice_id": voice.file_id,
        })
        await message.answer(
            "Для разбора запись будет отправлена в Google Gemini.",
            reply_markup=buttons("send_voice_ai", nonce, "Отправить в Gemini"),
        )


@router.callback_query(F.data.startswith("send_voice_ai:"))
async def draft_voice(
    callback: CallbackQuery, bot: Bot, settings: BotSettings, state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await pending(callback, state)
    if data is None:
        return
    if not isinstance(callback.message, Message) or not data.get("voice_id"):
        await callback.answer("Отправь голосовое сообщение заново.", show_alert=True)
        return
    await callback.answer("Разбираю запись.")
    await state.update_data(ai_task={})
    try:
        stream = await bot.download(data["voice_id"], timeout=10)
        voice = stream.read(VOICE_LIMIT + 1) if stream else b""
    except (TelegramAPIError, TimeoutError):
        voice = b""
    if not 0 < len(voice) <= VOICE_LIMIT:
        await callback.message.answer("Не удалось прочитать запись. Отправь её заново.")
        return
    await prepare_draft(
        callback.message, settings, state, session_factory,
        user_id=callback.from_user.id, voice=voice,
    )


@router.callback_query(F.data.startswith("discard_ai:"))
async def discard(callback: CallbackQuery, state: FSMContext) -> None:
    if await pending(callback, state) is None:
        return
    await state.update_data(ai_task={})
    await callback.answer("Отменено.")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("save_ai:"))
async def save(
    callback: CallbackQuery, state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await pending(callback, state)
    if data is None:
        return
    if not data.get("draft"):
        await callback.answer("Черновик ещё не готов.", show_alert=True)
        return
    draft = TaskDraft.model_validate_json(data["draft"])
    async with session_factory.begin() as session:
        user = await get_user(session, callback.from_user.id)
        if user is not None:
            task = await create_task(session, user_id=user.telegram_id, title=draft.title)
            if draft.due_at is not None:
                task.due_at = draft.due_at.astimezone(UTC)
    await state.update_data(ai_task={})
    if user is None:
        await callback.answer("Отправь /start, чтобы создать профиль.", show_alert=True)
        return
    await callback.answer("Задача сохранена.")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(card(task, ZoneInfo(user.timezone)), reply_markup=keyboard(task))
