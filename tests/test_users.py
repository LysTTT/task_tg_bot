from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.database import create_db_engine
from unittest.mock import AsyncMock

from bot.reports import deliver
from core.models import Base, Delivery, Task, TaskEvent, TaskStatus, TermsVersion, User
from core.services.tasks import change_status, create_task
from core.services.terms import accept_current_terms, has_current_consent
from core.services.users import delete_user, upsert_user


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_db_engine(Settings())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = "test_users_" + uuid4().hex
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.run_sync(Base.metadata.create_all)
                yield async_sessionmaker(
                    connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_registration_preserves_settings_and_rolls_back_errors(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as session:
        user = await upsert_user(
            session, telegram_id=1001, first_name="Анна", username="anna"
        )
        assert user.timezone == "Europe/Moscow"
        assert user.created_at is not None
        registered_at = user.created_at
        user.timezone = "Asia/Yekaterinburg"
        user.is_blocked = True

    async with sessions.begin() as session:
        await upsert_user(session, telegram_id=1001, first_name="Аня", username=None)

    with pytest.raises(RuntimeError, match="Проверка отката"):
        async with sessions.begin() as session:
            await upsert_user(session, telegram_id=1002, first_name="Тест", username=None)
            raise RuntimeError("Проверка отката")

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        user = await session.get(User, 1001)
        assert user is not None
        assert user.first_name == "Аня"
        assert user.username is None
        assert user.timezone == "Asia/Yekaterinburg"
        assert user.is_blocked
        assert user.created_at == registered_at
        assert user.terms_version is None
        assert await session.get(User, 1002) is None


@pytest.mark.asyncio
async def test_accept_terms_rejects_old_version_and_preserves_acceptance_time(sessions):
    async with sessions.begin() as session:
        await upsert_user(session, telegram_id=1001, first_name="Анна", username=None)
        old = TermsVersion(
            version="1.0", url_terms="https://example.org/terms",
            url_privacy="https://example.org/privacy",
        )
        current = TermsVersion(
            version="2.0", url_terms="https://example.org/terms",
            url_privacy="https://example.org/privacy", is_current=True,
        )
        session.add_all([old, current])

    async with sessions.begin() as session:
        assert not await accept_current_terms(session, telegram_id=1001, terms_id=old.id)
        assert not await has_current_consent(session, 1001)
        user = await session.get(User, 1001)
        assert user.terms_version is None
        assert await accept_current_terms(session, telegram_id=1001, terms_id=current.id)
        assert user.terms_version == "2.0"
        assert user.terms_accepted_at is not None

        # now() одинаково для всех сессий внутри внешней транзакции теста.
        accepted_at = datetime(2020, 1, 1, tzinfo=UTC)
        user.terms_accepted_at = accepted_at

    async with sessions.begin() as session:
        assert await accept_current_terms(session, telegram_id=1001, terms_id=current.id)
        user = await session.get(User, 1001)
        assert user.terms_accepted_at == accepted_at
        assert await has_current_consent(session, 1001)
        version = await session.get(TermsVersion, current.id)
        version.is_current = False
        assert not await has_current_consent(session, 1001)


@pytest.mark.asyncio
async def test_create_task_and_event_share_transaction(sessions):
    async with sessions.begin() as session:
        await upsert_user(session, telegram_id=1001, first_name="Анна", username=None)

    async with sessions.begin() as session:
        task = await create_task(session, user_id=1001, title="  Купить хлеб  ")
        task_id = task.id

    with pytest.raises(RuntimeError, match="Проверка отката"):
        async with sessions.begin() as session:
            await create_task(session, user_id=1001, title="Отменённая вставка")
            await session.flush()
            raise RuntimeError("Проверка отката")

    async with sessions() as session:
        task = (await session.scalars(select(Task))).one()
        event = (await session.scalars(select(TaskEvent))).one()
        assert (task.id, task.user_id, task.title, task.status) == (
            task_id, 1001, "Купить хлеб", TaskStatus.NEW
        )
        assert (event.task_id, event.old_status, event.new_status) == (
            task_id, None, TaskStatus.NEW
        )


@pytest.mark.asyncio
async def test_task_ownership_delivery_and_profile_deletion(sessions):
    async with sessions.begin() as session:
        owner = await upsert_user(session, telegram_id=1001, first_name="Анна", username=None)
        await upsert_user(session, telegram_id=1002, first_name="Иван", username=None)
        session.add(TermsVersion(
            version="test-delivery", url_terms="https://example.org/terms",
            url_privacy="https://example.org/privacy", is_current=True,
        ))
        await session.flush()
        owner.terms_version = "test-delivery"
        owner.terms_accepted_at = datetime.now(UTC)
        task = await create_task(session, user_id=1001, title="Задача Анны")

    async with sessions.begin() as session:
        assert await change_status(
            session, user_id=1002, task_id=task.id, status=TaskStatus.COMPLETED
        ) is None
        await change_status(session, user_id=1001, task_id=task.id, status=TaskStatus.COMPLETED)
        await change_status(session, user_id=1001, task_id=task.id, status=TaskStatus.COMPLETED)

    bot = AsyncMock()
    now = datetime.now(UTC)
    await deliver(bot, sessions, 1002, "test-reminder", now, "Напоминание")
    for _ in range(2):
        await deliver(bot, sessions, 1001, "test-reminder", now, "Напоминание")
    bot.send_message.assert_awaited_once()

    async with sessions.begin() as session:
        events = (await session.scalars(
            select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id)
        )).all()
        assert [(event.old_status, event.new_status) for event in events] == [
            (None, TaskStatus.NEW),
            (TaskStatus.NEW, TaskStatus.COMPLETED),
        ]
        await delete_user(session, 1001)

    async with sessions() as session:
        assert await session.get(User, 1001) is None
        assert await session.get(User, 1002) is not None
        assert await session.get(Task, task.id) is None
        assert await session.scalar(select(TaskEvent.id)) is None
        assert await session.scalar(select(Delivery.key)) is None


@pytest.mark.asyncio
async def test_plain_text_uses_gemini_before_creating_task(sessions, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace

    from aiogram import Bot, Dispatcher
    from aiogram.types import Update
    from pydantic import SecretStr

    from bot.handlers import drafts, menu, start, tasks
    from bot.terms_middleware import TermsMiddleware
    from core.services.gemini import TaskDraft

    async with sessions.begin() as session:
        user = await upsert_user(session, telegram_id=1001, first_name="Анна", username=None)
        session.add(TermsVersion(
            version="test-ai", url_terms="https://example.org/terms",
            url_privacy="https://example.org/privacy", is_current=True,
        ))
        await session.flush()
        user.terms_version = "test-ai"
        user.terms_accepted_at = datetime.now(UTC)
        user.timezone = "Europe/Moscow"

    bread_task = TaskDraft.model_validate_json(
        '{"kind":"task","title":"Купить хлеб","due_at":"2026-12-18T18:00:00+03:00"}'
    )
    parse = AsyncMock(side_effect=[TaskDraft(kind="not_task", title="", due_at=None), bread_task, bread_task])
    monkeypatch.setattr(drafts, "parse_task", parse)
    transcript = "Купить хлеб восемнадцатого декабря в шесть вечера."
    transcribe = AsyncMock(side_effect=[transcript, ""])
    monkeypatch.setattr(drafts, "transcribe_voice", transcribe)
    monkeypatch.setattr(Bot, "download", AsyncMock(side_effect=[BytesIO(b"voice"), BytesIO(b"voice")]))
    send = AsyncMock()
    sender = {"id": 1001, "is_bot": False, "first_name": "Анна"}
    message = {"chat": {"id": 1001, "type": "private"}, "date": 1794981600}
    dispatcher = Dispatcher(
        settings=SimpleNamespace(gemini_api_key=SecretStr("test")),
        session_factory=sessions,
    )
    dispatcher.message.outer_middleware(TermsMiddleware())
    dispatcher.callback_query.outer_middleware(TermsMiddleware())
    routers = (start.router, menu.router, drafts.router, tasks.router)
    dispatcher.include_routers(*routers)

    try:
        async with Bot("123456:test") as bot:
            monkeypatch.setattr(bot.session, "make_request", send)
            for index, text in enumerate(("/start", "Помощь", "Настройки", "Утренний отчёт", "09:15"), 10):
                await dispatcher.feed_update(bot, Update(
                    update_id=index,
                    message={**message, "message_id": index, "from": sender, "text": text},
                ))
                response = send.await_args.args[1]
                if text == "/start":
                    assert response.reply_markup.keyboard[0][0].text == "Новая задача"
                elif text == "Помощь":
                    assert "«Изменить срок»" in response.text
                elif text == "09:15":
                    assert "09:15" in response.text
            parse.assert_not_awaited()

            for index, text in enumerate(("Привет!", "Купить хлеб 18 декабря в 6 вечера"), 1):
                update = Update(
                    update_id=index,
                    message={**message, "message_id": index, "from": sender, "text": text},
                )
                await dispatcher.feed_update(bot, update)
                assert parse.await_args.kwargs["text"] == text
                async with sessions() as session:
                    assert await session.scalar(select(func.count()).select_from(Task)) == 0

            preview = send.await_args.args[1]
            assert "18.12.2026 18:00" in preview.text
            save_button = preview.reply_markup.inline_keyboard[0][0]
            await dispatcher.feed_update(bot, Update(
                update_id=3,
                callback_query={
                    "id": "save-test", "from": sender, "chat_instance": "test",
                    "message": {**message, "message_id": 3, "text": preview.text},
                    "data": save_button.callback_data,
                },
            ))
            await dispatcher.feed_update(bot, Update(
                update_id=20,
                message={**message, "message_id": 20, "from": sender, "text": "Все задачи"},
            ))
            listing = send.await_args.args[1]
            open_button = listing.reply_markup.inline_keyboard[0][0]
            assert open_button.text.startswith("Открыть №")
            await dispatcher.feed_update(bot, Update(
                update_id=21,
                callback_query={
                    "id": "open-test", "from": sender, "chat_instance": "test",
                    "message": {**message, "message_id": 20, "text": listing.text},
                    "data": open_button.callback_data,
                },
            ))
            assert "Срок: 18.12.2026 18:00" in send.await_args.args[1].text
            assert parse.await_count == 2

            for index in (30, 32):
                await dispatcher.feed_update(bot, Update(
                    update_id=index,
                    message={
                        **message, "message_id": index, "from": sender,
                        "voice": {"file_id": "voice", "file_unique_id": "voice", "duration": 5, "file_size": 5},
                    },
                ))
                prompt = send.await_args.args[1]
                button = prompt.reply_markup.inline_keyboard[0][0]
                assert button.text == "Распознать голосовое"
                await dispatcher.feed_update(bot, Update(
                    update_id=index + 1,
                    callback_query={
                        "id": f"voice-{index}", "from": sender, "chat_instance": "test",
                        "message": {**message, "message_id": index, "text": prompt.text},
                        "data": button.callback_data,
                    },
                ))
                response = send.await_args.args[1]
                if index == 30:
                    assert f"Распознано: {transcript}" in response.text
                    assert "Срок: 18.12.2026 18:00" in response.text
                else:
                    assert "Не удалось разобрать речь" in response.text
            assert transcribe.await_count == 2
            assert parse.await_count == 3
            assert parse.await_args.kwargs == {"text": transcript}
    finally:
        # Роутеры уровня модуля могут подключаться только к одному диспетчеру.
        dispatcher.sub_routers.clear()
        for router in routers:
            router._parent_router = None

    async with sessions() as session:
        task = (await session.scalars(select(Task))).one()
        assert (task.title, task.due_at) == ("Купить хлеб", datetime(2026, 12, 18, 15, tzinfo=UTC))
        assert (await session.scalars(select(TaskEvent))).one().task_id == task.id
        assert (await session.get(User, 1001)).morning_report_time.strftime("%H:%M") == "09:15"
