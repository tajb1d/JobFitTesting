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

    # Scoring weights (plan §6). Must sum to 1.
    weight_skill: float = 0.50
    weight_semantic: float = 0.35
    weight_structure: float = 0.15

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
