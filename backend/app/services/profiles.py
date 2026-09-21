import uuid

from sqlalchemy.orm import Session

from app.models import Profile
from app.schemas.profile import ProfileUpdate


def get_profile(db: Session, user_id: uuid.UUID) -> Profile | None:
    return db.get(Profile, user_id)


def upsert_profile(db: Session, user_id: uuid.UUID, data: ProfileUpdate) -> Profile:
    """Create or fully replace the user's profile. `user_id` must come from auth, never the body."""
    profile = db.get(Profile, user_id)
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)

    profile.target_roles = data.target_roles
    profile.location = data.location
    profile.remote_ok = data.remote_ok
    profile.experience_level = data.experience_level

    db.commit()
    db.refresh(profile)
    return profile
