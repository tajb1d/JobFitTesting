"""run_ingestion end to end on the real DB (rolled back), with recorded board payloads as the
fetch source and a fake embedder. No live network."""

import copy
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingestion.pipeline import run_ingestion
from app.ingestion.seed import seed_companies
from app.ingestion.sources import FetchError, normalize_greenhouse, normalize_lever
from app.models import Application, Company, Job, JobRequirement
from app.services.jd_parser import parse_job
from tests.conftest import FakeEmbedder

pytestmark = pytest.mark.db

ATS = Path(__file__).parent / "data" / "ats"
GREENHOUSE = json.loads((ATS / "greenhouse_discord.json").read_text())["jobs"]
LEVER = json.loads((ATS / "lever_outreach.json").read_text())


@pytest.fixture
def isolated(db_session: Session) -> Session:
    """Hide the real corpus inside the rolled-back transaction: deactivate every company and
    close every job (seen now, so pruning leaves them alone)."""
    db_session.execute(text("UPDATE companies SET active = false"))
    db_session.execute(text("UPDATE jobs SET status = 'closed', last_seen = now()"))
    return db_session


@pytest.fixture
def companies(isolated: Session) -> tuple[Company, Company]:
    suffix = uuid.uuid4().hex[:8]
    gh = Company(name="Discord (test)", ats="greenhouse", board_token=f"test-gh-{suffix}")
    lv = Company(name="Outreach (test)", ats="lever", board_token=f"test-lv-{suffix}")
    isolated.add_all([gh, lv])
    isolated.commit()
    return gh, lv


def _settings(**overrides):
    base = {"ingestion_max_open_jobs": 1000, "ingestion_max_jobs_per_company": 50}
    return get_settings().model_copy(update=base | overrides)


def _boards(gh_jobs=GREENHOUSE, lever_jobs=LEVER):
    return {"greenhouse": [normalize_greenhouse(j) for j in gh_jobs],
            "lever": [normalize_lever(p) for p in lever_jobs]}


def _fetcher(boards: dict, fail: set[str] = frozenset()):
    calls = []

    def fetch(company: Company):
        calls.append(company.ats)
        if company.ats in fail:
            raise FetchError("HTTP 404")
        return boards[company.ats]

    fetch.calls = calls
    return fetch


def _jobs(db: Session, company: Company) -> dict[str, Job]:
    db.expire_all()
    return {j.external_id: j for j in db.scalars(select(Job).where(Job.company_id == company.id))}


def _requirements(db: Session, job: Job) -> list[JobRequirement]:
    return list(db.scalars(select(JobRequirement).where(JobRequirement.job_id == job.id)
                           .order_by(JobRequirement.id)))


def test_first_run_stores_parsed_embedded_jobs(isolated, companies, fake_embedder):
    gh, lv = companies
    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())

    assert (summary.companies_ok, summary.companies_failed) == (2, 0)
    assert (summary.jobs_fetched, summary.jobs_new, summary.jobs_unchanged) == (5, 5, 0)
    assert summary.open_jobs == 5

    jobs = _jobs(isolated, lv)
    job = jobs["5becd4e1-3474-4f36-b5dd-4b2cd0eb1179"]
    expected = parse_job(normalize_lever(LEVER[0]).description_html, job.title)
    assert job.status == "open" and job.is_remote
    assert job.level == "staff" and job.min_years == 5
    assert job.description_text == expected.text
    assert job.embedding is not None and len(job.embedding) > 0
    assert job.skills == [{"skill": s.canonical, "category": s.category, "weight": s.weight,
                           "count": s.count, "sections": sorted(s.sections)} for s in expected.skills]
    reqs = _requirements(isolated, job)
    assert [(r.section, r.text) for r in reqs] == [(r.section, r.text) for r in expected.requirements]
    assert all(r.embedding is not None for r in reqs)
    assert len(_jobs(isolated, gh)) == 3

    # Jobs as documents (for retrieval), requirements as queries (same as pasted analyses).
    assert {input_type for input_type, _ in fake_embedder.calls} == {"document", "query"}
    # Noun chunks missing from skills.json are counted for the report (plan §5).
    assert summary.unmatched_terms
    assert "Frequent unmatched terms" in summary.render()


