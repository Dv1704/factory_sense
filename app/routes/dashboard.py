from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import Optional
from datetime import date, timedelta
import json

from app.core.database import get_db
from app.models.user import User
from app.models.mill_data import MachineDailyStats, Alert, BearingRisk
from app.routes.data import get_api_key_mill, MACHINE_SPECS
from app.routes.auth import require_owner

router = APIRouter()

@router.get("/machine-specs")
async def get_machine_specs(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    await get_api_key_mill(x_api_key, db)
    return {k: {"name": v["name"]} for k, v in MACHINE_SPECS.items()}

@router.get("/billing")
async def get_billing_info(
    current_user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db)
):
    return {
        "status": "active",
        "plan": "Enterprise Pro",
        "next_billing_date": "2026-04-01",
        "amount_due": "$499.00"
    }

@router.get("/summary")
async def get_dashboard_summary(
    date: Optional[date] = None,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    mill = await get_api_key_mill(x_api_key, db)
    
    if not date:
        latest_date_query = select(func.max(MachineDailyStats.date)).where(MachineDailyStats.mill_id == mill.id)
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
        MachineDailyStats.mill_id == mill.id,
        MachineDailyStats.date == date
    )
    result = await db.execute(stats_query)
    stats = result.scalars().all()

    alerts_query = select(func.count(Alert.id)).where(
        Alert.mill_id == mill.id,
        Alert.is_acknowledged.is_(False)
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
    mill = await get_api_key_mill(x_api_key, db)
    
    # Get latest stats for each machine
    subquery = (
        select(
            MachineDailyStats.machine_id,
            func.max(MachineDailyStats.date).label("max_date")
        )
        .where(MachineDailyStats.mill_id == mill.id)
        .group_by(MachineDailyStats.machine_id)
        .subquery()
    )
    
    query = (
        select(MachineDailyStats)
        .join(subquery, (MachineDailyStats.machine_id == subquery.c.machine_id) & (MachineDailyStats.date == subquery.c.max_date))
        .where(MachineDailyStats.mill_id == mill.id)
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
            except (json.JSONDecodeError, TypeError):
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
    mill = await get_api_key_mill(x_api_key, db)
    
    days = 7
    window_sizes = [7]
    if range == "30d":
        days = 30
        window_sizes = [7, 30]
    
    # Fetch extra days to compute rolling averages accurately
    fetch_days = days + max(window_sizes)
    start_date = date.today() - timedelta(days=fetch_days)
    
    query = (
        select(MachineDailyStats)
        .where(
            MachineDailyStats.mill_id == mill.id,
            MachineDailyStats.machine_id == machine_id,
            MachineDailyStats.date >= start_date
        )
        .order_by(MachineDailyStats.date.asc())
    )
    
    result = await db.execute(query)
    trends = result.scalars().all()
    
    if not trends:
        return []
        
    import pandas as pd
    df = pd.DataFrame([{
        "date": t.date,
        "energy_kwh": t.total_energy_kwh,
        "carbon_kg": t.total_co2_kg,
        "avg_current": t.avg_current_A or 0.0,
        "health_score": t.health_score or 0.0
    } for t in trends])
    
    # Calculate rolling averages
    df['rolling_7d_current'] = df['avg_current'].rolling(window=7, min_periods=1).mean()
    if 30 in window_sizes:
        df['rolling_30d_current'] = df['avg_current'].rolling(window=30, min_periods=1).mean()
    else:
        df['rolling_30d_current'] = None
        
    # Filter to requested range
    expected_start = date.today() - timedelta(days=days)
    df_filtered = df[df['date'] >= expected_start]
    
    response = []
    for _, row in df_filtered.iterrows():
        response.append({
            "date": row['date'],
            "energy_kwh": round(row['energy_kwh'], 2),
            "carbon_kg": round(row['carbon_kg'], 2),
            "avg_current": round(row['avg_current'], 2),
            "health_score": round(row['health_score'], 1),
            "rolling_7d_current": round(row['rolling_7d_current'], 2) if pd.notnull(row['rolling_7d_current']) else None,
            "rolling_30d_current": round(row['rolling_30d_current'], 2) if pd.notnull(row['rolling_30d_current']) else None
        })
        
    return response
