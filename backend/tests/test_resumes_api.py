import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.api.deps import get_current_user
from app.config import EMBEDDING_DIM
from app.main import app
from app.models import Resume, ResumeBullet
from app.services.resumes import MAX_PDF_BYTES, clean_filename, resume_embedding_text

RESUMES = "/api/v1/resumes"


def _upload(client, data: bytes, name="resume.pdf", content_type="application/pdf"):
    return client.post(RESUMES, files={"file": (name, data, content_type)})


@pytest.fixture
def pdf_bytes(fixture_pdfs) -> bytes:
    return fixture_pdfs[0].read_bytes()


def _backdate(db_session, resume_id: str, minutes: int = 5) -> None:
    """Tests run in one transaction, where Postgres' now() is frozen, so consecutive uploads
    get identical created_at. In production each upload is its own transaction; backdating
    restores that ordering."""
    db_session.execute(
        update(Resume)
        .where(Resume.id == uuid.UUID(resume_id))
        .values(created_at=func.now() - timedelta(minutes=minutes))
    )


# ---------------------------------------------------------------- pure helpers (no DB)


def test_clean_filename():
    assert clean_filename("C:\\Users\\me\\Resume.pdf") == "Resume.pdf"
    assert clean_filename("../../etc/passwd") == "passwd"
    assert clean_filename("bad\x00name\n.pdf") == "badname.pdf"
    assert clean_filename(None) == "resume.pdf"
    assert len(clean_filename("a" * 500)) == 200


def test_embedding_text_prefixed_with_target_roles():
    assert resume_embedding_text("body", []) == "body"
    assert resume_embedding_text("body", ["Data Analyst", "BI Developer"]) == (
        "Target roles: Data Analyst, BI Developer\n\nbody"
    )


def test_upload_requires_auth(client):
    r = _upload(client, b"%PDF-1.4")
    assert r.status_code == 401


# ---------------------------------------------------------------- upload (real DB)

pytestmark_db = pytest.mark.db


@pytestmark_db
def test_upload_processes_and_stores_resume(db_client, db_session, test_user_id, pdf_bytes, fake_embedder):
    r = _upload(db_client, pdf_bytes, name="My Resume.pdf")
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["filename"] == "My Resume.pdf"
    assert body["is_active"] is True
    assert 0 <= body["structure_score"] <= 1
    assert len(body["structure_checks"]) == 8
    assert sum(c["points"] for c in body["structure_checks"]) == round(body["structure_score"] * 100)
    failed = {c["id"] for c in body["structure_checks"] if not c["passed"]}
    assert {f["check"] for f in body["general_feedback"]} == failed
    assert body["skills"] and body["sections"] and body["bullets"]
    assert {"experience", "education", "skills"} <= {s["key"] for s in body["sections"]}

    # Two Voyage calls: the whole resume as a query, the bullets as documents.
    assert [t for t, _ in fake_embedder.calls] == ["query", "document"]
    assert fake_embedder.calls[1][1] == [b["text"] for b in body["bullets"]]

    resume = db_session.get(Resume, uuid.UUID(body["id"]))
    assert resume.user_id == test_user_id
    assert len(resume.embedding) == EMBEDDING_DIM
    rows = db_session.scalars(select(ResumeBullet).where(ResumeBullet.resume_id == resume.id)).all()
    assert len(rows) == len(body["bullets"])
    assert all(len(b.embedding) == EMBEDDING_DIM for b in rows)


@pytestmark_db
def test_resume_embedding_uses_profile_target_roles(db_client, pdf_bytes, fake_embedder):
    db_client.put("/api/v1/profile", json={"target_roles": ["Data Analyst"]})
    assert _upload(db_client, pdf_bytes).status_code == 201
    query_text = fake_embedder.calls[0][1][0]
    assert query_text.startswith("Target roles: Data Analyst\n\n")


@pytestmark_db
def test_new_upload_becomes_the_only_active_resume(db_client, db_session, pdf_bytes):
    first = _upload(db_client, pdf_bytes).json()
    _backdate(db_session, first["id"])
    second = _upload(db_client, pdf_bytes).json()
    listed = db_client.get(RESUMES).json()
    assert [r["id"] for r in listed] == [second["id"], first["id"]]  # newest first
    assert [r["is_active"] for r in listed] == [True, False]
    assert "text" not in listed[0] and "bullets" not in listed[0]  # summaries only


@pytestmark_db
def test_get_one_resume(db_client, pdf_bytes):
    created = _upload(db_client, pdf_bytes).json()
    r = db_client.get(f"{RESUMES}/{created['id']}")
    assert r.status_code == 200
    assert r.json() == created


@pytestmark_db
def test_other_users_cannot_see_or_delete_a_resume(db_client, test_user_id, pdf_bytes):
    created = _upload(db_client, pdf_bytes).json()
    url = f"{RESUMES}/{created['id']}"

    stranger = uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: stranger
    assert db_client.get(url).status_code == 404
    assert db_client.delete(url).status_code == 404
    assert db_client.get(RESUMES).json() == []

    # Back as the owner: it's still there, so the 404s came from the user_id filter.
    app.dependency_overrides[get_current_user] = lambda: test_user_id
    assert db_client.get(url).status_code == 200


@pytestmark_db
def test_unknown_id_is_not_found(db_client):
    assert db_client.get(f"{RESUMES}/{uuid.uuid4()}").status_code == 404
    assert db_client.delete(f"{RESUMES}/{uuid.uuid4()}").status_code == 404


@pytestmark_db
def test_deleting_active_resume_promotes_newest_remaining(db_client, db_session, pdf_bytes):
    oldest = _upload(db_client, pdf_bytes).json()
    _backdate(db_session, oldest["id"], minutes=10)
    middle = _upload(db_client, pdf_bytes).json()
    _backdate(db_session, middle["id"], minutes=5)
    newest = _upload(db_client, pdf_bytes).json()

    assert db_client.delete(f"{RESUMES}/{newest['id']}").status_code == 204
    listed = db_client.get(RESUMES).json()
    assert [(r["id"], r["is_active"]) for r in listed] == [
        (middle["id"], True),
        (oldest["id"], False),
    ]
    # Bullets were removed by the DB cascade.
    remaining = db_session.scalars(
        select(ResumeBullet).where(ResumeBullet.resume_id == uuid.UUID(newest["id"]))
    ).all()
    assert remaining == []


@pytestmark_db
def test_embedding_failure_stores_nothing(db_client, db_session, test_user_id, pdf_bytes, fake_embedder):
    fake_embedder.fail = True
    r = _upload(db_client, pdf_bytes)
    assert r.status_code == 503
    assert db_session.scalars(select(Resume).where(Resume.user_id == test_user_id)).all() == []


# ---------------------------------------------------------------- rejected uploads


@pytestmark_db
def test_oversize_upload_rejected(db_client):
    r = _upload(db_client, b"%PDF-1.4" + b"0" * MAX_PDF_BYTES)
    assert r.status_code == 413


@pytestmark_db
def test_wrong_content_type_rejected(db_client):
    r = _upload(db_client, b"hello", name="resume.docx", content_type="application/msword")
    assert r.status_code == 415


@pytestmark_db
def test_non_pdf_bytes_rejected(db_client):
    r = _upload(db_client, b"not really a pdf")
    assert r.status_code == 422
    assert "isn't a PDF" in r.json()["detail"]
