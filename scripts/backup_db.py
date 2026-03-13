import os
import subprocess
import datetime
import logging
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def backup_database():
    """
    Perform a database backup using pg_dump.
    Expects DATABASE_URL to be in the format: postgresql+asyncpg://user:password@host:port/dbname
    """
    try:
        # Parse db connection from settings.database_url
        # Example: postgresql+asyncpg://factory_user:factory_password@db/factorysense
        db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        
        # Define backup filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            
        backup_file = os.path.join(backup_dir, f"backup_{timestamp}.sql")
        
        logger.info(f"Starting database backup to {backup_file}...")
        
        # Construct the pg_dump command
        # Note: In production, it's better to use a .pgpass file or set the PGPASSWORD env var
        # For simplicity in this script, we'll try to extract the password and use PGPASSWORD
        
        # Example extraction (naive)
        parts = db_url.split("://")[1].split("@")
        creds = parts[0].split(":")
        user = creds[0]
        password = creds[1]
        
        host_db = parts[1].split("/")
        host_port = host_db[0].split(":")
        host = host_port[0]
        dbname = host_db[1]
        
        env = os.environ.copy()
        env["PGPASSWORD"] = password
        
        cmd = [
            "pg_dump",
            "-h", host,
            "-U", user,
            "-d", dbname,
            "-f", backup_file
        ]
        
        subprocess.run(cmd, env=env, check=True)
        logger.info("Backup completed successfully.")
        
        # Rotation logic: Keep last 7 backups
        all_backups = sorted(
            [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith(".sql")],
            key=os.path.getmtime
        )
        if len(all_backups) > 7:
            for b_to_delete in all_backups[:-7]:
                os.remove(b_to_delete)
                logger.info(f"Deleted old backup: {b_to_delete}")
                
    except Exception as e:
        logger.error(f"Backup failed: {e}")

if __name__ == "__main__":
    backup_database()
