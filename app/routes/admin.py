from datetime import datetime
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

# ── helpers ───────────────────────────────────────────────────────────────────

def _is_superadmin(user: User) -> bool:
    return user.role == UserRole.superadmin


async def _assert_owns_user(target_user: User, admin: User) -> None:
    """Mill admin may only act on users they created."""
    if not _is_superadmin(admin) and target_user.created_by_id != admin.id:
        raise HTTPException(status_code=403, detail="Access denied to this user")


async def _assert_owns_mill(mill: Mill, admin: User) -> None:
    """Mill admin may only act on mills they administer."""
    if not _is_superadmin(admin) and mill.admin_id != admin.id:
        raise HTTPException(status_code=403, detail="Access denied to this mill")


# ── schemas ───────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: UserRole = UserRole.manager   # mill admins create managers by default


class UserUpdate(BaseModel):
    password: str


class MillCreate(BaseModel):
    mill_id: str
    # For superadmin: optionally assign to a different user. For mill admin: always self.
    user_id: Optional[int] = None
    email: Optional[EmailStr] = None


class StatsUpdate(BaseModel):
    health_score: float
    bearing_risk: str
    message: str


class StatusMessage(BaseModel):
    status: str
    message: str


class UserListItem(BaseModel):
    id: int
    email: str
    role: str
    mill_count: int
    created_at: datetime


class CreateUserResponse(BaseModel):
    status: str
    user_id: int


class MillListItem(BaseModel):
    id: int
    user_id: int
    owner_email: str
    mill_id: str
    api_key: str
    has_baseline: bool
    admin_id: Optional[int] = None
    created_at: datetime


class CreateMillResponse(BaseModel):
    status: str
    mill_id: int
    api_key: str


class ProcessingTaskItem(BaseModel):
    id: int
    task_id: str
    user_id: int
    mill_id: str
    filename: str
    status: ProcessingStatus
    progress: float
    message: Optional[str] = None
    task_type: str
    records_processed: int
    total_records: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    estimated_seconds_remaining: Optional[float] = None


class UploadHistoryItem(BaseModel):
    mill_id: str
    filename: str
    timestamp: datetime
    status: str


# ── users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=List[UserListItem])
async def list_users(
    email: Optional[str] = None,
    role: Optional[UserRole] = None,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Superadmin: all users.
    Mill admin: only users they created (their managers).
    """
    query = select(User)

    if not _is_superadmin(admin):
        query = query.where(User.created_by_id == admin.id)

    if email:
        query = query.where(User.email.contains(email))
    if role:
        query = query.where(User.role == role)

    result = await db.execute(query)
    users = result.scalars().all()

    enriched = []
    for u in users:
        mill_count = (
            await db.execute(select(func.count(Mill.id)).where(Mill.user_id == u.id))
        ).scalar() or 0
        enriched.append({
            "id": u.id,
            "email": u.email,
            "role": u.role.value,
            "mill_count": mill_count,
            "created_at": u.created_at,
        })
    return enriched


@router.post("/users", response_model=CreateUserResponse)
async def create_user(
    user: UserCreate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new user.
    Mill admin can only create manager-role users.
    Superadmin can create any role except superadmin (use env seed for that).
    """
    if not _is_superadmin(admin):
        if user.role != UserRole.manager:
            raise HTTPException(
                status_code=403,
                detail="Mill admins can only create manager accounts",
            )

    if user.role == UserRole.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Superadmin accounts cannot be created via API",
        )

    existing = await db.execute(select(User).where(User.email == user.email))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Email already exists")

    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role,
        created_by_id=admin.id,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"status": "success", "user_id": new_user.id}


