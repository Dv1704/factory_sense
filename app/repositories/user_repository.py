from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User, Mill, UserRole


async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()


async def get_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()


async def create(db: AsyncSession, *, email: str, password_hash: str, role: UserRole,
                  created_by_id: Optional[int] = None) -> User:
    user = User(email=email, password_hash=password_hash, role=role, created_by_id=created_by_id)
    db.add(user)
    await db.flush()
    return user


async def list_users(db: AsyncSession, *, created_by_id: Optional[int] = None,
                      email_contains: Optional[str] = None, role: Optional[UserRole] = None):
    query = select(User)
    if created_by_id is not None:
        query = query.where(User.created_by_id == created_by_id)
    if email_contains:
        query = query.where(User.email.contains(email_contains))
    if role:
        query = query.where(User.role == role)
    result = await db.execute(query)
    return result.scalars().all()


async def count_mills_for_user(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(select(func.count(Mill.id)).where(Mill.user_id == user_id))
    return result.scalar() or 0


async def count_all(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.id)))
    return result.scalar() or 0