def test_reparse_refreshes_unchanged_jobs_without_reembedding_them(
    isolated, companies, fake_embedder
):
    gh = companies[0]
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())
    stale, fresh = _jobs(isolated, gh)["8537955002"], _jobs(isolated, gh)["8806482002"]
    fresh_requirements = [(r.id, r.text) for r in _requirements(isolated, fresh)]
    stale_embedding = list(stale.embedding)
    # Simulate a job stored by an older parser: wrong skills and a missing requirement.
    stale.skills, stale.level = [], None
    isolated.delete(_requirements(isolated, stale)[0])
    isolated.commit()
    calls_before = len(fake_embedder.calls)

    plain = run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())
    assert plain.jobs_unchanged == 5 and plain.jobs_reparsed == 0
    assert _jobs(isolated, gh)["8537955002"].skills == []  # hash unchanged, so not reparsed

    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings(), reparse=True)
    assert (summary.jobs_unchanged, summary.jobs_reparsed, summary.requirements_reembedded) == (5, 5, 1)
    stale = _jobs(isolated, gh)["8537955002"]
    expected = parse_job(normalize_greenhouse(GREENHOUSE[0]).description_html, stale.title)
    assert stale.level == "staff" and stale.skills
    assert [r.text for r in _requirements(isolated, stale)] == [r.text for r in expected.requirements]
    assert list(stale.embedding) == stale_embedding          # job vector kept
    new_calls = fake_embedder.calls[calls_before:]
    assert [t for t, _ in new_calls] == ["query"]            # one job's requirements only
    # Jobs whose requirement list didn't change keep their rows (and vectors).
    assert [(r.id, r.text) for r in _requirements(isolated, _jobs(isolated, gh)["8806482002"])] == fresh_requirements


def test_second_run_only_bumps_unchanged_jobs(isolated, companies, fake_embedder):
    boards = _boards()
    run_ingestion(isolated, fake_embedder, _fetcher(boards), _settings())
    calls_after_first = len(fake_embedder.calls)
    isolated.execute(text("UPDATE jobs SET last_seen = now() - interval '1 day' "
                          "WHERE company_id = :c"), {"c": companies[0].id})

    summary = run_ingestion(isolated, fake_embedder, _fetcher(boards), _settings())
    assert (summary.jobs_new, summary.jobs_updated, summary.jobs_unchanged) == (0, 0, 5)
    assert len(fake_embedder.calls) == calls_after_first  # nothing re-embedded
    now = isolated.scalar(select(func.now()))
    assert all(j.last_seen == now for j in _jobs(isolated, companies[0]).values())


def test_changed_job_is_reparsed_and_requirements_replaced(isolated, companies, fake_embedder):
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())
    job = _jobs(isolated, companies[0])["8537955002"]
    old_requirement_ids = {r.id for r in _requirements(isolated, job)}

    edited = copy.deepcopy(GREENHOUSE)
    edited[0]["title"] = "Senior Software Engineer, Notifications"
    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards(gh_jobs=edited)), _settings())

    assert (summary.jobs_updated, summary.jobs_unchanged) == (1, 4)
    job = _jobs(isolated, companies[0])["8537955002"]
    assert job.title == "Senior Software Engineer, Notifications" and job.level == "senior"
    new_requirements = _requirements(isolated, job)
    assert new_requirements and old_requirement_ids.isdisjoint(r.id for r in new_requirements)


def test_missing_jobs_close_and_reopen_when_listed_again(isolated, companies, fake_embedder):
    gh = companies[0]
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())

    summary = run_ingestion(isolated, fake_embedder,
                            _fetcher(_boards(gh_jobs=GREENHOUSE[1:])), _settings())
    assert summary.jobs_closed == 1
    assert _jobs(isolated, gh)["8537955002"].status == "closed"

    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())
    assert (summary.jobs_unchanged, summary.jobs_new) == (5, 0)  # same content: no re-embed
    assert _jobs(isolated, gh)["8537955002"].status == "open"


