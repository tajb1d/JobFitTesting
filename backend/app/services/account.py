"""DELETE /account (plan §11): the user's rows, then their Supabase auth user."""

import logging
import uuid
from functools import lru_cache
from typing import Protocol

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Analysis, Application, Profile, Resume

logger = logging.getLogger(__name__)


class AuthAdminError(Exception):
    """The Supabase admin API didn't delete the user."""


class AuthAdmin(Protocol):
    def delete_user(self, user_id: uuid.UUID) -> None: ...


class SupabaseAuthAdmin:
    """Supabase Auth admin API, authenticated with the server-only secret key."""

    def __init__(self, supabase_url: str, secret_key: str, client: httpx.Client | None = None):
        self.base = f"{supabase_url.rstrip('/')}/auth/v1/admin/users"
        self.secret_key = secret_key
        self.client = client or httpx.Client(timeout=10)

    def delete_user(self, user_id: uuid.UUID) -> None:
        if not self.secret_key:
            raise AuthAdminError("SUPABASE_SECRET_KEY is not set")
        headers = {"apikey": self.secret_key, "Authorization": f"Bearer {self.secret_key}"}
        try:
            r = self.client.delete(f"{self.base}/{user_id}", headers=headers)
        except httpx.HTTPError as e:
            raise AuthAdminError(f"request failed: {e}") from e
        if r.status_code == 404:
            return  # already gone: deleting is idempotent
        if r.status_code >= 300:
            raise AuthAdminError(f"HTTP {r.status_code}: {r.text[:200]}")


@lru_cache
def get_auth_admin() -> AuthAdmin:
    """FastAPI dependency. Tests override it with a fake."""
    s = get_settings()
    return SupabaseAuthAdmin(s.supabase_url, s.supabase_secret_key)


def delete_account(db: Session, user_id: uuid.UUID, admin: AuthAdmin) -> None:
    """Delete all of the user's data, then the auth user. The rows would also cascade from
    auth.users, but deleting them first means a failed admin call never leaves data behind;
    retrying the request then only repeats the (idempotent) admin call."""
    for model in (Analysis, Application, Resume, Profile):  # resume_bullets cascade
        db.execute(delete(model).where(model.user_id == user_id))
    db.commit()
    admin.delete_user(user_id)
    logger.info("account deleted user_id=%s", user_id)
