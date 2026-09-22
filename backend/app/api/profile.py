import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.profile import MeResponse, ProfileOut, ProfileUpdate
from app.services import profiles
from app.services.embeddings import EmbeddingProvider, get_embedder

router = APIRouter(tags=["profile"])


@router.get("/me", response_model=MeResponse)
def read_me(
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    profile = profiles.get_profile(db, user_id)
    return MeResponse(
        user_id=user_id,
        profile=ProfileOut.model_validate(profile) if profile else None,
    )


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileUpdate,
    user_id: uuid.UUID = Depends(get_current_user),
    db: Session = Depends(get_db),
    embedder: EmbeddingProvider = Depends(get_embedder),
) -> ProfileOut:
    return ProfileOut.model_validate(profiles.upsert_profile(db, user_id, body, embedder))
