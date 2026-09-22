import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profile, Resume
from app.schemas.profile import ProfileUpdate
from app.services.embeddings import EmbeddingError, EmbeddingProvider
from app.services.resumes import resume_embedding_text

logger = logging.getLogger(__name__)


def get_profile(db: Session, user_id: uuid.UUID) -> Profile | None:
    return db.get(Profile, user_id)


def upsert_profile(
    db: Session, user_id: uuid.UUID, data: ProfileUpdate, embedder: EmbeddingProvider | None = None
) -> Profile:
    """Create or fully replace the user's profile. `user_id` must come from auth, never the body.

    The resume embedding is prefixed with the target roles (plan §5) and drives retrieval, so
    when the roles change the active resume is re-embedded. That's best effort: the profile
    is saved even if Voyage is down, and the old vector keeps working."""
    profile = db.get(Profile, user_id)
    old_roles = list(profile.target_roles) if profile else []
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)

    profile.target_roles = data.target_roles
    profile.location = data.location
    profile.remote_ok = data.remote_ok
    profile.experience_level = data.experience_level

    db.commit()
    db.refresh(profile)
    if embedder is not None and data.target_roles != old_roles:
        _reembed_active_resume(db, user_id, data.target_roles, embedder)
    return profile


def _reembed_active_resume(
    db: Session, user_id: uuid.UUID, roles: list[str], embedder: EmbeddingProvider
) -> None:
    resume = db.scalars(select(Resume).where(Resume.user_id == user_id, Resume.is_active)
                        .order_by(Resume.created_at.desc()).limit(1)).first()
    if resume is None:
        return
    text = resume.text
    db.commit()  # no connection held during the Voyage call
    try:
        vector = embedder.embed([resume_embedding_text(text, roles)], "query")[0]
    except EmbeddingError:
        logger.warning("resume re-embed after role change failed user_id=%s", user_id, exc_info=True)
        return
    resume.embedding = vector
    db.commit()
