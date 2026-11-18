from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Task, TaskEvent, User


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.get(User, telegram_id)


async def delete_user(session: AsyncSession, telegram_id: int) -> None:
    user = await session.get(User, telegram_id, with_for_update=True)
    if user is None:
        return
    task_ids = select(Task.id).where(Task.user_id == telegram_id).with_for_update()
    await session.execute(delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids)))
    await session.execute(delete(Task).where(Task.user_id == telegram_id))
    await session.delete(user)


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    first_name: str,
    username: str | None,
) -> User:
    statement = insert(User).values(
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[User.telegram_id],
        set_={
            "first_name": statement.excluded.first_name,
            "username": statement.excluded.username,
        },
    ).returning(User)

    result = await session.scalars(
        statement, execution_options={"populate_existing": True}
    )
    return result.one()
