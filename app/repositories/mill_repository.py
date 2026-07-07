from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import Mill, User


async def get_by_api_key(db: AsyncSession, api_key: str) -> Optional[Mill]:
    result = await db.execute(select(Mill).where(Mill.api_key == api_key))
    return result.scalars().first()


async def get_first_for_user(db: AsyncSession, user_id: int) -> Optional[Mill]:
    result = await db.execute(select(Mill).where(Mill.user_id == user_id))
    return result.scalars().first()


async def list_for_user(db: AsyncSession, user_id: int):
    result = await db.execute(select(Mill).where(Mill.user_id == user_id))
    return result.scalars().all()


async def create(db: AsyncSession, *, user_id: int, admin_id: int, mill_id: str, api_key: str) -> Mill:
    mill = Mill(user_id=user_id, admin_id=admin_id, mill_id=mill_id, api_key=api_key)
    db.add(mill)
    await db.flush()
    return mill


async def list_mills(db: AsyncSession, *, admin_id: Optional[int] = None,
                      mill_id_contains: Optional[str] = None, user_id: Optional[int] = None):
    query = select(Mill, User).join(User, Mill.user_id == User.id)
    if admin_id is not None:
        query = query.where(Mill.admin_id == admin_id)
    if mill_id_contains:
        query = query.where(Mill.mill_id.contains(mill_id_contains))
    if user_id is not None:
        query = query.where(Mill.user_id == user_id)
    result = await db.execute(query)
    return result.all()


async def list_all_with_owner(db: AsyncSession):
    result = await db.execute(select(Mill, User).join(User, Mill.user_id == User.id))
    return result.all()


async def get_owned_by_admin(db: AsyncSession, mill_id: str, admin_id: int) -> Optional[Mill]:
    result = await db.execute(
        select(Mill).where(Mill.mill_id == mill_id, Mill.admin_id == admin_id)
    )
    return result.scalars().first()


async def list_mill_ids_for_admin(db: AsyncSession, admin_id: int):
    result = await db.execute(select(Mill.mill_id).where(Mill.admin_id == admin_id))
    return result.scalars().all()


async def count_all(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(Mill.id)))
    return result.scalar() or 0


async def count_with_baseline(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(Mill.id)).where(Mill.has_uploaded_baseline == True))
    return result.scalar() or 0
