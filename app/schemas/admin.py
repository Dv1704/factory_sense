from pydantic import BaseModel, EmailStr
from app.models.user import UserRole

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: UserRole

class UserUpdate(BaseModel):
    password: str

class MillCreate(BaseModel):
    mill_tag: str
    mill_name: str
    user_id: int

class StatsUpdate(BaseModel):
    health_score: float
    bearing_risk: str
    message: str
