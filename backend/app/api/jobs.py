import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.job import JobOut, JobRequirementOut, JobSkillOut, JobSummary
from app.services import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    _user_id: uuid.UUID = Depends(get_current_user),  # corpus data, but still auth-only
    db: Session = Depends(get_db),
) -> JobOut:
    found = jobs.get_job(db, job_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    job, company = found
    return JobOut(
        **JobSummary.from_job(job, company).model_dump(),
        description_text=job.description_text,
        skills=[JobSkillOut(skill=s["skill"], category=s["category"], weight=s["weight"])
                for s in job.skills or []],
        requirements=[JobRequirementOut(section=r.section, text=r.text)
                      for r in jobs.get_requirements(db, [job.id])[job.id]],
    )
