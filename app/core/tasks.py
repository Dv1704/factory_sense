import pandas as pd
import numpy as np
import io
import gzip
import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import insert, update, text

from app.models.mill_data import (
    MachineDataPoint, MachineDailyStats, Alert, AlertType, 
    MachineBaseline, MachineBaselineHistory, ProcessingTask, ProcessingStatus, BearingRisk
)
from app.models.user import Mill
from app.core import physics, analysis
from app.core.validation import validate_and_clean_csv
from app.core.config import settings

logger = logging.getLogger(__name__)

async def update_task_progress(
    db_factory, 
    task_id: str, 
    progress: float, 
    status: ProcessingStatus = ProcessingStatus.PROCESSING,
    message: Optional[str] = None,
    records_processed: int = 0,
    total_records: int = 0,
    estimated_seconds: Optional[float] = None
):
    async with db_factory() as db:
        stmt = update(ProcessingTask).where(ProcessingTask.task_id == task_id).values(
            progress=progress,
            status=status,
            message=message,
            records_processed=records_processed,
            total_records=total_records,
            estimated_seconds_remaining=estimated_seconds
        )
        if status == ProcessingStatus.PROCESSING and progress == 0:
             stmt = stmt.values(started_at=datetime.utcnow())
        if status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED]:
            stmt = stmt.values(completed_at=datetime.utcnow(), estimated_seconds_remaining=0)
        
        await db.execute(stmt)
        await db.commit()

