"""Who administers this instance. Identity comes from the environment, not the database."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.config import settings
from app.db.models import User


class _HasEmail(Protocol):
    email: str


def admin_emails() -> set[str]:
    """The configured instance administrators, lowercased. Empty means nobody."""
    return {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}


def is_admin_email(email: str) -> bool:
    """Return True when this address is listed in HEYM_ADMIN_EMAILS."""
    allowed = admin_emails()
    return bool(allowed) and email.strip().lower() in allowed


def is_instance_admin(user: _HasEmail) -> bool:
    """Return True when the user is listed in HEYM_ADMIN_EMAILS."""
    return is_admin_email(getattr(user, "email", ""))


def instance_admin_clause(user_id: UUID) -> ColumnElement[bool]:
    """Resolve administration from the stored identity, including in synchronous queries."""
    allowed = admin_emails()
    if not allowed:
        return false()
    return (
        select(User.id)
        .where(User.id == user_id, func.lower(func.trim(User.email)).in_(sorted(allowed)))
        .correlate(None)
        .exists()
    )


async def is_instance_admin_id(db: AsyncSession, user_id: UUID) -> bool:
    """Check the configured administrator list for a user known only by ID."""
    if not admin_emails():
        return False
    result = await db.execute(select(instance_admin_clause(user_id)))
    return result.scalar() is True
