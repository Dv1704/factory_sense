from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
from datetime import datetime, timedelta
from typing import Optional
import secrets

from app.core.database import get_db
from app.models.user import User, Mill, UserRole
from app.core.config import settings

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    mill_id: str
    # Self-registration creates mill_admin (admin) by default.
    # Superadmin is never self-registered — it is seeded via env vars at startup.
    # Managers are created by mill admins via the /admin/users endpoint.
    role: Optional[UserRole] = UserRole.admin


class Token(BaseModel):
    access_token: str
    token_type: str
    api_key: Optional[str] = None
    mill_id: Optional[str] = None


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_superadmin_user(current_user: User = Depends(get_current_user)):
    """Only the platform owner (superadmin) passes this guard."""
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    """Mill admins and superadmin both pass this guard.
    Routes that use this dependency must additionally check
    current_user.role == UserRole.superadmin to decide whether
    to apply per-mill data scoping.
    """
    if current_user.role not in (UserRole.admin, UserRole.superadmin):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user


@router.post("/register", response_model=dict)
async def register(user: UserRegister, db: AsyncSession = Depends(get_db)):
    """
    Public self-registration — creates a mill admin account with one initial mill.
    Superadmin role cannot be claimed here; it is seeded at server startup via env vars.
    """
    if user.role == UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin accounts cannot be self-registered",
        )

    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role,
    )
    db.add(new_user)
    await db.flush()

    api_key = f"fsa_{user.mill_id}_{secrets.token_hex(16)}"
    new_mill = Mill(
        user_id=new_user.id,
        admin_id=new_user.id,   # self-registered admin owns their own first mill
        mill_id=user.mill_id,
        api_key=api_key,
    )
    db.add(new_mill)
    await db.commit()
    await db.refresh(new_user)

    return {"status": "success", "api_key": api_key, "message": "Account created. Save this API key!"}


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login", response_model=Token)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    db_user = result.scalars().first()

    if not db_user or not verify_password(body.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)

    mill_result = await db.execute(select(Mill).where(Mill.user_id == db_user.id))
    first_mill = mill_result.scalars().first()
    api_key = first_mill.api_key if first_mill else None
    mill_id = first_mill.mill_id if first_mill else "N/A"

    access_token = create_access_token(
        data={"sub": db_user.email, "mill_id": mill_id, "role": db_user.role.value},
        expires_delta=access_token_expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "api_key": api_key,
        "mill_id": mill_id,
    }


@router.post("/logout")
async def logout():
    return {"status": "success", "message": "Successfully logged out"}


@router.get("/me")
async def read_users_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Mill).where(Mill.user_id == current_user.id))
    mills = result.scalars().all()

    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "created_at": current_user.created_at,
        "mills": [
            {
                "mill_id": m.mill_id,
                "api_key": m.api_key,
                "has_baseline": m.has_uploaded_baseline,
            }
            for m in mills
        ],
    }
