from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta
import json
from pydantic import BaseModel

from app.core.database import get_db
from app.models.user import User, Mill
from app.models.mill_data import MachineDailyStats, Alert, AlertStatus, MachineDataPoint, BearingRisk
from app.routes.data import get_api_key_mill, MACHINE_SPECS

router = APIRouter()

class ReferenceMetrics(BaseModel):
    baseline_mean: float
    baseline_std: float
    baseline_p95: float

class AvailabilityMetrics(BaseModel):
    data_coverage_hours: Optional[float]
    data_availability_pct: Optional[float]   # % of the 24h day we received data for
    gap_count: Optional[int]                  # readings where gap > 2× sampling interval
    max_gap_minutes: Optional[float]          # longest single silence period
    avg_sampling_interval_minutes: Optional[float]

class MachineSummaryResponse(BaseModel):
    machine_id: str
    energy_consumption: float
    carbon_emissions: float
    avg_current: float
    run_hours: float
    reference_metrics: ReferenceMetrics
    health_score: float
    health_score_breakdown: Dict[str, Any]
    status: str
    availability: AvailabilityMetrics

class MachineTrendResponse(BaseModel):
    date: date
    energy_kwh: float
    carbon_kg: float
    avg_current: float
    run_hours: float
    health_score: float
    data_coverage_hours: Optional[float]
    data_availability_pct: Optional[float]
    gap_count: Optional[int]
    max_gap_minutes: Optional[float]
    rolling_7d_current: Optional[float]
    rolling_30d_current: Optional[float]

class DashboardSummaryResponse(BaseModel):
    total_energy_kwh: float
    total_co2_kg: float
    machine_count: int
    active_alerts_count: int
    date: Optional[date]

@router.get("/machine-specs")
async def get_machine_specs(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    """Returns the safety thresholds and metadata for all machines."""
    # Remove max_a from specs before returning
    clean_specs = {k: {"name": v["name"]} for k, v in MACHINE_SPECS.items()}
    return clean_specs

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    date: Optional[date] = None,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    mill = await get_api_key_mill(x_api_key, db)
    
    if not date:
        # Get latest date available for this mill
        latest_date_query = select(func.max(MachineDailyStats.date)).where(MachineDailyStats.user_id == mill.user_id)
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
        MachineDailyStats.user_id == mill.user_id,
        MachineDailyStats.date == date
    )
    result = await db.execute(stats_query)
    stats = result.scalars().all()

    # Query active alerts (active + acknowledged; excludes resolved)
    alerts_query = select(func.count(Alert.id)).where(
        Alert.user_id == mill.user_id,
        Alert.status != AlertStatus.resolved,
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

@router.get("/machines", response_model=List[MachineSummaryResponse])
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
        .where(MachineDailyStats.user_id == mill.user_id)
        .group_by(MachineDailyStats.machine_id)
        .subquery()
    )
    
    query = (
        select(MachineDailyStats)
        .join(subquery, (MachineDailyStats.machine_id == subquery.c.machine_id) & (MachineDailyStats.date == subquery.c.max_date))
        .where(MachineDailyStats.user_id == mill.user_id)
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
            "run_hours": round(s.run_hours, 2),
            "reference_metrics": {
                "baseline_mean": round(s.reference_mean, 2) if s.reference_mean else 0.0,
                "baseline_std": round(s.reference_std, 2) if s.reference_std else 0.0,
                "baseline_p95": round(s.reference_p95, 2) if s.reference_p95 else 0.0
            },
            "health_score": round(s.health_score, 1),
            "health_score_breakdown": health_breakdown,
            "status": status,
            "availability": {
                "data_coverage_hours": round(s.data_coverage_hours, 2) if s.data_coverage_hours is not None else None,
                "data_availability_pct": round(s.data_availability_pct, 1) if s.data_availability_pct is not None else None,
                "gap_count": s.gap_count,
                "max_gap_minutes": round(s.max_gap_minutes, 1) if s.max_gap_minutes is not None else None,
                "avg_sampling_interval_minutes": round(s.avg_sampling_interval_minutes, 2) if s.avg_sampling_interval_minutes is not None else None,
            },
        })
    
    return machines

@router.get("/machines/{machine_id}/trends", response_model=List[MachineTrendResponse])
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
            MachineDailyStats.user_id == mill.user_id,
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
        "run_hours": t.run_hours,
        "health_score": t.health_score or 0.0,
        "data_coverage_hours": t.data_coverage_hours,
        "data_availability_pct": t.data_availability_pct,
        "gap_count": t.gap_count,
        "max_gap_minutes": t.max_gap_minutes,
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
            "run_hours": round(row['run_hours'], 2),
            "health_score": round(row['health_score'], 1),
            "data_coverage_hours": round(row['data_coverage_hours'], 2) if pd.notnull(row['data_coverage_hours']) else None,
            "data_availability_pct": round(row['data_availability_pct'], 1) if pd.notnull(row['data_availability_pct']) else None,
            "gap_count": int(row['gap_count']) if pd.notnull(row['gap_count']) else None,
            "max_gap_minutes": round(row['max_gap_minutes'], 1) if pd.notnull(row['max_gap_minutes']) else None,
            "rolling_7d_current": round(row['rolling_7d_current'], 2) if pd.notnull(row['rolling_7d_current']) else None,
            "rolling_30d_current": round(row['rolling_30d_current'], 2) if pd.notnull(row['rolling_30d_current']) else None,
        })

    return response
