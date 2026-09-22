"""Greenhouse and Lever posting links for POST /analyses (plan §11). Only these two public
APIs are ever called, at fixed hosts built from ids that matched strict patterns, so a user
can't make the server fetch arbitrary URLs."""

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from app.ingestion.sources import USER_AGENT, NormalizedJob, normalize_greenhouse, normalize_lever

UNSUPPORTED_MESSAGE = (
    "Only Greenhouse and Lever job links are supported. Paste the job description instead."
)
_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_LEVER_HOSTS = {"jobs.lever.co"}
_GREENHOUSE_PATH = re.compile(r"^/(?P<token>[A-Za-z0-9_-]{1,100})/jobs/(?P<id>\d{1,20})/?$")
_LEVER_PATH = re.compile(
    r"^/(?P<token>[A-Za-z0-9_.-]{1,100})/(?P<id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/apply)?/?$"
)


class UnsupportedJobUrl(Exception):
    pass


class PostingNotFound(Exception):
    """The ATS says the posting doesn't exist (removed or closed)."""


class PostingUnavailable(Exception):
    """The ATS couldn't be reached or answered unexpectedly."""


@dataclass(frozen=True)
class PostingRef:
    ats: str  # "greenhouse" | "lever"
    token: str
    external_id: str

    @property
    def api_url(self) -> str:
        if self.ats == "greenhouse":
            return f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs/{self.external_id}"
        return f"https://api.lever.co/v0/postings/{self.token}/{self.external_id}?mode=json"


def parse_posting_url(url: str) -> PostingRef:
    try:
        parts = urlsplit(url.strip())
    except ValueError as e:
        raise UnsupportedJobUrl(UNSUPPORTED_MESSAGE) from e
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.port:
        raise UnsupportedJobUrl(UNSUPPORTED_MESSAGE)
    if host in _GREENHOUSE_HOSTS and (m := _GREENHOUSE_PATH.match(parts.path)):
        return PostingRef("greenhouse", m["token"].lower(), m["id"])
    if host in _LEVER_HOSTS and (m := _LEVER_PATH.match(parts.path)):
        return PostingRef("lever", m["token"].lower(), m["id"].lower())
    raise UnsupportedJobUrl(UNSUPPORTED_MESSAGE)


def fetch_posting(client: httpx.Client, ref: PostingRef) -> NormalizedJob:
    try:
        r = client.get(ref.api_url)
    except httpx.HTTPError as e:
        raise PostingUnavailable(str(e)) from e
    if r.status_code == 404:
        raise PostingNotFound()
    if r.status_code != 200:
        raise PostingUnavailable(f"HTTP {r.status_code}")
    try:
        payload = r.json()
        return normalize_greenhouse(payload) if ref.ats == "greenhouse" else normalize_lever(payload)
    except (ValueError, KeyError, TypeError) as e:
        raise PostingUnavailable(f"unexpected payload: {e!r}") from e


@lru_cache
def get_posting_client() -> httpx.Client:
    """FastAPI dependency. Tests override it with an httpx.MockTransport client."""
    return httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT})
