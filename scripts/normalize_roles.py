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
    Assumes 'admin' and 'manager' values already exist in the 'userrole' type.
    """
    logger.info("Starting role normalization...")
    
    async with engine.begin() as conn:
        try:
            # 1. Map existing roles to lowercase admin/manager
            # Map 'owner' -> 'admin'
            # Map 'member' -> 'manager'
            # Everything else to 'manager' by default if it's not 'admin'
            
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
