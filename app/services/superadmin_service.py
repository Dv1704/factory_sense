import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.metrics import request_metrics, get_uptime_seconds, get_start_datetime
from app.models.mill_data import BearingRisk, ProcessingStatus
from app.repositories import (
    alert_repository,
    data_point_repository,
    mill_repository,
    stats_repository,
    task_repository,
    user_repository,
)


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


async def get_platform_health(db: AsyncSession) -> dict:
    """
    Superadmin command center. Returns a single Health Score (0-100 / green-yellow-red)
    plus aggregated telemetry: system uptime, API latency, database status, machine health
    distribution, alert counts, task pipeline status, and third-party service checks.
    """
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Platform overview
    total_users = await user_repository.count_all(db)
    total_mills = await mill_repository.count_all(db)
    mills_with_baseline = await mill_repository.count_with_baseline(db)
    total_machines = await stats_repository.count_distinct_machines(db)

    # Active mills: any machine_daily_stats row dated yesterday or today
    active_mill_rows = await stats_repository.list_active_mill_ids_since(db, yesterday)
    active_mills_count = len(active_mill_rows)
    inactive_mills_count = max(total_mills - active_mills_count, 0)

    # Machine health distribution (latest stats per machine)
    high_risk_machines = await stats_repository.count_by_bearing_risk_since(db, BearingRisk.HIGH, yesterday)
    warning_machines = await stats_repository.count_by_bearing_risk_since(db, BearingRisk.WARNING, yesterday)
    healthy_machines = max(total_machines - high_risk_machines - warning_machines, 0)

    # Alert summary
    open_alerts_total = await alert_repository.count_open(db)
    alert_type_rows = await alert_repository.get_open_type_breakdown(db)
    alert_breakdown = {row[0].value: row[1] for row in alert_type_rows}

    # Processing task pipeline
    failed_tasks_24h = await task_repository.count_by_status_since(db, ProcessingStatus.FAILED, cutoff_24h)
    completed_tasks_24h = await task_repository.count_by_status_since(db, ProcessingStatus.COMPLETED, cutoff_24h)
    pending_tasks = await task_repository.count_pending(db)

    # Stuck: PROCESSING with started_at older than 1 hour
    stuck_cutoff = now - timedelta(hours=1)
    stuck_tasks = await task_repository.count_stuck(db, stuck_cutoff)

    # API metrics (in-memory)
    avg_latency = request_metrics.avg_latency_ms
    p95_latency = request_metrics.p95_latency_ms
    total_req = request_metrics.total_requests
    error_rate = request_metrics.error_rate_pct

    # Database health probe
    db_start = time.perf_counter()
    try:
        await user_repository.count_all(db)
        db_latency_ms = round((time.perf_counter() - db_start) * 1000, 2)
        db_status = "healthy" if db_latency_ms < 100 else ("degraded" if db_latency_ms < 500 else "slow")
    except Exception:
        db_latency_ms = -1
        db_status = "unhealthy"

    # Third-party service statuses
    sentry_status = "configured" if settings.sentry_dsn else "not_configured"

    # IoT gateway: any raw data point in the last hour
    iot_cutoff = now - timedelta(hours=1)
    recent_data_points = await data_point_repository.count_since(db, iot_cutoff)
    iot_status = "active" if recent_data_points > 0 else "idle"

    # Composite health score
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


async def get_alerts_overview(db: AsyncSession) -> list:
    """
    Returns all unacknowledged alerts across every mill, enriched with the
    owning user's email. Ordered newest-first, capped at 200 rows.
    """
    rows = await alert_repository.get_platform_overview(db, limit=200)
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
