import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.schemas.job import JobSummary
from app.schemas.profile import ExperienceLevel
from app.schemas.recommendation import RecommendationItem, RecommendationsOut, Subscores
from app.services import recommendations
from app.services.analyses import ResumeNotFound

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationsOut)
def get_recommendations(
    resume_id: uuid.UUID | None = Query(None, description="Defaults to the active resume."),
    remote: bool | None = Query(None, description="true = remote only, false = on-site only."),
    location: str | None = Query(None, max_length=200, description="Substring of the job location."),
    level: ExperienceLevel | None = None,
    show_stretch: bool = False,
    limit: int = Query(20, ge=1, le=50),
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecommendationsOut:
    try:
        resume, items = recommendations.recommend(
            db, user_id, get_settings(), resume_id=resume_id, remote=remote,
            location=(location or "").strip() or None, level=level,
            show_stretch=show_stretch, limit=limit,
        )
    except ResumeNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found. Upload a resume first.")
    return RecommendationsOut(
        resume_id=resume.id,
        items=[
            RecommendationItem(
                job=JobSummary.from_job(r.job, r.company),
                score=r.score,
                subscores=Subscores(skill=r.result.skill_score, semantic=r.result.semantic_score,
                                    structure=r.result.structure_score),
                levels_above=r.levels_above,
                stretch=r.stretch,
                matched_skills=r.matched_skills,
                missing_skills=r.missing_skills,
            )
            for r in items
        ],
    )
