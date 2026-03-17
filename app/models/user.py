from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base

class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"

class Mill(Base):
    __tablename__ = "mills"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False) # Business name
    mill_tag = Column(String, unique=True, index=True, nullable=True) # ID used in CSVs (e.g. MILL_01)
    api_key = Column(String, unique=True, index=True, nullable=True)
    has_uploaded_baseline = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="mill")
    invitations = relationship("Invitation", back_populates="mill")

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER, nullable=False)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String, unique=True, index=True, nullable=True)
    password_reset_token = Column(String, unique=True, index=True, nullable=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    mill = relationship("Mill", back_populates="users")

class Invitation(Base):
    __tablename__ = "invitations"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    mill_id = Column(Integer, ForeignKey("mills.id"), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_accepted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    mill = relationship("Mill", back_populates="invitations")
