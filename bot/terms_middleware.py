from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.start import AcceptTerms
from core.services.terms import has_current_consent


class TermsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message | CallbackQuery, data: dict):
        message = event.message if isinstance(event, CallbackQuery) else event
        user = event.from_user
        if message is None or message.chat.type != "private" or user is None:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if event.data in {"delete_profile", "keep_profile"} or await AcceptTerms.filter()(event):
                return await handler(event, data)
        elif await Command("start", "terms", "consent", "delete_me")(event, data["bot"]):
            return await handler(event, data)

        async with data["session_factory"]() as session:
            allowed = await has_current_consent(session, user.id)

        if allowed:
            return await handler(event, data)

        prompt = "Чтобы пользоваться ботом, отправь /start и прими соглашение."
        if isinstance(event, CallbackQuery):
            await event.answer(prompt, show_alert=True)
        else:
            await event.answer(prompt)
