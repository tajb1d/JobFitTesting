from fastapi import APIRouter

from app.api import account, analyses, applications, jobs, profile, recommendations, resumes

# Everything here is mounted under /api/v1 and requires auth.
api_router = APIRouter()
api_router.include_router(profile.router)
api_router.include_router(resumes.router)
api_router.include_router(analyses.router)
api_router.include_router(recommendations.router)
api_router.include_router(jobs.router)
api_router.include_router(applications.router)
api_router.include_router(account.router)
