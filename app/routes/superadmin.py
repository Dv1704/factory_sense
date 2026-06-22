import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, distinct

from app.core.database import get_db
from app.core.config import settings
from app.core.metrics import request_metrics, get_uptime_seconds, get_start_datetime
from app.models.user import User, Mill
from app.models.mill_data import (
    Alert,
    AlertStatus,
    MachineDailyStats,
    MachineDataPoint,
    ProcessingTask,
    ProcessingStatus,
    BearingRisk,
)
from app.routes.auth import get_current_superadmin_user

router = APIRouter()


def _compute_health_score(
    open_alerts: int,
    high_risk_machines: int,
    failed_tasks_24h: int,
    stuck_tasks: int,
    inactive_mills: int,
    total_mills: int,
    error_rate_pct: float,
    avg_latency_ms: float,
) -> tuple:
    score = 100

    # High-risk machines are the heaviest signal — each deducts 5, capped at 25
    score -= min(high_risk_machines * 5, 25)

    # Remaining open alerts (non-critical) — 1 point each, capped at 15
    remaining_alerts = max(open_alerts - high_risk_machines, 0)
    score -= min(remaining_alerts, 15)

    # Failed processing tasks in last 24h
    score -= min(failed_tasks_24h * 3, 15)

    # Stuck tasks (processing > 1h) — likely hung workers
    score -= min(stuck_tasks * 5, 10)

    # Mills silent for 48h — proportional to fleet size
    if total_mills > 0:
        score -= min(int((inactive_mills / total_mills) * 20), 20)

    # API error rate
    if error_rate_pct > 10:
        score -= 20
    elif error_rate_pct > 5:
        score -= 10

    # API latency
    if avg_latency_ms > 2000:
        score -= 25   # severe: pushes score to yellow even when otherwise clean
    elif avg_latency_ms > 1000:
        score -= 10

    score = max(0, min(100, score))

    if score >= 80:
        level = "green"
    elif score >= 50:
        level = "yellow"
    else:
        level = "red"

    return score, level


