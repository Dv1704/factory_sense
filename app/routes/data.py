from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import insert
from typing import List, Optional
import pandas as pd
import io
import gzip
import shutil
import os
from datetime import date, datetime

from app.core.database import get_db
from app.models.user import User
from app.models.mill_data import RawFile, MachineDailyStats, BearingRisk, MachineDataPoint, Alert, AlertType
from app.core.config import settings
from app.core import physics, analysis
from sqlalchemy.sql import text

router = APIRouter()

async def check_db_connection(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

MACHINE_SPECS = {
    "1BK1": {"name": "1st Break Roll", "max_a": 25.0},
    "1BK2": {"name": "1st Break Roll", "max_a": 25.0},
    "2BK1": {"name": "2nd Break Roll", "max_a": 25.0},
    "2BK2": {"name": "2nd Break Roll", "max_a": 25.0},
    "AF1": {"name": "1st Scratch Roll", "max_a": 25.0},
    "AF2": {"name": "2nd Scratch Roll", "max_a": 25.0},
    "AC1": {"name": "1st Coarse Roll", "max_a": 25.0},
    "AC2": {"name": "2nd Coarse Roll", "max_a": 25.0},
    "3BK_C": {"name": "3rd Break Roll (Coarse)", "max_a": 20.0},
    "3BK_F": {"name": "3rd Break Roll (Fine)", "max_a": 20.0},
    "X": {"name": "X-Roll", "max_a": 20.0},
    "5BK": {"name": "5th Break Roll", "max_a": 18.0},
    "4BK_F": {"name": "4th Break Roll (Fine)", "max_a": 14.0},
    "4BK_C": {"name": "4th Break Roll (Coarse)", "max_a": 28.0},
}

async def get_api_key_user(x_api_key: str = Header(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.api_key == x_api_key))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return user

async def insert_data_points_task(data_points: List[dict], db_factory):
    """Background task to insert raw data points in chunks."""
    if not data_points:
        return
    chunk_size = 5000
    for i in range(0, len(data_points), chunk_size):
        chunk = data_points[i:i + chunk_size]
        try:
            async with db_factory() as db:
                await db.execute(insert(MachineDataPoint).values(chunk))
                await db.commit()
        except Exception as e:
            # In production, we'd log this properly.
            print(f"Background insertion error at chunk {i}: {e}")

@router.post("/upload")
async def upload_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user = await get_api_key_user(x_api_key, db)
    
    content = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    except pd.errors.EmptyDataError:
        return {"status": "success", "records_processed": 0, "message": "File was empty, no records processed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid CSV format: {str(e)}")
    
    if df.empty:
        return {"status": "success", "records_processed": 0, "message": "File has no data rows, no records processed"}
    
    required_cols = {'timestamp', 'mill_id', 'machine_id', 'current_A', 'motor_state'}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(missing_cols)}")

    # Specific row-level cleaning
    df = df.dropna(subset=['timestamp', 'machine_id', 'mill_id'])
    df['current_A'] = pd.to_numeric(df['current_A'], errors='coerce').fillna(0.0)
    df['motor_state'] = df['motor_state'].astype(str).str.upper().str.strip()
    
    if df.empty:
        return {"status": "success", "records_processed": 0, "message": "No valid data rows found after cleaning"}

    file_path = f"data/raw/mill_{user.mill_id}/{file.filename}.gz"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with gzip.open(file_path, 'wb') as f_out:
        f_out.write(content)
        
    V = settings.voltage
    PF = settings.power_factor
    EFF = settings.efficiency
    EF = settings.grid_emission_factor
    SQRT3 = 1.7320508075688772
    
    # Ensure UTC
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['power_kw'] = (SQRT3 * V * df['current_A'] * PF * EFF) / 1000
    df['energy_kwh'] = df['power_kw'] / 60
    df['co2_kg'] = df['energy_kwh'] * EF
    
    data_points = df.to_dict('records')

    file_date = df['timestamp'].dt.date.iloc[0]
    machines = df['machine_id'].unique().tolist()
    
    # Pre-fetch history for risk analysis (optimized to avoid OOM)
    history_map = {}
    for machine_id in machines:
        try:
            h_result = await db.execute(
                select(MachineDataPoint.current_A)
                .where(MachineDataPoint.machine_id == machine_id)
                .order_by(MachineDataPoint.timestamp.desc())
                .limit(1440)
            )
            history_map[machine_id] = h_result.scalars().all()
        except Exception as e:
            print(f"Error pre-fetching history for machine {machine_id}: {e}")
            history_map[machine_id] = []

    processing_errors = []
    processed_count = 0
    for machine_id in machines:
        try:
            m_df = df[df['machine_id'] == machine_id]
            spec = MACHINE_SPECS.get(machine_id, {"max_a": 25.0})
            
            total_kwh = m_df['energy_kwh'].sum()
            total_co2 = m_df['co2_kg'].sum()
            run_hours = len(m_df[m_df['motor_state'] == 'RUNNING']) / 60.0
            
            baseline_kwh = analysis.calculate_baseline_kwh(m_df, spec["max_a"])
            running_mask = m_df['motor_state'] == 'RUNNING'
            excess_kwh_sum = (m_df.loc[running_mask, 'energy_kwh'] - baseline_kwh).clip(lower=0).sum()
            excess_co2_sum = excess_kwh_sum * EF
            
            combined_history = (m_df['current_A'].tolist() + history_map.get(machine_id, []))[:1440]
            risk = analysis.assess_bearing_risk(combined_history, spec["max_a"])
            health_score = analysis.calculate_health_score_refined(excess_co2_sum, total_co2, risk)
            
            stats = MachineDailyStats(
                date=file_date, mill_id=user.mill_id, machine_id=machine_id,
                total_energy_kwh=total_kwh, baseline_kwh=baseline_kwh, excess_kwh=excess_kwh_sum,
                total_co2_kg=total_co2, excess_co2_kg=excess_co2_sum,
                bearing_risk=risk, health_score=health_score, run_hours=run_hours,
                avg_current=m_df['current_A'].mean(),
                max_current=m_df['current_A'].max(),
                load_ratio=(m_df['current_A'].mean() / spec["max_a"]) * 100 if spec["max_a"] > 0 else 0
            )
            db.add(stats)
            
            # Identify Alerts: Check if max_current exceeds safety threshold
            if stats.max_current > spec["max_a"]:
                alert = Alert(
                    mill_id=user.mill_id,
                    machine_id=machine_id,
                    type=AlertType.HIGH_LOAD,
                    message=f"Machine {machine_id} exceeded safety threshold: {stats.max_current}A > {spec['max_a']}A"
                )
                db.add(alert)
            processed_count += 1
        except Exception as e:
            msg = f"Error processing machine {machine_id}: {str(e)}"
            print(msg)
            processing_errors.append(msg)

    raw_file = RawFile(mill_id=user.mill_id, filename=file.filename, status="PROCESSED")
    db.add(raw_file)
    await db.commit()

    from app.core.database import AsyncSessionLocal
    background_tasks.add_task(insert_data_points_task, data_points, AsyncSessionLocal)
    
    return {
        "status": "success", 
        "records_processed": len(df), 
        "machines_processed": processed_count,
        "performance": "optimized",
        "errors": processing_errors
    }

