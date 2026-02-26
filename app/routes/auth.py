from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
from typing import Optional
import secrets

from app.core.database import get_db
from app.models.user import User
from app.core.config import settings

router = APIRouter()

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    mill_id: str

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

@router.post("/register", response_model=dict)
async def register(user: UserRegister, db: AsyncSession = Depends(get_db)):
    # Check if user exists
    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Generate API Key
    api_key = f"fsa_{user.mill_id}_{secrets.token_hex(16)}"
    
    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        mill_id=user.mill_id,
        api_key=api_key
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return {"status": "success", "api_key": api_key, "message": "User created. Save this API key!"}

@router.post("/login", response_model=Token)
async def login(user: UserRegister, db: AsyncSession = Depends(get_db)):
     # Simplified login for MVP, normally use OAuth2PasswordRequestForm
    result = await db.execute(select(User).where(User.email == user.email))
    db_user = result.scalars().first()
    
    if not db_user or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": db_user.email, "mill_id": db_user.mill_id}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "api_key": db_user.api_key}

@router.post("/logout")
async def logout():
    """
    Log out the user. 
    Since we use JWT, the client should discard the token.
    Server-side invalidation could be implemented with a blocklist if needed.
    """
    return {"status": "success", "message": "Successfully logged out"}
