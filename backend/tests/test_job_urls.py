"""Posting-link parsing (offline)."""

import pytest

from app.services.job_urls import PostingRef, UnsupportedJobUrl, parse_posting_url


@pytest.mark.parametrize("url,ref", [
    ("https://boards.greenhouse.io/stripe/jobs/7206428", PostingRef("greenhouse", "stripe", "7206428")),
    ("https://job-boards.greenhouse.io/Discord/jobs/8537955002?gh_jid=1",
     PostingRef("greenhouse", "discord", "8537955002")),
    ("http://boards.greenhouse.io/airbnb/jobs/123/", PostingRef("greenhouse", "airbnb", "123")),
    ("https://jobs.lever.co/palantir/5becd4e1-3474-4f36-b5dd-4b2cd0eb1179",
     PostingRef("lever", "palantir", "5becd4e1-3474-4f36-b5dd-4b2cd0eb1179")),
    ("https://jobs.lever.co/zoox/5BECD4E1-3474-4F36-B5DD-4B2CD0EB1179/apply",
     PostingRef("lever", "zoox", "5becd4e1-3474-4f36-b5dd-4b2cd0eb1179")),
])
def test_supported_links(url, ref):
    assert parse_posting_url(url) == ref


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/123",
    "https://stripe.com/jobs/listing/engineer/7206428",
    "https://boards.greenhouse.io/stripe",
    "https://boards.greenhouse.io.evil.com/stripe/jobs/1",
    "https://user:pw@boards.greenhouse.io/stripe/jobs/1",
    "https://boards.greenhouse.io:8443/stripe/jobs/1",
    "https://boards.greenhouse.io/stripe/jobs/abc",
    "https://jobs.lever.co/palantir",
    "javascript:alert(1)",
    "not a url",
])
def test_unsupported_links(url):
    with pytest.raises(UnsupportedJobUrl, match="Paste the job description"):
        parse_posting_url(url)


def test_api_urls_are_fixed_hosts():
    assert PostingRef("greenhouse", "stripe", "1").api_url == \
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/1"
    assert PostingRef("lever", "zoox", "a").api_url == "https://api.lever.co/v0/postings/zoox/a?mode=json"