def test_failed_fetch_keeps_jobs_and_deactivates_after_three(isolated, companies, fake_embedder):
    gh, lv = companies
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())

    for attempt in range(1, 4):
        summary = run_ingestion(isolated, fake_embedder,
                                _fetcher(_boards(), fail={"lever"}), _settings())
        isolated.refresh(lv)
        assert lv.consecutive_failures == attempt
        assert summary.companies_failed == 1 and summary.jobs_closed == 0
    assert not lv.active and summary.companies_deactivated == 1
    assert "deactivated" in summary.failures[0]
    # A failed fetch never closes the company's existing jobs.
    assert all(j.status == "open" for j in _jobs(isolated, lv).values())

    # Inactive companies aren't fetched; a success resets the counter.
    fetch = _fetcher(_boards())
    run_ingestion(isolated, fake_embedder, fetch, _settings())
    assert fetch.calls == ["greenhouse"]
    isolated.refresh(gh)
    assert gh.consecutive_failures == 0


def test_limit_companies_leaves_other_companies_alone(isolated, companies, fake_embedder):
    gh, lv = companies
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())

    fetch = _fetcher(_boards())
    summary = run_ingestion(isolated, fake_embedder, fetch, _settings(), limit_companies=1)
    assert fetch.calls == ["greenhouse"]  # lowest id first
    assert summary.jobs_closed == 0
    assert all(j.status == "open" for j in _jobs(isolated, lv).values())


def test_corpus_and_per_company_caps_keep_most_recent(isolated, companies, fake_embedder):
    gh, lv = companies
    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards()),
                            _settings(ingestion_max_jobs_per_company=2))
    assert summary.jobs_over_cap == 1 and summary.jobs_new == 4
    # Discord's oldest posting (first_published 2026-05-11) is left out.
    assert set(_jobs(isolated, gh)) == {"8806482002", "8571766002"}

    # A lower corpus cap closes the oldest open jobs, whichever company they belong to.
    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards()),
                            _settings(ingestion_max_jobs_per_company=2, ingestion_max_open_jobs=2))
    open_ids = {ext for c in companies for ext, j in _jobs(isolated, c).items() if j.status == "open"}
    assert open_ids == {"8806482002", "3c27d8b2-bdef-4a0c-8507-092ef90fab33"}  # Sep 15, Jul 9
    assert summary.open_jobs == 2


def test_prune_deletes_old_closed_jobs_but_keeps_applications(
    isolated, companies, fake_embedder, test_user_id
):
    gh = companies[0]
    run_ingestion(isolated, fake_embedder, _fetcher(_boards()), _settings())
    jobs = _jobs(isolated, gh)
    saved, stale, recent = jobs["8537955002"], jobs["8806482002"], jobs["8571766002"]
    isolated.add(Application(user_id=test_user_id, job_id=saved.id))
    isolated.execute(text("UPDATE jobs SET status = 'closed', last_seen = now() - interval '15 days' "
                          "WHERE id IN (:a, :b)"), {"a": saved.id, "b": stale.id})
    isolated.execute(text("UPDATE jobs SET status = 'closed', last_seen = now() - interval '5 days' "
                          "WHERE id = :c"), {"c": recent.id})
    isolated.commit()

    # Discord's board is empty now, so nothing reopens these three.
    summary = run_ingestion(isolated, fake_embedder, _fetcher(_boards(gh_jobs=[])), _settings())
    remaining = _jobs(isolated, gh)
    assert summary.jobs_pruned == 1
    assert set(remaining) == {"8537955002", "8571766002"}  # saved job and 5-day-old job kept
    assert remaining["8537955002"].status == "closed"


def test_embedding_failure_keeps_earlier_work_and_reports(isolated, companies):
    summary = run_ingestion(isolated, FakeEmbedder(fail=True), _fetcher(_boards()), _settings())
    assert summary.error and "embedding failed" in summary.error
    assert summary.jobs_new == 0
    assert summary.companies_ok == 2  # fetch outcomes were committed
    assert _jobs(isolated, companies[0]) == {}


def test_seed_upserts_names_without_reactivating(isolated):
    token = f"test-seed-{uuid.uuid4().hex[:8]}"
    seed_companies(isolated, [{"name": "Old", "ats": "lever", "board_token": token}])
    company = isolated.scalar(select(Company).where(Company.board_token == token))
    company.active, company.consecutive_failures = False, 3
    isolated.commit()

    seed_companies(isolated, [{"name": "New", "ats": "lever", "board_token": token}])
    isolated.expire_all()
    rows = list(isolated.scalars(select(Company).where(Company.board_token == token)))
    assert len(rows) == 1
    assert (rows[0].name, rows[0].active, rows[0].consecutive_failures) == ("New", False, 3)