async def process_operational_data(
    task_id: str,
    file_content: bytes,
    mill_id: int,
    mill_tag: str,
    filename: str,
    db_factory
):
    try:
        await update_task_progress(db_factory, task_id, 0.1, message="Started processing...")
        
        # Read total rows for progress calculation
        try:
            full_df = pd.read_csv(io.BytesIO(file_content))
            total_rows = len(full_df)
        except Exception as e:
            await update_task_progress(db_factory, task_id, 0, status=ProcessingStatus.FAILED, message=f"CSV Read Error: {str(e)}")
            return

        if total_rows == 0:
            await update_task_progress(db_factory, task_id, 1.0, status=ProcessingStatus.COMPLETED, message="Empty file.")
            return

        await update_task_progress(db_factory, task_id, 0.2, total_records=total_rows, message="Validating data...")
        
        # In a real large-scale scenario, we'd chunk the validation too.
        # But for now, we'll keep the core validation on the whole DF if it fits in memory.
        # If it's REALLY large, we should process in chunks from the start.
        
        chunk_size = 5000
        processed_rows = 0
        start_time = time.time()
        
        # Constants for calculations
        V = settings.voltage
        PF = settings.power_factor
        EFF = settings.efficiency
        EF = settings.grid_emission_factor
        SQRT3 = 1.7320508075688772

        # We'll need some global context for the whole file
        all_machines_in_file = set()
        
        # Use a simplified generator for chunked processing
        reader = pd.read_csv(io.BytesIO(file_content), chunksize=chunk_size)
        
        for i, chunk in enumerate(reader):
            chunk_start_ts = time.time()
            
            if 'timestamp' in chunk.columns:
                chunk['timestamp'] = pd.to_datetime(chunk['timestamp'], errors='coerce', utc=True)
            
            chunk, validation_errors = validate_and_clean_csv(chunk)
            if chunk.empty:
                processed_rows += chunk_size # Move on
                continue

            # Calculations
            chunk['power_kw'] = (SQRT3 * V * chunk['current_A'] * PF * EFF) / 1000
            chunk['energy_kwh'] = chunk['power_kw'] / 60
            chunk['co2_kg'] = chunk['energy_kwh'] * EF
            chunk['date'] = chunk['timestamp'].dt.date
            
            # Identify machines
            machines = chunk['machine_id'].unique().tolist()
            all_machines_in_file.update(machines)
            
            # Data point insertion
            data_points = chunk.to_dict('records')
            for dp in data_points:
                dp['mill_id'] = mill_id
                dp['mill_tag'] = mill_tag
                dp.pop('date', None) # Remove extra aggregation column
                
            async with db_factory() as db:
                # Sub-batch insertion to avoid (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError)
                # "the number of query arguments cannot exceed 32767"
                # With 9 columns, 1000 records = 9000 parameters, well within the limit.
                sub_batch_size = 1000
                for start_idx in range(0, len(data_points), sub_batch_size):
                    end_idx = start_idx + sub_batch_size
                    batch = data_points[start_idx:end_idx]
                    await db.execute(insert(MachineDataPoint).values(batch))
                
                # Baseline fetch
                baseline_res = await db.execute(
                    select(MachineBaseline).where(
                        MachineBaseline.mill_id == mill_id,
                        MachineBaseline.machine_id.in_(machines)
                    )
                )
                baselines = {b.machine_id: b for b in baseline_res.scalars().all()}
                
                # Grouped processing for stats
                agg_df = chunk.groupby(['date', 'machine_id']).agg(
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
                    
                    baseline = baselines.get(m_id)
                    if not baseline:
                        continue 
                    
                    # Drift detection: Fetch last 7 days of average current
                    history_stmt = select(MachineDailyStats.avg_current_A).where(
                        MachineDailyStats.mill_id == mill_id,
                        MachineDailyStats.machine_id == m_id,
                        MachineDailyStats.date < curr_date
                    ).order_by(MachineDailyStats.date.desc()).limit(7)
                    history_res = await db.execute(history_stmt)
                    history_vals = [r for r in history_res.scalars().all() if r is not None][::-1] # Ascending
                    
                    is_drifting = analysis.detect_drift(history_vals)
                    
                    m_chunk_df = chunk[(chunk['date'] == curr_date) & (chunk['machine_id'] == m_id)]
                    baseline_kwh = analysis.calculate_baseline_kwh(m_chunk_df)
                    running_mask = m_chunk_df['motor_state'] == 'RUNNING'
                    excess_kwh_sum = (m_chunk_df.loc[running_mask, 'energy_kwh'] - baseline_kwh).clip(lower=0).sum()
                    
                    health_score, health_details = analysis.calculate_health_score_v2(
                        float(agg_row['avg_current']), float(agg_row['max_current']),
                        baseline.mean_current, baseline.std_current, baseline.p95_current, is_drifting
                    )
                    
                    # Upsert stats
                    stats_stmt = select(MachineDailyStats).where(
                        MachineDailyStats.mill_id == mill_id,
                        MachineDailyStats.machine_id == m_id,
                        MachineDailyStats.date == curr_date
                    )
                    stats_res = await db.execute(stats_stmt)
                    stats = stats_res.scalars().first()
                    
                    if stats:
                        stats.total_energy_kwh += float(agg_row['total_kwh'])
                        stats.total_co2_kg += float(agg_row['total_co2'])
                        stats.run_hours += float(agg_row['run_hours'])
                        stats.excess_kwh += float(excess_kwh_sum)
                        stats.excess_co2_kg += float(excess_kwh_sum * EF)
                        
                        # Update reference metrics if they've been refined
                        stats.reference_mean = baseline.mean_current
                        stats.reference_std = baseline.std_current
                        stats.reference_p95 = baseline.p95_current
                        
                        # Re-calculate health score using the updated baseline
                        stats.health_score = health_score 
                        stats.health_score_details = json.dumps(health_details)
                    else:
                        stats = MachineDailyStats(
                            mill_id=mill_id, date=curr_date, mill_tag=mill_tag, machine_id=m_id,
                            total_energy_kwh=float(agg_row['total_kwh']), baseline_kwh=baseline_kwh, 
                            excess_kwh=float(excess_kwh_sum), total_co2_kg=float(agg_row['total_co2']), 
                            excess_co2_kg=float(excess_kwh_sum * EF), bearing_risk=BearingRisk.NORMAL, 
                            health_score=health_score, run_hours=float(agg_row['run_hours']),
                            avg_current_A=float(agg_row['avg_current']), max_current=float(agg_row['max_current']),
                            std_current=float(agg_row['std_current']),
                            reference_mean=baseline.mean_current, reference_std=baseline.std_current, 
                            reference_p95=baseline.p95_current, health_score_details=json.dumps(health_details)
                        )
                        db.add(stats)

                    # Alert Generation
                    if health_score < 70 or is_drifting:
                        alert_msg = f"Machine {m_id}: Health Score dropped to {health_score:.1f}."
                        if is_drifting:
                            alert_msg += " Increasing load drift detected."
                        
                        # Check if alert already exists for today to avoid spamming
                        alert_check = await db.execute(select(Alert).where(
                            Alert.mill_id == mill_id,
                            Alert.machine_id == m_id,
                            Alert.timestamp >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                        ))
                        if not alert_check.scalars().first():
                            db.add(Alert(
                                mill_id=mill_id,
                                mill_tag=mill_tag,
                                machine_id=m_id,
                                type=AlertType.WARNING,
                                message=alert_msg
                            ))
                
                await db.commit()
            
            processed_rows += len(chunk)
            progress = 0.2 + (0.75 * (processed_rows / total_rows))
            
            # Calculate ETA
            elapsed = time.time() - start_time
            avg_time_per_row = elapsed / processed_rows if processed_rows > 0 else 0
            remaining_rows = total_rows - processed_rows
            eta_seconds = remaining_rows * avg_time_per_row
            
            await update_task_progress(
                db_factory, task_id, progress, 
                records_processed=processed_rows, 
                total_records=total_rows,
                estimated_seconds=eta_seconds,
                message=f"Processing chunk {i+1}..."
            )

        await update_task_progress(db_factory, task_id, 1.0, status=ProcessingStatus.COMPLETED, message="Processing complete.")
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        await update_task_progress(db_factory, task_id, progress if 'progress' in locals() else 0, status=ProcessingStatus.FAILED, message=str(e))

async def process_baseline_data(
    task_id: str,
    file_content: bytes,
    mill_id: int,
    mill_tag: str,
    db_factory
):
    try:
        await update_task_progress(db_factory, task_id, 0.1, message="Initializing baseline processing...")
        
        # Incremental pooling requires accumulating NEW data first, then merging with OLD data.
        chunk_size = 10000
        machine_stats_accumulator = {} 
        
        reader = pd.read_csv(io.BytesIO(file_content), chunksize=chunk_size)
        
        # Pass 1: Aggregate NEW stats from the file
        processed_rows = 0
        file_size = len(file_content)
        
        for i, chunk in enumerate(reader):
            chunk, _ = validate_and_clean_csv(chunk)
            if chunk.empty:
                continue
            
            running_chunk = chunk[chunk['motor_state'] == 'RUNNING']
            if running_chunk.empty:
                continue
                
            for m_id, m_df in running_chunk.groupby('machine_id'):
                if m_id not in machine_stats_accumulator:
                    machine_stats_accumulator[m_id] = {
                        'n': 0,
                        'sum': 0.0,
                        'sum_sq': 0.0,
                        'samples': [] 
                    }
                
                vals = m_df['current_A'].values
                machine_stats_accumulator[m_id]['n'] += len(vals)
                machine_stats_accumulator[m_id]['sum'] += vals.sum()
                machine_stats_accumulator[m_id]['sum_sq'] += (vals ** 2).sum()
                
                # Subsample for P95 estimation
                if len(machine_stats_accumulator[m_id]['samples']) < 1000:
                    machine_stats_accumulator[m_id]['samples'].extend(vals[:1000 - len(machine_stats_accumulator[m_id]['samples'])].tolist())

            processed_rows += len(chunk)
            await update_task_progress(
                db_factory, task_id, 0.1 + (0.5 * min(1.0, processed_rows / (file_size / 100) if file_size > 0 else 1)), 
                message=f"Aggregating stats: {processed_rows} rows processed...",
                records_processed=processed_rows
            )

        if not machine_stats_accumulator:
            await update_task_progress(db_factory, task_id, 1.0, status=ProcessingStatus.FAILED, message="No valid running data found in baseline file.")
            return

        # Pass 2: Merge NEW stats with OLD stats in DB
        machines = list(machine_stats_accumulator.keys())
        total_machines = len(machines)
        baselines_updated = 0
        
        async with db_factory() as db:
            for i, m_id in enumerate(machines):
                stats = machine_stats_accumulator[m_id]
                n_new = stats['n']
                mu_new = stats['sum'] / n_new
                var_new = (stats['sum_sq'] / n_new) - (mu_new ** 2)
                sigma_new = np.sqrt(max(0, var_new))
                p95_new = np.percentile(stats['samples'], 95) if stats['samples'] else mu_new
                
                stmt = select(MachineBaseline).where(
                    MachineBaseline.mill_id == mill_id,
                    MachineBaseline.machine_id == m_id
                )
                result = await db.execute(stmt)
                baseline = result.scalars().first()
                
                if baseline and baseline.data_points_count > 0:
                    # Robust Pooled Statistics
                    n_old = baseline.data_points_count
                    mu_old = baseline.mean_current
                    sigma_old = baseline.std_current
                    n_total = n_old + n_new
                    
                    mu_pooled = (n_old * mu_old + n_new * mu_new) / n_total
                    
                    # Pooled Standard Deviation (Variance formula considering different means)
                    # var_pooled = [ (n1-1)s1^2 + (n2-1)s2^2 + (n1*n2/n_total)*(mu1-mu2)^2 ] / (n_total - 1)
                    var_old = sigma_old ** 2
                    term1 = (n_old - 1) * var_old if n_old > 1 else 0
                    term2 = (n_new - 1) * var_new if n_new > 1 else 0
                    term3 = (n_old * n_new / n_total) * ((mu_old - mu_new) ** 2)
                    var_pooled = (term1 + term2 + term3) / (n_total - 1) if n_total > 1 else 0
                    sigma_pooled = np.sqrt(max(0, var_pooled))
                    
                    # Pooled P95 (Weighted approximation)
                    p95_pooled = (n_old * baseline.p95_current + n_new * p95_new) / n_total
                    
                    baseline.mean_current = float(mu_pooled)
                    baseline.std_current = float(sigma_pooled)
                    baseline.p95_current = float(p95_pooled)
                    baseline.data_points_count = n_total
                    update_type = "INCREMENTAL_AUTO"
                else:
                    if not baseline:
                        baseline = MachineBaseline(
                            mill_id=mill_id, mill_tag=mill_tag, machine_id=m_id,
                            mean_current=float(mu_new), std_current=float(sigma_new), 
                            p95_current=float(p95_new), data_points_count=n_new
                        )
                        db.add(baseline)
                    else:
                        baseline.mean_current = float(mu_new)
                        baseline.std_current = float(sigma_new)
                        baseline.p95_current = float(p95_new)
                        baseline.data_points_count = n_new
                    update_type = "INITIAL_UPLOAD"

                history = MachineBaselineHistory(
                    mill_id=mill_id, mill_tag=mill_tag, machine_id=m_id,
                    mean_current=baseline.mean_current, std_current=baseline.std_current,
                    p95_current=baseline.p95_current, data_points_count=n_new,
                    update_type=update_type
                )
                db.add(history)
                baselines_updated += 1
                
                progress = 0.6 + (0.4 * ((i + 1) / total_machines))
                await update_task_progress(
                    db_factory, task_id, progress, 
                    message=f"Merging baseline for {m_id}..."
                )

            mill_stmt = update(Mill).where(Mill.id == mill_id).values(has_uploaded_baseline=True)
            await db.execute(mill_stmt)
            await db.commit()

        await update_task_progress(db_factory, task_id, 1.0, status=ProcessingStatus.COMPLETED, message=f"Successfully completed incremental baseline update for {baselines_updated} machines.")

    except Exception as e:
        logger.error(f"Baseline task {task_id} failed: {e}")
        await update_task_progress(db_factory, task_id, 0, status=ProcessingStatus.FAILED, message=str(e))
