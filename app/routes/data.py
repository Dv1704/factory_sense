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
from datetime import date, datetime, timedelta
import json

from app.core.database import get_db
from app.models.user import User
from app.models.mill_data import RawFile, MachineDailyStats, BearingRisk, MachineDataPoint, Alert, AlertType, MachineBaseline
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
    chunk_size = 500
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

    # Ensure 'date' column for grouping
    df['date'] = df['timestamp'].dt.date
    dates_in_file = sorted(df['date'].unique().tolist())
    machines = df['machine_id'].unique().tolist()
    
    # 1. Batch fetch history for all machines (using specific query for efficiency)
    history_map = {}
    try:
        h_result = await db.execute(
            select(MachineDataPoint.machine_id, MachineDataPoint.current_A)
            .where(MachineDataPoint.machine_id.in_(machines))
            .order_by(MachineDataPoint.machine_id, MachineDataPoint.timestamp.desc())
        )
        temp_history = {}
        for row in h_result.all():
            m_id, val = row[0], row[1]
            if m_id not in temp_history:
                temp_history[m_id] = []
            if len(temp_history[m_id]) < 1440:
                temp_history[m_id].append(val)
        history_map = temp_history
    except Exception as e:
        print(f"Error pre-fetching history: {e}")

    # 2. Batch fetch ALL relevant stats (dates in file + their previous days)
    try:
        all_relevant_dates = set(dates_in_file)
        for d in dates_in_file:
            all_relevant_dates.add(d - timedelta(days=1))
            
        stats_result = await db.execute(
            select(MachineDailyStats)
            .where(
                MachineDailyStats.mill_id == user.mill_id,
                MachineDailyStats.machine_id.in_(machines),
                MachineDailyStats.date.in_(list(all_relevant_dates))
            )
        )
        all_stats = stats_result.scalars().all()
        # stats_map format: {(machine_id, date): stats_obj}
        stats_map = {(s.machine_id, s.date): s for s in all_stats}
        
        # 3. Batch check for existing alerts for all machines
        existing_alerts_res = await db.execute(
            select(Alert.machine_id, Alert.timestamp)
            .where(
                Alert.mill_id == user.mill_id,
                Alert.machine_id.in_(machines),
                Alert.type == AlertType.CO2_INCREASE
            )
        )
        # alert_set format: {(machine_id, date)}
        existing_alert_set = set()
        for row in existing_alerts_res.all():
            m_id, ts = row[0], row[1]
            existing_alert_set.add((m_id, ts.date()))
    except Exception as e:
        print(f"Error fetching batch metadata: {e}")
        stats_map, existing_alert_set = {}, set()

    # 1. Batch fetch baselines
    baseline_res = await db.execute(
        select(MachineBaseline).where(MachineBaseline.machine_id.in_(machines), MachineBaseline.mill_id == user.mill_id)
    )
    baselines = {b.machine_id: b for b in baseline_res.scalars().all()}

    # 4. Vectorized processing by grouping (date, machine_id)
    processing_errors = []
    processed_count = 0
    
    try:
        # Calculate base metrics for all combinations at once
        agg_df = df.groupby(['date', 'machine_id']).agg(
            total_kwh=('energy_kwh', 'sum'),
            total_co2=('co2_kg', 'sum'),
            avg_current=('current_A', 'mean'),
            max_current=('current_A', 'max'),
            std_current=('current_A', 'std'),
            run_count=('motor_state', lambda x: (x == 'RUNNING').sum())
        ).reset_index()
        agg_df['run_hours'] = agg_df['run_count'] / 60.0
        
        for _, agg_row in agg_df.iterrows():
            curr_date = agg_row['date']
            m_id = agg_row['machine_id']
            m_df = df[(df['date'] == curr_date) & (df['machine_id'] == m_id)]
            
            # Get or initialize baseline
            baseline = baselines.get(m_id)
            if not baseline:
                # Initialize baseline from current data if none exists
                mu, sigma, p95 = analysis.calculate_baseline_stats(m_df)
                baseline = MachineBaseline(
                    mill_id=user.mill_id, machine_id=m_id,
                    mean_current=mu, std_current=sigma, p95_current=p95
                )
                db.add(baseline)
                baselines[m_id] = baseline
                await db.flush() # Get ID if needed, though not used here

            baseline_mu = baseline.mean_current
            baseline_sigma = baseline.std_current
            baseline_p95 = baseline.p95_current

            # Drift Detection: last 7 days check
            is_drifting = False
            try:
                # Get last 7 days of stats for this machine
                prev_stats_res = await db.execute(
                    select(MachineDailyStats.avg_current_A)
                    .where(
                        MachineDailyStats.mill_id == user.mill_id,
                        MachineDailyStats.machine_id == m_id,
                        MachineDailyStats.date < curr_date
                    )
                    .order_by(MachineDailyStats.date.desc())
                    .limit(7)
                )
                recent_avg_currents = [r[0] for r in prev_stats_res.all()]
                if len(recent_avg_currents) >= 5:
                    # Check if strictly increasing
                    current_series = [agg_row['avg_current']] + recent_avg_currents
                    # We want to see if today > yesterday > day before...
                    # but recent_avg_currents is [yesterday, day-2, ...]
                    increasing = True
                    for i in range(len(current_series) - 1):
                        if current_series[i] <= current_series[i+1]:
                            increasing = False
                            break
                    if increasing:
                        is_drifting = True
            except Exception as e:
                print(f"Error in drift detection for {m_id}: {e}")

            baseline_kwh = analysis.calculate_baseline_kwh(m_df)
            running_mask = m_df['motor_state'] == 'RUNNING'
            excess_kwh_sum = (m_df.loc[running_mask, 'energy_kwh'] - baseline_kwh).clip(lower=0).sum()
            excess_co2_sum = excess_kwh_sum * EF
            
            # Health Score v2
            health_score, health_details = analysis.calculate_health_score_v2(
                float(agg_row['avg_current']), float(agg_row['max_current']),
                baseline_mu, baseline_sigma, baseline_p95, is_drifting
            )
            
            combined_history = (m_df['current_A'].tolist() + history_map.get(m_id, []))[:1440]
            risk = analysis.assess_bearing_risk(combined_history)
            
            # Upsert using pre-fetched map
            stats = stats_map.get((m_id, curr_date))
            if stats:
                stats.total_energy_kwh += float(agg_row['total_kwh'])
                stats.total_co2_kg += float(agg_row['total_co2'])
                stats.run_hours += float(agg_row['run_hours'])
                stats.excess_kwh += float(excess_kwh_sum)
                stats.excess_co2_kg += float(excess_co2_sum)
                stats.avg_current_A = (stats.avg_current_A + float(agg_row['avg_current'])) / 2
                stats.max_current = max(stats.max_current or 0.0, float(agg_row['max_current']))
                stats.std_current = float(agg_row['std_current'])
                stats.health_score = health_score
                stats.health_score_details = json.dumps(health_details)
                stats.reference_mean = float(baseline_mu)
                stats.reference_std = float(baseline_sigma)
                stats.reference_p95 = float(baseline_p95)
            else:
                stats = MachineDailyStats(
                    date=curr_date, mill_id=user.mill_id, machine_id=m_id,
                    total_energy_kwh=float(agg_row['total_kwh']), baseline_kwh=baseline_kwh, excess_kwh=float(excess_kwh_sum),
                    total_co2_kg=float(agg_row['total_co2']), excess_co2_kg=float(excess_co2_sum),
                    bearing_risk=risk, health_score=health_score, run_hours=float(agg_row['run_hours']),
                    avg_current_A=float(agg_row['avg_current']), max_current=float(agg_row['max_current']),
                    std_current=float(agg_row['std_current']),
                    reference_mean=float(baseline_mu), reference_std=float(baseline_sigma), reference_p95=float(baseline_p95),
                    health_score_details=json.dumps(health_details)
                )
                db.add(stats)
                stats_map[(m_id, curr_date)] = stats # Update map
            
            # Alerts for new conditions
            if health_details['load_penalty'] > 0:
                alert = Alert(
                    mill_id=user.mill_id, machine_id=m_id,
                    type=AlertType.WARNING,
                    message=f"Machine {m_id} Load Shift: Mean Current ({float(agg_row['avg_current']):.1f}A) > Baseline + 2*Std"
                )
                db.add(alert)
            if health_details['peak_penalty'] > 0:
                alert = Alert(
                    mill_id=user.mill_id, machine_id=m_id,
                    type=AlertType.WARNING,
                    message=f"Machine {m_id} Peak Stress: Max Current ({float(agg_row['max_current']):.1f}A) > Baseline P95"
                )
                db.add(alert)
            if is_drifting:
                alert = Alert(
                    mill_id=user.mill_id, machine_id=m_id,
                    type=AlertType.WARNING,
                    message=f"Machine {m_id} Drift Detected: Current rising for 5+ days"
                )
                db.add(alert)

            # CO2 Increase Check (Legacy)
            prev_date = curr_date - timedelta(days=1)
            prev_stats = stats_map.get((m_id, prev_date))
            if prev_stats and prev_stats.total_co2_kg > 0:
                increase_pct = ((stats.total_co2_kg - prev_stats.total_co2_kg) / prev_stats.total_co2_kg) * 100
                if increase_pct > 5.0 and (m_id, curr_date) not in existing_alert_set:
                    alert = Alert(
                        mill_id=user.mill_id,
                        machine_id=m_id,
                        type=AlertType.CO2_INCREASE,
                        message=f"Machine {m_id} CO2 increased by {increase_pct:.1f}% compared to previous day"
                    )
                    db.add(alert)
                    existing_alert_set.add((m_id, curr_date))

            processed_count += 1
    except Exception as e:
        msg = f"Error processing grouping: {str(e)}"
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
            
    total_co2_kg = sum(s.total_co2_kg for s in latest_stats.values())
    total_energy_kwh = sum(s.total_energy_kwh for s in latest_stats.values())
    total_excess_co2 = sum(s.excess_co2_kg for s in latest_stats.values())
    total_excess_kwh = sum(s.excess_kwh for s in latest_stats.values())
    avoidable_cost = total_excess_kwh * 0.15
    
    machine_analytics = []
    for machine_id, s in latest_stats.items():
        spec = MACHINE_SPECS.get(machine_id, {"name": f"Machine {machine_id}"})
        insights = analysis.generate_machine_insights(s.excess_co2_kg, s.bearing_risk, s.health_score)
        
        # Parse health details for breakdown
        health_breakdown = {}
        if s.health_score_details:
            try:
                health_breakdown = json.loads(s.health_score_details)
            except:
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
