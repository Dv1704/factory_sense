import asyncio
from sqlalchemy import text
from app.core.database import engine, Base
from app.models.mill_data import MachineBaseline, MachineDailyStats, RawFile, MachineDataPoint, Alert, MachineBaselineHistory, ProcessingTask, ProcessingStatus
from app.models.user import User, Mill, Invitation

async def recreate_tables():
    async with engine.begin() as conn:
        # Drop all tables in public schema with CASCADE
        tables = [
            "invitations", "alerts", "machine_data_points", 
            "machine_baseline_history", "machine_baselines", 
            "machine_daily_stats", "processing_tasks", 
            "raw_files", "users", "mills"
        ]
        for table in tables:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        
        # Drop custom types as well
        types = ["userrole", "bearingrisk", "alerttype", "processingstatus"]
        for t in types:
            await conn.execute(text(f"DROP TYPE IF EXISTS {t} CASCADE"))
            
        # Now create them all from metadata
        await conn.run_sync(Base.metadata.create_all)
    print("Tables and Types dropped and recreated successfully.")

if __name__ == "__main__":
    asyncio.run(recreate_tables())
