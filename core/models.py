from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Text,
    Time,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TermsVersion(Base):
    __tablename__ = "terms_versions"
    __table_args__ = (
        Index(
            "uq_terms_versions_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    version: Mapped[str] = mapped_column(Text, unique=True)
    url_terms: Mapped[str] = mapped_column(Text)
    url_privacy: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=false())


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default="UTC")
    morning_report_time: Mapped[time | None] = mapped_column(Time)
    evening_report_time: Mapped[time | None] = mapped_column(Time)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    terms_version: Mapped[str | None] = mapped_column(
        Text, ForeignKey("terms_versions.version")
    )
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, server_default=false())


class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


def _task_status_type(name: str) -> Enum:
    return Enum(
        TaskStatus,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda statuses: [status.value for status in statuses],
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        _task_status_type("task_status"),
        server_default=TaskStatus.NEW.value,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    old_status: Mapped[TaskStatus | None] = mapped_column(
        _task_status_type("task_event_old_status")
    )
    new_status: Mapped[TaskStatus] = mapped_column(
        _task_status_type("task_event_new_status")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Delivery(Base):
    __tablename__ = "deliveries"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
