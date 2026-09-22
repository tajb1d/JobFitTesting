"""Ingestion pipeline (plan §7): fetch → select → diff by content hash → parse, embed, upsert
→ close missing → prune → cap. Plain functions over a Session, an embedder and a fetch
callable, so tests run it with recorded payloads and a fake embedder."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingestion.sources import FetchError, NormalizedJob
from app.ingestion.vocab import TermStats, format_report, requirement_texts, unmatched_terms
from app.models import Application, Company, Job, JobRequirement
from app.services.embeddings import EmbeddingError, EmbeddingProvider
from app.services.jd_parser import ParsedJob, parse_job

logger = logging.getLogger(__name__)

# Jobs parsed, embedded and written per transaction. Progress survives a mid-run failure.
CHUNK_SIZE = 20
# The job embedding is title + description; the tail of very long postings is mostly
# benefits and legal boilerplate, so cap what we pay to embed.
JOB_EMBED_MAX_CHARS = 8000

FetchBoard = Callable[[Company], list[NormalizedJob]]


@dataclass
class Summary:
    companies_ok: int = 0
    companies_failed: int = 0
    companies_deactivated: int = 0
    jobs_fetched: int = 0
    jobs_over_cap: int = 0      # fetched but outside the per-company / corpus cap
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    jobs_closed: int = 0        # no longer listed (or fell outside the cap)
    jobs_cap_closed: int = 0    # closed by the corpus cap after this run's upserts
    jobs_pruned: int = 0
    jobs_reparsed: int = 0          # --reparse: unchanged jobs parsed again
    requirements_reembedded: int = 0  # ...whose requirement list changed
    open_jobs: int = 0
    embedding_calls: int = 0
    embedding_tokens: int = 0
    failures: list[str] = field(default_factory=list)
    # Noun chunks not in skills.json (plan §5) from this run's parsed jobs, per company.
    unmatched_terms: TermStats = field(default_factory=TermStats)
    error: str | None = None

    def render(self) -> str:
        lines = [
            "Ingestion summary",
            f"  companies   ok {self.companies_ok}, failed {self.companies_failed}"
            f" (deactivated {self.companies_deactivated})",
            f"  jobs        fetched {self.jobs_fetched}, over cap (skipped) {self.jobs_over_cap}",
            f"              new {self.jobs_new}, updated {self.jobs_updated},"
            f" unchanged {self.jobs_unchanged}, closed {self.jobs_closed + self.jobs_cap_closed},"
            f" pruned {self.jobs_pruned}",
            f"  corpus      {self.open_jobs} open jobs",
            f"  embeddings  {self.embedding_calls} API calls, {self.embedding_tokens} tokens",
        ]
        if self.jobs_reparsed:
            lines.insert(4, f"  reparsed    {self.jobs_reparsed} jobs, requirements re-embedded for"
                            f" {self.requirements_reembedded}")
        lines += [f"  failed: {f}" for f in self.failures]
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        if self.unmatched_terms:
            lines.append("Frequent unmatched terms (candidates for skills.json):")
            lines.append(format_report(self.unmatched_terms))
        return "\n".join(lines)


def _recency_key(job: NormalizedJob) -> tuple[bool, float]:
    # Most recently posted first; undated postings last.
    return (job.posted_at is None, -job.posted_at.timestamp() if job.posted_at else 0.0)


def select_jobs(
    fetched: dict[int, list[NormalizedJob]], per_company: int, cap: int
) -> dict[int, list[NormalizedJob]]:
    """Keep the most recently posted jobs: at most per_company from each board, then at most
    cap overall. Duplicate external ids within a board keep their first occurrence."""
    pool: list[tuple[int, NormalizedJob]] = []
    for company_id, jobs in fetched.items():
        unique = list({j.external_id: j for j in reversed(jobs)}.values())[::-1]
        pool += [(company_id, j) for j in sorted(unique, key=_recency_key)[:per_company]]
    pool.sort(key=lambda cj: _recency_key(cj[1]))
    selected: dict[int, list[NormalizedJob]] = {company_id: [] for company_id in fetched}
    for company_id, job in pool[:cap]:
        selected[company_id].append(job)
    return selected


def fetch_companies(
    db: Session, companies: list[Company], fetch: FetchBoard, settings: Settings, summary: Summary
) -> dict[int, list[NormalizedJob]]:
    """Fetch every board. A failure is logged and counted; after max_failures in a row the
    company goes inactive. Only successfully fetched companies appear in the result."""
    fetched: dict[int, list[NormalizedJob]] = {}
    outcomes: dict[int, str | None] = {}
    for company in companies:
        try:
            fetched[company.id] = fetch(company)
            outcomes[company.id] = None
        except FetchError as e:
            logger.warning("fetch failed for %s (%s/%s): %s",
                           company.name, company.ats, company.board_token, e)
            outcomes[company.id] = str(e)
    # Write outcomes after the network loop so no transaction is held open while fetching.
    for company in companies:
        error = outcomes[company.id]
        if error is None:
            company.consecutive_failures = 0
            summary.companies_ok += 1
            continue
        company.consecutive_failures += 1
        summary.companies_failed += 1
        note = f"{company.name} ({company.ats}/{company.board_token}): {error}"
        if company.consecutive_failures >= settings.ingestion_max_failures:
            company.active = False
            summary.companies_deactivated += 1
            note += " → deactivated"
        summary.failures.append(note)
    db.commit()
    return fetched


def _diff_company(
    db: Session, company_id: int, jobs: list[NormalizedJob], summary: Summary
) -> tuple[list[tuple[NormalizedJob, Job | None]], list[tuple[NormalizedJob, Job]]]:
    """Bump unchanged jobs and close ones no longer selected. Returns the new or changed jobs
    (paired with their existing row, if any) that need parsing and embedding, and the
    unchanged ones (for --reparse)."""
    existing = {j.external_id: j for j in db.scalars(select(Job).where(Job.company_id == company_id))}
    unchanged: list[tuple[NormalizedJob, Job]] = []
    pending: list[tuple[NormalizedJob, Job | None]] = []
    for job in jobs:
        row = existing.get(job.external_id)
        if row is not None and row.content_hash == job.content_hash:
            unchanged.append((job, row))
        else:
            pending.append((job, row))
    if unchanged:
        db.execute(update(Job).where(Job.id.in_([row.id for _, row in unchanged]))
                   .values(last_seen=func.now(), status="open"))
    summary.jobs_unchanged += len(unchanged)

    listed = {j.external_id for j in jobs}
    to_close = [r.id for ext, r in existing.items() if r.status == "open" and ext not in listed]
    if to_close:
        db.execute(update(Job).where(Job.id.in_(to_close)).values(status="closed"))
    summary.jobs_closed += len(to_close)
    return pending, unchanged


def _skills_json(parsed: ParsedJob) -> list[dict]:
    return [{"skill": s.canonical, "category": s.category, "weight": s.weight,
             "count": s.count, "sections": sorted(s.sections)} for s in parsed.skills]


def _apply_parse(row: Job, parsed: ParsedJob) -> None:
    row.description_text = parsed.text
    row.skills = _skills_json(parsed)
    row.level = parsed.level
    row.min_years = parsed.min_years


def _count_unmatched(company_id: int, parsed: list[ParsedJob], summary: Summary) -> None:
    for p in parsed:
        summary.unmatched_terms.add(company_id, unmatched_terms(requirement_texts(p.sections)))


def job_embedding_text(title: str, description_text: str) -> str:
    return f"{title}\n\n{description_text}"[:JOB_EMBED_MAX_CHARS]


def _process_chunk(
    db: Session,
    company_id: int,
    chunk: list[tuple[NormalizedJob, Job | None]],
    embedder: EmbeddingProvider,
    summary: Summary,
) -> None:
    parsed = [parse_job(job.description_html, job.title) for job, _ in chunk]
    _count_unmatched(company_id, parsed, summary)
    # Embed before touching the DB: a Voyage failure leaves this chunk's rows as they were.
    job_vectors = embedder.embed(
        [job_embedding_text(job.title, p.text) for (job, _), p in zip(chunk, parsed)], "document"
    )
    # Same input_type as pasted-description analyses (app/services/analyses.py), so corpus
    # jobs and pasted jobs score on the same scale.
    requirement_texts = [r.text for p in parsed for r in p.requirements]
    requirement_vectors = iter(embedder.embed(requirement_texts, "query"))

    for (job, row), p, vector in zip(chunk, parsed, job_vectors):
        if row is None:
            row = Job(company_id=company_id, external_id=job.external_id)
            db.add(row)
            summary.jobs_new += 1
        else:
            db.execute(delete(JobRequirement).where(JobRequirement.job_id == row.id))
            summary.jobs_updated += 1
        row.title = job.title
        row.location = job.location
        row.is_remote = job.is_remote
        row.url = job.url
        row.content_hash = job.content_hash
        _apply_parse(row, p)
        row.embedding = vector
        row.status = "open"
        row.posted_at = job.posted_at
        row.last_seen = func.now()
        db.flush()  # assigns row.id for new jobs
        db.add_all(JobRequirement(job_id=row.id, section=r.section, text=r.text,
                                  embedding=next(requirement_vectors))
                   for r in p.requirements)
    db.commit()


def _reparse_chunk(
    db: Session, company_id: int, chunk: list[tuple[NormalizedJob, Job]],
    embedder: EmbeddingProvider, summary: Summary,
) -> None:
    """Re-run the parser on unchanged jobs (after parser or skills.json changes). Skills,
    level and min_years are refreshed; requirements are re-embedded only when their list
    changed. The job embedding depends only on title + text, so it's kept."""
    parsed = [parse_job(job.description_html, job.title) for job, _ in chunk]
    _count_unmatched(company_id, parsed, summary)
    stored: dict = {}
    for r in db.scalars(select(JobRequirement)
                        .where(JobRequirement.job_id.in_([row.id for _, row in chunk]))
                        .order_by(JobRequirement.id)):
        stored.setdefault(r.job_id, []).append((r.section, r.text))
    changed = [(row, p) for (_, row), p in zip(chunk, parsed)
               if [(r.section, r.text) for r in p.requirements] != stored.get(row.id, [])]
    texts = [r.text for _, p in changed for r in p.requirements]
    vectors = iter(embedder.embed(texts, "query") if texts else [])

    for (_, row), p in zip(chunk, parsed):
        _apply_parse(row, p)
    for row, p in changed:
        db.execute(delete(JobRequirement).where(JobRequirement.job_id == row.id))
        db.add_all(JobRequirement(job_id=row.id, section=r.section, text=r.text,
                                  embedding=next(vectors)) for r in p.requirements)
    summary.jobs_reparsed += len(chunk)
    summary.requirements_reembedded += len(changed)
    db.commit()


