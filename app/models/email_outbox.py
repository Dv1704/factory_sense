from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from sqlalchemy.sql import func
import enum
from app.core.database import Base


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class EmailOutbox(Base):
    """Transactional outbox: rows are written in the same DB transaction as the
    business action that triggers them, so the intent to send survives a crash
    even if the actual Resend call never happens. A background sweep in
    app.main delivers PENDING/FAILED rows and marks them SENT."""
    __tablename__ = "email_outbox"

    id = Column(Integer, primary_key=True, index=True)
    to_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    html_body = Column(Text, nullable=True)
    status = Column(Enum(OutboxStatus), default=OutboxStatus.PENDING, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
