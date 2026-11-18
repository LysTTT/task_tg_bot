from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.start import AcceptTerms
from bot.menu import ACTIONS
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
        else:
            public_commands = ("start", "terms", "consent", "delete_me")
            menu_action = ACTIONS.get(event.text)
            if (menu_action and menu_action[0] in public_commands) or await Command(*public_commands)(event, data["bot"]):
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