def prune_closed_jobs(db: Session, retention_days: int) -> int:
    """Delete closed jobs not seen for retention_days. Jobs a user has an application on are
    kept (closed) so the tracker doesn't lose them: applications cascade-delete with the job."""
    result = db.execute(
        delete(Job)
        .where(Job.status == "closed")
        .where(Job.last_seen < func.now() - func.make_interval(0, 0, 0, retention_days))
        .where(~exists().where(Application.job_id == Job.id))
    )
    return result.rowcount


def enforce_corpus_cap(db: Session, cap: int) -> int:
    """Close the oldest open jobs beyond the cap (keep the most recently posted)."""
    open_count = db.scalar(select(func.count()).select_from(Job).where(Job.status == "open"))
    excess = open_count - cap
    if excess <= 0:
        return 0
    oldest = (select(Job.id).where(Job.status == "open")
              .order_by(Job.posted_at.asc().nulls_first(), Job.first_seen.asc()).limit(excess))
    db.execute(update(Job).where(Job.id.in_(oldest)).values(status="closed"))
    return excess


def run_ingestion(
    db: Session,
    embedder: EmbeddingProvider,
    fetch: FetchBoard,
    settings: Settings,
    limit_companies: int | None = None,
    reparse: bool = False,
) -> Summary:
    summary = Summary()
    query = select(Company).where(Company.active).order_by(Company.id)
    if limit_companies is not None:
        query = query.limit(limit_companies)
    companies = list(db.scalars(query))
    db.commit()

    fetched = fetch_companies(db, companies, fetch, settings, summary)
    summary.jobs_fetched = sum(len(jobs) for jobs in fetched.values())
    selected = select_jobs(fetched, settings.ingestion_max_jobs_per_company,
                           settings.ingestion_max_open_jobs)
    summary.jobs_over_cap = summary.jobs_fetched - sum(len(jobs) for jobs in selected.values())

    # Only successfully fetched companies are diffed, so a failed fetch (or a
    # --limit-companies run) never closes another company's jobs.
    diffs = {company_id: _diff_company(db, company_id, jobs, summary)
             for company_id, jobs in selected.items()}
    db.commit()

    try:
        for company_id, (pending, unchanged) in diffs.items():
            for i in range(0, len(pending), CHUNK_SIZE):
                _process_chunk(db, company_id, pending[i : i + CHUNK_SIZE], embedder, summary)
                logger.info("processed %d new/updated jobs", summary.jobs_new + summary.jobs_updated)
            if reparse:
                for i in range(0, len(unchanged), CHUNK_SIZE):
                    _reparse_chunk(db, company_id, unchanged[i : i + CHUNK_SIZE], embedder, summary)
                logger.info("reparsed %d unchanged jobs", summary.jobs_reparsed)
    except EmbeddingError as e:
        db.rollback()
        summary.error = f"embedding failed, remaining jobs left for the next run: {e}"
        logger.error(summary.error)

    summary.jobs_pruned = prune_closed_jobs(db, settings.ingestion_closed_retention_days)
    summary.jobs_cap_closed = enforce_corpus_cap(db, settings.ingestion_max_open_jobs)
    summary.open_jobs = db.scalar(select(func.count()).select_from(Job).where(Job.status == "open"))
    db.commit()

    summary.embedding_calls = getattr(embedder, "request_count", 0)
    summary.embedding_tokens = getattr(embedder, "token_count", 0)
    return summary

