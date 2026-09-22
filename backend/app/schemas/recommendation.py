import uuid

from pydantic import BaseModel

from app.schemas.analysis import SkillWeight
from app.schemas.job import JobSummary


class Subscores(BaseModel):
    skill: float | None
    semantic: float | None
    structure: float


class RecommendationItem(BaseModel):
    job: JobSummary
    score: float
    subscores: Subscores
    # Job level minus the user's (plan §8); None when either is unknown.
    levels_above: int | None
    # Above the user's level or experience: penalized, and only shown with show_stretch
    # when it's far above.
    stretch: bool
    matched_skills: list[SkillWeight]
    missing_skills: list[SkillWeight]


class RecommendationsOut(BaseModel):
    resume_id: uuid.UUID
    items: list[RecommendationItem]
