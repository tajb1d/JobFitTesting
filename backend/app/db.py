from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Created on first use, so importing the app never touches the database."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set (backend/.env or environment).")
    # Must be the Supabase *session* pooler (:5432). The transaction pooler (:6543) breaks
    # psycopg 3's automatic prepared statements.
    # Small pool: Render free tier runs one worker, and the pooler caps client connections.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=300)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request. Services commit explicitly."""
    with get_sessionmaker()() as session:
        yield session
