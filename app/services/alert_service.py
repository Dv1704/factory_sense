from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mill_data import Alert, AlertStatus
from app.repositories import alert_repository


def _alert_dict(a: Alert) -> dict:
    return {
        "id": a.id,
        "machine_id": a.machine_id,
        "type": a.type,
        "message": a.message,
        "timestamp": a.timestamp,
        "status": a.status or AlertStatus.active,
        "acknowledged_at": a.acknowledged_at,
        "resolved_at": a.resolved_at,
        "resolution_note": a.resolution_note,
        "resolution_category": a.resolution_category,
    }


async def get_active_feed(db: AsyncSession, user_id: int) -> list:
    """Active feed: returns active and acknowledged alerts (everything not resolved)."""
    alerts = await alert_repository.get_active_feed(db, user_id)
    return [_alert_dict(a) for a in alerts]


async def get_history(db: AsyncSession, user_id: int, *, machine_id: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> list:
    """Alert history log: resolved alerts only, newest first."""
    alerts = await alert_repository.get_history(db, user_id, machine_id=machine_id, limit=limit, offset=offset)
    return [_alert_dict(a) for a in alerts]


async def acknowledge(db: AsyncSession, alert_id: int, user_id: int) -> dict:
    alert = await alert_repository.get_by_id(db, alert_id, user_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == AlertStatus.resolved:
        raise HTTPException(status_code=400, detail="Cannot acknowledge a resolved alert")

    alert.status = AlertStatus.acknowledged
    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "success", "alert": _alert_dict(alert)}


async def resolve(db: AsyncSession, alert_id: int, user_id: int, *,
                   resolution_note: Optional[str], resolution_category) -> dict:
    """Mark an alert as resolved and move it to history."""
    alert = await alert_repository.get_by_id(db, alert_id, user_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == AlertStatus.resolved:
        raise HTTPException(status_code=400, detail="Alert is already resolved")

    alert.status = AlertStatus.resolved
    alert.is_acknowledged = True
    alert.resolved_at = datetime.now(timezone.utc)
    if alert.acknowledged_at is None:
        alert.acknowledged_at = alert.resolved_at
    alert.resolution_note = resolution_note
    alert.resolution_category = resolution_category.value if resolution_category else None
    await db.commit()

    return {"status": "success", "alert": _alert_dict(alert)}
