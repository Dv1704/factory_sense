from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Date, Enum, Boolean
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

class RawFile(Base):
    __tablename__ = "raw_files"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(String, index=True, nullable=False)
    filename = Column(String, nullable=False)
    upload_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, default="PENDING")

class MachineDailyStats(Base):
    __tablename__ = "machine_daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, index=True, nullable=False)
    mill_id = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    
    total_energy_kwh = Column(Float, nullable=False)
    baseline_kwh = Column(Float, nullable=False)
    excess_kwh = Column(Float, nullable=False)
    
    total_co2_kg = Column(Float, nullable=False)
    excess_co2_kg = Column(Float, nullable=False)
    
    bearing_risk = Column(Enum(BearingRisk), default=BearingRisk.NORMAL, nullable=False)
    health_score = Column(Float, nullable=False)
    
    run_hours = Column(Float, nullable=False)
    # New fields for machine metrics
    avg_current_A = Column(Float, nullable=True)
    max_current = Column(Float, nullable=True)
    std_current = Column(Float, nullable=True)
    health_score_details = Column(String, nullable=True) # JSON string of penalties

class MachineBaseline(Base):
    __tablename__ = "machine_baselines"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    
    mean_current = Column(Float, nullable=False)
    std_current = Column(Float, nullable=False)
    p95_current = Column(Float, nullable=False)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class MachineDataPoint(Base):
    __tablename__ = "machine_data_points"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    mill_id = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    current_A = Column(Float, nullable=False)
    motor_state = Column(String, nullable=False)
    
    power_kw = Column(Float, nullable=False)
    energy_kwh = Column(Float, nullable=False)
    co2_kg = Column(Float, nullable=False)

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    mill_id = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=True)
    type = Column(Enum(AlertType), nullable=False)
    message = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    is_acknowledged = Column(Boolean, default=False)
