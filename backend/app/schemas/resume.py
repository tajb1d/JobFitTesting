import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models import Resume, ResumeBullet


class SkillOut(BaseModel):
    canonical: str
    category: str
    count: int
    in_skills_section: bool


class CheckOut(BaseModel):
    id: str
    label: str
    passed: bool
    points: int
    max_points: int
    detail: str


class FeedbackItemOut(BaseModel):
    check: str
    severity: Literal["high", "medium", "low"]
    message: str


class SectionOut(BaseModel):
    key: str
    heading: str


class BulletOut(BaseModel):
    section: str | None
    text: str


class ResumeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    is_active: bool
    structure_score: float | None
    created_at: datetime


class ResumeOut(ResumeSummary):
    """Full processed resume: the general review shown after upload."""

    structure_checks: list[CheckOut]
    general_feedback: list[FeedbackItemOut]
    skills: list[SkillOut]
    suggested_roles: list[str]
    sections: list[SectionOut]
    bullets: list[BulletOut]

    @classmethod
    def from_model(cls, resume: Resume, bullets: list[ResumeBullet]) -> "ResumeOut":
        return cls(
            id=resume.id,
            filename=resume.filename,
            is_active=resume.is_active,
            structure_score=resume.structure_score,
            created_at=resume.created_at,
            structure_checks=resume.structure_checks or [],
            general_feedback=resume.general_feedback or [],
            skills=resume.skills or [],
            suggested_roles=resume.suggested_roles,
            sections=[
                SectionOut(key=s["key"], heading=s["heading"])
                for s in resume.sections or []
                if s["heading"] is not None  # skip the name/contact header block
            ],
            bullets=[BulletOut(section=b.section, text=b.text) for b in bullets],
        )
