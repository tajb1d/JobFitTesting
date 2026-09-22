"""The user's job tracker (plan §11). Every query filters by user_id from auth."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, Company, Job
from app.schemas.application import ApplicationCreate, ApplicationUpdate


class JobNotFound(Exception):
    pass


class AlreadySaved(Exception):
    pass


def _with_jobs(db: Session, user_id: uuid.UUID):
    return (
        select(Application, Job, Company.name)
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        .where(Application.user_id == user_id)
    )


def list_applications(
    db: Session, user_id: uuid.UUID, status: str | None = None
) -> list[tuple[Application, Job, str]]:
    query = _with_jobs(db, user_id).order_by(Application.updated_at.desc())
    if status:
        query = query.where(Application.status == status)
    return [tuple(row) for row in db.execute(query).all()]


def get_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> tuple[Application, Job, str] | None:
    row = db.execute(_with_jobs(db, user_id).where(Application.id == application_id)).first()
    return tuple(row) if row else None


def create_application(
    db: Session, user_id: uuid.UUID, data: ApplicationCreate
) -> tuple[Application, Job, str]:
    if db.get(Job, data.job_id) is None:
        raise JobNotFound()
    application = Application(user_id=user_id, job_id=data.job_id, status=data.status,
                              notes=data.notes)
    db.add(application)
    try:
        db.commit()
    except IntegrityError:  # UNIQUE(user_id, job_id)
        db.rollback()
        raise AlreadySaved()
    return get_application(db, user_id, application.id)


def update_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, data: ApplicationUpdate
) -> tuple[Application, Job, str] | None:
    found = get_application(db, user_id, application_id)
    if found is None:
        return None
    application = found[0]
    for field in data.model_fields_set:  # partial update: only what was sent
        setattr(application, field, getattr(data, field))
    db.commit()
    db.refresh(application)
    return get_application(db, user_id, application_id)


def delete_application(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> bool:
    application = db.scalars(select(Application).where(
        Application.id == application_id, Application.user_id == user_id)).first()
    if application is None:
        return False
    db.delete(application)
    db.commit()
    return True
