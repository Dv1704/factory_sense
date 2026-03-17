from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordRequestForm, HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta
from typing import Optional, Union, List
import secrets

from app.core.database import get_db
from app.models.user import User, Mill, UserRole, Invitation
from app.core.config import settings
from app.utility.email import EmailService

router = APIRouter()
security = HTTPBearer()

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    mill_name: str # The name of the mill
    mill_tag: str  # The physical ID used in CSVs (e.g., MILL_01)

class TeammateInvite(BaseModel):
    email: EmailStr
    role: UserRole

class AcceptInvitation(BaseModel):
    email: EmailStr
    password: str
    token: str

class VerifyEmail(BaseModel):
    email: EmailStr
    token: str

class ForgotPassword(BaseModel):
    email: EmailStr

class ResetPassword(BaseModel):
    token: str
    new_password: str

class InvitationResponse(BaseModel):
    id: int
    email: EmailStr
    role: UserRole
    expires_at: datetime
    is_accepted: bool
    created_at: datetime

    class Config:
        from_attributes = True

class TeammateResponse(BaseModel):
    id: int
    email: EmailStr
    role: UserRole
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True

class TeammateUpdate(BaseModel):
    role: UserRole

class Token(BaseModel):
    access_token: str
    token_type: str
    api_key: Optional[str] = None

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = auth.credentials
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        mill_id: int = payload.get("mill_id")
        if email is None or mill_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.email == email, User.mill_id == mill_id))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    
    # We allow unverified users to get their info via /me, 
    # but other dependencies (like require_verified) will block them.
    return user

async def require_verified(current_user: User = Depends(get_current_user)):
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Email verification required to access this resource"
        )
    return current_user

async def require_owner(current_user: User = Depends(require_verified)):
    if current_user.role not in [UserRole.OWNER, UserRole.ADMIN]:
        raise HTTPException(status_code=403, detail="Owner privileges required")
    return current_user

async def require_manager(current_user: User = Depends(require_verified)):
    if current_user.role not in [UserRole.OWNER, UserRole.MANAGER, UserRole.ADMIN]:
        raise HTTPException(status_code=403, detail="Manager or Owner privileges required")
    return current_user

async def get_current_admin_user(current_user: User = Depends(require_verified)):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user

@router.post("/register", response_model=dict)
async def register(user: UserRegister, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Check if user exists
    user_result = await db.execute(select(User).where(User.email == user.email))
    if user_result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Check if mill name or tag already taken
    mill_result = await db.execute(
        select(Mill).where((Mill.name == user.mill_name) | (Mill.mill_tag == user.mill_tag))
    )
    if mill_result.scalars().first():
        raise HTTPException(status_code=400, detail="Mill name or tag already taken")

    # Generate API Key
    api_key = f"fsa_{user.mill_tag}_{secrets.token_hex(16)}"

    # Create Mill (The Tenant)
    new_mill = Mill(
        name=user.mill_name,
        mill_tag=user.mill_tag,
        api_key=api_key
    )
    db.add(new_mill)
    await db.flush() # Get new_mill.id

    # Create Owner User
    verification_token = secrets.token_hex(32)
    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=UserRole.OWNER,
        mill_id=new_mill.id,
        verification_token=verification_token
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Dispatch Verification Email
    background_tasks.add_task(EmailService.send_verification_email, new_user.email, verification_token)
    
    # Generate Access Token
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": new_user.email, "mill_id": new_mill.id}, 
        expires_delta=access_token_expires
    )
    
    return {
        "status": "success", 
        "mill_id": new_mill.id,
        "api_key": api_key, 
        "access_token": access_token,
        "token_type": "bearer",
        "message": f"Mill '{user.mill_name}' registered. You are the Owner."
    }