@router.put(
    "/users/{user_id}/reset-password",
    response_model=StatusMessage,
    responses={404: {"description": "User not found"}, 403: {"description": "Access denied to this user"}},
)
async def reset_password(
    user_id: int,
    user_update: UserUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await _assert_owns_user(user, admin)

    user.password_hash = get_password_hash(user_update.password)
    await db.commit()
    return {"status": "success", "message": f"Password reset for {user.email}"}


# ── mills ─────────────────────────────────────────────────────────────────────

@router.get("/mills", response_model=List[MillListItem])
async def list_mills(
    mill_id: Optional[str] = None,
    user_id: Optional[int] = None,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Superadmin: all mills across all customers.
    Mill admin: only mills they own (admin_id == current user).
    """
    query = select(Mill, User).join(User, Mill.user_id == User.id)

    if not _is_superadmin(admin):
        query = query.where(Mill.admin_id == admin.id)

    if mill_id:
        query = query.where(Mill.mill_id.contains(mill_id))
    if user_id:
        query = query.where(Mill.user_id == user_id)

    result = await db.execute(query)
    return [
        {
            "id": m.id,
            "user_id": m.user_id,
            "owner_email": u.email,
            "mill_id": m.mill_id,
            "api_key": m.api_key,
            "has_baseline": m.has_uploaded_baseline,
            "admin_id": m.admin_id,
            "created_at": m.created_at,
        }
        for m, u in result.all()
    ]


@router.post("/mills", response_model=CreateMillResponse)
async def create_mill(
    mill: MillCreate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a mill.
    Mill admin: mill is always created under themselves (admin_id = self).
    Superadmin: can assign to any user_id / email; admin_id stays as superadmin.
    """
    if _is_superadmin(admin):
        # Superadmin can target any user
        target_user_id = mill.user_id
        if not target_user_id and mill.email:
            res = await db.execute(select(User).where(User.email == mill.email))
            u = res.scalars().first()
            if not u:
                raise HTTPException(status_code=404, detail="User not found")
            target_user_id = u.id
        if not target_user_id:
            raise HTTPException(status_code=400, detail="Provide user_id or email")
    else:
        # Mill admin always owns their own mills; ignore any user_id in payload
        target_user_id = admin.id

    api_key = f"fsa_{mill.mill_id}_{secrets.token_hex(16)}"
    new_mill = Mill(
        user_id=target_user_id,
        admin_id=admin.id,
        mill_id=mill.mill_id,
        api_key=api_key,
    )
    db.add(new_mill)
    await db.commit()
    await db.refresh(new_mill)
    return {"status": "success", "mill_id": new_mill.id, "api_key": api_key}


# ── tasks & uploads ───────────────────────────────────────────────────────────

@router.get("/tasks", response_model=List[ProcessingTaskItem])
async def list_tasks(
    status: Optional[ProcessingStatus] = None,
    mill_id: Optional[str] = None,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Processing task queue, scoped to the calling admin's mills."""
    from app.models.mill_data import ProcessingTask

    query = select(ProcessingTask)

    if not _is_superadmin(admin):
        # Get mill_ids this admin owns
        admin_mill_ids_q = select(Mill.mill_id).where(Mill.admin_id == admin.id)
        admin_mill_ids = (await db.execute(admin_mill_ids_q)).scalars().all()
        query = query.where(ProcessingTask.mill_id.in_(admin_mill_ids))

    if status:
        query = query.where(ProcessingTask.status == status)
    if mill_id:
        query = query.where(ProcessingTask.mill_id == mill_id)

    result = await db.execute(query.order_by(ProcessingTask.created_at.desc()))
    return result.scalars().all()


@router.get("/uploads", response_model=List[UploadHistoryItem])
async def global_upload_history(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload history, scoped to the calling admin's mills."""
    query = select(RawFile).order_by(RawFile.upload_timestamp.desc()).limit(100)

    if not _is_superadmin(admin):
        admin_mill_ids_q = select(Mill.mill_id).where(Mill.admin_id == admin.id)
        admin_mill_ids = (await db.execute(admin_mill_ids_q)).scalars().all()
        query = select(RawFile).where(
            RawFile.mill_id.in_(admin_mill_ids)
        ).order_by(RawFile.upload_timestamp.desc()).limit(100)

    result = await db.execute(query)
    return [
        {
            "mill_id": h.mill_id,
            "filename": h.filename,
            "timestamp": h.upload_timestamp,
            "status": h.status,
        }
        for h in result.scalars().all()
    ]


@router.put(
    "/stats/{stats_id}",
    response_model=StatusMessage,
    responses={404: {"description": "Stats record not found"}, 403: {"description": "Access denied to this mill's stats"}},
)
async def correct_machine_stats(
    stats_id: int,
    update_data: StatsUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Manual data correction — mill admin may only correct stats for their own mills."""
    from app.models.mill_data import MachineDailyStats

    result = await db.execute(
        select(MachineDailyStats).where(MachineDailyStats.id == stats_id)
    )
    stats = result.scalars().first()
    if not stats:
        raise HTTPException(status_code=404, detail="Stats record not found")

    if not _is_superadmin(admin):
        # Verify the stats belong to a mill this admin owns
        mill_res = await db.execute(
            select(Mill).where(
                Mill.mill_id == stats.mill_id,
                Mill.admin_id == admin.id,
            )
        )
        if not mill_res.scalars().first():
            raise HTTPException(status_code=403, detail="Access denied to this mill's stats")

    stats.health_score = update_data.health_score
    stats.bearing_risk = update_data.bearing_risk
    stats.health_score_details = f"MANUALLY CORRECTED: {update_data.message}"

    await db.commit()
    return {"status": "success", "message": f"Stats {stats_id} corrected."}
