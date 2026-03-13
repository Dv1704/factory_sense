import os
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import delete, select
from app.core.database import AsyncSessionLocal
from app.models.mill_data import MachineDataPoint
from app.core.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def archive_old_data(dry_run: bool = False):
    """
    Find MachineDataPoint records older than the retention period,
    export them to CSV, and delete them from the database.
    """
    days = settings.raw_data_retention_days
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    logger.info(f"Archiving data older than {cutoff_date} ({days} days retention)")
    
    async with AsyncSessionLocal() as db:
        # 1. Fetch data to archive
        stmt = select(MachineDataPoint).where(MachineDataPoint.timestamp < cutoff_date)
        result = await db.execute(stmt)
        data_to_archive = result.scalars().all()
        
        if not data_to_archive:
            logger.info("No data found to archive.")
            return

        logger.info(f"Found {len(data_to_archive)} records to archive.")
        
        # 2. Convert to DataFrame and Export
        df = pd.DataFrame([{
            "id": d.id,
            "timestamp": d.timestamp,
            "machine_id": d.machine_id,
            "mill_id": d.mill_id,
            "current_A": d.current_A,
            "power_kw": d.power_kw,
            "energy_kwh": d.energy_kwh,
            "co2_kg": d.co2_kg
        } for d in data_to_archive])
        
        archive_dir = "archives"
        if not os.path.exists(archive_dir):
            os.makedirs(archive_dir)
            
        archive_file = os.path.join(archive_dir, f"raw_data_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv.gz")
        
        if not dry_run:
            df.to_csv(archive_file, index=False, compression='gzip')
            logger.info(f"Archive saved to {archive_file}")
            
            # 3. Delete from DB
            delete_stmt = delete(MachineDataPoint).where(MachineDataPoint.timestamp < cutoff_date)
            await db.execute(delete_stmt)
            await db.commit()
            logger.info(f"Deleted {len(data_to_archive)} records from database.")
        else:
            logger.info(f"[DRY RUN] Would save to {archive_file} and delete {len(data_to_archive)} records.")

if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    asyncio.run(archive_old_data(dry_run=dry_run))
