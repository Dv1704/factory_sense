import asyncio
import logging
import time

from fastapi import FastAPI, Request
from pydantic import BaseModel
import sentry_sdk
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine, Base, AsyncSessionLocal
from app.core.metrics import request_metrics
from app.routes import auth, data, dashboard, alerts, admin, superadmin
from app.models.user import User, UserRole
from app.models.mill_data import RawFile, MachineDailyStats, MachineDataPoint, Alert
from app.models.email_outbox import EmailOutbox
from app.services.email_service import dispatch_pending_emails

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000
        request_metrics.record(latency_ms, is_error=response.status_code >= 500)
        return response


tags_metadata = [
    {
        "name": "Auth",
        "description": "Operations for user registration, authentication (JWT), and session management.",
    },
    {
        "name": "Data",
        "description": "Upload functionality. API-key secured endpoints for automated industrial data ingestion and baselining.",
    },
    {
        "name": "Dashboard",
        "description": "Aggregated metrics, health scores, and trends for front-end visualization.",
    },
    {
        "name": "Alerts",
        "description": "Stateful alert lifecycle: active feed, acknowledge, resolve, and history log.",
    },
    {
        "name": "Admin",
        "description": "Mill admin tools: manage own mills, managers, and upload history. Requires mill admin JWT.",
    },
    {
        "name": "Superadmin",
        "description": "Platform owner only. Platform-wide health telemetry, all mills, all users. Requires superadmin JWT.",
    },
]

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

app = FastAPI(
    title="FactorySenseAI API - Production Ready",
    description="Backend API for FactorySense Machine Health & Telemetry Platform",
    version="1.0.0",
    openapi_tags=tags_metadata,
)

origins = [
    "https://stikkverse.vercel.app",
    "https://stikkverse.app",
    "https://www.stikkverse.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "https://144-91-111-151.nip.io",
]

app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── startup migrations ────────────────────────────────────────────────────────

_NEW_COLUMNS = [
    ("machine_daily_stats", "data_coverage_hours", "FLOAT"),
    ("machine_daily_stats", "data_availability_pct", "FLOAT"),
    ("machine_daily_stats", "gap_count", "INTEGER"),
    ("machine_daily_stats", "max_gap_minutes", "FLOAT"),
    ("machine_daily_stats", "avg_sampling_interval_minutes", "FLOAT"),
    # RBAC columns
    ("users", "created_by_id", "INTEGER"),
    ("mills", "admin_id", "INTEGER"),
    # Alert lifecycle columns
    ("alerts", "status", "VARCHAR(20) DEFAULT 'active'"),
    ("alerts", "acknowledged_at", "TIMESTAMPTZ"),
    ("alerts", "resolved_at", "TIMESTAMPTZ"),
    ("alerts", "resolution_note", "TEXT"),
    ("alerts", "resolution_category", "VARCHAR(50)"),
    # Password reset
    ("users", "reset_token", "VARCHAR(255)"),
    ("users", "reset_token_expires", "TIMESTAMPTZ"),
    # Magic-link login
    ("users", "magic_link_token", "VARCHAR(255)"),
    ("users", "magic_link_token_expires", "TIMESTAMPTZ"),
]


async def _migrate_columns(conn) -> None:
    """Add new nullable columns to existing tables without Alembic."""
    is_postgres = "postgresql" in str(engine.url)
    for table, column, col_type in _NEW_COLUMNS:
        try:
            if is_postgres:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}")
                )
            else:
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result.fetchall()}
                if column not in existing:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        except Exception as exc:
            logger.warning("Column migration skipped (%s.%s): %s", table, column, exc)


async def _migrate_enum() -> None:
    """Add the superadmin value to the userrole enum (PostgreSQL only).
    Must run outside a transaction block."""
    is_postgres = "postgresql" in str(engine.url)
    if not is_postgres:
        return
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'superadmin'")
            )
    except Exception as exc:
        logger.warning("Enum migration skipped (may already exist): %s", exc)


async def _seed_superadmin() -> None:
    """Create the superadmin account on first boot if env vars are configured."""
    if not (settings.superadmin_email and settings.superadmin_password):
        return

    from app.routes.auth import get_password_hash
    from sqlalchemy.future import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.email == settings.superadmin_email)
        )
        if result.scalars().first():
            return  # already exists

        superadmin = User(
            email=settings.superadmin_email,
            password_hash=get_password_hash(settings.superadmin_password),
            role=UserRole.superadmin,
        )
        db.add(superadmin)
        await db.commit()
        logger.info("Superadmin account created: %s", settings.superadmin_email)


OUTBOX_SWEEP_INTERVAL_SECONDS = 30

_outbox_sweep_task: "asyncio.Task | None" = None


async def _outbox_sweep_loop() -> None:
    """Safety net for the transactional outbox: catches any row whose immediate
    post-commit dispatch never ran (process restart, crash) and retries FAILED
    rows up to MAX_ATTEMPTS."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await dispatch_pending_emails(db)
        except Exception as exc:
            logger.warning("Outbox sweep iteration failed: %s", exc)
        await asyncio.sleep(OUTBOX_SWEEP_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup():
    global _outbox_sweep_task
    await _migrate_enum()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)
    await _seed_superadmin()
    _outbox_sweep_task = asyncio.create_task(_outbox_sweep_loop())


@app.on_event("shutdown")
async def shutdown():
    if _outbox_sweep_task:
        _outbox_sweep_task.cancel()


# ── routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(data.router, prefix="/api/v1", tags=["Data"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(superadmin.router, prefix="/api/v1/superadmin", tags=["Superadmin"])


class RootResponse(BaseModel):
    message: str


@app.get("/", response_model=RootResponse)
async def root():
    return {"message": "FactorySenseAI API is running"}