@router.get("/health", summary="Platform Health Dashboard")
async def get_platform_health(
    admin: User = Depends(get_current_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Superadmin command center. Returns a single Health Score (0-100 / green-yellow-red)
    plus aggregated telemetry: system uptime, API latency, database status, machine health
    distribution, alert counts, task pipeline status, and third-party service checks.
    """
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_48h = now - timedelta(hours=48)
    today = date.today()
    yesterday = today - timedelta(days=1)

    # ── Platform overview ──────────────────────────────────────────────────────
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_mills = (await db.execute(select(func.count(Mill.id)))).scalar() or 0
    mills_with_baseline = (
        await db.execute(
            select(func.count(Mill.id)).where(Mill.has_uploaded_baseline == True)
        )
    ).scalar() or 0
    total_machines = (
        await db.execute(
            select(func.count(distinct(MachineDailyStats.machine_id)))
        )
    ).scalar() or 0

    # Active mills: any machine_daily_stats row dated yesterday or today
    active_mill_rows = await db.execute(
        select(distinct(MachineDailyStats.mill_id)).where(
            MachineDailyStats.date >= yesterday
        )
    )
    active_mills_count = len(active_mill_rows.fetchall())
    inactive_mills_count = max(total_mills - active_mills_count, 0)

    # ── Machine health distribution (latest stats per machine) ─────────────────
    high_risk_machines = (
        await db.execute(
            select(func.count(distinct(MachineDailyStats.machine_id))).where(
                MachineDailyStats.bearing_risk == BearingRisk.HIGH,
                MachineDailyStats.date >= yesterday,
            )
        )
    ).scalar() or 0

    warning_machines = (
        await db.execute(
            select(func.count(distinct(MachineDailyStats.machine_id))).where(
                MachineDailyStats.bearing_risk == BearingRisk.WARNING,
                MachineDailyStats.date >= yesterday,
            )
        )
    ).scalar() or 0

    healthy_machines = max(total_machines - high_risk_machines - warning_machines, 0)

    # ── Alert summary ──────────────────────────────────────────────────────────
    open_alerts_total = (
        await db.execute(
            select(func.count(Alert.id)).where(Alert.status != AlertStatus.resolved)
        )
    ).scalar() or 0

    alert_type_rows = await db.execute(
        select(Alert.type, func.count(Alert.id))
        .where(Alert.status != AlertStatus.resolved)
        .group_by(Alert.type)
    )
    alert_breakdown = {
        row[0].value: row[1] for row in alert_type_rows.fetchall()
    }

    # ── Processing task pipeline ───────────────────────────────────────────────
    failed_tasks_24h = (
        await db.execute(
            select(func.count(ProcessingTask.id)).where(
                ProcessingTask.status == ProcessingStatus.FAILED,
                ProcessingTask.created_at >= cutoff_24h,
            )
        )
    ).scalar() or 0

    completed_tasks_24h = (
        await db.execute(
            select(func.count(ProcessingTask.id)).where(
                ProcessingTask.status == ProcessingStatus.COMPLETED,
                ProcessingTask.created_at >= cutoff_24h,
            )
        )
    ).scalar() or 0

    pending_tasks = (
        await db.execute(
            select(func.count(ProcessingTask.id)).where(
                ProcessingTask.status == ProcessingStatus.PENDING
            )
        )
    ).scalar() or 0

    # Stuck: PROCESSING with started_at older than 1 hour
    stuck_cutoff = now - timedelta(hours=1)
    stuck_tasks = (
        await db.execute(
            select(func.count(ProcessingTask.id)).where(
                ProcessingTask.status == ProcessingStatus.PROCESSING,
                ProcessingTask.started_at <= stuck_cutoff,
            )
        )
    ).scalar() or 0

    # ── API metrics (in-memory) ────────────────────────────────────────────────
    avg_latency = request_metrics.avg_latency_ms
    p95_latency = request_metrics.p95_latency_ms
    total_req = request_metrics.total_requests
    error_rate = request_metrics.error_rate_pct

    # ── Database health probe ──────────────────────────────────────────────────
    db_start = time.perf_counter()
    try:
        await db.execute(select(func.count(User.id)))
        db_latency_ms = round((time.perf_counter() - db_start) * 1000, 2)
        db_status = "healthy" if db_latency_ms < 100 else ("degraded" if db_latency_ms < 500 else "slow")
    except Exception:
        db_latency_ms = -1
        db_status = "unhealthy"

    # ── Third-party service statuses ───────────────────────────────────────────
    sentry_status = "configured" if settings.sentry_dsn else "not_configured"

    # IoT gateway: any raw data point in the last hour
    iot_cutoff = now - timedelta(hours=1)
    recent_data_points = (
        await db.execute(
            select(func.count(MachineDataPoint.id)).where(
                MachineDataPoint.timestamp >= iot_cutoff
            )
        )
    ).scalar() or 0
    iot_status = "active" if recent_data_points > 0 else "idle"

    # ── Compute composite Health Score ─────────────────────────────────────────
    health_score, health_level = _compute_health_score(
        open_alerts=open_alerts_total,
        high_risk_machines=high_risk_machines,
        failed_tasks_24h=failed_tasks_24h,
        stuck_tasks=stuck_tasks,
        inactive_mills=inactive_mills_count,
        total_mills=total_mills,
        error_rate_pct=error_rate,
        avg_latency_ms=avg_latency,
    )

    return {
        "health_score": health_score,
        "health_level": health_level,
        "computed_at": now.isoformat(),
        "system": {
            "uptime_seconds": get_uptime_seconds(),
            "started_at": get_start_datetime().isoformat(),
            "api_avg_latency_ms": avg_latency,
            "api_p95_latency_ms": p95_latency,
            "api_total_requests": total_req,
            "api_error_rate_pct": error_rate,
        },
        "platform": {
            "total_users": total_users,
            "total_mills": total_mills,
            "mills_with_baseline": mills_with_baseline,
            "total_machines": total_machines,
            "active_mills_48h": active_mills_count,
            "inactive_mills_48h": inactive_mills_count,
        },
        "machine_health_distribution": {
            "healthy": healthy_machines,
            "warning": warning_machines,
            "critical": high_risk_machines,
        },
        "alerts": {
            "total_open": open_alerts_total,
            "by_type": alert_breakdown,
        },
        "processing_tasks": {
            "pending": pending_tasks,
            "completed_24h": completed_tasks_24h,
            "failed_24h": failed_tasks_24h,
            "stuck": stuck_tasks,
        },
        "services": {
            "database": {"status": db_status, "latency_ms": db_latency_ms},
            "sentry": {"status": sentry_status},
            "iot_gateway": {
                "status": iot_status,
                "data_points_last_hour": recent_data_points,
            },
        },
    }


@router.get("/mills/activity", summary="Per-Mill Data Activity Report")
async def get_mill_activity(
    admin: User = Depends(get_current_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lists every registered mill with its last-seen data date, machine count,
    7-day average health score, and a simple activity status:
    active (≤1d), stale (2-7d), inactive (>7d), or no_data.
    Results are sorted most-concerning first.
    """
    today = date.today()
    seven_days_ago = today - timedelta(days=7)

    mills_res = await db.execute(
        select(Mill, User).join(User, Mill.user_id == User.id)
    )
    mills = mills_res.all()

    activity = []
    for mill, user in mills:
        latest_date = (
            await db.execute(
                select(func.max(MachineDailyStats.date)).where(
                    MachineDailyStats.mill_id == mill.mill_id
                )
            )
        ).scalar()

        machine_count = (
            await db.execute(
                select(func.count(distinct(MachineDailyStats.machine_id))).where(
                    MachineDailyStats.mill_id == mill.mill_id
                )
            )
        ).scalar() or 0

        avg_health = (
            await db.execute(
                select(func.avg(MachineDailyStats.health_score)).where(
                    MachineDailyStats.mill_id == mill.mill_id,
                    MachineDailyStats.date >= seven_days_ago,
                )
            )
        ).scalar()

        open_alerts = (
            await db.execute(
                select(func.count(Alert.id)).where(
                    Alert.mill_id == mill.mill_id,
                    Alert.status != AlertStatus.resolved,
                )
            )
        ).scalar() or 0

        if latest_date:
            days_silent = (today - latest_date).days
            if days_silent <= 1:
                status = "active"
            elif days_silent <= 7:
                status = "stale"
            else:
                status = "inactive"
        else:
            days_silent = None
            status = "no_data"

        activity.append({
            "mill_id": mill.mill_id,
            "owner_email": user.email,
            "has_baseline": mill.has_uploaded_baseline,
            "machine_count": machine_count,
            "last_data_date": latest_date.isoformat() if latest_date else None,
            "days_since_last_data": days_silent,
            "avg_health_score_7d": round(avg_health, 1) if avg_health is not None else None,
            "open_alerts": open_alerts,
            "status": status,
        })

    status_order = {"no_data": 0, "inactive": 1, "stale": 2, "active": 3}
    activity.sort(key=lambda x: status_order.get(x["status"], 99))
    return activity


@router.get("/alerts/overview", summary="Platform-Wide Open Alerts")
async def get_alerts_overview(
    admin: User = Depends(get_current_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all unacknowledged alerts across every mill, enriched with the
    owning user's email. Ordered newest-first, capped at 200 rows.
    """
    result = await db.execute(
        select(Alert, User)
        .join(User, Alert.user_id == User.id)
        .where(Alert.status != AlertStatus.resolved)
        .order_by(Alert.timestamp.desc())
        .limit(200)
    )
    rows = result.all()

    return [
        {
            "id": a.id,
            "mill_id": a.mill_id,
            "machine_id": a.machine_id,
            "type": a.type.value,
            "message": a.message,
            "timestamp": a.timestamp.isoformat(),
            "owner_email": u.email,
        }
        for a, u in rows
    ]
