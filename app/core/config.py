import yaml
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    voltage: float = 400.0
    power_factor: float = 0.85
    efficiency: float = 0.90
    grid_emission_factor: float = 0.233
    raw_data_retention_days: int = 3650
    
    database_url: str = "postgresql+asyncpg://user:password@localhost/factorysense"
    secret_key: str = "CHANGE_ME_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    sentry_dsn: Optional[str] = None
    
    # SMTP Settings
    smtp_server: str = "localhost"
    smtp_port: int = 1025
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: str = "noreply@factorysense.ai"

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    # Load from config.yaml if present, override with env vars
    try:
        with open("config.yaml", "r") as f:
            config_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config_data = {}
    
    return Settings(**config_data)

settings = get_settings()
