from aiogram import F, Router
from aiogram.filters import CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers import start, tasks
from bot.menu import ACTIONS, CANCEL, HELP, HOME, SECTIONS, keyboard

router = Router()
router.message.filter(F.chat.type == "private", F.from_user)

WAITING_FOR_VALUE = "menu:waiting"
TASK_COMMANDS = {
    **dict.fromkeys(tasks.LISTS, tasks.list_tasks),
    "task": tasks.show_task,
    "deadline": tasks.set_deadline,
    "timezone": start.set_timezone,
    "morning": start.set_report_time,
    "evening": start.set_report_time,
}
PROFILE_COMMANDS = {
    "settings": start.show_settings,
    "terms": start.show_terms,
    "consent": start.show_consent,
}


@router.message(F.text.in_({HOME, HELP, CANCEL, *SECTIONS, *ACTIONS}))
async def open_menu(message: Message, state: FSMContext, session_factory) -> None:
    data = await state.get_data()
    section = data.get("menu_section", HOME)
    await state.set_state(None)
    await state.update_data(menu_command=None)
    label = message.text

    if label == HELP:
        await start.show_help(message)
    elif label == CANCEL:
        await message.answer("Ввод отменён.", reply_markup=keyboard(section))
    elif label == HOME or label in SECTIONS:
        await state.update_data(menu_section=label)
        await message.answer(label, reply_markup=keyboard(label))
    else:
        command, _, prompt = ACTIONS[label]
        if command == "ai":
            await message.answer(prompt, reply_markup=keyboard(section))
        elif prompt:
            await state.update_data(menu_command=command)
            await state.set_state(WAITING_FOR_VALUE)
            await message.answer(prompt)
        elif command == "delete_me":
            await start.confirm_delete(message)
        elif command in PROFILE_COMMANDS:
            await PROFILE_COMMANDS[command](message, session_factory)
        else:
            await TASK_COMMANDS[command](message, CommandObject(command=command), session_factory)


@router.message(StateFilter(WAITING_FOR_VALUE), F.text, ~F.text.startswith("/"))
async def receive_value(message: Message, state: FSMContext, session_factory) -> None:
    data = await state.get_data()
    command = data["menu_command"]
    await state.set_state(None)
    await state.update_data(menu_command=None)
    await TASK_COMMANDS[command](
        message, CommandObject(command=command, args=message.text.strip()), session_factory,
    )
