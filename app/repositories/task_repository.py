from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.mill_data import ProcessingTask, ProcessingStatus, RawFile


async def create_raw_file(db: AsyncSession, *, user_id: int, mill_id: str, filename: str) -> RawFile:
    raw_file = RawFile(user_id=user_id, mill_id=mill_id, filename=filename, status="PENDING")
    db.add(raw_file)
    return raw_file


async def create_processing_task(db: AsyncSession, *, task_id: str, user_id: int, mill_id: str,
                                  filename: str, task_type: str) -> ProcessingTask:
    task = ProcessingTask(
        task_id=task_id,
        user_id=user_id,
        mill_id=mill_id,
        filename=filename,
        task_type=task_type,
        status=ProcessingStatus.PENDING,
    )
    db.add(task)
    return task


async def get_by_task_id(db: AsyncSession, task_id: str, user_id: int) -> Optional[ProcessingTask]:
    result = await db.execute(
        select(ProcessingTask).where(
            ProcessingTask.task_id == task_id, ProcessingTask.user_id == user_id
        )
    )
    return result.scalars().first()


async def list_tasks(db: AsyncSession, *, mill_ids: Optional[list] = None,
                      status: Optional[ProcessingStatus] = None, mill_id: Optional[str] = None):
    query = select(ProcessingTask)
    if mill_ids is not None:
        query = query.where(ProcessingTask.mill_id.in_(mill_ids))
    if status:
        query = query.where(ProcessingTask.status == status)
    if mill_id:
        query = query.where(ProcessingTask.mill_id == mill_id)
    result = await db.execute(query.order_by(ProcessingTask.created_at.desc()))
    return result.scalars().all()


async def list_upload_history(db: AsyncSession, user_id: int, limit: int = 100):
    result = await db.execute(
        select(RawFile)
        .where(RawFile.user_id == user_id)
        .order_by(RawFile.upload_timestamp.desc())
    )
    return result.scalars().all()


async def list_global_upload_history(db: AsyncSession, *, mill_ids: Optional[list] = None, limit: int = 100):
    query = select(RawFile).order_by(RawFile.upload_timestamp.desc()).limit(limit)
    if mill_ids is not None:
        query = select(RawFile).where(RawFile.mill_id.in_(mill_ids)).order_by(
            RawFile.upload_timestamp.desc()
        ).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def count_by_status_since(db: AsyncSession, status: ProcessingStatus, since: datetime) -> int:
    result = await db.execute(
        select(func.count(ProcessingTask.id)).where(
            ProcessingTask.status == status, ProcessingTask.created_at >= since
        )
    )
    return result.scalar() or 0


async def count_pending(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(ProcessingTask.id)).where(ProcessingTask.status == ProcessingStatus.PENDING)
    )
    return result.scalar() or 0


async def count_stuck(db: AsyncSession, started_before: datetime) -> int:
    result = await db.execute(
        select(func.count(ProcessingTask.id)).where(
            ProcessingTask.status == ProcessingStatus.PROCESSING,
            ProcessingTask.started_at <= started_before,
        )
    )
    return result.scalar() or 0
