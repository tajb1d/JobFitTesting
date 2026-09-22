import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

JobDescription = Annotated[str, StringConstraints(strip_whitespace=True, min_length=100, max_length=20_000)]


class AnalysisCreate(BaseModel):
    """Exactly one of job_description, job_id, job_url (plan §11)."""

    resume_id: uuid.UUID
    job_description: JobDescription | None = None
    job_id: uuid.UUID | None = None
    job_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    job_title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = Field(
        default=None, description="Optional; inferred from the description's first line if omitted."
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "AnalysisCreate":
        given = [f for f in ("job_description", "job_id", "job_url") if getattr(self, f)]
        if len(given) != 1:
            raise ValueError("Provide exactly one of job_description, job_id, or job_url.")
        return self


class SkillWeight(BaseModel):
    skill: str
    weight: float


class WeakRequirement(BaseModel):
    text: str
    section: str
    similarity: float


class FeedbackItemOut(BaseModel):
    type: Literal["missing_skill", "weak_requirement", "structure", "strength"]
    severity: Literal["high", "medium", "low"]
    message: str


class AnalysisSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_id: uuid.UUID | None
    job_title: str | None
    final_score: float | None
    created_at: datetime


class AnalysisOut(AnalysisSummary):
    skill_score: float | None
    semantic_score: float | None
    structure_score: float | None
    tfidf_score: float | None
    matched_skills: list[SkillWeight]
    missing_skills: list[SkillWeight]
    weak_requirements: list[WeakRequirement]
    feedback_items: list[FeedbackItemOut]
