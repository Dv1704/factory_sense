from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.models.mill_data import Alert, AlertStatus, ResolutionCategory
from app.routes.data import get_api_key_mill

router = APIRouter()


class ResolveRequest(BaseModel):
    resolution_note: Optional[str] = None
    resolution_category: Optional[ResolutionCategory] = None

    @model_validator(mode="after")
    def require_at_least_one(self) -> "ResolveRequest":
        if not self.resolution_note and not self.resolution_category:
            raise ValueError("Provide at least a resolution_note or resolution_category")
        return self


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


@router.get("/")
async def get_alerts(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Active feed: returns alerts that are not yet resolved (active + acknowledged)."""
    mill = await get_api_key_mill(x_api_key, db)

    result = await db.execute(
        select(Alert)
        .where(
            Alert.user_id == mill.user_id,
            Alert.status != AlertStatus.resolved,
        )
        .order_by(Alert.timestamp.desc())
    )
    return [_alert_dict(a) for a in result.scalars().all()]


@router.get("/history")
async def get_alert_history(
    machine_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Alert history log: resolved alerts only, newest first."""
    mill = await get_api_key_mill(x_api_key, db)

    query = select(Alert).where(
        Alert.user_id == mill.user_id,
        Alert.status == AlertStatus.resolved,
    )
    if machine_id:
        query = query.where(Alert.machine_id == machine_id)

    query = query.order_by(Alert.resolved_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return [_alert_dict(a) for a in result.scalars().all()]


@router.patch("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    mill = await get_api_key_mill(x_api_key, db)

    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == mill.user_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == AlertStatus.resolved:
        raise HTTPException(status_code=400, detail="Cannot acknowledge a resolved alert")

    alert.status = AlertStatus.acknowledged
    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "success", "alert": _alert_dict(alert)}


@router.patch("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    body: ResolveRequest,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Mark an alert as resolved and move it to history."""
    mill = await get_api_key_mill(x_api_key, db)

    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == mill.user_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == AlertStatus.resolved:
        raise HTTPException(status_code=400, detail="Alert is already resolved")

    alert.status = AlertStatus.resolved
    alert.is_acknowledged = True
    alert.resolved_at = datetime.now(timezone.utc)
    if alert.acknowledged_at is None:
        alert.acknowledged_at = alert.resolved_at
    alert.resolution_note = body.resolution_note
    alert.resolution_category = body.resolution_category.value if body.resolution_category else None
    await db.commit()

    return {"status": "success", "alert": _alert_dict(alert)}
