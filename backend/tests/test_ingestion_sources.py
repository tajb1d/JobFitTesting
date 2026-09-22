"""ATS fetchers, normalization and selection against recorded board payloads (no network)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.ingestion.pipeline import select_jobs
from app.ingestion.seed import load_company_file
from app.ingestion.sources import (
    USER_AGENT,
    FetchError,
    NormalizedJob,
    content_hash,
    fetch_board,
    normalize_greenhouse,
    normalize_lever,
)

ATS = Path(__file__).parent / "data" / "ats"
GREENHOUSE = json.loads((ATS / "greenhouse_discord.json").read_text())
LEVER = json.loads((ATS / "lever_outreach.json").read_text())


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})


# ---------------------------------------------------------------- normalization


def test_normalize_greenhouse():
    job = normalize_greenhouse(GREENHOUSE["jobs"][0])
    assert job.external_id == "8537955002"
    assert job.title == "Engineering Manager, Notifications"
    assert job.location == "Remote" and job.is_remote
    assert job.url == "https://job-boards.greenhouse.io/discord/jobs/8537955002"
    assert job.posted_at == datetime(2026, 5, 11, 20, 30, 57, tzinfo=UTC)  # first_published
    # Greenhouse's entity-escaped HTML is decoded to text with bullets.
    assert "<" not in job.description_text and "&lt;" not in job.description_text
    assert "• 5+ years of experience as a Software Engineer" in job.description_text

    onsite = normalize_greenhouse(GREENHOUSE["jobs"][1])
    assert onsite.location == "San Francisco Bay Area" and not onsite.is_remote


def test_normalize_lever_rebuilds_lists_as_headings():
    job = normalize_lever(LEVER[0])
    assert job.external_id == "5becd4e1-3474-4f36-b5dd-4b2cd0eb1179"
    assert job.title == "Customer Advocacy Manager"
    assert job.location == "United States"
    assert job.is_remote  # workplaceType "remote", though the location doesn't say so
    assert job.url.startswith("https://jobs.lever.co/outreach/")
    assert job.posted_at == datetime.fromtimestamp(LEVER[0]["createdAt"] / 1000, tz=UTC)
    lines = job.description_text.splitlines()
    assert "Our Vision of You:" in lines  # list title kept as its own heading line
    assert "Why You’ll Love It Here" in lines  # the `additional` closing section

    hybrid = normalize_lever(LEVER[1])
    assert hybrid.location == "Seattle, WA" and not hybrid.is_remote


def test_recorded_postings_parse_into_sections_and_requirements():
    from app.services.jd_parser import parse_job

    lever = normalize_lever(LEVER[0])
    parsed = parse_job(lever.description_html, lever.title)
    assert "required" in [s.key for s in parsed.sections]  # "Our Vision of You"
    assert parsed.min_years == 5
    assert {"Databricks", "SAP"}.isdisjoint(s.canonical for s in parsed.skills)  # customer names

    gh = normalize_greenhouse(GREENHOUSE["jobs"][0])
    parsed = parse_job(gh.description_html, gh.title)
    assert parsed.level == "staff"  # "manager" (plan §8)
    assert {"Python", "Kubernetes"} <= {s.canonical for s in parsed.skills}


def test_content_hash_changes_with_title_or_text_only():
    job = normalize_greenhouse(GREENHOUSE["jobs"][0])
    assert job.content_hash == content_hash(job.title, job.description_text)
    assert content_hash("A", "text") != content_hash("B", "text")
    assert content_hash("A", "text") != content_hash("A", "text!")
    assert content_hash("A", "text") == content_hash("A", "text")


# ---------------------------------------------------------------- fetchers


def test_fetch_greenhouse_board():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=GREENHOUSE)

    jobs = fetch_board(_client(handler), "greenhouse", "discord")
    assert [j.external_id for j in jobs] == [str(j["id"]) for j in GREENHOUSE["jobs"]]
    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/discord/jobs?content=true"
    assert seen[0].headers["user-agent"] == USER_AGENT


def test_fetch_lever_board():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json=LEVER)

    jobs = fetch_board(_client(handler), "lever", "outreach")
    assert len(jobs) == 2
    assert seen == ["https://api.lever.co/v0/postings/outreach?mode=json"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"status": 404, "error": "Job not found"}),
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"unexpected": []}),
    ],
)
def test_fetch_failures_raise_fetch_error(response):
    with pytest.raises(FetchError):
        fetch_board(_client(lambda request: response), "greenhouse", "gone")


def test_network_error_raises_fetch_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(FetchError, match="request failed"):
        fetch_board(_client(handler), "lever", "x")


# ---------------------------------------------------------------- selection


def _job(ext: str, days_ago: int | None) -> NormalizedJob:
    posted = None if days_ago is None else datetime(2026, 9, 1, tzinfo=UTC) - timedelta(days=days_ago)
    return NormalizedJob(ext, f"Job {ext}", None, False, f"https://x/{ext}", "", "text", posted)


def test_select_jobs_keeps_most_recent_per_company_then_overall():
    fetched = {
        1: [_job("a-old", 30), _job("a-new", 1), _job("a-mid", 10), _job("a-undated", None)],
        2: [_job("b-new", 2), _job("b-old", 40)],
    }
    by_company = select_jobs(fetched, per_company=2, cap=10)
    assert [j.external_id for j in by_company[1]] == ["a-new", "a-mid"]
    assert [j.external_id for j in by_company[2]] == ["b-new", "b-old"]

    capped = select_jobs(fetched, per_company=2, cap=3)
    assert sorted(j.external_id for jobs in capped.values() for j in jobs) == ["a-mid", "a-new", "b-new"]


def test_select_jobs_dedupes_and_keeps_empty_companies():
    fetched = {1: [_job("a", 1), _job("a", 1)], 2: []}
    selected = select_jobs(fetched, per_company=5, cap=5)
    assert [j.external_id for j in selected[1]] == ["a"]
    assert selected[2] == []  # still diffed, so its old jobs get closed


# ---------------------------------------------------------------- companies file


def test_companies_file_is_valid():
    rows = load_company_file()
    assert 50 <= len(rows) <= 70
    assert {r["ats"] for r in rows} == {"greenhouse", "lever"}
    keys = [(r["ats"], r["board_token"]) for r in rows]
    assert len(keys) == len(set(keys))
