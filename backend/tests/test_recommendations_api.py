"""GET /recommendations (plan §9) and GET /jobs/{id}, on the real DB (rolled back) with the
real corpus hidden and a few controlled jobs."""

import uuid

import pytest

from app.api.deps import get_current_user
from app.main import app

RECS = "/api/v1/recommendations"
pytestmark = pytest.mark.db

# fixture_pdfs[0] (sample-resume-2022#2) lists C++, SQL and .NET, but not Java or Go.
MATCH = [("C++", "language", 3.0), ("SQL", "language", 3.0)]
MISS = [("Java", "language", 3.0), ("Kubernetes", "devops", 3.0)]


@pytest.fixture
def resume_id(db_client, fixture_pdfs) -> str:
    r = db_client.post("/api/v1/resumes",
                       files={"file": ("r.pdf", fixture_pdfs[0].read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_level(client, level):
    r = client.put("/api/v1/profile", json={"target_roles": [], "experience_level": level})
    assert r.status_code == 200, r.text


def _titles(body) -> list[str]:
    return [i["job"]["title"] for i in body["items"]]


def test_requires_auth(client):
    assert client.get(RECS).status_code == 401


def test_no_resume_is_404(db_client, make_job):
    r = db_client.get(RECS)
    assert r.status_code == 404 and "Upload a resume" in r.json()["detail"]


def test_ranks_by_score_and_returns_the_documented_shape(db_client, resume_id, make_job):
    make_job("Backend Engineer", skills=MATCH)
    make_job("Platform Engineer", skills=MISS)
    body = db_client.get(RECS).json()

    assert body["resume_id"] == resume_id
    assert _titles(body) == ["Backend Engineer", "Platform Engineer"]
    top = body["items"][0]
    assert set(top) == {"job", "score", "subscores", "levels_above", "stretch",
                        "matched_skills", "missing_skills"}
    assert set(top["job"]) == {"id", "company", "title", "location", "is_remote", "url",
                               "level", "min_years", "posted_at", "status"}
    assert top["job"]["company"] == "Acme (test)"
    assert top["subscores"]["skill"] == 1.0
    assert [s["skill"] for s in top["matched_skills"]] == ["C++", "SQL"]
    assert [s["skill"] for s in body["items"][1]["missing_skills"]] == ["Java", "Kubernetes"]
    assert 0 <= body["items"][1]["score"] < top["score"] <= 100


def test_eligibility_hides_far_above_and_penalizes_one_above(db_client, resume_id, make_job):
    _set_level(db_client, "entry")
    make_job("Engineer (entry)", skills=MATCH, level="entry")
    make_job("Engineer (mid)", skills=MATCH, level="mid")          # 1 above: penalized
    make_job("Engineer (senior)", skills=MATCH, level="senior")    # 2 above: hidden
    make_job("Engineer (8 yrs)", skills=MATCH, min_years=8)        # entry max 2 + 3 <= 8: hidden

    body = db_client.get(RECS).json()
    items = {i["job"]["title"]: i for i in body["items"]}
    assert set(items) == {"Engineer (entry)", "Engineer (mid)"}
    entry, mid = items["Engineer (entry)"], items["Engineer (mid)"]
    assert (entry["levels_above"], entry["stretch"]) == (0, False)
    assert (mid["levels_above"], mid["stretch"]) == (1, True)
    assert mid["score"] == pytest.approx(entry["score"] * 0.85, abs=0.1)  # same match, one level

    stretch = db_client.get(RECS, params={"show_stretch": True}).json()
    items = {i["job"]["title"]: i for i in stretch["items"]}
    assert {"Engineer (senior)", "Engineer (8 yrs)"} <= set(items)
    assert items["Engineer (senior)"]["stretch"] and items["Engineer (senior)"]["levels_above"] == 2
    # show_stretch disables hiding, not the penalty: 0.85^2.
    assert items["Engineer (senior)"]["score"] == pytest.approx(entry["score"] * 0.85**2, abs=0.1)


def test_unknown_levels_are_neither_hidden_nor_penalized(db_client, resume_id, make_job):
    make_job("Staff Engineer", skills=MATCH, level="staff")  # user has no level set
    item = db_client.get(RECS).json()["items"][0]
    assert (item["levels_above"], item["stretch"]) == (None, False)


def test_filters_are_applied(db_client, resume_id, make_job):
    make_job("Remote Engineer", skills=MATCH, is_remote=True, location="Remote - US")
    make_job("NYC Engineer", skills=MATCH, location="New York, NY", level="entry")
    make_job("SF Engineer", skills=MATCH, location="San Francisco, CA")
    make_job("Closed Engineer", skills=MATCH, status="closed")

    assert _titles(db_client.get(RECS, params={"remote": True}).json()) == ["Remote Engineer"]
    assert "Remote Engineer" not in _titles(db_client.get(RECS, params={"remote": False}).json())
    assert _titles(db_client.get(RECS, params={"location": "san fran"}).json()) == ["SF Engineer"]
    assert _titles(db_client.get(RECS, params={"level": "entry"}).json()) == ["NYC Engineer"]
    assert "Closed Engineer" not in _titles(db_client.get(RECS).json())
    assert len(db_client.get(RECS, params={"limit": 2}).json()["items"]) == 2
    assert db_client.get(RECS, params={"limit": 51}).status_code == 422
    assert db_client.get(RECS, params={"location": "100%_"}).json()["items"] == []  # escaped


def test_retrieval_prefers_the_nearest_jobs(db_client, db_session, resume_id, make_job, monkeypatch):
    from app.config import get_settings
    from app.models import Resume
    from tests.conftest import unit_vector

    near = make_job("Near", axis=10)
    make_job("Far", axis=200)
    db_session.get(Resume, uuid.UUID(resume_id)).embedding = unit_vector(10)
    db_session.commit()
    monkeypatch.setattr(get_settings(), "recommendation_candidates", 1)
    assert [i["job"]["id"] for i in db_client.get(RECS).json()["items"]] == [str(near.id)]


def test_other_users_resume_is_404(db_client, resume_id, make_job):
    app.dependency_overrides[get_current_user] = lambda: uuid.uuid4()
    assert db_client.get(RECS, params={"resume_id": resume_id}).status_code == 404


# ---------------------------------------------------------------- job detail


def test_job_detail(db_client, make_job):
    job = make_job("Data Engineer", skills=MATCH,
                   requirements=[("required", "3+ years of SQL"), ("nice", "Airflow experience")])
    body = db_client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["title"] == "Data Engineer" and body["company"] == "Acme (test)"
    assert body["requirements"] == [{"section": "required", "text": "3+ years of SQL"},
                                    {"section": "nice", "text": "Airflow experience"}]
    assert [s["skill"] for s in body["skills"]] == ["C++", "SQL"]
    assert body["description_text"].startswith("Data Engineer")


def test_closed_job_detail_is_still_viewable_and_missing_is_404(db_client, make_job):
    closed = make_job("Old Role", status="closed")
    assert db_client.get(f"/api/v1/jobs/{closed.id}").json()["status"] == "closed"
    assert db_client.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404


def test_job_detail_requires_auth(client):
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 401


def test_feed_semantic_score_matches_the_full_analysis(db_client, db_session, resume_id, make_job):
    """The feed computes requirement similarities in Postgres; a job_id analysis does the same
    maths in numpy on the stored vectors. Both must give the same S_sem and final score."""
    from app.models import ResumeBullet
    from tests.conftest import unit_vector

    bullets = db_session.query(ResumeBullet).filter(ResumeBullet.resume_id == uuid.UUID(resume_id)).all()
    for i, b in enumerate(bullets):  # varied directions, some close to the requirements below
        v = unit_vector(40 + i)
        v[41] += 0.8
        b.embedding = v
    db_session.commit()
    job = make_job("Backend Engineer", skills=MATCH, axis=40,
                   requirements=[("required", "Design APIs"), ("responsibilities", "Own services"),
                                 ("nice", "Kafka")])

    feed = db_client.get(RECS).json()["items"][0]
    analysis = db_client.post("/api/v1/analyses", json={"resume_id": resume_id, "job_id": str(job.id)}).json()
    assert feed["subscores"]["semantic"] is not None and feed["subscores"]["semantic"] > 0
    assert feed["subscores"]["semantic"] == pytest.approx(analysis["semantic_score"], abs=1e-4)
    assert feed["score"] == pytest.approx(analysis["final_score"], abs=0.1)
