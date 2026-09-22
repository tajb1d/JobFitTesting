"""ATS fetchers and normalization. Each board's postings become NormalizedJobs; everything
downstream is ATS-agnostic."""

import hashlib
import html
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.services.jd_parser import html_to_text

USER_AGENT = "JobFit/0.1 (student job-matching project; daily ingestion of public job boards)"
GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"


class FetchError(Exception):
    """A board could not be fetched or wasn't in the expected shape."""


@dataclass
class NormalizedJob:
    external_id: str
    title: str
    location: str | None
    is_remote: bool
    url: str
    description_html: str
    description_text: str
    posted_at: datetime | None

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.description_text)


def content_hash(title: str, description_text: str) -> str:
    return hashlib.sha256(f"{title}\n{description_text}".encode()).hexdigest()


def make_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def fetch_board(client: httpx.Client, ats: str, token: str) -> list[NormalizedJob]:
    """Fetch and normalize one board. Raises FetchError on any failure (404 included)."""
    url = (GREENHOUSE_URL if ats == "greenhouse" else LEVER_URL).format(token=token)
    try:
        r = client.get(url)
    except httpx.HTTPError as e:
        raise FetchError(f"request failed: {e}") from e
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code}")
    try:
        payload = r.json()
        if ats == "greenhouse":
            return [normalize_greenhouse(j) for j in payload["jobs"]]
        return [normalize_lever(p) for p in payload]
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError(f"unexpected payload: {e!r}") from e


def _is_remote(*values: str | None) -> bool:
    return any(v and "remote" in v.lower() for v in values)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_greenhouse(job: dict) -> NormalizedJob:
    # Greenhouse returns `content` HTML-escaped; html_to_text unescapes it.
    description_html = job.get("content") or ""
    location = ((job.get("location") or {}).get("name") or "").strip() or None
    return NormalizedJob(
        external_id=str(job["id"]),
        title=job["title"].strip(),
        location=location,
        is_remote=_is_remote(location),
        url=job["absolute_url"],
        description_html=description_html,
        description_text=html_to_text(description_html),
        posted_at=_parse_iso(job.get("first_published") or job.get("updated_at")),
    )


def normalize_lever(posting: dict) -> NormalizedJob:
    # Lever splits a posting into an intro, titled lists (e.g. "Requirements") and a closing
    # section. Rebuild one document so the parser sees the list titles as headings.
    parts = [posting.get("description") or ""]
    for lst in posting.get("lists") or []:
        heading = html.escape((lst.get("text") or "").strip())
        parts.append(f"<h3>{heading}</h3><ul>{lst.get('content') or ''}</ul>")
    parts.append(posting.get("additional") or "")
    description_html = "\n".join(p for p in parts if p)

    categories = posting.get("categories") or {}
    location = (categories.get("location") or "").strip() or None
    created = posting.get("createdAt")
    return NormalizedJob(
        external_id=str(posting["id"]),
        title=posting["text"].strip(),
        location=location,
        is_remote=posting.get("workplaceType") == "remote" or _is_remote(location),
        url=posting["hostedUrl"],
        description_html=description_html,
        description_text=html_to_text(description_html),
        posted_at=datetime.fromtimestamp(created / 1000, tz=UTC) if created else None,
    )
