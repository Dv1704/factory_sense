from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional

from app.core.database import get_db
from app.models.user import User, Mill, UserRole
from app.models.mill_data import RawFile, ProcessingStatus
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
    user_id: Optional[int] = None
    email: Optional[EmailStr] = None
    mill_id: str

class StatsUpdate(BaseModel):
    health_score: float
    bearing_risk: str
    message: str # Why it was corrected

@router.get("/users")
async def list_users(
    email: Optional[str] = None,
    role: Optional[UserRole] = None,
    admin: User = Depends(get_current_admin_user), 
    db: AsyncSession = Depends(get_db)
):
    """List all users in the platform."""
    query = select(User)
    if email:
        query = query.where(User.email.contains(email))
    if role:
        query = query.where(User.role == role)
        
    result = await db.execute(query)
    users = result.scalars().all()
    
    # Enrich with mill count
    enriched_users = []
    for u in users:
        # This is a bit inefficient (N+1), but fine for admin panel with small number of users
        # Better: use a join with count
        mill_count_res = await db.execute(select(func.count(Mill.id)).where(Mill.user_id == u.id))
        mill_count = mill_count_res.scalar()
        
        enriched_users.append({
            "id": u.id, 
            "email": u.email, 
            "role": u.role.value, 
            "mill_count": mill_count,
            "created_at": u.created_at
        })
    return enriched_users

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
async def list_mills(
    mill_id: Optional[str] = None,
    user_id: Optional[int] = None,
    admin: User = Depends(get_current_admin_user), 
    db: AsyncSession = Depends(get_db)
):
    """List all registered mills."""
    # Join with User to get owner email
    query = select(Mill, User).join(User, Mill.user_id == User.id)
    if mill_id:
        query = query.where(Mill.mill_id.contains(mill_id))
    if user_id:
        query = query.where(Mill.user_id == user_id)
        
    result = await db.execute(query)
    mills_with_users = result.all()
    
    return [
        {
            "id": m.id, 
            "user_id": m.user_id, 
            "owner_email": u.email,
            "mill_id": m.mill_id, 
            "api_key": m.api_key, 
            "has_baseline": m.has_uploaded_baseline,
            "created_at": m.created_at
        } for m, u in mills_with_users
    ]

@router.post("/mills")
async def create_mill(mill: MillCreate, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """Add a new mill for an existing user."""
    user_id = mill.user_id
    if not user_id and mill.email:
        result = await db.execute(select(User).where(User.email == mill.email))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user_id = user.id
    
    if not user_id:
        raise HTTPException(status_code=400, detail="Either user_id or email must be provided")

    # check if mill_id already exists for this user or globally?
    # Usually mill_id is unique per mill.
    
    api_key = f"fsa_{mill.mill_id}_{secrets.token_hex(16)}"
    new_mill = Mill(
        user_id=user_id,
        mill_id=mill.mill_id,
        api_key=api_key
    )
    db.add(new_mill)
    await db.commit()
    await db.refresh(new_mill)
    return {"status": "success", "mill_id": new_mill.id, "api_key": api_key}

@router.get("/tasks")
async def list_tasks(
    status: Optional[ProcessingStatus] = None,
    mill_id: Optional[str] = None,
    admin: User = Depends(get_current_admin_user), 
    db: AsyncSession = Depends(get_db)
):
    """View all background processing tasks."""
    from app.models.mill_data import ProcessingTask
    query = select(ProcessingTask)
    if status:
        query = query.where(ProcessingTask.status == status)
    if mill_id:
        query = query.where(ProcessingTask.mill_id == mill_id)
        
    result = await db.execute(query.order_by(ProcessingTask.created_at.desc()))
    tasks = result.scalars().all()
    return tasks

@router.get("/uploads")
async def global_upload_history(admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    """View platform-wide upload history."""
    result = await db.execute(select(RawFile).order_by(RawFile.upload_timestamp.desc()).limit(100))
    history = result.scalars().all()
    return [
        {
            "mill_id": h.mill_id,
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
