from datetime import date
from typing import Optional

from sqlalchemy import func, distinct, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.mill_data import MachineDailyStats, BearingRisk


async def get_for_date(db: AsyncSession, user_id: int, target_date: date):
    result = await db.execute(
        select(MachineDailyStats).where(
            MachineDailyStats.user_id == user_id,
            MachineDailyStats.date == target_date,
        )
    )
    return result.scalars().all()


async def get_latest_date_for_user(db: AsyncSession, user_id: int) -> Optional[date]:
    result = await db.execute(
        select(func.max(MachineDailyStats.date)).where(MachineDailyStats.user_id == user_id)
    )
    return result.scalar()


async def get_latest_date_for_mill(db: AsyncSession, mill_id: str) -> Optional[date]:
    result = await db.execute(
        select(func.max(MachineDailyStats.date)).where(MachineDailyStats.mill_id == mill_id)
    )
    return result.scalar()


async def get_latest_per_machine(db: AsyncSession, user_id: int):
    subquery = (
        select(
            MachineDailyStats.machine_id,
            func.max(MachineDailyStats.date).label("max_date"),
        )
        .where(MachineDailyStats.user_id == user_id)
        .group_by(MachineDailyStats.machine_id)
        .subquery()
    )
    query = (
        select(MachineDailyStats)
        .join(
            subquery,
            (MachineDailyStats.machine_id == subquery.c.machine_id)
            & (MachineDailyStats.date == subquery.c.max_date),
        )
        .where(MachineDailyStats.user_id == user_id)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_trends(db: AsyncSession, user_id: int, machine_id: str, start_date: date):
    result = await db.execute(
        select(MachineDailyStats)
        .where(
            MachineDailyStats.user_id == user_id,
            MachineDailyStats.machine_id == machine_id,
            MachineDailyStats.date >= start_date,
        )
        .order_by(MachineDailyStats.date.asc())
    )
    return result.scalars().all()


async def get_range(db: AsyncSession, user_id: int, *, start_date: Optional[date] = None,
                     end_date: Optional[date] = None, machine_id: Optional[str] = None):
    query = select(MachineDailyStats).where(MachineDailyStats.user_id == user_id)
    if start_date:
        query = query.where(MachineDailyStats.date >= start_date)
    if end_date:
        query = query.where(MachineDailyStats.date <= end_date)
    if machine_id:
        query = query.where(MachineDailyStats.machine_id == machine_id)
    result = await db.execute(query.order_by(MachineDailyStats.date.desc(), MachineDailyStats.id.desc()))
    return result.scalars().all()


async def get_by_id(db: AsyncSession, stats_id: int) -> Optional[MachineDailyStats]:
    result = await db.execute(select(MachineDailyStats).where(MachineDailyStats.id == stats_id))
    return result.scalars().first()


async def count_distinct_machines(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(distinct(MachineDailyStats.machine_id))))
    return result.scalar() or 0


async def count_machines_for_mill(db: AsyncSession, mill_id: str) -> int:
    result = await db.execute(
        select(func.count(distinct(MachineDailyStats.machine_id))).where(
            MachineDailyStats.mill_id == mill_id
        )
    )
    return result.scalar() or 0


async def count_by_bearing_risk_since(db: AsyncSession, risk: BearingRisk, since: date) -> int:
    result = await db.execute(
        select(func.count(distinct(MachineDailyStats.machine_id))).where(
            MachineDailyStats.bearing_risk == risk,
            MachineDailyStats.date >= since,
        )
    )
    return result.scalar() or 0


async def list_active_mill_ids_since(db: AsyncSession, since: date):
    result = await db.execute(
        select(distinct(MachineDailyStats.mill_id)).where(MachineDailyStats.date >= since)
    )
    return result.fetchall()


async def get_avg_health_score(db: AsyncSession, mill_id: str, since: date) -> Optional[float]:
    result = await db.execute(
        select(func.avg(MachineDailyStats.health_score)).where(
            MachineDailyStats.mill_id == mill_id,
            MachineDailyStats.date >= since,
        )
    )
    return result.scalar()


async def delete_for_mill(db: AsyncSession, user_id: int, mill_id: str) -> None:
    await db.execute(
        delete(MachineDailyStats).where(
            MachineDailyStats.user_id == user_id, MachineDailyStats.mill_id == mill_id
        )
    )
