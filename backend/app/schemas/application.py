import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, model_validator

from app.schemas.job import JobSummary

ApplicationStatus = Literal["saved", "applied", "interviewing", "offer", "rejected"]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID
    status: ApplicationStatus = "saved"
    notes: Notes | None = None


class ApplicationUpdate(BaseModel):
    """PATCH body: only the fields sent are changed. Send notes: null to clear them."""

    status: ApplicationStatus | None = None
    notes: Notes | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "ApplicationUpdate":
        if not self.model_fields_set:
            raise ValueError("Send status and/or notes.")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status can't be null.")
        return self


class ApplicationOut(BaseModel):
    id: uuid.UUID
    job: JobSummary
    status: ApplicationStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime
