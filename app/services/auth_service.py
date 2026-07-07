from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
import secrets

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User, Mill, UserRole
from app.repositories import user_repository, mill_repository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
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
    user = await user_repository.get_by_email(db, email)
    if user is None:
        raise credentials_exception
    return user


async def get_current_superadmin_user(current_user: User = Depends(get_current_user)) -> User:
    """Only the platform owner (superadmin) passes this guard."""
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Mill admins and superadmin both pass this guard.
    Routes that use this dependency must additionally check
    current_user.role == UserRole.superadmin to decide whether
    to apply per-mill data scoping.
    """
    if current_user.role not in (UserRole.admin, UserRole.superadmin):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user


async def get_api_key_mill(x_api_key: str = Header(...), db: AsyncSession = Depends(get_db)) -> Mill:
    mill = await mill_repository.get_by_api_key(db, x_api_key)
    if not mill:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return mill


async def register(db: AsyncSession, user) -> dict:
    """Public self-registration — creates a mill admin account with one initial mill.
    Superadmin role cannot be claimed here; it is seeded at server startup via env vars.
    """
    if user.role == UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin accounts cannot be self-registered",
        )

    existing = await user_repository.get_by_email(db, user.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = await user_repository.create(
        db, email=user.email, password_hash=get_password_hash(user.password), role=user.role
    )

    api_key = f"fsa_{user.mill_id}_{secrets.token_hex(16)}"
    await mill_repository.create(
        db, user_id=new_user.id, admin_id=new_user.id, mill_id=user.mill_id, api_key=api_key
    )
    await db.commit()
    await db.refresh(new_user)

    return {"status": "success", "api_key": api_key, "message": "Account created. Save this API key!"}


async def login(db: AsyncSession, email: Optional[str], password: Optional[str]) -> dict:
    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password are required")

    db_user = await user_repository.get_by_email(db, email)
    if not db_user or not verify_password(password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)

    first_mill = await mill_repository.get_first_for_user(db, db_user.id)
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


async def get_profile(db: AsyncSession, current_user: User) -> dict:
    mills = await mill_repository.list_for_user(db, current_user.id)
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