@router.get("/data/history")
async def get_upload_history(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user = await get_api_key_user(x_api_key, db)
    result = await db.execute(
        select(RawFile)
        .where(RawFile.mill_id == user.mill_id)
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

@router.get("/mill/{mill_id}/summary")
async def get_summary(
    mill_id: str, 
    start_date: Optional[date] = None, 
    end_date: Optional[date] = None,
    machine_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    is_db_connected = await check_db_connection(db)
    
    query = select(MachineDailyStats).where(MachineDailyStats.mill_id == mill_id)
    
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
            
    total_excess_co2 = sum(s.excess_co2_kg for s in latest_stats.values())
    total_excess_kwh = sum(s.excess_kwh for s in latest_stats.values())
    avoidable_cost = total_excess_kwh * 0.15
    
    machine_analytics = []
    for machine_id, s in latest_stats.items():
        spec = MACHINE_SPECS.get(machine_id, {"name": f"Machine {machine_id}"})
        insights = analysis.generate_machine_insights(s.excess_co2_kg, s.bearing_risk, s.health_score)
        
        machine_analytics.append({
            "machine_id": machine_id,
            "name": spec.get("name", f"Machine {machine_id}"),
            "total_co2_kg": round(s.total_co2_kg, 2),
            "total_energy_kwh": round(s.total_energy_kwh, 2),
            "excess_co2_kg": round(s.excess_co2_kg, 2),
            "kilowatt_usage": round(s.total_energy_kwh / (s.run_hours if s.run_hours > 0 else 1), 2),
            "health_score": round(s.health_score, 1),
            "bearing_risk": s.bearing_risk,
            "insights": insights
        })
        
    return {
        "mill_id": mill_id,
        "db_connected": is_db_connected,
        "last_updated": datetime.now().isoformat(),
        "summary_metrics": {
            "total_excess_co2_kg": round(total_excess_co2, 2),
            "avoidable_cost_usd": round(avoidable_cost, 2),
        },
        "machines": machine_analytics
    }
