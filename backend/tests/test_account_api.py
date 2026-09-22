"""DELETE /account: the user's rows, then the Supabase auth user (faked here)."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.main import app
from app.models import Analysis, Application, Profile, Resume, ResumeBullet
from app.services.account import AuthAdminError, SupabaseAuthAdmin, get_auth_admin

SE_JOB = open(__file__.replace("test_account_api.py", "data/software_engineer.txt")).read()


class FakeAdmin:
    def __init__(self, fail: bool = False):
        self.deleted: list[uuid.UUID] = []
        self.fail = fail

    def delete_user(self, user_id: uuid.UUID) -> None:
        if self.fail:
            raise AuthAdminError("simulated")
        self.deleted.append(user_id)


def _count(db, model, user_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


@pytest.fixture
def user_with_data(db_client, db_session, test_user_id, fixture_pdfs, make_job):
    db_client.put("/api/v1/profile", json={"target_roles": ["Engineer"], "experience_level": "entry"})
    resume_id = db_client.post(
        "/api/v1/resumes", files={"file": ("r.pdf", fixture_pdfs[0].read_bytes(), "application/pdf")}
    ).json()["id"]
    assert db_client.post("/api/v1/analyses",
                          json={"resume_id": resume_id, "job_description": SE_JOB}).status_code == 201
    job = make_job("Backend Engineer")
    assert db_client.post("/api/v1/applications", json={"job_id": str(job.id)}).status_code == 201
    for model in (Profile, Resume, Analysis, Application):
        assert _count(db_session, model, test_user_id) >= 1
    return resume_id


@pytest.mark.db
def test_delete_account_removes_everything_then_the_auth_user(
    db_client, db_session, test_user_id, user_with_data
):
    admin = FakeAdmin()
    app.dependency_overrides[get_auth_admin] = lambda: admin
    assert db_client.delete("/api/v1/account").status_code == 204

    db_session.expire_all()
    for model in (Profile, Resume, Analysis, Application):
        assert _count(db_session, model, test_user_id) == 0, model.__name__
    assert db_session.scalar(select(func.count()).select_from(ResumeBullet)
                             .where(ResumeBullet.resume_id == uuid.UUID(user_with_data))) == 0
    assert admin.deleted == [test_user_id]


@pytest.mark.db
def test_admin_failure_is_502_but_data_is_already_gone(db_client, db_session, test_user_id, user_with_data):
    app.dependency_overrides[get_auth_admin] = lambda: FakeAdmin(fail=True)
    r = db_client.delete("/api/v1/account")
    assert r.status_code == 502 and "try again" in r.json()["detail"]
    db_session.expire_all()
    assert _count(db_session, Resume, test_user_id) == 0


def test_requires_auth(client):
    assert client.delete("/api/v1/account").status_code == 401


# ---------------------------------------------------------------- Supabase admin client


def _admin(handler) -> SupabaseAuthAdmin:
    return SupabaseAuthAdmin("https://proj.supabase.co/", "sb_secret_test",
                             client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_admin_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    user_id = uuid.uuid4()
    _admin(handler).delete_user(user_id)
    assert seen[0].method == "DELETE"
    assert str(seen[0].url) == f"https://proj.supabase.co/auth/v1/admin/users/{user_id}"
    assert seen[0].headers["apikey"] == "sb_secret_test"
    assert seen[0].headers["authorization"] == "Bearer sb_secret_test"


def test_admin_treats_404_as_already_deleted_and_raises_on_errors():
    _admin(lambda r: httpx.Response(404, json={})).delete_user(uuid.uuid4())  # no error
    with pytest.raises(AuthAdminError, match="500"):
        _admin(lambda r: httpx.Response(500, text="boom")).delete_user(uuid.uuid4())
    with pytest.raises(AuthAdminError, match="SUPABASE_SECRET_KEY"):
        SupabaseAuthAdmin("https://proj.supabase.co", "").delete_user(uuid.uuid4())
