import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import get_current_user
from app.config import get_settings
from app.main import app
from app.models import Analysis
from app.services.job_urls import get_posting_client

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


# ---------------------------------------------------------------- by job_id / job_url

GREENHOUSE = json.loads((Path(__file__).parent / "data" / "ats" / "greenhouse_discord.json").read_text())


class PostingRequests(list):
    """URLs the server asked the ATS for; respond() changes what the fake ATS returns."""

    def __init__(self):
        super().__init__()
        self.response = lambda: httpx.Response(200, json=GREENHOUSE["jobs"][1])

    def respond(self, fn) -> None:
        self.response = fn


@pytest.fixture
def posting_requests() -> PostingRequests:
    """Serve the recorded Greenhouse posting for any ATS request; record the URLs asked for."""
    seen = PostingRequests()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return seen.response()

    app.dependency_overrides[get_posting_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(handler))
    return seen


@pytestmark_db
def test_analyze_corpus_job_by_id_uses_stored_embeddings(
    db_client, db_session, resume_id, make_job, fake_embedder
):
    job = make_job("Backend Engineer", skills=[("SQL", "language", 3.0), ("Java", "language", 3.0)],
                   requirements=[("required", "Experience with SQL databases"),
                                 ("responsibilities", "Build reliable APIs")])
    calls_before = len(fake_embedder.calls)
    r = db_client.post(ANALYSES, json={"resume_id": resume_id, "job_id": str(job.id)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(fake_embedder.calls) == calls_before  # no Voyage call: vectors were stored
    assert body["job_id"] == str(job.id) and body["job_title"] == "Backend Engineer"
    assert {s["skill"] for s in body["matched_skills"] + body["missing_skills"]} == {"SQL", "Java"}
    assert body["semantic_score"] is not None and body["tfidf_score"] is not None
    assert db_session.get(Analysis, uuid.UUID(body["id"])).job_id == job.id


@pytestmark_db
def test_unknown_job_id_is_404(db_client, resume_id):
    r = db_client.post(ANALYSES, json={"resume_id": resume_id, "job_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["detail"] == "Job not found"


@pytestmark_db
def test_job_url_of_an_ingested_posting_uses_the_stored_job(
    db_client, resume_id, make_job, posting_requests, fake_embedder
):
    job = make_job("Stored Role", external_id="4242")
    url = f"https://job-boards.greenhouse.io/{make_job.company.board_token.upper()}/jobs/4242?gh_src=x"
    calls_before = len(fake_embedder.calls)
    r = db_client.post(ANALYSES, json={"resume_id": resume_id, "job_url": url})
    assert r.status_code == 201, r.text
    assert r.json()["job_id"] == str(job.id)
    assert posting_requests == [] and len(fake_embedder.calls) == calls_before


@pytestmark_db
def test_job_url_not_in_corpus_is_fetched_and_scored(db_client, resume_id, posting_requests, fake_embedder):
    r = db_client.post(ANALYSES, json={
        "resume_id": resume_id, "job_url": "https://boards.greenhouse.io/discord/jobs/999000111"})  # not ingested
    assert r.status_code == 201, r.text
    body = r.json()
    assert posting_requests == ["https://boards-api.greenhouse.io/v1/boards/discord/jobs/999000111"]
    assert body["job_id"] is None and body["job_title"] == "Commercial Policy Lead"
    assert fake_embedder.calls[-1][0] == "query"  # requirements embedded like a pasted job


@pytestmark_db
def test_job_url_failures(db_client, resume_id, posting_requests):
    url = "https://jobs.lever.co/outreach/3c27d8b2-bdef-4a0c-8507-092ef90fab33/apply"
    posting_requests.respond(lambda: httpx.Response(404, json={"ok": False}))
    r = db_client.post(ANALYSES, json={"resume_id": resume_id, "job_url": url})
    assert r.status_code == 422 and "wasn't found" in r.json()["detail"]
    assert posting_requests[-1] == ("https://api.lever.co/v0/postings/outreach/"
                                    "3c27d8b2-bdef-4a0c-8507-092ef90fab33?mode=json")

    def boom():
        raise httpx.ConnectError("down")
    posting_requests.respond(boom)
    assert db_client.post(ANALYSES, json={"resume_id": resume_id, "job_url": url}).status_code == 503


@pytestmark_db
@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/123",
    "https://boards.greenhouse.io.evil.com/acme/jobs/1",
    "https://boards.greenhouse.io/acme/jobs/1/../../admin",
    "ftp://boards.greenhouse.io/acme/jobs/1",
    "https://jobs.lever.co/acme/not-a-uuid",
])
def test_unsupported_job_url_is_422_with_advice(db_client, resume_id, posting_requests, url):
    r = db_client.post(ANALYSES, json={"resume_id": resume_id, "job_url": url})
    assert r.status_code == 422 and "Paste the job description" in r.json()["detail"]
    assert posting_requests == []  # never fetched


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
