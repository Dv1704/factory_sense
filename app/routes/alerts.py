from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.models.mill_data import Alert
from app.routes.data import get_api_key_mill

router = APIRouter()

@router.get("/")
async def get_alerts(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    mill = await get_api_key_mill(x_api_key, db)
    
    result = await db.execute(
        select(Alert)
        .where(Alert.mill_id == mill.id, Alert.is_acknowledged.is_(False))
        .order_by(Alert.timestamp.desc())
    )
    alerts = result.scalars().all()
    
    return [
        {
            "id": a.id,
            "machine_id": a.machine_id,
            "type": a.type,
            "message": a.message,
            "timestamp": a.timestamp
        } for a in alerts
    ]

@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    mill = await get_api_key_mill(x_api_key, db)
    
    # Check if alert exists and belongs to this mill
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.mill_id == mill.id)
    )
    alert = result.scalars().first()
    
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    alert.is_acknowledged = True
    await db.commit()
    
    return {"status": "success", "message": f"Alert {alert_id} acknowledged"}
