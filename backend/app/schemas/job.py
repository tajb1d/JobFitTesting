import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models import Job
from app.schemas.profile import ExperienceLevel


class JobSummary(BaseModel):
    """A corpus job as shown in the feed, the tracker and analyses."""

    id: uuid.UUID
    company: str
    title: str
    location: str | None
    is_remote: bool
    url: str
    level: ExperienceLevel | None
    min_years: int | None
    posted_at: datetime | None
    status: Literal["open", "closed"]

    @classmethod
    def from_job(cls, job: Job, company: str) -> "JobSummary":
        return cls(id=job.id, company=company, title=job.title, location=job.location,
                   is_remote=job.is_remote, url=job.url, level=job.level,
                   min_years=job.min_years, posted_at=job.posted_at, status=job.status)


class JobSkillOut(BaseModel):
    skill: str
    category: str
    weight: float


class JobRequirementOut(BaseModel):
    section: str | None
    text: str


class JobOut(JobSummary):
    description_text: str
    skills: list[JobSkillOut]
    requirements: list[JobRequirementOut]
