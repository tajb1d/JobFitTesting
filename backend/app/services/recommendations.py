"""Job feed (plan §9): pgvector retrieval, then an in-memory rerank with the same scorer as
analyses, then the §8 eligibility adjustment. No external API calls: jobs were parsed and
embedded at ingestion, and the resume at upload."""

import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session, defer

from app.config import Settings
from app.models import Company, Job, Profile, Resume
from app.services import jobs, resumes
from app.services.analyses import ResumeNotFound, resume_for_scoring
from app.services.eligibility import eligibility_adjustment
from app.services.matcher import MatchResult, score

TOP_SKILLS = 3


@dataclass
class Recommendation:
    job: Job
    company: str
    score: float
    result: MatchResult
    levels_above: int | None
    stretch: bool

    @property
    def matched_skills(self) -> list[dict]:
        return self.result.matched_skills[:TOP_SKILLS]  # job skills are stored by weight

    @property
    def missing_skills(self) -> list[dict]:
        return self.result.missing_skills[:TOP_SKILLS]


def _resolve_resume(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID | None) -> Resume:
    if resume_id is not None:
        resume = resumes.get_resume(db, user_id, resume_id)
    else:
        resume = db.scalars(select(Resume).where(Resume.user_id == user_id, Resume.is_active)
                            .order_by(Resume.created_at.desc()).limit(1)).first()
    if resume is None:
        raise ResumeNotFound()
    return resume


def retrieve(
    db: Session,
    resume: Resume,
    limit: int,
    *,
    remote: bool | None = None,
    location: str | None = None,
    level: str | None = None,
) -> list[tuple[Job, str]]:
    """Nearest open jobs to the resume embedding (cosine), with filters in SQL."""
    # With filters, a plain HNSW scan can return fewer than `limit` rows; iterative scan
    # (pgvector 0.8) keeps searching. Relaxed order is fine: every candidate is reranked.
    # One round trip for both settings; is_local=true scopes them to this transaction.
    db.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                    "set_config('hnsw.ef_search', '200', true)"))
    query = (
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.status == "open", Job.embedding.is_not(None))
        .order_by(Job.embedding.cosine_distance(resume.embedding))
        .limit(limit)
        .options(defer(Job.embedding))  # only needed for the ordering, inside Postgres
    )
    if remote is not None:
        query = query.where(Job.is_remote.is_(remote))
    if location:
        escaped = location.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Job.location.ilike(f"%{escaped}%", escape="\\"))
    if level:
        query = query.where(Job.level == level)
    return [(job, company) for job, company in db.execute(query).all()]


def recommend(
    db: Session,
    user_id: uuid.UUID,
    settings: Settings,
    *,
    resume_id: uuid.UUID | None = None,
    remote: bool | None = None,
    location: str | None = None,
    level: str | None = None,
    show_stretch: bool = False,
    limit: int = 20,
) -> tuple[Resume, list[Recommendation]]:
    resume = _resolve_resume(db, user_id, resume_id)
    if resume.embedding is None:
        return resume, []
    profile = db.get(Profile, user_id)
    user_level = profile.experience_level if profile else None

    candidates = retrieve(db, resume, settings.recommendation_candidates,
                          remote=remote, location=location, level=level)
    requirements = jobs.requirement_best_matches(db, [job.id for job, _ in candidates], resume)
    # Similarities come precomputed from Postgres, so no vectors are needed here.
    scoring = resume_for_scoring(resume, [])

    ranked: list[Recommendation] = []
    for job, company in candidates:
        eligibility = eligibility_adjustment(
            job.level, job.min_years, user_level,
            show_stretch=show_stretch,
            penalty=settings.eligibility_level_penalty,
            hide_levels_above=settings.eligibility_hide_levels_above,
            hide_years_margin=settings.eligibility_hide_years_margin,
        )
        if eligibility.hidden:
            continue
        reqs = requirements[job.id]
        result = score(jobs.stored_job_to_parsed(job, reqs), None, scoring, settings,
                       with_tfidf=False, best=np.array([r.best for r in reqs]))
        ranked.append(Recommendation(
            job=job, company=company,
            score=round(result.final_score * eligibility.multiplier, 1),
            result=result, levels_above=eligibility.levels_above, stretch=eligibility.stretch,
        ))
    ranked.sort(key=lambda r: -r.score)
    return resume, ranked[:limit]
