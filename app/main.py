from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import engine, Base
from app.routes import auth, data, dashboard, alerts
from app.models.user import User
from app.models.mill_data import RawFile, MachineDailyStats, MachineDataPoint, Alert

app = FastAPI(title="FactorySenseAI API")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, replace with specific frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        # Create tables (for MVP only - usually use Alembic)
        await conn.run_sync(Base.metadata.create_all)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(data.router, prefix="/api/v1", tags=["Data"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])

@app.get("/")
async def root():
    return {"message": "FactorySenseAI API is running"}
