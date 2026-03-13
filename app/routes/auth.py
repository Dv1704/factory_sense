from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import datetime, timedelta
from typing import Optional
import secrets

from app.core.database import get_db
from app.models.user import User, Mill, UserRole
from app.core.config import settings

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    mill_id: str
    role: Optional[UserRole] = UserRole.MANAGER

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

async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user

@router.post("/register", response_model=dict)
async def register(user: UserRegister, db: AsyncSession = Depends(get_db)):
    # Check if user exists
    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create User
    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role
    )
    db.add(new_user)
    await db.flush() # Get user.id
    
    # Generate API Key for the first mill
    api_key = f"fsa_{user.mill_id}_{secrets.token_hex(16)}"
    
    # Create first Mill
    new_mill = Mill(
        user_id=new_user.id,
        mill_id=user.mill_id,
        api_key=api_key
    )
    db.add(new_mill)
    await db.commit()
    await db.refresh(new_user)
    
    return {"status": "success", "api_key": api_key, "message": "User created with initial mill. Save this API key!"}

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    db_user = result.scalars().first()
    
    if not db_user or not verify_password(form_data.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    
    # Get first mill for this user
    mill_result = await db.execute(select(Mill).where(Mill.user_id == db_user.id))
    first_mill = mill_result.scalars().first()
    api_key = first_mill.api_key if first_mill else None
    mill_id = first_mill.mill_id if first_mill else "N/A"

    access_token = create_access_token(
        data={"sub": db_user.email, "mill_id": mill_id}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "api_key": api_key}

@router.post("/logout")
async def logout():
    """
    Log out the user. 
    Since we use JWT, the client should discard the token.
    Server-side invalidation could be implemented with a blocklist if needed.
    """
    return {"status": "success", "message": "Successfully logged out"}

@router.get("/me")
async def read_users_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value
    }
