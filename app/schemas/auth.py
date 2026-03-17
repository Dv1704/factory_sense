from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional
from app.models.user import UserRole

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    mill_name: str
    mill_tag: str

class TeammateInvite(BaseModel):
    email: EmailStr
    role: UserRole

class AcceptInvitation(BaseModel):
    email: EmailStr
    password: str
    token: str
    full_name: str

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
