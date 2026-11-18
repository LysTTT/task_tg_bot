from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import TermsVersion, User


async def get_current_terms(session: AsyncSession) -> TermsVersion | None:
    result = await session.scalars(
        select(TermsVersion).where(TermsVersion.is_current.is_(True))
    )
    return result.one_or_none()


async def has_current_consent(session: AsyncSession, telegram_id: int) -> bool:
    accepted_id = await session.scalar(
        select(User.telegram_id)
        .join(TermsVersion, User.terms_version == TermsVersion.version)
        .where(
            User.telegram_id == telegram_id,
            User.terms_accepted_at.is_not(None),
            TermsVersion.is_current.is_(True),
        )
    )
    return accepted_id is not None


async def accept_current_terms(
    session: AsyncSession, *, telegram_id: int, terms_id: int
) -> bool:
    terms = await session.scalar(
        select(TermsVersion)
        .where(TermsVersion.id == terms_id, TermsVersion.is_current.is_(True))
        .with_for_update(read=True)
    )
    if terms is None:
        return False

    user = await session.get(User, telegram_id, with_for_update=True)
    if user is None:
        return False

    if user.terms_version != terms.version or user.terms_accepted_at is None:
        user.terms_version = terms.version
        user.terms_accepted_at = await session.scalar(select(func.now()))
    return True
