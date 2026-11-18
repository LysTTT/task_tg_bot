from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Task, TaskEvent, TaskStatus


async def create_task(session: AsyncSession, *, user_id: int, title: str) -> Task:
    title = title.strip()
    if not title:
        raise ValueError("Название задачи не должно быть пустым.")

    task = Task(user_id=user_id, title=title, status=TaskStatus.NEW)
    session.add(task)
    await session.flush()

    session.add(
        TaskEvent(task_id=task.id, old_status=None, new_status=task.status)
    )
    return task


async def get_task(
    session: AsyncSession, *, user_id: int, task_id: int, lock: bool = False
) -> Task | None:
    query = select(Task).where(Task.id == task_id, Task.user_id == user_id)
    return await session.scalar(query.with_for_update() if lock else query)


async def change_status(
    session: AsyncSession, *, user_id: int, task_id: int, status: TaskStatus
) -> Task | None:
    task = await get_task(session, user_id=user_id, task_id=task_id, lock=True)
    if task is not None and task.status != status:
        session.add(TaskEvent(task_id=task.id, old_status=task.status, new_status=status))
        task.status = status
    return task
