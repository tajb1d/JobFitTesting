import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import JWT_AUDIENCE, get_settings
from app.db import get_db, get_engine
from app.main import app
from app.services.auth import get_jwks_client

TEST_KID = "test-kid"
_RANDOM = object()


class _ForbiddenDB:
    """Stands in for the DB session in auth tests. Any use fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"DB accessed ({name}) before auth succeeded")


# ---------------------------------------------------------------- auth (offline)


@pytest.fixture(scope="session")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def jwks_client(signing_key) -> PyJWKClient:
    """A real PyJWKClient whose network fetch returns our test public key."""
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk |= {"kid": TEST_KID, "alg": "ES256", "use": "sig"}
    client = PyJWKClient("https://example.invalid/jwks.json")
    client.fetch_data = lambda: {"keys": [jwk]}
    return client


@pytest.fixture
def make_token(signing_key) -> Callable[..., str]:
    """Sign an ES256 token shaped like Supabase's. Pass claim=None to drop a claim."""

    def _make(
        *,
        sub: Any = _RANDOM,
        kid: str | None = TEST_KID,
        expires_in: int = 3600,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": str(uuid.uuid4()) if sub is _RANDOM else sub,
            "aud": JWT_AUDIENCE,
            "iss": get_settings().supabase_auth_url,
            "iat": now,
            "exp": now + expires_in,
            "role": "authenticated",
        } | overrides
        claims = {k: v for k, v in claims.items() if v is not None}
        headers = {"kid": kid} if kid else {}
        return jwt.encode(claims, signing_key, algorithm="ES256", headers=headers)

    return _make


@pytest.fixture
def client(jwks_client) -> Iterator[TestClient]:
    """App client with the test JWKS. The DB is a Mock that fails the test if touched,
    so auth failures are proven to stop before any query runs."""

    def _no_db() -> Iterator["_ForbiddenDB"]:
        yield _ForbiddenDB()

    app.dependency_overrides[get_jwks_client] = lambda: jwks_client
    app.dependency_overrides[get_db] = _no_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- database (real Supabase)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Session on the real DB inside a transaction that is always rolled back.
    Service-level commit() calls only release a savepoint, so nothing persists."""
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    conn = get_engine().connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def test_user_id(db_session: Session) -> uuid.UUID:
    """The real Supabase test user. profiles.user_id FKs to auth.users, so a random UUID
    would be rejected. Their existing profile (if any) is removed inside the transaction
    so every test starts clean; the rollback restores it."""
    email = get_settings().test_user_email
    if not email:
        pytest.skip("TEST_USER_EMAIL not set")
    user_id = db_session.execute(
        text("SELECT id FROM auth.users WHERE email = :email"), {"email": email}
    ).scalar()
    if user_id is None:
        pytest.skip(f"Test user {email} not found in auth.users")
    db_session.execute(text("DELETE FROM profiles WHERE user_id = :u"), {"u": user_id})
    return user_id


@pytest.fixture
def db_client(db_session: Session, test_user_id: uuid.UUID) -> Iterator[TestClient]:
    """App client authenticated as the test user, backed by the rolled-back session."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: test_user_id
    yield TestClient(app)
    app.dependency_overrides.clear()
