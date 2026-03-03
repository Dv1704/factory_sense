from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional
from datetime import date, datetime, timedelta
import json

from app.core.database import get_db
from app.models.user import User
from app.models.mill_data import MachineDailyStats, Alert, MachineDataPoint, BearingRisk
from app.routes.data import get_api_key_user, MACHINE_SPECS

router = APIRouter()

@router.get("/machine-specs")
async def get_machine_specs(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    """Returns the safety thresholds and metadata for all machines."""
    # Remove max_a from specs before returning
    clean_specs = {k: {"name": v["name"]} for k, v in MACHINE_SPECS.items()}
    return clean_specs

@router.get("/summary")
async def get_dashboard_summary(
    date: Optional[date] = None,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user = await get_api_key_user(x_api_key, db)
    
    if not date:
        # Get latest date available for this mill
        latest_date_query = select(func.max(MachineDailyStats.date)).where(MachineDailyStats.mill_id == user.mill_id)
        result = await db.execute(latest_date_query)
        date = result.scalar()
    
    if not date:
        return {
            "total_energy_kwh": 0,
            "total_co2_kg": 0,
            "machine_count": 0,
            "active_alerts_count": 0
        }

    # Query stats for the given date
    stats_query = select(MachineDailyStats).where(
        MachineDailyStats.mill_id == user.mill_id,
        MachineDailyStats.date == date
    )
    result = await db.execute(stats_query)
    stats = result.scalars().all()

    # Query active alerts
    alerts_query = select(func.count(Alert.id)).where(
        Alert.mill_id == user.mill_id,
        Alert.is_acknowledged == False
    )
    result = await db.execute(alerts_query)
    alerts_count = result.scalar() or 0

    total_energy = sum(s.total_energy_kwh for s in stats)
    total_co2 = sum(s.total_co2_kg for s in stats)

    return {
        "total_energy_kwh": round(total_energy, 2),
        "total_co2_kg": round(total_co2, 2),
        "machine_count": len(stats),
        "active_alerts_count": alerts_count,
        "date": date
    }

@router.get("/machines")
async def get_machines(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user = await get_api_key_user(x_api_key, db)
    
    # Get latest stats for each machine
    subquery = (
        select(
            MachineDailyStats.machine_id,
            func.max(MachineDailyStats.date).label("max_date")
        )
        .where(MachineDailyStats.mill_id == user.mill_id)
        .group_by(MachineDailyStats.machine_id)
        .subquery()
    )
    
    query = (
        select(MachineDailyStats)
        .join(subquery, (MachineDailyStats.machine_id == subquery.c.machine_id) & (MachineDailyStats.date == subquery.c.max_date))
        .where(MachineDailyStats.mill_id == user.mill_id)
    )
    
    result = await db.execute(query)
    all_stats = result.scalars().all()
    
    machines = []
    for s in all_stats:
        status = "normal"
        if s.bearing_risk == BearingRisk.NORMAL:
            status = "normal"
        else:
            status = "warning"

        # Parse health details for breakdown
        health_breakdown = {}
        if s.health_score_details:
            try:
                health_breakdown = json.loads(s.health_score_details)
            except:
                pass

        machines.append({
            "machine_id": s.machine_id,
            "energy_consumption": round(s.total_energy_kwh, 2),
            "carbon_emissions": round(s.total_co2_kg, 2),
            "avg_current": round(s.avg_current_A, 2) if s.avg_current_A else 0,
            "reference_metrics": {
                "baseline_mean": round(s.reference_mean, 2) if s.reference_mean else 0.0,
                "baseline_std": round(s.reference_std, 2) if s.reference_std else 0.0,
                "baseline_p95": round(s.reference_p95, 2) if s.reference_p95 else 0.0
            },
            "health_score": round(s.health_score, 1),
            "health_score_breakdown": health_breakdown,
            "status": status
        })
    
    return machines

@router.get("/machines/{machine_id}/trends")
async def get_machine_trends(
    machine_id: str,
    range: str = "7d",
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user = await get_api_key_user(x_api_key, db)
    
    days = 7
    if range == "30d":
        days = 30
    
    start_date = date.today() - timedelta(days=days)
    
    query = (
        select(MachineDailyStats)
        .where(
            MachineDailyStats.mill_id == user.mill_id,
            MachineDailyStats.machine_id == machine_id,
            MachineDailyStats.date >= start_date
        )
        .order_by(MachineDailyStats.date.asc())
    )
    
    result = await db.execute(query)
    trends = result.scalars().all()
    
    return [
        {
            "date": t.date,
            "energy_kwh": round(t.total_energy_kwh, 2),
            "carbon_kg": round(t.total_co2_kg, 2)
        } for t in trends
    ]
