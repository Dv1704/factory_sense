import json
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.core import analysis
from app.core.database import AsyncSessionLocal
from app.core.tasks import process_operational_data, process_baseline_data
from app.repositories import baseline_repository, data_point_repository, stats_repository, task_repository, alert_repository

MACHINE_SPECS = {
    "1BK1": {"name": "1st Break Roll"},
    "1BK2": {"name": "1st Break Roll"},
    "2BK1": {"name": "2nd Break Roll"},
    "2BK2": {"name": "2nd Break Roll"},
    "AF1": {"name": "1st Scratch Roll"},
    "AF2": {"name": "2nd Scratch Roll"},
    "AC1": {"name": "1st Coarse Roll"},
    "AC2": {"name": "2nd Coarse Roll"},
    "3BK_C": {"name": "3rd Break Roll (Coarse)"},
    "3BK_F": {"name": "3rd Break Roll (Fine)"},
    "X": {"name": "X-Roll"},
    "5BK": {"name": "5th Break Roll"},
    "4BK_F": {"name": "4th Break Roll (Fine)"},
    "4BK_C": {"name": "4th Break Roll (Coarse)"},
}


async def check_db_connection(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _process_upload_request(background_tasks, file, mill, db, task_type, background_fn, message, estimated_seconds):
    task_id = str(uuid.uuid4())
    content = await file.read()

    await task_repository.create_raw_file(db, user_id=mill.user_id, mill_id=mill.mill_id, filename=file.filename)
    await task_repository.create_processing_task(
        db, task_id=task_id, user_id=mill.user_id, mill_id=mill.mill_id,
        filename=file.filename, task_type=task_type,
    )
    await db.commit()

    background_tasks.add_task(
        background_fn, task_id, content, mill.user_id, mill.mill_id, file.filename, AsyncSessionLocal
    )

    return {
        "task_id": task_id,
        "message": message,
        "estimated_initial_seconds": estimated_seconds,
    }


async def process_operational_upload(background_tasks, file, mill, db) -> dict:
    if not mill.has_uploaded_baseline:
        raise HTTPException(
            status_code=403,
            detail="You must upload baseline data before uploading operational data. Use /api/v1/baseline/upload.",
        )
    return await _process_upload_request(
        background_tasks, file, mill, db, "OPERATIONAL_DATA", process_operational_data,
        "Upload received. Processing started in background.", 5.0,
    )


async def process_baseline_upload(background_tasks, file, mill, db, task_type: str) -> dict:
    message = f"{task_type.replace('_', ' ').capitalize()} received. Processing started in background."
    return await _process_upload_request(
        background_tasks, file, mill, db, task_type, process_baseline_data, message, 3.0
    )


async def get_task_status(db: AsyncSession, task_id: str, user_id: int):
    task = await task_repository.get_by_task_id(db, task_id, user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def get_upload_history(db: AsyncSession, user_id: int) -> list:
    history = await task_repository.list_upload_history(db, user_id)
    return [
        {"filename": h.filename, "timestamp": h.upload_timestamp, "status": h.status}
        for h in history
    ]


async def list_baselines(db: AsyncSession, user_id: int) -> list:
    baselines = await baseline_repository.list_for_user(db, user_id)
    return [
        {
            "machine_id": b.machine_id,
            "mean_current": b.mean_current,
            "std_current": b.std_current,
            "p95_current": b.p95_current,
            "updated_at": b.updated_at,
        }
        for b in baselines
    ]


async def list_baseline_history(db: AsyncSession, user_id: int) -> list:
    history = await baseline_repository.list_history_for_user(db, user_id)
    return [
        {
            "machine_id": h.machine_id,
            "mean_current": h.mean_current,
            "std_current": h.std_current,
            "p95_current": h.p95_current,
            "data_points_count": h.data_points_count,
            "update_type": h.update_type,
            "timestamp": h.timestamp,
        }
        for h in history
    ]


async def list_machine_baseline_history(db: AsyncSession, user_id: int, machine_id: str) -> list:
    history = await baseline_repository.list_history_for_machine(db, user_id, machine_id)
    return [
        {
            "mean_current": h.mean_current,
            "std_current": h.std_current,
            "p95_current": h.p95_current,
            "data_points_count": h.data_points_count,
            "update_type": h.update_type,
            "timestamp": h.timestamp,
        }
        for h in history
    ]


async def update_baseline(db: AsyncSession, user_id: int, machine_id: str, baseline_data) -> dict:
    baseline = await baseline_repository.get_for_machine(db, user_id, machine_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")

    baseline.mean_current = baseline_data.mean_current
    baseline.std_current = baseline_data.std_current
    baseline.p95_current = baseline_data.p95_current

    await db.commit()
    return {"status": "success", "message": f"Baseline for {machine_id} updated"}


async def delete_baseline(db: AsyncSession, user_id: int, machine_id: str) -> dict:
    baseline = await baseline_repository.get_for_machine(db, user_id, machine_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")

    await db.delete(baseline)
    await db.commit()
    return {"status": "success", "message": f"Baseline for {machine_id} deleted"}


async def delete_all_baseline(db: AsyncSession, mill) -> dict:
    """Deletes all baseline data and baseline history for the mill."""
    await baseline_repository.delete_for_mill(db, mill.user_id, mill.mill_id)
    mill.has_uploaded_baseline = False
    await db.commit()
    return {"status": "success", "message": "All baseline data for the mill has been deleted"}


async def delete_all_operational(db: AsyncSession, mill) -> dict:
    """Deletes all machine data points, daily statistics, and alerts for the mill."""
    await data_point_repository.delete_for_mill(db, mill.user_id, mill.mill_id)
    await stats_repository.delete_for_mill(db, mill.user_id, mill.mill_id)
    await alert_repository.delete_for_mill(db, mill.user_id, mill.mill_id)
    await db.commit()
    return {"status": "success", "message": "All operational data for the mill has been deleted"}


async def get_mill_summary(db: AsyncSession, mill_id: str, user_id: int, *,
                           start_date: Optional[date], end_date: Optional[date],
                           machine_id: Optional[str]) -> dict:
    is_db_connected = await check_db_connection(db)

    all_stats = await stats_repository.get_range(
        db, user_id, start_date=start_date, end_date=end_date, machine_id=machine_id
    )

    latest_stats = {}
    for s in all_stats:
        if s.machine_id not in latest_stats:
            latest_stats[s.machine_id] = s

    total_co2_kg = sum(s.total_co2_kg for s in latest_stats.values())
    total_energy_kwh = sum(s.total_energy_kwh for s in latest_stats.values())
    total_excess_co2 = sum(s.excess_co2_kg for s in latest_stats.values())
    total_excess_kwh = sum(s.excess_kwh for s in latest_stats.values())
    avoidable_cost = total_excess_kwh * 0.15

    machine_analytics = []
    for m_id, s in latest_stats.items():
        spec = MACHINE_SPECS.get(m_id, {"name": f"Machine {m_id}"})
        insights = analysis.generate_machine_insights(s.excess_co2_kg, s.bearing_risk, s.health_score)

        health_breakdown = {}
        if s.health_score_details:
            try:
                health_breakdown = json.loads(s.health_score_details)
            except Exception:
                pass

        machine_analytics.append({
            "machine_id": m_id,
            "name": spec.get("name", f"Machine {m_id}"),
            "total_co2_kg": round(s.total_co2_kg, 2),
            "total_energy_kwh": round(s.total_energy_kwh, 2),
            "run_hours": round(s.run_hours, 1),
            "avg_current_A": round(s.avg_current_A, 2) if s.avg_current_A else 0.0,
            "reference_metrics": {
                "baseline_mean": round(s.reference_mean, 2) if s.reference_mean else 0.0,
                "baseline_std": round(s.reference_std, 2) if s.reference_std else 0.0,
                "baseline_p95": round(s.reference_p95, 2) if s.reference_p95 else 0.0,
            },
            "health_score": round(s.health_score, 1),
            "health_score_breakdown": health_breakdown,
            "bearing_risk": s.bearing_risk,
            "excess_co2_kg": round(s.excess_co2_kg, 2),
            "insights": insights,
        })

    return {
        "mill_id": mill_id,
        "db_connected": is_db_connected,
        "last_updated": datetime.now().isoformat(),
        "summary_metrics": {
            "total_energy_kwh": round(total_energy_kwh, 2),
            "total_co2_kg": round(total_co2_kg, 2),
            "total_excess_co2_kg": round(total_excess_co2, 2),
            "avoidable_cost_usd": round(avoidable_cost, 2),
        },
        "machines": machine_analytics,
    }
