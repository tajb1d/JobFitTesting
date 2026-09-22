"""Full analyses: one resume against one job description (plan §6). Every query filters by
user_id."""

import logging
import math
import uuid
from datetime import timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.config import get_settings
from app.models import Analysis, Resume
from app.services import resumes
from app.services.embeddings import EmbeddingProvider
from app.services.feedback import FeedbackGenerator, build_feedback
from app.services.jd_parser import ParsedJob, parse_job
from app.services.matcher import MatchResult, ResumeForScoring, score

logger = logging.getLogger(__name__)

RATE_WINDOW = timedelta(hours=1)


class ResumeNotFound(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after_seconds: int):
        super().__init__(f"retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


def check_rate_limit(db: Session, user_id: uuid.UUID, limit: int) -> None:
    """At most `limit` analyses per user per rolling hour. Counted from stored analyses, so
    it survives restarts and needs no extra infrastructure."""
    since = func.now() - RATE_WINDOW
    count, oldest = db.execute(
        select(func.count(), func.min(Analysis.created_at)).where(
            Analysis.user_id == user_id, Analysis.created_at > since
        )
    ).one()
    if count >= limit:
        now = db.execute(select(func.now())).scalar_one()
        retry = max(math.ceil((oldest + RATE_WINDOW - now).total_seconds()), 1)
        raise RateLimited(retry)


def _resume_for_scoring(resume: Resume, bullets: list) -> ResumeForScoring:
    vectors = [b.embedding for b in bullets if b.embedding is not None]
    return ResumeForScoring(
        text=resume.text,
        skills={s["canonical"] for s in resume.skills or []},
        bullet_vectors=np.array(vectors) if vectors else np.empty((0, 0)),
        resume_vector=None if resume.embedding is None else np.asarray(resume.embedding),
        structure_score=float(resume.structure_score or 0.0),
    )


def feedback_context(job: ParsedJob, result: MatchResult, resume: Resume) -> dict:
    """What the LLM sees: the structured result only, never resume text (plan §6)."""
    return {
        "job_title": job.title,
        "subscores": {
            "skill_coverage": result.skill_score,
            "semantic_match": result.semantic_score,
            "resume_structure": result.structure_score,
        },
        "matched_skills": result.matched_skills,
        "missing_skills": result.missing_skills,
        "weak_requirements": result.weak_requirements,
        "failed_structure_checks": [
            {"label": c["label"], "detail": c["detail"]}
            for c in resume.structure_checks or []
            if not c["passed"]
        ],
    }


def create_analysis(
    db: Session,
    user_id: uuid.UUID,
    resume_id: uuid.UUID,
    job_description: str,
    job_title: str | None,
    embedder: EmbeddingProvider,
    feedback: FeedbackGenerator,
) -> Analysis:
    """Score a pasted job description against one of the user's resumes, generate feedback,
    and store the analysis. Raises ResumeNotFound, RateLimited, or EmbeddingError."""
    settings = get_settings()
    resume = resumes.get_resume(db, user_id, resume_id)
    if resume is None:
        raise ResumeNotFound()
    check_rate_limit(db, user_id, settings.analyses_per_hour)
    bullets = resumes.get_bullets(db, resume)
    db.commit()  # end the read transaction; no connection held during Voyage/Anthropic calls

    job = parse_job(job_description, job_title)
    # Requirements are embedded as queries against resume bullets (stored as documents).
    # Ingested corpus jobs must use the same input_type so scores are comparable.
    requirement_vectors = np.array(
        embedder.embed([r.text for r in job.requirements], "query")
    )
    result = score(job, requirement_vectors, _resume_for_scoring(resume, bullets), settings)
    items, source = build_feedback(feedback_context(job, result, resume), feedback)
    logger.info("analysis feedback source=%s items=%d", source, len(items))

    analysis = Analysis(
        user_id=user_id,
        resume_id=resume.id,
        job_id=None,
        job_title=job.title,
        job_description_text=job.text,
        final_score=result.final_score,
        skill_score=result.skill_score,
        semantic_score=result.semantic_score,
        structure_score=result.structure_score,
        tfidf_score=result.tfidf_score,
        matched_skills=result.matched_skills,
        missing_skills=result.missing_skills,
        weak_requirements=result.weak_requirements,
        feedback_items=items,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def list_analyses(db: Session, user_id: uuid.UUID) -> list[Analysis]:
    stmt = (
        select(Analysis)
        .options(
            load_only(
                Analysis.id,
                Analysis.resume_id,
                Analysis.job_id,
                Analysis.job_title,
                Analysis.final_score,
                Analysis.created_at,
            )
        )
        .where(Analysis.user_id == user_id)
        .order_by(Analysis.created_at.desc())
    )
    return list(db.scalars(stmt))


def get_analysis(db: Session, user_id: uuid.UUID, analysis_id: uuid.UUID) -> Analysis | None:
    stmt = select(Analysis).where(Analysis.id == analysis_id, Analysis.user_id == user_id)
    return db.scalars(stmt).one_or_none()


def delete_analysis(db: Session, user_id: uuid.UUID, analysis_id: uuid.UUID) -> bool:
    analysis = get_analysis(db, user_id, analysis_id)
    if analysis is None:
        return False
    db.delete(analysis)
    db.commit()
    return True
