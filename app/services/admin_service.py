from typing import Optional
import secrets

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, Mill, UserRole
from app.models.mill_data import ProcessingStatus
from app.repositories import user_repository, mill_repository, task_repository, stats_repository
from app.services.auth_service import get_password_hash


def is_superadmin(user: User) -> bool:
    return user.role == UserRole.superadmin


async def _assert_owns_user(target_user: User, admin: User) -> None:
    """Mill admin may only act on users they created."""
    if not is_superadmin(admin) and target_user.created_by_id != admin.id:
        raise HTTPException(status_code=403, detail="Access denied to this user")


async def _assert_owns_mill(mill: Mill, admin: User) -> None:
    """Mill admin may only act on mills they administer."""
    if not is_superadmin(admin) and mill.admin_id != admin.id:
        raise HTTPException(status_code=403, detail="Access denied to this mill")


async def list_users(db: AsyncSession, admin: User, *, email: Optional[str], role: Optional[UserRole]) -> list:
    """
    Superadmin: all users.
    Mill admin: only users they created (their managers).
    """
    created_by_id = None if is_superadmin(admin) else admin.id
    users = await user_repository.list_users(db, created_by_id=created_by_id, email_contains=email, role=role)

    enriched = []
    for u in users:
        mill_count = await user_repository.count_mills_for_user(db, u.id)
        enriched.append({
            "id": u.id,
            "email": u.email,
            "role": u.role.value,
            "mill_count": mill_count,
            "created_at": u.created_at,
        })
    return enriched


async def create_user(db: AsyncSession, admin: User, user) -> dict:
    """
    Create a new user.
    Mill admin can only create manager-role users.
    Superadmin can create any role except superadmin (use env seed for that).
    """
    if not is_superadmin(admin):
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

    existing = await user_repository.get_by_email(db, user.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    new_user = await user_repository.create(
        db, email=user.email, password_hash=get_password_hash(user.password),
        role=user.role, created_by_id=admin.id,
    )
    await db.commit()
    await db.refresh(new_user)
    return {"status": "success", "user_id": new_user.id}


async def reset_password(db: AsyncSession, admin: User, user_id: int, user_update) -> dict:
    user = await user_repository.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await _assert_owns_user(user, admin)

    user.password_hash = get_password_hash(user_update.password)
    await db.commit()
    return {"status": "success", "message": f"Password reset for {user.email}"}


async def list_mills(db: AsyncSession, admin: User, *, mill_id: Optional[str], user_id: Optional[int]) -> list:
    """
    Superadmin: all mills across all customers.
    Mill admin: only mills they own (admin_id == current user).
    """
    admin_id = None if is_superadmin(admin) else admin.id
    rows = await mill_repository.list_mills(db, admin_id=admin_id, mill_id_contains=mill_id, user_id=user_id)
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
        for m, u in rows
    ]


async def create_mill(db: AsyncSession, admin: User, mill) -> dict:
    """
    Create a mill.
    Mill admin: mill is always created under themselves (admin_id = self).
    Superadmin: can assign to any user_id / email; admin_id stays as superadmin.
    """
    if is_superadmin(admin):
        # Superadmin can target any user
        target_user_id = mill.user_id
        if not target_user_id and mill.email:
            u = await user_repository.get_by_email(db, mill.email)
            if not u:
                raise HTTPException(status_code=404, detail="User not found")
            target_user_id = u.id
        if not target_user_id:
            raise HTTPException(status_code=400, detail="Provide user_id or email")
    else:
        # Mill admin always owns their own mills; ignore any user_id in payload
        target_user_id = admin.id

    api_key = f"fsa_{mill.mill_id}_{secrets.token_hex(16)}"
    new_mill = await mill_repository.create(
        db, user_id=target_user_id, admin_id=admin.id, mill_id=mill.mill_id, api_key=api_key
    )
    await db.commit()
    await db.refresh(new_mill)
    return {"status": "success", "mill_id": new_mill.id, "api_key": api_key}


async def list_tasks(db: AsyncSession, admin: User, *, status: Optional[ProcessingStatus], mill_id: Optional[str]) -> list:
    """Processing task queue, scoped to the calling admin's mills."""
    mill_ids = None
    if not is_superadmin(admin):
        mill_ids = await mill_repository.list_mill_ids_for_admin(db, admin.id)
    return await task_repository.list_tasks(db, mill_ids=mill_ids, status=status, mill_id=mill_id)


async def global_upload_history(db: AsyncSession, admin: User) -> list:
    """Upload history, scoped to the calling admin's mills."""
    mill_ids = None
    if not is_superadmin(admin):
        mill_ids = await mill_repository.list_mill_ids_for_admin(db, admin.id)
    history = await task_repository.list_global_upload_history(db, mill_ids=mill_ids, limit=100)
    return [
        {
            "mill_id": h.mill_id,
            "filename": h.filename,
            "timestamp": h.upload_timestamp,
            "status": h.status,
        }
        for h in history
    ]


async def correct_machine_stats(db: AsyncSession, admin: User, stats_id: int, update_data) -> dict:
    """Manual data correction — mill admin may only correct stats for their own mills."""
    stats = await stats_repository.get_by_id(db, stats_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Stats record not found")

    if not is_superadmin(admin):
        owned = await mill_repository.get_owned_by_admin(db, stats.mill_id, admin.id)
        if not owned:
            raise HTTPException(status_code=403, detail="Access denied to this mill's stats")

    stats.health_score = update_data.health_score
    stats.bearing_risk = update_data.bearing_risk
    stats.health_score_details = f"MANUALLY CORRECTED: {update_data.message}"

    await db.commit()
    return {"status": "success", "message": f"Stats {stats_id} corrected."}
