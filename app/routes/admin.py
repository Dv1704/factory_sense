from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from app.core.database import get_db
from app.models.user import User, Mill, UserRole
from app.models.mill_data import RawFile
from app.routes.auth import get_current_admin_user, get_password_hash
from pydantic import BaseModel, EmailStr
import secrets

router = APIRouter()

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
    message: str # Why it was corrected

@router.get("/users")
async def list_users(admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """List all users in the platform."""
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [{"id": u.id, "email": u.email, "role": u.role.value, "created_at": u.created_at} for u in users]

@router.post("/users")
async def create_user(user: UserCreate, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """Admin endpoint to create a new user directly."""
    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already exists")
    
    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"status": "success", "user_id": new_user.id}

@router.put("/users/{user_id}/reset-password")
async def reset_password(user_id: int, user_update: UserUpdate, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """Force reset a user's password."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password_hash = get_password_hash(user_update.password)
    await db.commit()
    return {"status": "success", "message": f"Password reset for user {user.email}"}

@router.get("/mills")
async def list_mills(admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """List all registered mills."""
    result = await db.execute(select(Mill))
    mills = result.scalars().all()
    return [
        {
            "id": m.id, 
            "mill_tag": m.mill_tag, 
            "api_key": m.api_key, 
            "has_baseline": m.has_uploaded_baseline,
            "created_at": m.created_at
        } for m in mills
    ]

@router.post("/mills")
async def create_mill(mill: MillCreate, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """Add a new mill and assign it to an existing user."""
    # check user
    result = await db.execute(select(User).where(User.id == mill.user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    api_key = f"fsa_{mill.mill_tag}_{secrets.token_hex(16)}"
    new_mill = Mill(
        name=mill.mill_name,
        mill_tag=mill.mill_tag,
        api_key=api_key
    )
    db.add(new_mill)
    await db.flush()
    
    # Associate user with the new mill and promote to OWNER if appropriate
    user.mill_id = new_mill.id
    user.role = UserRole.OWNER # Default to Owner when creating a mill for them
    
    await db.commit()
    await db.refresh(new_mill)
    return {"status": "success", "mill_id": new_mill.id, "api_key": api_key}

@router.get("/uploads")
async def global_upload_history(admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """View platform-wide upload history."""
    result = await db.execute(select(RawFile).order_by(RawFile.upload_timestamp.desc()).limit(100))
    history = result.scalars().all()
    return [
        {
            "mill_id": h.mill_id,
            "mill_tag": h.mill_tag,
            "filename": h.filename,
            "timestamp": h.upload_timestamp,
            "status": h.status
        } for h in history
    ]

@router.put("/stats/{stats_id}")
async def correct_machine_stats(
    stats_id: int, 
    update_data: StatsUpdate, 
    admin: User = Depends(get_current_admin_user), 
    db: AsyncSession = Depends(get_db)
):
    """Manual data correction tool for machine statistics."""
    from app.models.mill_data import MachineDailyStats
    result = await db.execute(select(MachineDailyStats).where(MachineDailyStats.id == stats_id))
    stats = result.scalars().first()
    if not stats:
        raise HTTPException(status_code=404, detail="Stats record not found")
    
    stats.health_score = update_data.health_score
    stats.bearing_risk = update_data.bearing_risk
    # LOG the correction in details
    stats.health_score_details = f"MANUALLY CORRECTED: {update_data.message}"
    
    await db.commit()
    return {"status": "success", "message": f"Stats {stats_id} corrected manually."}
