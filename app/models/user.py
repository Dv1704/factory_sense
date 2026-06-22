from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base

class UserRole(str, enum.Enum):
    superadmin = "superadmin"   # app owner only — sees entire platform
    admin = "admin"             # mill admin — manages their own mills
    manager = "manager"         # operator — accesses data via API key

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.manager, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Which mill admin created this user (NULL for self-registered admins and superadmin)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    mills = relationship("Mill", back_populates="owner",
                         foreign_keys="Mill.user_id", cascade="all, delete-orphan")

class Mill(Base):
    __tablename__ = "mills"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    mill_id = Column(String, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=True)
    has_uploaded_baseline = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # The mill admin account that owns/administers this mill.
    # user_id = whoever holds the API key (may be a manager).
    # admin_id = the mill admin who created and is responsible for this mill.
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    owner = relationship("User", back_populates="mills", foreign_keys=[user_id])
