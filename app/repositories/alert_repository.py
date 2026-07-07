from typing import Optional

from sqlalchemy import func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.mill_data import Alert, AlertStatus
from app.models.user import User


async def get_active_feed(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(Alert)
        .where(Alert.user_id == user_id, Alert.status != AlertStatus.resolved)
        .order_by(Alert.timestamp.desc())
    )
    return result.scalars().all()


async def get_history(db: AsyncSession, user_id: int, *, machine_id: Optional[str] = None,
                       limit: int = 50, offset: int = 0):
    query = select(Alert).where(Alert.user_id == user_id, Alert.status == AlertStatus.resolved)
    if machine_id:
        query = query.where(Alert.machine_id == machine_id)
    query = query.order_by(Alert.resolved_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return result.scalars().all()


async def get_by_id(db: AsyncSession, alert_id: int, user_id: int) -> Optional[Alert]:
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id)
    )
    return result.scalars().first()


async def count_open(db: AsyncSession, *, user_id: Optional[int] = None,
                      mill_id: Optional[str] = None) -> int:
    query = select(func.count(Alert.id)).where(Alert.status != AlertStatus.resolved)
    if user_id is not None:
        query = query.where(Alert.user_id == user_id)
    if mill_id is not None:
        query = query.where(Alert.mill_id == mill_id)
    result = await db.execute(query)
    return result.scalar() or 0


async def get_open_type_breakdown(db: AsyncSession):
    result = await db.execute(
        select(Alert.type, func.count(Alert.id))
        .where(Alert.status != AlertStatus.resolved)
        .group_by(Alert.type)
    )
    return result.fetchall()


async def get_platform_overview(db: AsyncSession, limit: int = 200):
    result = await db.execute(
        select(Alert, User)
        .join(User, Alert.user_id == User.id)
        .where(Alert.status != AlertStatus.resolved)
        .order_by(Alert.timestamp.desc())
        .limit(limit)
    )
    return result.all()


async def delete_for_mill(db: AsyncSession, user_id: int, mill_id: str) -> None:
    await db.execute(delete(Alert).where(Alert.user_id == user_id, Alert.mill_id == mill_id))
