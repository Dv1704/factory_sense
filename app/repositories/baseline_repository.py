from typing import Optional

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.mill_data import MachineBaseline, MachineBaselineHistory


async def list_for_user(db: AsyncSession, user_id: int):
    result = await db.execute(select(MachineBaseline).where(MachineBaseline.user_id == user_id))
    return result.scalars().all()


async def get_for_machine(db: AsyncSession, user_id: int, machine_id: str) -> Optional[MachineBaseline]:
    result = await db.execute(
        select(MachineBaseline).where(
            MachineBaseline.user_id == user_id,
            MachineBaseline.machine_id == machine_id,
        )
    )
    return result.scalars().first()


async def list_history_for_user(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(MachineBaselineHistory)
        .where(MachineBaselineHistory.user_id == user_id)
        .order_by(MachineBaselineHistory.timestamp.desc())
    )
    return result.scalars().all()


async def list_history_for_machine(db: AsyncSession, user_id: int, machine_id: str):
    result = await db.execute(
        select(MachineBaselineHistory)
        .where(
            MachineBaselineHistory.user_id == user_id,
            MachineBaselineHistory.machine_id == machine_id,
        )
        .order_by(MachineBaselineHistory.timestamp.desc())
    )
    return result.scalars().all()


async def delete_for_mill(db: AsyncSession, user_id: int, mill_id: str) -> None:
    await db.execute(
        delete(MachineBaseline).where(
            MachineBaseline.user_id == user_id, MachineBaseline.mill_id == mill_id
        )
    )
    await db.execute(
        delete(MachineBaselineHistory).where(
            MachineBaselineHistory.user_id == user_id, MachineBaselineHistory.mill_id == mill_id
        )
    )
