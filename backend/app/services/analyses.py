"""Full analyses: one resume against one job (plan §6), given as pasted text, a corpus job id,
or a Greenhouse/Lever link. Every query filters by user_id."""

import logging
import math
import uuid
from datetime import timedelta

import httpx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.config import get_settings
from app.models import Analysis, Resume
from app.services import jobs, resumes
from app.services.embeddings import EmbeddingProvider
from app.services.job_urls import fetch_posting, parse_posting_url
from app.services.feedback import FeedbackGenerator, build_feedback
from app.services.jd_parser import ParsedJob, parse_job
from app.services.matcher import MatchResult, ResumeForScoring, score

logger = logging.getLogger(__name__)

RATE_WINDOW = timedelta(hours=1)


class ResumeNotFound(Exception):
    pass


class JobNotFound(Exception):
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


def resume_for_scoring(resume: Resume, bullets: list) -> ResumeForScoring:
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


def _embed_requirements(job: ParsedJob, embedder: EmbeddingProvider) -> np.ndarray:
    # Requirements are embedded as queries against resume bullets (stored as documents), the
    # same input_type ingestion uses, so pasted and corpus jobs score on one scale.
    return np.array(embedder.embed([r.text for r in job.requirements], "query"))


def create_analysis(
    db: Session,
    user_id: uuid.UUID,
    resume_id: uuid.UUID,
    embedder: EmbeddingProvider,
    feedback: FeedbackGenerator,
    *,
    job_description: str | None = None,
    job_title: str | None = None,
    job_id: uuid.UUID | None = None,
    job_url: str | None = None,
    posting_client: httpx.Client | None = None,
) -> Analysis:
    """Score one job against one of the user's resumes, generate feedback, and store the
    analysis. The job is exactly one of: pasted text, a corpus job id (stored parse and
    embeddings, no Voyage call), or a Greenhouse/Lever link (the stored job if ingested,
    otherwise fetched from the ATS and scored like pasted text).

    Raises UnsupportedJobUrl, ResumeNotFound, RateLimited, JobNotFound, PostingNotFound,
    PostingUnavailable, or EmbeddingError."""
    settings = get_settings()
    ref = parse_posting_url(job_url) if job_url else None  # reject bad links before any work
    resume = resumes.get_resume(db, user_id, resume_id)
    if resume is None:
        raise ResumeNotFound()
    check_rate_limit(db, user_id, settings.analyses_per_hour)
    bullets = resumes.get_bullets(db, resume)

    stored = None
    if job_id is not None:
        stored = jobs.get_job(db, job_id)
        if stored is None:
            raise JobNotFound()
    elif ref is not None:
        stored = jobs.find_job(db, ref.ats, ref.token, ref.external_id)
    stored_job = stored[0] if stored else None
    if stored_job is not None:
        reqs = jobs.get_requirements(db, [stored_job.id])[stored_job.id]
        job = jobs.stored_job_to_parsed(stored_job, reqs)
        requirement_vectors = jobs.requirement_vectors(reqs)
    db.commit()  # end the read transaction; no connection held during ATS/Voyage/LLM calls

    if stored_job is None:
        if ref is not None:
            posting = fetch_posting(posting_client, ref)
            job = parse_job(posting.description_html, posting.title)
        else:
            job = parse_job(job_description, job_title)
        requirement_vectors = _embed_requirements(job, embedder)

    result = score(job, requirement_vectors, resume_for_scoring(resume, bullets), settings)
    items, source = build_feedback(feedback_context(job, result, resume), feedback)
    logger.info("analysis feedback source=%s items=%d", source, len(items))

    analysis = Analysis(
        user_id=user_id,
        resume_id=resume.id,
        job_id=stored_job.id if stored_job is not None else None,
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
