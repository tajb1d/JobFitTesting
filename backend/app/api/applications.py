import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import Application, Job
from app.schemas.application import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationStatus,
    ApplicationUpdate,
)
from app.schemas.job import JobSummary
from app.services import applications

router = APIRouter(prefix="/applications", tags=["applications"])


def _out(row: tuple[Application, Job, str]) -> ApplicationOut:
    application, job, company = row
    return ApplicationOut(id=application.id, job=JobSummary.from_job(job, company),
                          status=application.status, notes=application.notes,
                          created_at=application.created_at, updated_at=application.updated_at)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    status_filter: ApplicationStatus | None = Query(None, alias="status"),
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApplicationOut]:
    return [_out(row) for row in applications.list_applications(db, user_id, status_filter)]


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    body: ApplicationCreate,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    try:
        return _out(applications.create_application(db, user_id, body))
    except applications.JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    except applications.AlreadySaved:
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already saved this job.")


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: uuid.UUID,
    body: ApplicationUpdate,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    row = applications.update_application(db, user_id, application_id, body)
    if row is None:
        raise _not_found()
    return _out(row)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not applications.delete_application(db, user_id, application_id):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
