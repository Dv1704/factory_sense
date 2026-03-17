from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Date, Enum, Boolean, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum

class BearingRisk(str, enum.Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HIGH = "HIGH"

class AlertType(str, enum.Enum):
    DATA_GAP = "DATA_GAP"
    WARNING = "WARNING"
    CO2_INCREASE = "CO2_INCREASE"

class ProcessingStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class RawFile(Base):
    __tablename__ = "raw_files"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False) # Original string ID from CSV
    filename = Column(String, nullable=False)
    upload_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, default="PENDING")

class MachineDailyStats(Base):
    __tablename__ = "machine_daily_stats"
    __table_args__ = (
        Index('ix_machine_daily_stats_mill_machine_date', 'mill_id', 'machine_id', 'date'),
    )

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    date = Column(Date, index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False) # Original string ID from CSV
    machine_id = Column(String, index=True, nullable=False)
    
    total_energy_kwh = Column(Float, nullable=False)
    baseline_kwh = Column(Float, nullable=False)
    excess_kwh = Column(Float, nullable=False)
    
    total_co2_kg = Column(Float, nullable=False)
    excess_co2_kg = Column(Float, nullable=False)
    
    bearing_risk = Column(Enum(BearingRisk), default=BearingRisk.NORMAL, nullable=False)
    health_score = Column(Float, nullable=False)
    
    run_hours = Column(Float, nullable=False)
    avg_current_A = Column(Float, nullable=True)
    max_current = Column(Float, nullable=True)
    std_current = Column(Float, nullable=True)
    
    reference_mean = Column(Float, nullable=True)
    reference_std = Column(Float, nullable=True)
    reference_p95 = Column(Float, nullable=True)
    
    health_score_details = Column(String, nullable=True) # JSON string

class MachineBaseline(Base):
    __tablename__ = "machine_baselines"
    __table_args__ = (
        Index('ix_machine_baselines_mill_machine', 'mill_id', 'machine_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    
    mean_current = Column(Float, nullable=False)
    std_current = Column(Float, nullable=False)
    p95_current = Column(Float, nullable=False)
    data_points_count = Column(Integer, default=0, nullable=False)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class MachineBaselineHistory(Base):
    __tablename__ = "machine_baseline_history"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    
    mean_current = Column(Float, nullable=False)
    std_current = Column(Float, nullable=False)
    p95_current = Column(Float, nullable=False)
    data_points_count = Column(Integer, nullable=False)
    update_type = Column(String, nullable=False) # e.g., "UPLOAD", "MANUAL"
    
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class MachineDataPoint(Base):
    __tablename__ = "machine_data_points"
    __table_args__ = (
        Index('ix_machine_data_points_mill_machine_time', 'mill_id', 'machine_id', 'timestamp'),
    )

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    current_A = Column(Float, nullable=False)
    motor_state = Column(String, nullable=False)
    
    power_kw = Column(Float, nullable=False)
    energy_kwh = Column(Float, nullable=False)
    co2_kg = Column(Float, nullable=False)

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=True)
    type = Column(Enum(AlertType), nullable=False)
    message = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    is_acknowledged = Column(Boolean, default=False)

class ProcessingTask(Base):
    __tablename__ = "processing_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, unique=True, index=True, nullable=False)
    mill_id = Column(Integer, ForeignKey("mills.id"), index=True, nullable=False)
    mill_tag = Column(String, index=True, nullable=False)
    filename = Column(String, nullable=False)
    
    status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING, nullable=False)
    progress = Column(Float, default=0.0)
    message = Column(String, nullable=True)
    task_type = Column(String, nullable=False)
    records_processed = Column(Integer, default=0)
    total_records = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    estimated_seconds_remaining = Column(Float, nullable=True)
