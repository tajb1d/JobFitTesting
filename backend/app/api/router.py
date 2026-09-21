from fastapi import APIRouter

from app.api import profile

# Everything here is mounted under /api/v1 and requires auth.
api_router = APIRouter()
api_router.include_router(profile.router)
