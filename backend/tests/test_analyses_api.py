import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.deps import get_current_user
from app.config import get_settings
from app.main import app
from app.models import Analysis

ANALYSES = "/api/v1/analyses"
SE_JOB = (Path(__file__).parent / "data" / "software_engineer.txt").read_text()


@pytest.fixture
def resume_id(db_client, fixture_pdfs) -> str:
    r = db_client.post(
        "/api/v1/resumes",
        files={"file": ("r.pdf", fixture_pdfs[0].read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _analyze(client, resume_id, **extra):
    body = {"resume_id": resume_id, "job_description": SE_JOB} | extra
    return client.post(ANALYSES, json=body)


# ---------------------------------------------------------------- validation (no DB needed)


def test_requires_auth(client):
    assert client.post(ANALYSES, json={}).status_code == 401


# ---------------------------------------------------------------- create

pytestmark_db = pytest.mark.db


@pytestmark_db
def test_create_analysis(db_client, db_session, test_user_id, resume_id, fake_embedder, fake_feedback):
    r = _analyze(db_client, resume_id)
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["resume_id"] == resume_id
    assert body["job_title"] == "Software Engineer I (New Grad)"
    assert 0 <= body["final_score"] <= 100
    for key in ("skill_score", "semantic_score", "structure_score", "tfidf_score"):
        assert body[key] is not None and 0 <= body[key] <= 1, key
    assert body["matched_skills"] or body["missing_skills"]
    assert len(body["feedback_items"]) == 5 and body["feedback_items"][-1]["type"] == "strength"

    # Requirements embedded as queries (after the upload's own query + document calls).
    input_type, texts = fake_embedder.calls[-1]
    assert input_type == "query" and "Familiarity with SQL and relational databases" in texts

    # Claude got the structured result, not the resume text.
    context = fake_feedback.contexts[0]
    assert set(context) == {
        "job_title", "subscores", "matched_skills", "missing_skills",
        "weak_requirements", "failed_structure_checks",
    }

    stored = db_session.get(Analysis, uuid.UUID(body["id"]))
    assert stored.user_id == test_user_id and stored.job_id is None
    assert stored.job_description_text.startswith("Software Engineer I")


@pytestmark_db
def test_llm_failure_still_produces_an_analysis(db_client, resume_id, fake_feedback):
    fake_feedback.fail = True
    r = _analyze(db_client, resume_id)
    assert r.status_code == 201
    items = r.json()["feedback_items"]
    assert items and items[-1]["type"] == "strength"


@pytestmark_db
def test_embedding_failure_stores_nothing(db_client, db_session, test_user_id, resume_id, fake_embedder):
    fake_embedder.fail = True
    assert _analyze(db_client, resume_id).status_code == 503
    assert db_session.scalars(select(Analysis).where(Analysis.user_id == test_user_id)).all() == []


@pytestmark_db
def test_job_id_and_job_url_not_implemented_yet(db_client, resume_id):
    assert db_client.post(ANALYSES, json={"resume_id": resume_id, "job_id": str(uuid.uuid4())}).status_code == 501
    assert db_client.post(
        ANALYSES, json={"resume_id": resume_id, "job_url": "https://boards.greenhouse.io/x/jobs/1"}
    ).status_code == 501


@pytestmark_db
@pytest.mark.parametrize(
    "body",
    [
        {},                                                              # no source
        {"job_description": SE_JOB, "job_id": str(uuid.uuid4())},        # two sources
        {"job_description": "Too short to be a job description."},       # < 100 chars
        {"job_description": "x" * 20_001},                               # > 20,000 chars
    ],
)
def test_invalid_bodies_are_422(db_client, resume_id, body):
    assert db_client.post(ANALYSES, json={"resume_id": resume_id} | body).status_code == 422


@pytestmark_db
def test_someone_elses_resume_is_not_found(db_client, resume_id):
    app.dependency_overrides[get_current_user] = lambda: uuid.uuid4()
    r = _analyze(db_client, resume_id)
    assert r.status_code == 404 and r.json()["detail"] == "Resume not found"


# ---------------------------------------------------------------- read / delete


@pytestmark_db
def test_list_get_delete(db_client, test_user_id, resume_id):
    created = _analyze(db_client, resume_id).json()
    listed = db_client.get(ANALYSES).json()
    assert [a["id"] for a in listed] == [created["id"]]
    assert "feedback_items" not in listed[0]  # summaries only

    assert db_client.get(f"{ANALYSES}/{created['id']}").json() == created

    app.dependency_overrides[get_current_user] = lambda: uuid.uuid4()  # someone else
    assert db_client.get(f"{ANALYSES}/{created['id']}").status_code == 404
    assert db_client.delete(f"{ANALYSES}/{created['id']}").status_code == 404

    app.dependency_overrides[get_current_user] = lambda: test_user_id
    assert db_client.delete(f"{ANALYSES}/{created['id']}").status_code == 204
    assert db_client.get(f"{ANALYSES}/{created['id']}").status_code == 404


@pytestmark_db
def test_deleting_the_resume_deletes_its_analyses(db_client, resume_id):
    created = _analyze(db_client, resume_id).json()
    assert db_client.delete(f"/api/v1/resumes/{resume_id}").status_code == 204
    assert db_client.get(f"{ANALYSES}/{created['id']}").status_code == 404


# ---------------------------------------------------------------- rate limit


@pytestmark_db
def test_rate_limit(db_client, resume_id, fake_feedback, monkeypatch):
    monkeypatch.setattr(get_settings(), "analyses_per_hour", 2)
    assert _analyze(db_client, resume_id).status_code == 201
    assert _analyze(db_client, resume_id).status_code == 201
    r = _analyze(db_client, resume_id)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 3600
    assert len(fake_feedback.contexts) == 2  # the limited request never reached the LLM
