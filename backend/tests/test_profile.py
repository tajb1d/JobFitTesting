import uuid

import pytest

pytestmark = pytest.mark.db

ME = "/api/v1/me"
PROFILE = "/api/v1/profile"

FULL = {
    "target_roles": ["Backend Engineer", "Data Engineer"],
    "location": "Chicago, IL",
    "remote_ok": True,
    "experience_level": "entry",
}


def test_me_without_profile(db_client, test_user_id):
    r = db_client.get(ME)
    assert r.status_code == 200
    assert r.json() == {"user_id": str(test_user_id), "profile": None}


def test_put_then_get_round_trips(db_client, test_user_id):
    r = db_client.put(PROFILE, json=FULL)
    assert r.status_code == 200
    saved = r.json()
    assert {k: saved[k] for k in FULL} == FULL
    assert saved["updated_at"]

    me = db_client.get(ME).json()
    assert me["user_id"] == str(test_user_id)
    assert {k: me["profile"][k] for k in FULL} == FULL


def test_put_is_full_replace(db_client):
    db_client.put(PROFILE, json=FULL)
    r = db_client.put(PROFILE, json={"target_roles": ["ML Engineer"]})
    assert r.status_code == 200
    assert r.json() | {"updated_at": None} == {
        "target_roles": ["ML Engineer"],
        "location": None,
        "remote_ok": False,
        "experience_level": None,
        "updated_at": None,
    }


def test_body_cannot_choose_the_user(db_client, test_user_id):
    # user_id comes only from the token; a body field is ignored.
    r = db_client.put(PROFILE, json=FULL | {"user_id": str(uuid.uuid4())})
    assert r.status_code == 200
    assert db_client.get(ME).json()["user_id"] == str(test_user_id)


def test_roles_are_trimmed(db_client):
    r = db_client.put(PROFILE, json={"target_roles": ["  Backend Engineer  "]})
    assert r.json()["target_roles"] == ["Backend Engineer"]


@pytest.mark.parametrize(
    "body",
    [
        {"experience_level": "wizard"},
        {"target_roles": [""]},
        {"target_roles": ["x" * 101]},
        {"target_roles": [f"Role {i}" for i in range(11)]},
        {"remote_ok": "sometimes"},
    ],
)
def test_invalid_bodies_are_422(db_client, body):
    assert db_client.put(PROFILE, json=body).status_code == 422


# ---------------------------------------------------------------- resume re-embed on role change


def _upload(client, fixture_pdfs) -> str:
    r = client.post("/api/v1/resumes",
                    files={"file": ("r.pdf", fixture_pdfs[0].read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_changing_roles_reembeds_the_active_resume(db_client, db_session, fixture_pdfs, fake_embedder):
    from app.models import Resume

    resume_id = _upload(db_client, fixture_pdfs)
    before = list(db_session.get(Resume, uuid.UUID(resume_id)).embedding)
    calls = len(fake_embedder.calls)

    assert db_client.put(PROFILE, json=FULL).status_code == 200
    assert len(fake_embedder.calls) == calls + 1
    input_type, texts = fake_embedder.calls[-1]
    assert input_type == "query"
    assert texts[0].startswith("Target roles: Backend Engineer, Data Engineer\n\n")

    db_session.expire_all()
    stored = db_session.get(Resume, uuid.UUID(resume_id))
    assert stored.embedding is not None and len(stored.embedding) == len(before)

    # Same roles again (other fields changed): no new embedding call.
    assert db_client.put(PROFILE, json=FULL | {"location": "Austin, TX"}).status_code == 200
    assert len(fake_embedder.calls) == calls + 1


def test_reembed_failure_still_saves_the_profile(db_client, fixture_pdfs, fake_embedder):
    _upload(db_client, fixture_pdfs)
    fake_embedder.fail = True
    r = db_client.put(PROFILE, json=FULL)
    assert r.status_code == 200 and r.json()["target_roles"] == FULL["target_roles"]


def test_role_change_without_a_resume_makes_no_call(db_client, fake_embedder):
    assert db_client.put(PROFILE, json=FULL).status_code == 200
    assert fake_embedder.calls == []
