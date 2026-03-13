from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MANAGER, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    mills = relationship("Mill", back_populates="owner", cascade="all, delete-orphan")

class Mill(Base):
    __tablename__ = "mills"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    mill_id = Column(String, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=True)
    has_uploaded_baseline = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", back_populates="mills")
