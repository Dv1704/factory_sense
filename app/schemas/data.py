from pydantic import BaseModel, Field
from typing import Optional
from app.models.mill_data import ProcessingStatus

class BaselineUpdate(BaseModel):
    mean_current: float = Field(..., description="Mean current in Amperes")
    std_current: float = Field(..., description="Standard deviation of current")
    p95_current: float = Field(..., description="95th percentile of current")

class TaskResponse(BaseModel):
    task_id: str
    status: ProcessingStatus
    progress: float
    message: Optional[str] = None
    estimated_seconds_remaining: Optional[float] = None
    records_processed: int = 0
    total_records: int = 0

class UploadResponse(BaseModel):
    task_id: str
    message: str
    estimated_initial_seconds: float
