"""Corpus jobs as stored by ingestion. Jobs are shared data (not per user), so these lookups
take no user_id; callers that attach user data (applications, analyses) filter on it."""

import uuid
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, Job, JobRequirement, Resume, ResumeBullet
from app.services.jd_parser import JobSkill, ParsedJob, Requirement


def get_job(db: Session, job_id: uuid.UUID) -> tuple[Job, str] | None:
    """The job and its company name."""
    row = db.execute(
        select(Job, Company.name).join(Company, Company.id == Job.company_id).where(Job.id == job_id)
    ).first()
    return (row[0], row[1]) if row else None


def find_job(db: Session, ats: str, board_token: str, external_id: str) -> tuple[Job, str] | None:
    """A corpus job by its ATS posting id (for pasted Greenhouse/Lever links)."""
    row = db.execute(
        select(Job, Company.name).join(Company, Company.id == Job.company_id)
        .where(Company.ats == ats, Company.board_token == board_token.lower(),
               Job.external_id == external_id)
    ).first()
    return (row[0], row[1]) if row else None


def get_requirements(db: Session, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[JobRequirement]]:
    """Requirement rows (with embeddings) per job, in stored order."""
    found: dict[uuid.UUID, list[JobRequirement]] = {job_id: [] for job_id in job_ids}
    if job_ids:
        for r in db.scalars(select(JobRequirement).where(JobRequirement.job_id.in_(job_ids))
                            .order_by(JobRequirement.id)):
            found[r.job_id].append(r)
    return found


def requirement_best_matches(
    db: Session, job_ids: list[uuid.UUID], resume: Resume
) -> dict[uuid.UUID, list[Any]]:
    """Each requirement's best cosine against the resume, computed in Postgres: rows of
    (job_id, section, text, best), in stored order. The feed uses this instead of loading every
    requirement vector (about 10 MB for 100 jobs) to do the same maths in numpy. Matches
    against the resume bullets, or the whole-resume vector when it has none, as
    matcher.best_matches does."""
    found: dict[uuid.UUID, list[Any]] = {job_id: [] for job_id in job_ids}
    if not job_ids:
        return found
    rows = db.execute(
        select(JobRequirement.job_id, JobRequirement.section, JobRequirement.text,
               func.max(1 - JobRequirement.embedding.cosine_distance(ResumeBullet.embedding))
               .label("best"))
        .join(ResumeBullet, ResumeBullet.resume_id == resume.id)
        .where(JobRequirement.job_id.in_(job_ids), ResumeBullet.embedding.is_not(None))
        .group_by(JobRequirement.id)
        .order_by(JobRequirement.id)
    ).all()
    if not rows and resume.embedding is not None:  # no bullets: fall back to the resume vector
        rows = db.execute(
            select(JobRequirement.job_id, JobRequirement.section, JobRequirement.text,
                   (1 - JobRequirement.embedding.cosine_distance(resume.embedding)).label("best"))
            .where(JobRequirement.job_id.in_(job_ids))
            .order_by(JobRequirement.id)
        ).all()
    for row in rows:
        found[row.job_id].append(row)
    return found


def stored_job_to_parsed(job: Job, requirements: list[Any]) -> ParsedJob:
    """Rebuild the parser's output from what ingestion stored, so corpus jobs score through
    the same matcher as pasted descriptions without re-parsing or re-embedding.
    `requirements` are JobRequirement rows or anything with .section and .text."""
    skills = [JobSkill(s["skill"], s["category"], s["weight"], s["count"], set(s.get("sections", [])))
              for s in job.skills or []]
    return ParsedJob(
        title=job.title,
        text=job.description_text,
        sections=[],
        requirements=[Requirement(r.section or "required", r.text) for r in requirements],
        skills=skills,
        level=job.level,
        min_years=job.min_years,
    )


def requirement_vectors(requirements: list[JobRequirement]) -> np.ndarray:
    return np.array([r.embedding for r in requirements]) if requirements else np.empty((0, 0))
