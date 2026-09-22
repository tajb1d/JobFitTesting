from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.router import api_router
from app.config import get_settings
from app.services.nlp_analyzer import get_skill_matcher


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Load spaCy and build the skill matcher once, before the first request.
    get_skill_matcher()
    yield


app = FastAPI(title="JobFit API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    # Auth is a bearer header, not cookies.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# /health stays at the root (Render's health check); the API is versioned.
app.include_router(health.router)
app.include_router(api_router, prefix="/api/v1")
