"""Applications CRUD (plan §11) on the real DB (rolled back)."""

import uuid

import pytest

from app.api.deps import get_current_user
from app.main import app

APPS = "/api/v1/applications"
pytestmark = pytest.mark.db


def test_requires_auth(client):
    assert client.get(APPS).status_code == 401


def test_save_and_move_through_statuses(db_client, make_job):
    job = make_job("Backend Engineer")
    r = db_client.post(APPS, json={"job_id": str(job.id)})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "saved" and created["notes"] is None
    assert created["job"]["title"] == "Backend Engineer" and created["job"]["company"] == "Acme (test)"

    app_id = created["id"]
    for status in ("applied", "interviewing", "offer"):
        r = db_client.patch(f"{APPS}/{app_id}", json={"status": status})
        assert r.status_code == 200 and r.json()["status"] == status

    r = db_client.patch(f"{APPS}/{app_id}", json={"notes": "Recruiter call Tuesday"})
    assert r.json()["notes"] == "Recruiter call Tuesday" and r.json()["status"] == "offer"  # untouched
    assert db_client.patch(f"{APPS}/{app_id}", json={"notes": None}).json()["notes"] is None

    assert [a["id"] for a in db_client.get(APPS).json()] == [app_id]
    assert db_client.get(APPS, params={"status": "offer"}).json()[0]["id"] == app_id
    assert db_client.get(APPS, params={"status": "saved"}).json() == []

    assert db_client.delete(f"{APPS}/{app_id}").status_code == 204
    assert db_client.get(APPS).json() == []


def test_create_with_status_and_notes(db_client, make_job):
    job = make_job("Data Engineer")
    body = db_client.post(APPS, json={"job_id": str(job.id), "status": "applied", "notes": " hi "}).json()
    assert (body["status"], body["notes"]) == ("applied", "hi")


def test_duplicate_is_409_and_unknown_job_is_404(db_client, make_job):
    job = make_job("Backend Engineer")
    assert db_client.post(APPS, json={"job_id": str(job.id)}).status_code == 201
    r = db_client.post(APPS, json={"job_id": str(job.id)})
    assert r.status_code == 409 and "already saved" in r.json()["detail"]
    assert db_client.post(APPS, json={"job_id": str(uuid.uuid4())}).status_code == 404
    assert len(db_client.get(APPS).json()) == 1  # the failed insert didn't break the session


@pytest.mark.parametrize("body", [{}, {"status": None}, {"status": "ghosted"}, {"notes": "x" * 5001}])
def test_invalid_patch_is_422(db_client, make_job, body):
    job = make_job("Backend Engineer")
    app_id = db_client.post(APPS, json={"job_id": str(job.id)}).json()["id"]
    assert db_client.patch(f"{APPS}/{app_id}", json=body).status_code == 422


def test_other_users_applications_are_invisible(db_client, make_job, test_user_id):
    job = make_job("Backend Engineer")
    app_id = db_client.post(APPS, json={"job_id": str(job.id)}).json()["id"]

    app.dependency_overrides[get_current_user] = lambda: uuid.uuid4()
    assert db_client.get(APPS).json() == []
    assert db_client.patch(f"{APPS}/{app_id}", json={"status": "applied"}).status_code == 404
    assert db_client.delete(f"{APPS}/{app_id}").status_code == 404

    app.dependency_overrides[get_current_user] = lambda: test_user_id
    assert db_client.get(APPS).json()[0]["status"] == "saved"


def test_closed_jobs_stay_in_the_tracker(db_client, db_session, make_job):
    job = make_job("Backend Engineer")
    db_client.post(APPS, json={"job_id": str(job.id)})
    job.status = "closed"
    db_session.commit()
    assert db_client.get(APPS).json()[0]["job"]["status"] == "closed"
