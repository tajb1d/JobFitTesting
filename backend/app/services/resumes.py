"""Resume upload pipeline and storage (plan §5). Every query filters by user_id."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, load_only

from app.config import get_settings
from app.models import Profile, Resume, ResumeBullet
from app.services.embeddings import EmbeddingProvider
from app.services.nlp_analyzer import SkillMatch, extract_skills
from app.services.pdf_analyzer import analyze_pdf
from app.services.roles import suggest_roles
from app.services.structure_analyzer import StructureResult, analyze_structure

MAX_PDF_BYTES = 5 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"


class UploadError(Exception):
    """Rejected upload. The message is shown to the user."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ResumeAnalysis:
    text: str
    page_count: int
    structure: StructureResult
    skills: list[SkillMatch]
    suggested_roles: list[str]


def validate_upload(data: bytes, content_type: str | None) -> None:
    if len(data) > MAX_PDF_BYTES:
        raise UploadError(413, "The PDF must be 5 MB or smaller.")
    if (content_type or "").split(";")[0].strip().lower() != PDF_CONTENT_TYPE:
        raise UploadError(415, "Only PDF files are accepted.")
    if not data:
        raise UploadError(422, "The file is empty.")


def analyze_resume(data: bytes) -> ResumeAnalysis:
    """The pure part of the pipeline: PDF bytes in, analysis out. No DB, no network.
    Raises pdf_analyzer.PdfError for unreadable files."""
    doc = analyze_pdf(data)
    structure = analyze_structure(doc)
    skills = extract_skills((b.key, b.text) for b in structure.blocks)
    return ResumeAnalysis(
        text=doc.text,
        page_count=doc.page_count,
        structure=structure,
        skills=skills,
        suggested_roles=suggest_roles(skills, structure.job_titles),
    )


def resume_embedding_text(text: str, target_roles: list[str]) -> str:
    """Plan §5: the resume embedding is prefixed with the user's target roles, so retrieval
    leans toward the jobs they want. No prefix until they've set roles."""
    if not target_roles:
        return text
    return f"Target roles: {', '.join(target_roles)}\n\n{text}"


def clean_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(c for c in base if c.isprintable()).strip()
    return base[:200] or "resume.pdf"


def create_resume(
    db: Session,
    user_id: uuid.UUID,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    embedder: EmbeddingProvider,
) -> tuple[Resume, list[ResumeBullet]]:
    """Analyze, embed, and store a resume as the user's active one.
    Nothing is stored if analysis or embedding fails."""
    validate_upload(data, content_type)
    limit = get_settings().max_resumes_per_user
    stored = db.scalar(select(func.count()).select_from(Resume).where(Resume.user_id == user_id))
    if stored >= limit:
        raise UploadError(
            409,
            f"You've reached the limit of {limit} resumes. Delete one in Settings to upload another.",
        )
    analysis = analyze_resume(data)
    bullets = analysis.structure.bullets

    profile = db.get(Profile, user_id)
    target_roles = list(profile.target_roles) if profile else []
    db.commit()  # end the read transaction so no connection is held during the Voyage calls

    resume_vector = embedder.embed(
        [resume_embedding_text(analysis.text, target_roles)], "query"
    )[0]
    bullet_vectors = embedder.embed([b.text for b in bullets], "document")

    db.execute(
        update(Resume)
        .where(Resume.user_id == user_id, Resume.is_active.is_(True))
        .values(is_active=False)
    )
    resume = Resume(
        user_id=user_id,
        filename=clean_filename(filename),
        text=analysis.text,
        sections=analysis.structure.sections,
        skills=[s.as_dict() for s in analysis.skills],
        structure_score=analysis.structure.score,
        structure_checks=[c.as_dict() for c in analysis.structure.checks],
        general_feedback=analysis.structure.feedback,
        suggested_roles=analysis.suggested_roles,
        embedding=resume_vector,
        is_active=True,
    )
    db.add(resume)
    db.flush()  # assigns resume.id
    rows = [
        ResumeBullet(resume_id=resume.id, section=b.section, text=b.text, embedding=v)
        for b, v in zip(bullets, bullet_vectors, strict=True)
    ]
    db.add_all(rows)
    db.commit()
    db.refresh(resume)
    return resume, rows


def list_resumes(db: Session, user_id: uuid.UUID) -> list[Resume]:
    stmt = (
        select(Resume)
        .options(
            load_only(
                Resume.id,
                Resume.filename,
                Resume.is_active,
                Resume.structure_score,
                Resume.created_at,
            )
        )
        .where(Resume.user_id == user_id)
        .order_by(Resume.created_at.desc())
    )
    return list(db.scalars(stmt))


def get_resume(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume | None:
    stmt = select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    return db.scalars(stmt).one_or_none()


def get_bullets(db: Session, resume: Resume) -> list[ResumeBullet]:
    stmt = select(ResumeBullet).where(ResumeBullet.resume_id == resume.id).order_by(ResumeBullet.id)
    return list(db.scalars(stmt))


def set_active(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume | None:
    """Make one of the user's resumes the active one (the one the feed and new analyses use).
    Clears the flag on the others, as create_resume does on upload. None if not found."""
    resume = get_resume(db, user_id, resume_id)
    if resume is None:
        return None
    db.execute(
        update(Resume)
        .where(Resume.user_id == user_id, Resume.id != resume_id, Resume.is_active.is_(True))
        .values(is_active=False)
    )
    resume.is_active = True
    db.commit()
    db.refresh(resume)
    return resume


def delete_resume(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> bool:
    """Delete one of the user's resumes (bullets and analyses cascade in the DB). If it was
    active, the newest remaining resume becomes active. False if not found."""
    resume = get_resume(db, user_id, resume_id)
    if resume is None:
        return False
    was_active = resume.is_active
    db.delete(resume)
    db.flush()
    if was_active:
        newest = db.scalars(
            select(Resume)
            .where(Resume.user_id == user_id)
            .order_by(Resume.created_at.desc())
            .limit(1)
        ).first()
        if newest is not None:
            newest.is_active = True
    db.commit()
    return True
