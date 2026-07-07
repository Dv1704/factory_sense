import json
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mill_data import BearingRisk
from app.repositories import alert_repository, stats_repository
from app.services.data_service import MACHINE_SPECS


async def get_machine_specs() -> dict:
    """Returns the safety thresholds and metadata for all machines."""
    # Remove max_a from specs before returning
    return {k: {"name": v["name"]} for k, v in MACHINE_SPECS.items()}


async def get_dashboard_summary(db: AsyncSession, user_id: int, target_date: Optional[date]) -> dict:
    if not target_date:
        target_date = await stats_repository.get_latest_date_for_user(db, user_id)

    if not target_date:
        return {
            "total_energy_kwh": 0,
            "total_co2_kg": 0,
            "machine_count": 0,
            "active_alerts_count": 0,
            "date": None,
        }

    stats = await stats_repository.get_for_date(db, user_id, target_date)
    alerts_count = await alert_repository.count_open(db, user_id=user_id)

    total_energy = sum(s.total_energy_kwh for s in stats)
    total_co2 = sum(s.total_co2_kg for s in stats)

    return {
        "total_energy_kwh": round(total_energy, 2),
        "total_co2_kg": round(total_co2, 2),
        "machine_count": len(stats),
        "active_alerts_count": alerts_count,
        "date": target_date,
    }


async def get_machines(db: AsyncSession, user_id: int) -> list:
    all_stats = await stats_repository.get_latest_per_machine(db, user_id)

    machines = []
    for s in all_stats:
        status = "normal" if s.bearing_risk == BearingRisk.NORMAL else "warning"

        health_breakdown = {}
        if s.health_score_details:
            try:
                health_breakdown = json.loads(s.health_score_details)
            except Exception:
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
                "baseline_p95": round(s.reference_p95, 2) if s.reference_p95 else 0.0,
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


async def get_machine_trends(db: AsyncSession, user_id: int, machine_id: str, range_: str) -> list:
    days = 7
    window_sizes = [7]
    if range_ == "30d":
        days = 30
        window_sizes = [7, 30]

    # Fetch extra days to compute rolling averages accurately
    fetch_days = days + max(window_sizes)
    start_date = date.today() - timedelta(days=fetch_days)

    trends = await stats_repository.get_trends(db, user_id, machine_id, start_date)

    if not trends:
        return []

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

    df['rolling_7d_current'] = df['avg_current'].rolling(window=7, min_periods=1).mean()
    if 30 in window_sizes:
        df['rolling_30d_current'] = df['avg_current'].rolling(window=30, min_periods=1).mean()
    else:
        df['rolling_30d_current'] = None

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
