import logging
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.db import get_db
from app.services.auth import AuthError, AuthUnavailable, get_jwks_client, verify_access_token

__all__ = ["get_current_user", "get_db"]

logger = logging.getLogger(__name__)

# auto_error=False: we return our own 401 (not FastAPI's 403) and keep Swagger's Authorize button.
bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Sync on purpose: PyJWKClient fetches with blocking urllib, so FastAPI must run this in
# its threadpool rather than on the event loop.
def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    client: PyJWKClient = Depends(get_jwks_client),
) -> uuid.UUID:
    """Authenticated user's id. Every route touching user data must depend on this."""
    if creds is None:
        raise _unauthorized()
    try:
        return verify_access_token(creds.credentials, client)
    except AuthUnavailable:
        logger.warning("JWKS endpoint unreachable", exc_info=True)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth provider unavailable")
    except AuthError as e:
        logger.debug("Rejected token: %s", e)
        raise _unauthorized()
