from fastapi import FastAPI
import sentry_sdk
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import engine, Base
from app.routes import auth, data, dashboard, alerts, admin

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
        "description": "Fetch and acknowledge active machine alerts.",
    },
    {
        "name": "Admin",
        "description": "Administrative tools for platform management, mill creation, and user management. Requires Admin JWT.",
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
    servers=[
        {"url": "http://144.91.111.151:8000", "description": "Production server"},
        {"url": "http://localhost:8000", "description": "Local development"}
    ]
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, replace with specific frontend domains
    allow_credentials=False, # Must be False if allow_origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(data.router, prefix="/api/v1", tags=["Data"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])

@app.get("/")
async def root():
    return {"message": "FactorySenseAI API is running"}
