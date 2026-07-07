from datetime import datetime

from sqlalchemy import func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.mill_data import MachineDataPoint


async def count_since(db: AsyncSession, since: datetime) -> int:
    result = await db.execute(
        select(func.count(MachineDataPoint.id)).where(MachineDataPoint.timestamp >= since)
    )
    return result.scalar() or 0


async def delete_for_mill(db: AsyncSession, user_id: int, mill_id: str) -> None:
    await db.execute(
        delete(MachineDataPoint).where(
            MachineDataPoint.user_id == user_id, MachineDataPoint.mill_id == mill_id
        )
    )
