"""Supabase access-token verification (plan §4)."""

import uuid
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from app.config import JWT_ALGORITHMS, JWT_AUDIENCE, get_settings


class AuthError(Exception):
    """The token is missing, malformed, expired, or otherwise not acceptable. Maps to 401."""


class AuthUnavailable(Exception):
    """The JWKS endpoint could not be reached. Maps to 503, so an outage doesn't log users out."""


@lru_cache
def get_jwks_client() -> PyJWKClient:
    """Created lazily and reused. Caches the key set for 10 minutes and refetches once
    when it sees an unknown `kid` (key rotation)."""
    return PyJWKClient(
        get_settings().jwks_url,
        cache_jwk_set=True,
        lifespan=600,
        timeout=5,
    )


def verify_access_token(token: str, client: PyJWKClient) -> uuid.UUID:
    """Verify a Supabase access token and return the user's id (the `sub` claim)."""
    try:
        # Garbage fails here without a network call. Checking alg and kid up front also
        # stops kid-less or HS256 tokens from triggering JWKS refetches.
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in JWT_ALGORITHMS or not header.get("kid"):
            raise AuthError("unsupported token header")

        signing_key = client.get_signing_key(header["kid"])
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=JWT_ALGORITHMS,
            audience=JWT_AUDIENCE,
            issuer=get_settings().supabase_auth_url,
            options={"require": ["exp", "sub", "aud", "iss"]},
            leeway=10,
        )
        return uuid.UUID(claims["sub"])
    # Connection errors subclass PyJWKClientError, so they must be caught first.
    except jwt.PyJWKClientConnectionError as e:
        raise AuthUnavailable(str(e)) from e
    except (jwt.PyJWKClientError, jwt.InvalidTokenError, ValueError) as e:
        raise AuthError(str(e)) from e
