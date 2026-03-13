import asyncio
import os
import glob
from datetime import datetime, timedelta
import logging
from sqlalchemy.future import select
from sqlalchemy import delete
from app.core.database import AsyncSessionLocal
from app.models.mill_data import MachineDataPoint, RawFile
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_retention_policy():
    """
    Apply data retention policy to clean up old raw files and detailed data points.
    MachineDailyStats and Baselines are kept indefinitely as 'processed data'.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=settings.raw_data_retention_days)
    logger.info(f"Applying retention policy for data older than {cutoff_date.isoformat()}")

    # 1. Prune DB (MachineDataPoints)
    try:
        async with AsyncSessionLocal() as db:
            # Delete data points older than cutoff
            stmt = delete(MachineDataPoint).where(MachineDataPoint.timestamp < cutoff_date)
            result = await db.execute(stmt)
            await db.commit()
            logger.info(f"Deleted {result.rowcount} old MachineDataPoints.")
            
            # Delete RawFile records that are too old
            stmt_raw = delete(RawFile).where(RawFile.upload_timestamp < cutoff_date)
            result_raw = await db.execute(stmt_raw)
            await db.commit()
            logger.info(f"Deleted {result_raw.rowcount} old RawFile records.")
            
    except Exception as e:
        logger.error(f"Error pruning database: {e}")

    # 2. Prune Raw Files on disk
    try:
        base_dir = "data/raw"
        if not os.path.exists(base_dir):
            return
            
        deleted_files = 0
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                file_path = os.path.join(root, file)
                # Check file modification time
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < cutoff_date:
                    os.remove(file_path)
                    deleted_files += 1
                    
        logger.info(f"Deleted {deleted_files} old raw files from disk.")
    except Exception as e:
        logger.error(f"Error pruning files: {e}")

if __name__ == "__main__":
    asyncio.run(apply_retention_policy())
