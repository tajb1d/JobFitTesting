import uuid

import jwt
import pytest

from app.services.auth import AuthError, verify_access_token

ME = "/api/v1/me"


def _assert_401(r):
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


# ---------------------------------------------------------------- via the API


def test_missing_token_is_401(client):
    _assert_401(client.get(ME))


def test_non_bearer_scheme_is_401(client):
    _assert_401(client.get(ME, headers={"Authorization": "Basic dXNlcjpwYXNz"}))


def test_garbage_token_is_401(client):
    _assert_401(client.get(ME, headers={"Authorization": "Bearer not-a-jwt"}))


def test_expired_token_is_401(client, make_token):
    token = make_token(expires_in=-3600)
    _assert_401(client.get(ME, headers={"Authorization": f"Bearer {token}"}))


def test_profile_write_rejected_without_token(client):
    _assert_401(client.put("/api/v1/profile", json={"target_roles": ["Engineer"]}))


# ---------------------------------------------------------------- verify_access_token


def test_valid_token_returns_sub(jwks_client, make_token):
    user_id = uuid.uuid4()
    assert verify_access_token(make_token(sub=str(user_id)), jwks_client) == user_id


def test_wrong_audience_rejected(jwks_client, make_token):
    with pytest.raises(AuthError):
        verify_access_token(make_token(aud="anon"), jwks_client)


def test_wrong_issuer_rejected(jwks_client, make_token):
    with pytest.raises(AuthError):
        verify_access_token(make_token(iss="https://evil.example/auth/v1"), jwks_client)


def test_missing_sub_rejected(jwks_client, make_token):
    # e.g. a legacy service-role token
    with pytest.raises(AuthError):
        verify_access_token(make_token(sub=None), jwks_client)


def test_non_uuid_sub_rejected(jwks_client, make_token):
    with pytest.raises(AuthError):
        verify_access_token(make_token(sub="not-a-uuid"), jwks_client)


def test_missing_kid_rejected(jwks_client, make_token):
    with pytest.raises(AuthError):
        verify_access_token(make_token(kid=None), jwks_client)


def test_unknown_kid_rejected(jwks_client, make_token):
    with pytest.raises(AuthError):
        verify_access_token(make_token(kid="rotated-away"), jwks_client)


def test_hs256_rejected(jwks_client):
    # Algorithm-confusion guard: a symmetric token must never be accepted.
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": 9999999999},
        "shared-secret-at-least-32-bytes-long!!",
        algorithm="HS256",
        headers={"kid": "test-kid"},
    )
    with pytest.raises(AuthError):
        verify_access_token(token, jwks_client)


def test_token_signed_by_other_key_rejected(jwks_client, make_token):
    from cryptography.hazmat.primitives.asymmetric import ec

    other = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": 9999999999},
        other,
        algorithm="ES256",
        headers={"kid": "test-kid"},
    )
    with pytest.raises(AuthError):
        verify_access_token(forged, jwks_client)
