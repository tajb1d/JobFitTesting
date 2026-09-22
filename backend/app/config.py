from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Vector size for every pgvector column (voyage-4-lite at output_dimension=512).
# Deliberately a constant, not an env var: the DB schema can't follow an env override.
# Changing it requires a new Alembic migration.
EMBEDDING_DIM: Final = 512

JWT_AUDIENCE: Final = "authenticated"
JWT_ALGORITHMS: Final = ["ES256", "RS256"]


class Settings(BaseSettings):
    """Reads backend/.env; real environment variables (Render, CI) take precedence.

    Everything has a default so the app can be imported without a .env (CI, /health tests).
    Code that needs a value fails at use, not at import.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    supabase_url: str = ""
    supabase_secret_key: str = ""
    voyage_api_key: str = ""
    anthropic_api_key: str = ""

    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"

    # Dev/test only: the Supabase test user (scripts/get_token.py, tests).
    test_user_email: str = ""
    test_user_password: str = ""

    # Scoring weights (plan §6). Must sum to 1. A null subscore's weight is redistributed.
    weight_skill: float = 0.50
    weight_semantic: float = 0.35
    weight_structure: float = 0.15

    # S_sem calibration (plan §6): mean raw S_sem of resumes against unrelated jobs, from
    # scripts/calibrate.py on 2026-09-21 (3 fixture resumes x 50 unrelated corpus jobs,
    # voyage-4-lite; stdev 0.037). S_sem_raw does rank related above unrelated jobs (AUC
    # ~0.85 on title-labelled samples), but raw scores sit in a narrow band (~0.25-0.50), so
    # the rescaled S_sem stays small and moves the final score by a few points at most.
    sem_baseline: float = 0.33
    # A requirement is "weakly covered" when its best resume-bullet match is within this
    # margin of the unrelated-pair baseline.
    weak_requirement_margin: float = 0.05

    # Per-user limit on POST /analyses (each one calls the LLM).
    analyses_per_hour: int = 20

    # Recommendations (plan §9): pgvector candidates to rerank, and §8 eligibility rules.
    recommendation_candidates: int = 100
    eligibility_level_penalty: float = 0.85   # score multiplier per level above the user
    eligibility_hide_levels_above: int = 2    # hide jobs this many levels above (or more)
    eligibility_hide_years_margin: int = 3    # hide jobs asking for user max years + this

    # Job ingestion (plan §7).
    # Corpus cap on open jobs (~1,600 embedding tokens each), sized for Supabase's free 500 MB.
    ingestion_max_open_jobs: int = 3000
    # Per-company share of the corpus, so one big board (Stripe lists ~670) can't crowd out
    # the rest. Applied before the global cap; both keep the most recently posted.
    ingestion_max_jobs_per_company: int = 150
    # Closed jobs are deleted after this many days, unless a user has an application on them.
    ingestion_closed_retention_days: int = 14
    # A company goes inactive after this many consecutive failed fetches.
    ingestion_max_failures: int = 3
    # Pause between ATS requests (be polite: sequential, small delay).
    ingestion_request_delay: float = 1.0
    # Client-side Voyage pacing for ingestion, well under the paid-tier limits. An account
    # without a payment method is capped at 3 req/min and 10K tokens/min: set these to 3 and
    # 10000 (and lower the corpus cap) there.
    voyage_requests_per_minute: int = 300
    voyage_tokens_per_minute: int = 1_000_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def supabase_auth_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_auth_url}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
