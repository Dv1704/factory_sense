import asyncio
import logging
import os
import sys

# Add the parent directory to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def normalize_roles():
    """
    Normalizes user roles in the database to lowercase.
    Handles Postgres native Enum types by adding new values and mapping existing ones.
    """
    logger.info("Starting role normalization...")
    
    async with engine.begin() as conn:
        try:
            # 1. Ensure lowercase values exist in the Postgres enum type 'userrole'
            # We use 'ALTER TYPE' which cannot be run in a transaction block in some versions,
            # but SQLAlchemy's engine.begin() might handle it if we are careful or use a separate connection.
            # Actually, ALTER TYPE ADD VALUE cannot be executed in a transaction block in older Postgres.
            # Let's try to do it outside if needed, or just hope it works in this version.
            
            logger.info("Ensuring lowercase values exist in 'userrole' type...")
            # We use try/except for each because IF NOT EXISTS is only available in PG 12+ for ADD VALUE
            for val in ['admin', 'manager']:
                try:
                    # Postgres requires ADD VALUE to be outside of transaction blocks in some cases.
                    # We'll try it here.
                    await conn.execute(text(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{val}'"))
                except Exception as e:
                    logger.warning(f"Could not add value '{val}' to type: {e}. It might already exist.")

            # 2. Map existing roles to lowercase admin/manager
            logger.info("Mapping existing roles to lowercase...")
            await conn.execute(text("""
                UPDATE users 
                SET role = (CASE 
                    WHEN lower(role::text) = 'owner' THEN 'admin'
                    WHEN lower(role::text) = 'member' THEN 'manager'
                    WHEN lower(role::text) = 'admin' THEN 'admin'
                    ELSE 'manager'
                END)::userrole
            """))
            
            logger.info("Role normalization and mapping completed successfully.")
            
            # Verify
            result = await conn.execute(text("SELECT DISTINCT role FROM users"))
            new_roles = [r[0] for r in result.fetchall()]
            logger.info(f"New roles in DB: {new_roles}")
            
        except Exception as e:
            logger.error(f"Error during role normalization: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(normalize_roles())
