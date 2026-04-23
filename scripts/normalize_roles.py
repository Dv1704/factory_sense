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
    Handles both VARCHAR and Enum types.
    """
    logger.info("Starting role normalization...")
    
    async with engine.begin() as conn:
        # 1. Check if the 'role' column is an Enum type in Postgres
        # If it is, we might need to alter the enum type itself if it's a native type.
        # However, if it's stored as VARCHAR (common with SQLAlchemy Enum), a simple UPDATE works.
        
        try:
            # Try to update all roles to lowercase
            # We use lower() and cast if necessary.
            # In most cases with SQLAlchemy + Postgres, the Enum is represented as a type.
            # If it's a native enum, we might need to add new values or change them.
            
            # First, let's see what we have
            result = await conn.execute(text("SELECT DISTINCT role FROM users"))
            roles = [r[0] for r in result.fetchall()]
            logger.info(f"Current roles in DB: {roles}")
            
            # Update all roles to lowercase and map removed roles
            # Map 'owner' -> 'admin' (usually owners have admin rights)
            # Map 'member' -> 'manager'
            # Everything else to 'manager' by default if it's not 'admin'
            
            await conn.execute(text("""
                UPDATE users 
                SET role = CASE 
                    WHEN lower(role::text) = 'owner' THEN 'admin'
                    WHEN lower(role::text) = 'member' THEN 'manager'
                    WHEN lower(role::text) = 'admin' THEN 'admin'
                    ELSE 'manager'
                END
            """))
            
            logger.info("Role normalization and mapping completed successfully.")
            
            # Verify
            result = await conn.execute(text("SELECT DISTINCT role FROM users"))
            new_roles = [r[0] for r in result.fetchall()]
            logger.info(f"New roles in DB: {new_roles}")
            
        except Exception as e:
            logger.error(f"Error during role normalization: {e}")
            logger.info("If this failed due to Enum type constraints, you might need to manually alter the TYPE in Postgres.")
            raise

if __name__ == "__main__":
    asyncio.run(normalize_roles())
