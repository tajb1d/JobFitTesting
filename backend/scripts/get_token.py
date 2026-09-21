"""Sign in the Supabase test user and print an access token.

Usage (from backend/):
    python scripts/get_token.py
    curl -H "Authorization: Bearer $(python scripts/get_token.py)" localhost:8000/api/v1/me

Reads SUPABASE_URL, SUPABASE_SECRET_KEY, TEST_USER_EMAIL and TEST_USER_PASSWORD from
backend/.env or the environment. Prints only the token to stdout; errors go to stderr.
"""

import sys
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict


class TokenSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env", extra="ignore"
    )

    supabase_url: str = ""
    supabase_secret_key: str = ""
    test_user_email: str = ""
    test_user_password: str = ""


def main() -> int:
    s = TokenSettings()
    missing = [
        name
        for name, value in [
            ("SUPABASE_URL", s.supabase_url),
            ("SUPABASE_SECRET_KEY", s.supabase_secret_key),
            ("TEST_USER_EMAIL", s.test_user_email),
            ("TEST_USER_PASSWORD", s.test_user_password),
        ]
        if not value
    ]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        r = httpx.post(
            f"{s.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": s.supabase_secret_key},
            json={"email": s.test_user_email, "password": s.test_user_password},
            timeout=10,
        )
    except httpx.HTTPError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1

    if r.status_code != 200:
        body = r.json() if "json" in r.headers.get("content-type", "") else {}
        reason = body.get("error_description") or body.get("msg") or r.text[:200]
        print(f"Sign-in failed ({r.status_code}): {reason}", file=sys.stderr)
        return 1

    print(r.json()["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
