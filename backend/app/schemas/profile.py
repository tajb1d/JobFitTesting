import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Plan §8 eligibility levels.
ExperienceLevel = Literal["intern", "entry", "mid", "senior", "staff"]

RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProfileUpdate(BaseModel):
    """Body for PUT /profile. A full replace: omitted optional fields are cleared."""

    target_roles: list[RoleName] = Field(default_factory=list, max_length=10)
    location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    remote_ok: bool = False
    experience_level: ExperienceLevel | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    target_roles: list[str]
    location: str | None
    remote_ok: bool
    experience_level: ExperienceLevel | None
    updated_at: datetime


class MeResponse(BaseModel):
    user_id: uuid.UUID
    # None until the user completes onboarding.
    profile: ProfileOut | None
