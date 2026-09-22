from fastapi import APIRouter

from app.api import analyses, profile, resumes

# Everything here is mounted under /api/v1 and requires auth.
api_router = APIRouter()
api_router.include_router(profile.router)
api_router.include_router(resumes.router)
api_router.include_router(analyses.router)
