from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional
from datetime import date, datetime
import json

from sqlalchemy.sql import text
import logging
import uuid

from app.core.database import get_db, AsyncSessionLocal
from app.models.user import User, Mill
from app.models.mill_data import (
    RawFile, MachineDailyStats, MachineBaseline, MachineBaselineHistory,
    ProcessingTask, ProcessingStatus
)
from app.core import analysis
from app.schemas.data import BaselineUpdate, TaskResponse, UploadResponse
from app.routes.auth import require_manager
from app.core.tasks import process_operational_data, process_baseline_data

logger = logging.getLogger(__name__)

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

router = APIRouter()

async def check_db_connection(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

async def get_api_key_mill(x_api_key: str = Header(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Mill).where(Mill.api_key == x_api_key))
    mill = result.scalars().first()
    if not mill:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    # Check if Owner is verified
    from app.models.user import UserRole
    owner_result = await db.execute(
        select(User).where(User.mill_id == mill.id, User.role == UserRole.OWNER)
    )
    owner = owner_result.scalars().first()
    if owner and not owner.is_verified:
        raise HTTPException(
            status_code=403, 
            detail="Mill owner email verification required"
        )
    return mill

async def _process_baseline_request(background_tasks, file, mill, db, task_type):
    task_id = str(uuid.uuid4())
    content = await file.read()
    
    # Create processing task
    task = ProcessingTask(
        task_id=task_id,
        mill_id=mill.id,
        mill_tag=mill.mill_tag,
        filename=file.filename,
        task_type=task_type,
        status=ProcessingStatus.PENDING
    )
    db.add(task)
    await db.commit()

    # Trigger background task
    background_tasks.add_task(
        process_baseline_data,
        task_id,
        content,
        mill.id,
        mill.mill_tag,
        AsyncSessionLocal
    )
    
    return {
        "task_id": task_id,
        "message": f"{task_type.replace('_', ' ').capitalize()} received. Processing started in background.",
        "estimated_initial_seconds": 3.0
    }

@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="[FILE UPLOAD] Operational Data CSV",
    description="Uploads a CSV file with machine operational data. Processing is done in the background. Large files handled efficiently."
)
async def upload_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="The CSV file containing operational sensor data"),
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    if not mill.has_uploaded_baseline:
        raise HTTPException(
            status_code=403, 
            detail="You must upload baseline data before uploading operational data. Use /api/v1/baseline/upload."
        )

    task_id = str(uuid.uuid4())
    content = await file.read()
    
    # Save raw file entry
    raw_file = RawFile(mill_id=mill.id, mill_tag=mill.mill_tag, filename=file.filename, status="PENDING")
    db.add(raw_file)
    
    # Create processing task
    task = ProcessingTask(
        task_id=task_id,
        mill_id=mill.id,
        mill_tag=mill.mill_tag,
        filename=file.filename,
        task_type="OPERATIONAL_DATA",
        status=ProcessingStatus.PENDING
    )
    db.add(task)
    await db.commit()

    # Trigger background task
    background_tasks.add_task(
        process_operational_data,
        task_id,
        content,
        mill.id,
        mill.mill_tag,
        file.filename,
        AsyncSessionLocal
    )
    
    return {
        "task_id": task_id,
        "message": "Upload received. Processing started in background.",
        "estimated_initial_seconds": 5.0
    }

@router.post(
    "/baseline/update",
    response_model=UploadResponse,
    summary="[FILE UPLOAD] Incremental Bulk Baseline Update (CSV)",
    description="Uploads a CSV to INCREMENTALLY refine existing baselines for multiple machines."
)
async def bulk_update_baseline(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="The CSV file containing baseline measurements for multiple machines"),
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    return await _process_baseline_request(background_tasks, file, mill, db, "INCREMENTAL_BASELINE_UPDATE")

@router.post(
    "/baseline/upload",
    response_model=UploadResponse,
    summary="[FILE UPLOAD] Initial Mill Baseline (CSV)",
    description="Establish INITIAL machine baselines for the mill."
)
async def upload_baseline(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="The CSV file containing the initial baseline measurements"),
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    return await _process_baseline_request(background_tasks, file, mill, db, "INITIAL_BASELINE_LOAD")

