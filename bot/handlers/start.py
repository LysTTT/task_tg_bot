from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.services.terms import accept_current_terms, get_current_terms
from core.services.users import delete_user, get_user, upsert_user


router = Router()
router.message.filter(F.chat.type == "private")


class AcceptTerms(CallbackData, prefix="accept_terms"):
    terms_id: int


@router.message(CommandStart())
async def start(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:  #!!!!!!
    telegram_user = message.from_user
    if telegram_user is None:
        return

    async with session_factory.begin() as session:
        user = await upsert_user(
            session,
            telegram_id=telegram_user.id,
            first_name=telegram_user.first_name,
            username=telegram_user.username,
        )
        terms = await get_current_terms(session)

    greeting = f"Привет, {user.first_name}! Я TaskBot — твой помощник по задачам."
    if terms is None:
        await message.answer(f"{greeting}\n\nСоглашение пока не опубликовано.")
        return

    if user.terms_version == terms.version and user.terms_accepted_at is not None:
        await message.answer(f"{greeting}\n\nНапиши задачу одним сообщением. Обычный текст разбирает Google Gemini.")
        return

    await message.answer(
        f"{greeting}\n\nОзнакомься с соглашением версии {terms.version} "
        "и политикой обработки персональных данных. Обычный текст в чате "
        "отправляется в Google Gemini для разбора задач.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Соглашение", url=terms.url_terms)],
                [InlineKeyboardButton(text="Политика обработки данных", url=terms.url_privacy)],
                [
                    InlineKeyboardButton(
                        text="Принимаю",
                        callback_data=AcceptTerms(terms_id=terms.id).pack(),
                    )
                ],
            ]
        ),
    )


@router.message(Command("help"))
async def show_help(message: Message) -> None:
    await message.answer(
        "Команды TaskBot\n\n"
        "/start — открыть бота и при необходимости принять соглашение.\n"
        "/terms — открыть действующее соглашение.\n"
        "/help — показать эту справку.\n"
        "/settings — посмотреть настройки профиля.\n"
        "/timezone Europe/Moscow — установить часовой пояс.\n"
        "/morning 09:00 — задать время утреннего отчёта.\n"
        "/evening 19:30 — задать время вечернего отчёта.\n\n"
        "Напиши задачу обычным сообщением. Например, «Купить хлеб 18 декабря в 6 вечера».\n"
        "Google Gemini выделит название и срок. Проверь их и нажми «Сохранить».\n"
        "/task 12 — открыть карточку.\n"
        "/deadline 12 2026-12-01 18:00 — задать срок задачи.\n"
        "/today, /tomorrow, /week, /overdue, /completed, /all — списки задач.\n"
        "/search слова — поиск по названию.\n"
        "/consent — посмотреть сведения о согласии.\n"
        "/delete_me — удалить профиль и задачи."
    )


@router.message(Command("settings"))
async def show_settings(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    if message.from_user is None:
        return

    async with session_factory() as session:
        user = await get_user(session, message.from_user.id)

    if user is None:
        await message.answer("Профиль не найден. Отправь /start.")
        return

    morning = user.morning_report_time
    evening = user.evening_report_time
    morning_text = morning.strftime("%H:%M") if morning is not None else "не настроен"
    evening_text = evening.strftime("%H:%M") if evening is not None else "не настроен"
    await message.answer(
        "Настройки профиля\n\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Утренний отчёт: {morning_text}\n"
        f"Вечерний отчёт: {evening_text}"
    )


@router.message(Command("timezone"))
async def set_timezone(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None:
        return

    timezone = (command.args or "").strip()
    if not timezone:
        await message.answer("Укажи часовой пояс. Пример: /timezone Europe/Moscow")
        return

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer("Часовой пояс не найден. Пример: /timezone Europe/Moscow")
        return

    async with session_factory.begin() as session:
        user = await get_user(session, message.from_user.id)
        if user is not None:
            user.timezone = timezone

    if user is None:
        await message.answer("Профиль не найден. Отправь /start.")
        return

    await message.answer(f"Часовой пояс сохранён: {timezone}.")


@router.message(Command("morning", "evening"))
async def set_report_time(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None:
        return

    raw_time = (command.args or "").strip()
    example = f"Введи время в формате ЧЧ:ММ. Пример: /{command.command} 09:00"
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", raw_time):
        await message.answer(example)
        return

    try:
        report_time = datetime.strptime(raw_time, "%H:%M").time()
    except ValueError:
        await message.answer(example)
        return

    async with session_factory.begin() as session:
        user = await get_user(session, message.from_user.id)
        if user is not None:
            if command.command == "morning":
                user.morning_report_time = report_time
            else:
                user.evening_report_time = report_time

    if user is None:
        await message.answer("Профиль не найден. Отправь /start.")
        return

    await message.answer(f"Время отчёта сохранено: {report_time:%H:%M} ({user.timezone}).")


@router.message(Command("terms"))
async def show_terms(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        terms = await get_current_terms(session)
    if terms is None:
        await message.answer("Соглашение пока не опубликовано.")
        return
    await message.answer(
        f"Действующее соглашение ({terms.version}):\n{terms.url_terms}\n\n"
        f"Политика обработки данных:\n{terms.url_privacy}\n\n"
        "Чтобы принять документы, отправь /start."
    )


@router.message(Command("consent"))
async def show_consent(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    if message.from_user is None:
        return
    async with session_factory() as session:
        user = await get_user(session, message.from_user.id)
    if user is None or user.terms_version is None or user.terms_accepted_at is None:
        await message.answer("Согласие ещё не записано. Отправь /start.")
        return
    accepted_at = user.terms_accepted_at.astimezone(ZoneInfo(user.timezone))
    await message.answer(
        f"Принятая версия: {user.terms_version}\nДата согласия: {accepted_at:%d.%m.%Y %H:%M %Z}"
    )


@router.message(Command("delete_me"))
async def confirm_delete(message: Message) -> None:
    await message.answer(
        "Удалить профиль, согласие, задачи и их историю?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Удалить мои данные", callback_data="delete_profile"),
            InlineKeyboardButton(text="Отмена", callback_data="keep_profile"),
        ]]),
    )


@router.callback_query(F.data.in_({"delete_profile", "keep_profile"}), F.message.chat.type == "private")
async def handle_delete(callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]) -> None:
    if callback.data == "delete_profile":
        async with session_factory.begin() as session:
            await delete_user(session, callback.from_user.id)
        reply = "Профиль и задачи удалены. Для новой регистрации отправь /start."
    else:
        reply = "Удаление отменено."
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(reply)


@router.callback_query(AcceptTerms.filter(), F.message.chat.type == "private")
async def accept_terms(
    callback: CallbackQuery,
    callback_data: AcceptTerms,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory.begin() as session:
        accepted = await accept_current_terms(
            session,
            telegram_id=callback.from_user.id,
            terms_id=callback_data.terms_id,
        )

    if accepted:
        await callback.answer("Согласие сохранено.")
    else:
        await callback.answer("Отправь /start, чтобы открыть действующую версию.", show_alert=True)
