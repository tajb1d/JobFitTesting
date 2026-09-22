import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import EMBEDDING_DIM, JWT_AUDIENCE, get_settings
from app.db import get_db, get_engine
from app.main import app
from app.services.auth import get_jwks_client
from app.services.embeddings import get_embedder
from app.services.feedback import FeedbackError, FeedbackItem, get_feedback_generator

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
    db_session.execute(text("DELETE FROM resumes WHERE user_id = :u"), {"u": user_id})
    return user_id


class FakeEmbedder:
    """Stands in for Voyage. Records calls; returns a distinct unit-ish vector per text."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = fail

    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        from app.services.embeddings import EmbeddingError

        self.calls.append((input_type, list(texts)))
        if self.fail:
            raise EmbeddingError("simulated outage")
        return [[(i + 1) / 1000] * EMBEDDING_DIM for i in range(len(texts))]


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


class FakeFeedback:
    """Stands in for Claude. Records the context it was given."""

    def __init__(self, fail: bool = False):
        self.contexts: list[dict] = []
        self.fail = fail

    def generate(self, context: dict) -> list[FeedbackItem]:
        self.contexts.append(context)
        if self.fail:
            raise FeedbackError("simulated LLM failure")
        items = [FeedbackItem(type="missing_skill", severity="high",
                              message=f"If you've used skill {i}, add it to your resume.")
                 for i in range(4)]
        return items + [FeedbackItem(type="strength", severity="low",
                                     message="Your projects show relevant programming work.")]


@pytest.fixture
def fake_feedback() -> FakeFeedback:
    return FakeFeedback()


@pytest.fixture
def db_client(
    db_session: Session,
    test_user_id: uuid.UUID,
    fake_embedder: FakeEmbedder,
    fake_feedback: FakeFeedback,
) -> Iterator[TestClient]:
    """App client authenticated as the test user, backed by the rolled-back session, with
    Voyage and Claude replaced by fakes."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: test_user_id
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_feedback_generator] = lambda: fake_feedback
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- resume PDFs


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_pdfs() -> list[Path]:
    """Real resume PDFs in tests/fixtures/ (gitignored: they may contain personal data)."""
    pdfs = sorted(FIXTURES_DIR.glob("*.pdf"))
    if not pdfs:
        pytest.skip("No resume PDFs in tests/fixtures/")
    return pdfs


# ---------------------------------------------------------------- corpus jobs (real Supabase)


def unit_vector(axis: int) -> list[float]:
    """A distinct direction per axis, so tests control cosine similarity exactly."""
    v = [0.0] * EMBEDDING_DIM
    v[axis % EMBEDDING_DIM] = 1.0
    return v


@pytest.fixture
def make_job(db_session: Session):
    """Create open corpus jobs inside the rolled-back transaction. The real corpus is closed
    first (seen now, so nothing prunes it), so only these jobs are recommendable."""
    from app.models import Company, Job, JobRequirement

    db_session.execute(text("UPDATE jobs SET status = 'closed', last_seen = now()"))
    company = Company(name="Acme (test)", ats="greenhouse", board_token=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(company)
    db_session.flush()

    def _make(
        title: str,
        *,
        skills: list[tuple[str, str, float]] = (),     # (skill, category, weight)
        requirements: list[tuple[str, str]] = (("required", "Build and ship backend services"),),
        level: str | None = None,
        min_years: int | None = None,
        is_remote: bool = False,
        location: str | None = "New York, NY",
        axis: int = 0,
        status: str = "open",
        external_id: str | None = None,
    ) -> Job:
        job = Job(
            company_id=company.id,
            external_id=external_id or uuid.uuid4().hex,
            title=title, location=location, is_remote=is_remote,
            url=f"https://boards.greenhouse.io/{company.board_token}/jobs/1",
            description_text=f"{title}\n\n" + "\n".join(t for _, t in requirements),
            content_hash=uuid.uuid4().hex,
            skills=[{"skill": s, "category": c, "weight": w, "count": 1, "sections": ["required"]}
                    for s, c, w in skills],
            level=level, min_years=min_years, embedding=unit_vector(axis), status=status,
        )
        db_session.add(job)
        db_session.flush()
        db_session.add_all(JobRequirement(job_id=job.id, section=sec, text=t,
                                          embedding=unit_vector(axis + i + 1))
                           for i, (sec, t) in enumerate(requirements))
        db_session.commit()
        return job

    _make.company = company
    return _make