@router.get(
    "/task/{task_id}",
    response_model=TaskResponse,
    summary="Get Task Status",
    description="Polling endpoint to check background task status."
)
async def get_task_status(
    task_id: str,
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(ProcessingTask).where(
        ProcessingTask.task_id == task_id,
        ProcessingTask.mill_id == mill.id
    )
    result = await db.execute(stmt)
    task = result.scalars().first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    return task

@router.get("/data/history", summary="View Upload History")
async def get_upload_history(
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(RawFile)
        .where(RawFile.mill_id == mill.id)
        .order_by(RawFile.upload_timestamp.desc())
    )
    history = result.scalars().all()
    return [
        {
            "filename": h.filename,
            "timestamp": h.upload_timestamp,
            "status": h.status
        } for h in history
    ]

@router.get("/baseline", summary="List Current Baselines")
async def get_baselines(
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(MachineBaseline).where(MachineBaseline.mill_id == mill.id))
    baselines = result.scalars().all()
    return [
        {
            "machine_id": b.machine_id,
            "mean_current": b.mean_current,
            "std_current": b.std_current,
            "p95_current": b.p95_current,
            "updated_at": b.updated_at
        } for b in baselines
    ]

@router.get("/baseline/history", summary="View Global Baseline History")
async def get_baseline_history(
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(MachineBaselineHistory)
        .where(MachineBaselineHistory.mill_id == mill.id)
        .order_by(MachineBaselineHistory.timestamp.desc())
    )
    history = result.scalars().all()
    return [
        {
            "machine_id": h.machine_id,
            "mean_current": h.mean_current,
            "std_current": h.std_current,
            "p95_current": h.p95_current,
            "data_points_count": h.data_points_count,
            "update_type": h.update_type,
            "timestamp": h.timestamp
        } for h in history
    ]

@router.get("/baseline/{machine_id}/history", summary="View Machine Baseline History")
async def get_machine_baseline_history(
    machine_id: str,
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(MachineBaselineHistory)
        .where(
            MachineBaselineHistory.mill_id == mill.id,
            MachineBaselineHistory.machine_id == machine_id
        )
        .order_by(MachineBaselineHistory.timestamp.desc())
    )
    history = result.scalars().all()
    return [
        {
            "mean_current": h.mean_current,
            "std_current": h.std_current,
            "p95_current": h.p95_current,
            "data_points_count": h.data_points_count,
            "update_type": h.update_type,
            "timestamp": h.timestamp
        } for h in history
    ]

@router.put("/baseline/{machine_id}", summary="[MANUAL OVERRIDE - NO FILE] Update Single Baseline (JSON)")
async def manual_update_baseline(
    machine_id: str,
    baseline_data: BaselineUpdate,
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(MachineBaseline).where(
            MachineBaseline.mill_id == mill.id,
            MachineBaseline.machine_id == machine_id
        )
    )
    baseline = result.scalars().first()
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    
    baseline.mean_current = baseline_data.mean_current
    baseline.std_current = baseline_data.std_current
    baseline.p95_current = baseline_data.p95_current
    
    await db.commit()
    return {"status": "success", "message": f"Baseline for {machine_id} updated"}

@router.delete("/baseline/{machine_id}", summary="Delete Machine Baseline")
async def delete_baseline(
    machine_id: str,
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(MachineBaseline).where(
            MachineBaseline.mill_id == mill.id,
            MachineBaseline.machine_id == machine_id
        )
    )
    baseline = result.scalars().first()
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    
    await db.delete(baseline)
    await db.commit()
    return {"status": "success", "message": f"Baseline for {machine_id} deleted"}

@router.get("/mill/{mill_id}/summary", summary="Get Mill Operational Summary")
async def get_summary(
    mill_id: str, 
    start_date: Optional[date] = None, 
    end_date: Optional[date] = None,
    machine_id: Optional[str] = None,
    mill: Mill = Depends(get_api_key_mill),
    db: AsyncSession = Depends(get_db)
):
    is_db_connected = await check_db_connection(db)
    
    query = select(MachineDailyStats).where(MachineDailyStats.mill_id == mill.id)
    
    if start_date:
        query = query.where(MachineDailyStats.date >= start_date)
    if end_date:
        query = query.where(MachineDailyStats.date <= end_date)
    if machine_id:
        query = query.where(MachineDailyStats.machine_id == machine_id)
        
    result = await db.execute(query.order_by(MachineDailyStats.date.desc(), MachineDailyStats.id.desc()))
    all_stats = result.scalars().all()
    
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

    for machine_id, s in latest_stats.items():
        spec = MACHINE_SPECS.get(machine_id, {"name": f"Machine {machine_id}"})
        insights = analysis.generate_machine_insights(s.excess_co2_kg, s.bearing_risk, s.health_score)
        
        health_breakdown = {}
        if s.health_score_details:
            try:
                health_breakdown = json.loads(s.health_score_details)
            except (json.JSONDecodeError, TypeError):
                pass

        machine_analytics.append({
            "machine_id": machine_id,
            "name": spec.get("name", f"Machine {machine_id}"),
            "total_co2_kg": round(s.total_co2_kg, 2),
            "total_energy_kwh": round(s.total_energy_kwh, 2),
            "run_hours": round(s.run_hours, 1),
            "avg_current_A": round(s.avg_current_A, 2) if s.avg_current_A else 0.0,
            "reference_metrics": {
                "baseline_mean": round(s.reference_mean, 2) if s.reference_mean else 0.0,
                "baseline_std": round(s.reference_std, 2) if s.reference_std else 0.0,
                "baseline_p95": round(s.reference_p95, 2) if s.reference_p95 else 0.0
            },
            "health_score": round(s.health_score, 1),
            "health_score_breakdown": health_breakdown,
            "bearing_risk": s.bearing_risk,
            "excess_co2_kg": round(s.excess_co2_kg, 2),
            "insights": insights
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
        "machines": machine_analytics
    }