@router.post("/invite", response_model=dict)
async def invite_teammate(invite: TeammateInvite, background_tasks: BackgroundTasks, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    # Role Enforcement: Only Owner/Admin can invite Managers. Managers can only invite Members.
    if current_user.role == UserRole.MANAGER and invite.role != UserRole.MEMBER:
        raise HTTPException(status_code=403, detail="Managers can only invite Members")
    # require_owner already checks for OWNER role and email verification
    
    # 1. Check if user already exists
    user_result = await db.execute(select(User).where(User.email == invite.email))
    if user_result.scalars().first():
        raise HTTPException(status_code=400, detail="User already registered")
    
    # 2. Check for existing pending invitation
    invite_result = await db.execute(
        select(Invitation).where(
            Invitation.email == invite.email,
            Invitation.mill_id == current_user.mill_id,
            Invitation.is_accepted == False
        )
    )
    existing_invite = invite_result.scalars().first()
    
    if existing_invite:
        # If it exists, just update it (effectively "resending")
        existing_invite.token = secrets.token_urlsafe(32)
        existing_invite.expires_at = datetime.utcnow() + timedelta(hours=48)
        existing_invite.role = invite.role
        await db.commit()
        return {
            "status": "success",
            "message": f"Invitation refreshed for {invite.email}",
            "invite_link": f"/accept-invite?token={existing_invite.token}&email={invite.email}"
        }

    # 3. Create new invitation
    token = secrets.token_urlsafe(32)
    new_invite = Invitation(
        email=invite.email,
        mill_id=current_user.mill_id,
        role=invite.role,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=48)
    )
    db.add(new_invite)
    await db.commit()
    
    # Send Invitation Email
    mill_result = await db.execute(select(Mill).where(Mill.id == current_user.mill_id))
    mill = mill_result.scalars().first()
    invite_link = f"/accept-invite?token={token}&email={invite.email}"
    background_tasks.add_task(EmailService.send_invitation_email, invite.email, invite_link, mill.name, invite.role)

    return {
        "status": "success",
        "message": f"Invitation sent to {invite.email}",
        "invite_link": f"/accept-invite?token={token}&email={invite.email}"
    }

@router.get("/invitations", response_model=list[InvitationResponse])
async def list_invitations(current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    """List all pending invitations for the current Mill."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.mill_id == current_user.mill_id,
            Invitation.is_accepted == False
        ).order_by(Invitation.created_at.desc())
    )
    return result.scalars().all()

@router.delete("/invitations/{invitation_id}", response_model=dict)
async def revoke_invitation(invitation_id: int, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    """Revoke a pending invitation."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.mill_id == current_user.mill_id
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
        
    await db.delete(invitation)
    await db.commit()
    return {"status": "success", "message": "Invitation revoked"}

@router.post("/invitations/{invitation_id}/resend", response_model=dict)
async def resend_invitation(invitation_id: int, background_tasks: BackgroundTasks, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    """Resend a pending invitation (refreshes token and expiry)."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.mill_id == current_user.mill_id
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    
    if invitation.is_accepted:
        raise HTTPException(status_code=400, detail="Invitation already accepted")

    invitation.token = secrets.token_urlsafe(32)
    invitation.expires_at = datetime.utcnow() + timedelta(hours=48)
    await db.commit()
    
    # Send Invitation Email
    mill_result = await db.execute(select(Mill).where(Mill.id == current_user.mill_id))
    mill = mill_result.scalars().first()
    invite_link = f"/accept-invite?token={invitation.token}&email={invitation.email}"
    background_tasks.add_task(EmailService.send_invitation_email, invitation.email, invite_link, mill.name, invitation.role)

    return {
        "status": "success",
        "message": f"Invitation resent to {invitation.email}",
        "invite_link": invite_link
    }

@router.get("/invitations/validate", response_model=dict)
async def validate_invitation(token: str, email: str, db: AsyncSession = Depends(get_db)):
    """Check if an invitation is valid without accepting it."""
    result = await db.execute(
        select(Invitation).where(
            Invitation.email == email,
            Invitation.token == token,
            Invitation.is_accepted == False,
            Invitation.expires_at > datetime.utcnow()
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid, expired, or already used invitation")
    
    return {
        "status": "valid",
        "email": invitation.email,
        "role": invitation.role,
        "mill_id": invitation.mill_id,
        "expires_at": invitation.expires_at
    }

@router.post("/accept-invite", response_model=dict)
async def accept_invitation(data: AcceptInvitation, db: AsyncSession = Depends(get_db)):
    # Verify invitation strictly matches token and email
    result = await db.execute(
        select(Invitation).where(
            Invitation.email == data.email,
            Invitation.token == data.token,
            Invitation.is_accepted == False,
            Invitation.expires_at > datetime.utcnow()
        )
    )
    invitation = result.scalars().first()
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")

    # Double check user doesn't exist (safety)
    user_check = await db.execute(select(User).where(User.email == data.email))
    if user_check.scalars().first():
        raise HTTPException(status_code=400, detail="User already registered")

    # Create user - set is_verified=True immediately since they accepted via secure link
    new_user = User(
        email=data.email,
        password_hash=get_password_hash(data.password),
        role=invitation.role,
        mill_id=invitation.mill_id,
        is_verified=True
    )
    db.add(new_user)
    
    # Mark invitation as accepted (prevents reuse)
    invitation.is_accepted = True
    
    await db.commit()
    
    return {"status": "success", "message": "Account set up successfully. You can now log in."}

@router.post("/login", response_model=Token, summary="Login for Access Token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    db_user = result.scalars().first()
    
    if not db_user or not verify_password(form_data.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get Mill info
    mill_result = await db.execute(select(Mill).where(Mill.id == db_user.mill_id))
    mill = mill_result.scalars().first()
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": db_user.email, "mill_id": db_user.mill_id}, 
        expires_delta=access_token_expires
    )
    return {
        "access_token": access_token, 
        "token_type": "bearer", 
        "api_key": mill.api_key if mill else None
    }

@router.post("/verify-email", response_model=dict)
async def verify_email(data: VerifyEmail, db: AsyncSession = Depends(get_db)):
    # Find user with matching email and token
    result = await db.execute(
        select(User).where(User.email == data.email, User.verification_token == data.token)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification token or email")
    
    user.is_verified = True
    user.verification_token = None # Clear token after verification
    await db.commit()
    
    return {"status": "success", "message": "Email verified successfully"}

@router.post("/logout")
async def logout():
    return {"status": "success", "message": "Successfully logged out"}

@router.post("/forgot-password", response_model=dict)
async def forgot_password(data: ForgotPassword, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Generate a reset token and send an email."""
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalars().first()
    
    # Safety: Even if user not found, we return success to avoid email enumeration
    if user:
        token = secrets.token_urlsafe(32)
        user.password_reset_token = token
        user.reset_token_expires_at = datetime.utcnow() + timedelta(hours=1)
        await db.commit()
        
        reset_link = f"/reset-password?token={token}"
        background_tasks.add_task(EmailService.send_password_reset_email, user.email, reset_link)
    
    return {"status": "success", "message": "If this email is registered, you will receive a reset link shortly."}

@router.post("/reset-password", response_model=dict)
async def reset_password(data: ResetPassword, db: AsyncSession = Depends(get_db)):
    """Securely update password using the reset token."""
    result = await db.execute(
        select(User).where(
            User.password_reset_token == data.token,
            User.reset_token_expires_at > datetime.utcnow()
        )
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    user.password_hash = get_password_hash(data.new_password)
    user.password_reset_token = None
    user.reset_token_expires_at = None
    await db.commit()
    
    return {"status": "success", "message": "Password updated successfully. You can now log in."}

@router.get("/me", summary="Get Current User Info")
async def read_users_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "mill_id": current_user.mill_id,
        "is_verified": current_user.is_verified
    }

@router.get("/teammates", response_model=list[TeammateResponse])
async def list_teammates(current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    """List all users in the same Mill."""
    result = await db.execute(
        select(User).where(User.mill_id == current_user.mill_id).order_by(User.created_at.desc())
    )
    return result.scalars().all()

@router.put("/teammates/{user_id}/role", response_model=dict)
async def update_teammate_role(user_id: int, data: TeammateUpdate, current_user: User = Depends(require_owner), db: AsyncSession = Depends(get_db)):
    """Update a teammate's role (Owner only)."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.mill_id == current_user.mill_id)
    )
    target_user = result.scalars().first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Teammate not found")

    if target_user.id == current_user.id and data.role != UserRole.OWNER:
        # Check if there's another owner before allowing self-demotion
        owner_count_result = await db.execute(
            select(func.count(User.id)).where(User.mill_id == current_user.mill_id, User.role == UserRole.OWNER)
        )
        owner_count = owner_count_result.scalar()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last owner. Assign someone else as Owner first.")

    target_user.role = data.role
    await db.commit()
    return {"status": "success", "message": f"Role updated for {target_user.email} to {data.role}"}

@router.delete("/teammates/{user_id}", response_model=dict)
async def remove_teammate(user_id: int, current_user: User = Depends(require_owner), db: AsyncSession = Depends(get_db)):
    """Remove a teammate from the Mill. Prevents Owner self-deletion if last owner."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.mill_id == current_user.mill_id)
    )
    target_user = result.scalars().first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Teammate not found")

    if target_user.id == current_user.id:
        # Prevent self-deletion if last owner
        owner_count_result = await db.execute(
            select(func.count(User.id)).where(User.mill_id == current_user.mill_id, User.role == UserRole.OWNER)
        )
        owner_count = owner_count_result.scalar()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove yourself as you are the last owner. Delete the Mill or transfer ownership first.")

    await db.delete(target_user)
    await db.commit()
    return {"status": "success", "message": f"User {target_user.email} removed from Mill."}
